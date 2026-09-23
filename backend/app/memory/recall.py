"""Source-grouped, permission-filtered memory for ordinary actions as well as recall.

The caller supplies the visible active branch. Selection never mutates world state;
the full source ledger stays on the server and only selected short references travel.
"""

import json
import re
from hashlib import sha256

from sqlalchemy import select

from app.memory.events import epistemic_event, story_events
from app.memory.facts import fact_records


def public_historical_quotes(context):
    """Only selected public original words can license historical source text."""
    brief = context.get("response_brief", {})
    entries = brief.get("historical_memory", context.get("memory_evidence", []))
    return [r["text"] for r in entries
            if r.get("source", {}).get("visibility") == "public"
            and r.get("kind") in {"source_text", "npc_statement"}
            and r.get("text") and r.get("historical_only")]


def terms(value):
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value.lower())
    return {w for w in words if len(w) > 1} | {
        w[i:i + 2] for w in words for i in range(len(w) - 1)
    }


def source(event, scene=None):
    payload = event["payload"]
    return {
        "ref": f"e{event['seq']}", "seq": event["seq"],
        "scene": payload.get("scene_id") or scene,
        "turn": payload.get("cycle_id"), "time": event.get("occurred_at", event.get("created_at")),
        "actor": payload.get("actor_id") or event.get("actor_member_id"),
        "visibility": event.get("visibility", "public"),
    }


def quote_chunks(record, query, *, size=640):
    """Select complete source slices, with offsets and explicit partial coverage.

    Prefer sentence boundaries. Long unbroken text uses overlapping source slices;
    none is presented as the complete source or as a new compressed assertion.
    """
    text = record.get("text", "")
    if len(text) <= size:
        return [record]
    spans, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = max(text.rfind(c, start + size // 2, end) for c in "。！？；\n")
            if boundary >= 0:
                end = boundary + 1
        spans.append((start, end))
        if end == len(text):
            break
        start = end if text[end - 1] in "。！？；\n" else end - 128
    wanted = terms(query)
    ranked = sorted(spans, key=lambda s: len(wanted & terms(text[s[0]:s[1]])), reverse=True)
    return [{**record, "text": text[a:b], "excerpt": {"start": a, "end": b,
            "total": len(text), "partial": True}} for a, b in ranked[:2]]


def _identifiers(value):
    """Only explicit entity links establish identity, never a shared display name."""
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"id", "entity_id", "target_id", "target_entity_id", "action_target_id",
                       "item_id", "item_instance_id"} and isinstance(item, str):
                result.add(item)
            elif key in {"target_ids", "entity_ids", "related_entity_ids", "item_ids",
                         "item_instance_ids", "remaining_item_instance_ids"}:
                result.update(i for i in item or [] if isinstance(i, str))
            elif isinstance(item, (dict, list)):
                result.update(_identifiers(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_identifiers(item))
    return result


def _requested_result(query):
    """Reserve outcomes for an outcome/state question, not incidental scene overlap."""
    return bool(re.search(
        r"结果|成败|成功|失败|检定|骰|回执|资源|数值|魔法值|生命值|理智|血量|"
        r"实际状态|当前状态|完成了吗|做成了吗|拿到了吗|交出了吗|"
        r"(?:回顾|回想|核对|确认|追溯).{0,24}(?:转交|交接|持有|保管|操作|移动|位置)|"
        r"(?:现在|目前|当前).{0,12}(?:在哪|哪里|位置|谁持有|谁保管|谁拿着)|"
        r"(?:由谁|在谁)(?:的)?(?:持有|保管|拿着|手里|手中)", query, re.I))


def _legacy_check_target_requested(fact, query):
    # Older checks lack entity IDs. Their recorded purpose can still name the
    # exact current object, e.g. "本次确认密码盘" -> "密码盘". Require the whole
    # remaining phrase, never a shared speaker or arbitrary word overlap.
    purpose = fact.get("check_purpose", "").strip().rstrip("。！？；,;.!?")
    target = re.sub(
        r"^(?:(?:本次|这次|此前|之前|现在|我(?:们)?|先|再|继续|尝试|仔细|"
        r"确认|核对|检查|查看|观察|调查|搜索|寻找)\s*)+", "", purpose)
    return bool(2 <= len(target) <= 40 and target in query)


def _exact_value_requested(material, demand):
    """A relevant code is exact evidence; unrelated years/prices are not all mandatory."""
    if re.search(r"密码|口令|暗号|编号|代码|账号|账户|电话", material):
        return True
    for match in re.finditer(
        r"(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百千万两]+)\s*"
        r"(美元|英镑|元|年|月|日|小时|分钟|本|件|个|把|张|枚|份|支|瓶|扇)", material
    ):
        unit = match[1]
        if match[0] in demand:
            return True  # A pending task may name its own exact value.
        if unit in {"美元", "英镑", "元"}:
            requested = r"美元|英镑|多少钱|金额|价格|费用|酬金|报酬|支付|付款"
        elif unit in {"年", "月", "日", "小时", "分钟"}:
            requested = r"年份|哪年|哪一年|哪月|哪天|几几年|日期|何时|几时|多久|多长时间"
        elif unit == "本":
            requested = r"本|藏书|书名|书籍|书本|数量|数目"
        else:
            requested = re.escape(unit) + r"|数量|数目|多少|几个"
        if re.search(requested, demand):
            return True
    return False


def reference_resolution(events, query, scene_id, targets, target_ids, metadata):
    """Resolve references against caller-authorized current targets and visible history.

    The result guides evidence search only. It grants no authority to execute an
    action, and a missing/ambiguous antecedent never becomes a guessed target.
    """
    current = {t["id"]: t for t in targets if t.get("id")
               and t.get("fact_scope", "current_scene") == "current_scene"
               and (not t.get("scene_id") or not scene_id or t["scene_id"] == scene_id)}

    def named(text):
        return {eid for eid, target in current.items()
                if any(name and name in text for name in
                       [target.get("title", ""), *target.get("aliases", [])])}

    explicit = set(target_ids) & current.keys()
    names = named(query)
    resolved = explicit or names
    status, refs = ("explicit" if explicit else "named"), []
    # Multiple explicitly named objects remain a multi-target request. A shared
    # alias which refers to several entities is ambiguous, not multi-target.
    if len(names) > 1 and not explicit:
        unique = {eid for eid in names if any(
            name and name in query and sum(name in [t.get("title", ""), *t.get("aliases", [])]
                                          for t in current.values()) == 1
            for name in [current[eid].get("title", ""), *current[eid].get("aliases", [])])}
        if unique != names:
            resolved, status = set(), "ambiguous"
    deictic = bool(re.search(
        r"(?:这|那)(?:扇|个|道|把|件|张)(?:门|入口|锁|物品|钥匙)?|刚才那个", query))
    code_reference = bool(re.search(r"(?:刚才|这|那).{0,5}(?:暗号|口令|密码)", query))
    door_reference = bool(re.search(r"(?:这|那)(?:扇|道|个)?(?:门|入口)", query))
    if not resolved and status != "ambiguous" and deictic and not code_reference:
        status = "unresolved"
        for event in reversed(events[-8:]):
            if scene_id and metadata[event["seq"]]["scene_id"] != scene_id:
                continue
            payload = event["payload"]
            mentions = (_identifiers(payload) & current.keys()) | named(
                payload.get("text", "") + payload.get("content", ""))
            if door_reference:
                mentions = {eid for eid in mentions if any(re.search(r"门|入口|通道", name)
                    for name in [current[eid].get("title", ""), *current[eid].get("aliases", [])])}
            if not mentions:
                continue
            if len(mentions) == 1:
                resolved, status, refs = mentions, "recent_reference", [event["seq"]]
            else:
                status = "ambiguous"
            break
    if not resolved and not deictic and status != "ambiguous":
        status = "none"
    # "刚才那个暗号" can refer to an original quote without naming any entity.
    if not resolved and status != "ambiguous" and code_reference:
        status = "unresolved"
        for event in reversed(events[-8:]):
            if scene_id and metadata[event["seq"]]["scene_id"] != scene_id:
                continue
            text = (event["payload"].get("public_summary")
                    or event["payload"].get("content") or event["payload"].get("text", ""))
            if event["type"] in {"clue.revealed", "entity.revealed", "npc.spoke"} and re.search(
                r"暗号|口令|密码", text
            ):
                if len(re.findall(r"(?:暗号|口令|密码)\s*[：:]", text)) > 1:
                    status = "ambiguous"
                else:
                    refs, status = [event["seq"]], "recent_quote"
                break
    return {"status": status, "target_ids": sorted(resolved), "source_seqs": refs,
            "historical_only": True}


def select_memory(events, query, *, scene_id=None, tasks=(), memories=(), budget=2600,
                  targets=(), target_ids=()):
    """Select exact facts and related segments without a read-only question gate."""
    events, _ = story_events(events, include_initial_reveals=True)
    from app.memory.segments import source_metadata_map

    source_metadata = source_metadata_map(events)
    by_seq = {e["seq"]: e for e in events}
    resolution = reference_resolution(events, query, scene_id, targets, target_ids, source_metadata)
    resolved_ids = set(resolution["target_ids"])
    task_ids = set().union(*(_identifiers(t) for t in tasks)) if tasks else set()
    target_words = " ".join(" ".join([t.get("title", ""), *t.get("aliases", [])])
                            for t in targets if t.get("id") in resolved_ids)
    demand = query + " " + target_words + " " + " ".join(t.get("text", "") for t in tasks)
    wanted = terms(demand)

    def relevance(event, material):
        identifiers = _identifiers(event["payload"])
        linked = bool(identifiers & (resolved_ids | task_ids)
                      or event["seq"] in resolution["source_seqs"])
        lexical = len(wanted & terms(material))
        if not lexical and not linked:
            return 0
        local = bool(scene_id and source_metadata[event["seq"]]["scene_id"] == scene_id)
        return lexical + 30 * linked + 8 * local
    candidates = []
    facts = fact_records(events)
    represented = {f["source_event_seq"] for f in facts}
    for event in events:
        if event["seq"] not in represented and event["type"] in {
            "check.resolved", "combat.receipt", "combat.resolved", "combat.damage_confirmed",
            "sanity.progressed", "sanity.involuntary", "sanity.state_changed",
            "check.luck_spent", "check.consequence_applied",
        }:
            payload = event["payload"]
            facts.append({"kind": "resource_result" if event["type"] != "check.resolved"
                          else "result", "source_event_seq": event["seq"],
                          "text": payload.get("summary") or payload.get("display_text")
                          or payload.get("reason") or payload.get("name") or event["type"],
                          "exact_payload": payload})
    for fact in facts:
        event = by_seq.get(fact["source_event_seq"])
        if not event:
            continue
        p = event["payload"]
        material = " ".join(str(fact.get(k) or "") for k in (
            "text", "title", "speaker", "action_quote", "check_purpose"
        ))
        score = relevance(event, material)
        if not score:
            continue
        record = {
            "kind": fact["kind"], "text": fact["text"],
            "source": source(event, fact.get("scene_id")
                             or source_metadata[event["seq"]]["scene_id"]),
            "epistemic": epistemic_event(event)["epistemic_status"],
            "historical_only": True,
            **{k: fact[k] for k in ("title", "speaker", "result_fact") if fact.get(k)},
        }
        # Exact numerical/outcome/ownership fields outrank prose interpretation.
        fields = {k: p[k] for k in (
            "result", "operation", "operations", "passed", "operated_items", "resource_changes",
            "before", "after", "holder_id", "previous_holder_id", "target_member_id",
            "from_member_id", "to_member_id", "quantity",
            "entity_id", "target_id", "action_target_id", "item_id", "item_ids",
            "item_instance_id", "item_instance_ids", "recipient_member_id",
        ) if k in p}
        if fields:
            record["fields"] = fields
        if "exact_payload" in fact:
            record["fields"] = fact["exact_payload"]
        # A current observation can mention the same room as many old rolls or
        # moves. Only a requested outcome or an actual object/task link makes
        # those receipts mandatory. Actor/executor identity grants no such link.
        required = bool(
            fact["kind"] in {"result", "resource_result"} and (
                _requested_result(query)
                or _legacy_check_target_requested(fact, query)
                or (_identifiers(p) | _identifiers(fact.get("result_fact", {})))
                & (resolved_ids | task_ids)
            )
            or fact["kind"] in {"source_text", "npc_statement"} and (
                _exact_value_requested(fact.get("title", "") + fact.get("text", ""), demand)
                or re.search(r"原文|原话", query)
                or fact["kind"] == "npc_statement" and re.search(
                    r"证词|说法|说过|更正|原始", query)
                or _identifiers(p) & (resolved_ids | task_ids)
                or event["seq"] in resolution["source_seqs"]
            )
        )
        for chunk in quote_chunks(record, query + " " + target_words):
            candidates.append((required, score, event["seq"], chunk))

    # A promise remains attributed speech; no inferred completed operation or new task.
    for event in events:
        if event["type"] not in {"agent.spoke", "chat.message"}:
            continue
        text = event["payload"].get("text", "")
        if re.search(r"答应|承诺|我(?:会|来|负责)|等.{0,12}(?:再|就)", text):
            score = relevance(event, text)
            if score:
                record = {"kind": "commitment_candidate", "text": text,
                          "source": source(event, source_metadata[event["seq"]]["scene_id"]),
                          "epistemic": "attributed_intent_not_execution",
                          "historical_only": True}
                candidates.extend((False, score, event["seq"], r)
                                  for r in quote_chunks(record, query))

    selected, omitted, deduplicated, used = [], [], [], 2
    required_omitted, required_refs = [], []

    def add(record, required=False):
        nonlocal used
        size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":"))) + 1
        ref = record.get("source", {}).get("ref", "task")
        if used + size <= budget:
            selected.append(record)
            used += size
            if required:
                required_refs.append(ref)
            return True
        else:
            miss = {"ref": ref, "reason": "character_budget", "kind": record["kind"]}
            omitted.append(miss)
            if required:
                required_omitted.append(miss)
        return False

    ordered = sorted(candidates, key=lambda c: c[:3], reverse=True)
    for record in [*tasks, *(r for required, _, _, r in ordered if required)]:
        add(record, required=True)

    from app.memory.segments import active_segments

    ranked, seen_segments, segment_source_text = [], set(), {}
    covered_exact = {r["source"]["seq"] for r in selected
                     if r.get("source", {}).get("seq") is not None and not r.get("excerpt")
                     and r.get("kind") not in {"pending_task", "short_term_goal"}}
    by_id = {m.id: m for m in memories}
    for doc in active_segments(memories, events):
        memory = by_id[doc["segment_id"]]
        summary = doc.get("summary", {})
        text = summary.get("content", "") if isinstance(summary, dict) else str(summary)
        chunks = doc.get("source_chunks", [])
        seqs = {c["seq"] for c in chunks}
        score = len(wanted & terms(text + json.dumps(doc.get("index", {}), ensure_ascii=False)))
        if any(_identifiers(by_seq[seq]["payload"]) & (resolved_ids | task_ids)
               for seq in seqs if seq in by_seq):
            score += 30
        if score and scene_id in doc.get("index", {}).get("scenes", []):
            score += 8
        if score:
            ranked.append((score, memory.coverage_end or 0, memory, doc, summary))
    for _, _, memory, doc, summary in sorted(ranked, key=lambda r: r[:2], reverse=True):
        chunks = doc.get("source_chunks", [])
        seqs = {c["seq"] for c in chunks}
        signature = (memory.scope, tuple(sorted(
            (c["seq"], c["start"], c["end"], c["digest"]) for c in chunks)))
        if signature in seen_segments or seqs <= covered_exact:
            deduplicated.append({"ref": f"s{memory.coverage_start}-{memory.coverage_end}",
                                 "reason": "same_source_range" if signature in seen_segments
                                 else "exact_source_already_selected"})
            continue
        # Full exact positions/tasks live in the segment ledger and selected
        # fact/task records above. Do not resend that ledger inside prose.
        prose = {"content": summary.get("content", "")} if isinstance(summary, dict) else summary
        # Bound summaries render per-source lines. Remove only whole lines whose
        # complete original quote has already been reserved, retaining other events.
        if isinstance(prose, dict):
            lines = prose["content"].splitlines()
            prose["content"] = "\n".join(line for line in lines if not any(
                line.startswith(f"[e{seq} ") for seq in covered_exact))
        record = {"kind": "segment", "summary": prose, "historical_only": True,
                  "epistemic": "derived_summary_not_fact_authority",
                  "source": {"ref": f"s{memory.coverage_start}-{memory.coverage_end}",
                             "range": [memory.coverage_start, memory.coverage_end],
                             "hash": sha256(repr(signature).encode()).hexdigest()[:12],
                             "visibility": memory.scope},
                  "scenes": doc.get("index", {}).get("scenes", [])}
        if len([r for r in selected if r["kind"] == "segment"]) < 2:
            if add(record):
                seen_segments.add(signature)
                for seq in seqs:
                    segment_source_text.setdefault(seq, []).append(
                        prose.get("content", "") if isinstance(prose, dict) else str(prose))
        else:
            omitted.append({"ref": record["source"]["ref"], "reason": "segment_limit",
                            "kind": "segment"})
    # Supplementary original words come last; their source is already represented
    # when a bound segment was selected. Exact facts above are never displaced.
    for required, _, seq, record in ordered:
        if required:
            continue
        original = record.get("text", "")
        quoted = json.dumps(original, ensure_ascii=False)[1:-1]
        if original and any(original in text or quoted in text
                            for text in segment_source_text.get(seq, [])):
            deduplicated.append({"ref": record["source"]["ref"],
                                 "reason": "segment_source_selected"})
        else:
            add(record)
    return selected, {"selected_refs": [r["source"]["ref"] for r in selected],
                      "omitted": omitted, "source_group_count": len(by_seq),
                      "required_omitted": required_omitted,
                      "required_refs": list(dict.fromkeys(required_refs)),
                      "required_complete": not required_omitted,
                      "deduplicated": deduplicated, "reference_resolution": resolution,
                      "coverage_claim": "selected exact fields and recoverable sources only"}


async def visible_tasks(session, room_id, events, *, member_id=None, keeper=False, narrator=False):
    """Reuse BehaviorState; private goals are only visible to their owner and KP."""
    from app.memory.segments import source_metadata_map
    from app.persistence.adjudication_models import AgentBehaviorRecord

    by_seq = {e["seq"]: e for e in events}
    metadata = source_metadata_map(events)
    result = []
    for row in await session.scalars(select(AgentBehaviorRecord).where(
        AgentBehaviorRecord.room_id == room_id
    )):
        if not keeper and not narrator and row.member_id != member_id:
            continue
        state = row.document
        for request in state.get("pending_requests", []):
            event = by_seq.get(request.get("source_event_seq"))
            if not event or narrator and event.get("visibility") != "public":
                continue
            result.append({"kind": "pending_task", "text": request.get("text", ""),
                           "owner": row.member_id, "status": request.get("status", "pending"),
                           "operations": request.get("operations", []),
                           **{key: request[key] for key in (
                               "request_id", "executor_member_id", "target_id", "item_ids",
                               "item_instance_ids", "recipient_member_id", "quantity",
                               "remaining_quantity", "remaining_item_instance_ids",
                               "completion_event_seqs",
                           ) if key in request},
                           "source": source(event, metadata[event["seq"]]["scene_id"]),
                           "epistemic": "requested_not_executed", "historical_only": True})
        goal = state.get("current_short_term_goal")
        if goal and not narrator and (keeper or row.member_id == member_id):
            result.append({"kind": "short_term_goal", "text": goal, "owner": row.member_id,
                           "status": state.get("task_status", "pending"),
                           "source": {"ref": "behavior:" + row.member_id,
                                      "turn": state.get("task_cycle_id"),
                                      "scene": state.get("task_scene_id"),
                                      "visibility": "agent_private"},
                           "epistemic": "intent_not_execution", "historical_only": True})
    return result
