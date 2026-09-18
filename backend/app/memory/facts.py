"""Small, disposable evidence views of permission-filtered original events.

No second world state: IDs, quotes, speakers and outcomes come from the active
event branch. Summaries and model narration never establish possessions/actions.
"""

import json
import re

from app.knowledge.text import tokens
from app.memory.events import story_events


def recall_question(text):
    return bool(
        re.search(
            r"回顾|回想|复述|记得|记错|纠正|说错|不是.{0,30}而是|原文|原话|写(?:了|着|的).{0,16}(?:什么|啥)|"
            r"(?:核对|确认|说明).{0,20}(?:目前|当前|现在)(?:的)?位置|"
            r"(?:刚才|之前|先前).{0,30}(?:完成|进入|通过|抵达|转场|交给|得到|拿到).{0,16}(?:了吗|了么|了没有|了没|[?？])|"
            r"(?:说过|讲过|告诉过|提到过).{0,40}(?:什么|哪|怎么)|"
            r"(?:现在|目前|当前).{0,10}(?:在哪|哪里|哪一|几号)|(?:是否|有没有).{0,6}已经(?:到达|通过)|"
            r"(?:现在|目前|当前).{0,16}(?:由谁|谁在|谁拿|谁持有|谁保管)|(?:在谁|由谁)(?:的)?(?:手里|手中|拿着|持有|保管)|"
            r"(?:实际|真实)(?:的)?(?:结果|状态).{0,12}(?:什么|如何|怎样|[?？])|"
            r"(?:现在|目前|当前).{0,12}(?:持有|拿着|带着|保管).{0,8}(?:什么|哪些|[?？])|"
            r"(?:照明|灯光|电源|开关).{0,8}(?:是否|有没有).{0,6}(?:开启|打开|关闭)|"
            r"(?:之前|此前|先前|刚才|当时|那时|当初|开场|最初|起初|起始|醒来时|刚醒来|一开始|最开始|早先|曾经).{0,80}(?:什么|啥|怎么|怎样|如何|哪|谁|是否|有没有|说|写|结果|办法|经过)|"
            r"(?:便签|纸条|线索).{0,16}(?:写着|写了|内容)",
            text,
        )
        or re.search(
            r"(?:有没有|是否|曾经).{0,12}(?:交接|转交|交给|递给|领取|拿到|交出|打开|开启|关闭|使用|通过|进入)(?:过|了)|"
            r"(?:交接|转交|交给|递给|领取|拿到|交出|打开|开启|关闭|使用|通过|进入)过(?:吗|没有|[?？])|"
            r"(?:实际|真正).{0,8}(?:交接|流转|交付|交出)(?:结果|经过|过程|对象)",
            text,
        )
    )


def readonly_recall(text):
    if not recall_question(text):
        return False
    # Asking someone about their experience is a new conversation, even when
    # it concerns "just now". Historical quote/state requests still use recall.
    if (
        re.search(r"我[^。！？]{0,24}(?:问|询问|请教)", text)
        and re.search(r"发生|遭遇|伤|为什么|怎么样|听得见", text)
        and not re.search(r"原话|原文|复述|回顾|说过|告诉过|记错", text)
    ):
        return False
    from app.preparation.action_authority import action_kinds, declared_action

    # Classify independent current clauses before a historical purpose can
    # reserve the whole turn for recall. Quotes and past-tense self reports
    # remain evidence requests rather than executable attempts.
    unquoted = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|"[^"]*"', "", text)
    for sentence in re.split(r"(?<=[。！？;；\n])", unquoted):
        actor = re.search(r"(?:^|[，,…])\s*我(?:们)?", sentence)
        if not actor or re.search(r"如果|假如|假设", sentence):
            continue
        tail = sentence[actor.end() :]
        if re.match(r"(?:刚才|之前|先前|当时|曾经|记得|想问|想知道|是否|有没有)", tail):
            continue
        if sentence.rstrip().endswith(("？", "?")) and not declared_action(tail):
            continue
        if set(action_kinds(sentence)) - {"converse", "settle"}:
            return False
        # A declarative first-person attempt need not use a catalogued verb
        # (supporting someone, crouching, beckoning, etc.). This only chooses
        # the planning path; the ordinary authority guards still adjudicate it.
        if not sentence.rstrip().endswith(("？", "?")) and not re.match(
            r"(?:是|有|没有|不|没|记得|想起|回忆|回顾|想回顾|想知道|想问|认为|觉得|以为)",
            tail,
        ):
            return False
    # A separate present-tense operation remains actionable in a mixed turn.
    return not re.search(
        r"(?:然后|接着|现在|同时|再|并且)[，, ]*(?:我(?:们)?(?:要|去|就)?)?"
        r"(?:进入|前往|回到|返回|走向|走到|过去|离开|去(?!哪)|检查|查看|搜索|拿起|使用|打开|交给|扔|投|潜行)|"
        r"(?:我(?:们)?)(?:现在|这就)(?:进入|前往|返回|回到|过去|去(?!哪)|检查|查看|拿起|使用|打开)|"
        r"(?:^|[，,。；])(?:我(?:们)?)(?:先|仔细|认真|试着|尝试|准备|打算)*"
        r"(?:进入|前往|返回|回到|走向|走到|过去|去(?!哪)|检查|查看|观察|搜索|拿起|取下|揭下|翻看|翻转|"
        r"(?:把|将).{1,24}(?:翻过|翻转|揭下|拿起)|使用|打开|交给|扔|投|潜行)",
        text,
    )


def fact_records(visible_events, entities=(), *, members=None):
    member_names = dict(members or {})
    member_names.update(
        {
            e["payload"]["member_id"]: e["payload"]["display_name"]
            for e in visible_events
            if e["type"] == "member.joined"
            and e.get("visibility") == "public"
            and e["payload"].get("member_id")
            and e["payload"].get("display_name")
        }
    )
    events, _ = story_events(visible_events, include_initial_reveals=True)
    by_seq = {e["seq"]: e for e in events}
    cycle_actions = {}
    for e in events:
        cycle = e["payload"].get("cycle_id")
        if cycle and e["type"] in {"action.submitted", "agent.action_proposed"}:
            cycle_actions.setdefault(cycle, []).append(e)
    scene_names = {
        e["payload"]["id"]: e["payload"]["title"]
        for e in events
        if e["type"] == "entity.revealed"
        and e["payload"].get("type") == "scene"
        and e["payload"].get("id")
        and e["payload"].get("title")
    }
    records = []
    scene = None
    last_scene_title = None
    for e in events:
        p, kind = e["payload"], e["type"]
        previous_scene_title = last_scene_title
        scene = p.get("scene_id", scene)
        if kind == "entity.revealed" and p.get("type") == "scene":
            scene = p.get("id", scene)
        if kind == "scene.updated":
            scene = p.get("scene_title") or p.get("scene_id", scene)
            last_scene_title = p.get("scene_title", last_scene_title)
        base = dict(
            id=f"event:{e['seq']}",
            source_event_seq=e["seq"],
            speaker=p.get("actor_name") or e.get("actor_member_id"),
            scene_id=p.get("scene_id", scene),
            scene_title=scene_names.get(p.get("scene_id", scene), last_scene_title),
            scope="historical",
        )
        text = ""
        category = "result"
        if kind in {"entity.revealed", "entity.corrected", "clue.revealed"}:
            if p.get("type") == "npc":
                continue
            text = p.get("public_summary") or p.get("content", "")
            category = "source_text"
            base["source_type"] = p.get("type")
            if p.get("title"):
                base["title"] = p["title"]
            origins = [
                origin
                for origin in cycle_actions.get(p.get("cycle_id"), [])
                if origin["seq"] < e["seq"]
            ]
            if len(origins) == 1 and p.get("type") != "scene":
                # Link the actual reveal to its triggering attempt. Its content
                # remains the original reveal, never inferred from a successful
                # roll, narration, or a nearby unrelated action.
                origin = origins[0]
                if origin["payload"].get("text"):
                    base["action_quote"] = origin["payload"]["text"]
                    base["action_event_seq"] = origin["seq"]
        elif kind == "npc.spoke":
            text, category = p.get("text", ""), "npc_statement"
        elif kind in {"module.interaction", "combat.receipt", "combat.resolved", "check.resolved"}:
            text = p.get("text") or p.get("display_text") or p.get("summary", "")
            if kind == "check.resolved" and p.get("reason"):
                # The public check receipt retains the actual adjudicated task.
                # It is a check purpose, not proof that the task was achieved.
                base["check_purpose"] = p["reason"]
                check_actor = p.get("actor_name") or member_names.get(e.get("actor_member_id"))
                if check_actor:
                    base["check_actor"] = check_actor
            origin = by_seq.get(p.get("source_event_seq"), {})
            if not origin and kind in {"combat.receipt", "combat.resolved"}:
                origins = cycle_actions.get(p.get("cycle_id"), [])
                if len(origins) == 1:
                    origin = origins[0]
            action = origin.get("payload", {}).get("text")
            if action:
                base["action_quote"] = action
                base["action_event_seq"] = origin["seq"]
                actor = origin.get("payload", {}).get("actor_name") or member_names.get(
                    origin.get("actor_member_id")
                )
                if actor:
                    base["action_actor"] = actor
        elif kind == "scene.updated":
            text = "抵达：" + str(p.get("scene_title", "")) + "。" + p.get("scene_summary", "")
            category = "location"
            if previous_scene_title and previous_scene_title != last_scene_title:
                base["from_scene_title"] = previous_scene_title
        if text:
            records.append({**base, "kind": category, "text": text})
    # Initial reveals may precede game.started and be omitted from the ordinary
    # story window. The revealed projection retains the original event number.
    for entity in entities:
        seq = entity.get("revealed_event_seq")
        if entity.get("type") == "npc" or not seq:
            continue  # A character profile is not dialogue testimony.
        if any(r["source_event_seq"] == seq and r["kind"] == "source_text" for r in records):
            continue  # Prefer the actual reveal over a later projection's prose.
        records.append(
            dict(
                id=f"entity:{entity['id']}",
                source_event_seq=seq,
                kind="source_text",
                source_type=entity.get("type"),
                speaker=None,
                title=entity["title"],
                scene_id=None,
                scope=entity.get("fact_scope", "historical"),
                text=entity["public_summary"],
            )
        )
    return records


def select_facts(events, entities, query, *, budget=1700, limit=6, members=None):
    records = fact_records(events, entities, members=members)
    speakers = {r.get("speaker") for r in records if r["kind"] == "npc_statement"}
    speakers.update(e.get("title") for e in entities if e.get("type") == "npc")

    def asks_speech(question):
        return bool(
            re.search(
                r"台词|指示|(?:他|她|对方|那人|NPC).{0,10}(?:说|讲|告诉|原话)", question, re.I
            )
            or re.search(r"(?:先生|女士|员).{0,10}(?:说|讲|告诉|原话)", question)
            or any(s and s in question for s in speakers)
            and re.search(r"说|讲|告诉|原话", question)
        )

    wants_speech = asks_speech(query)
    stopwords = set(
        tokens(
            "回顾 实际 真实 结果 经过 办法 原文 原话 当时 刚才 之前 先前 "
            "我们 什么 怎么 哪些 一下 具体 证据 依据 核对 原记录 记录 说明 确认 究竟 说法 "
            "调查 查明 内容 没有 分别"
        )
    )
    parts = [p for p in re.split(r"[?？;；，,。.!！]", query) if set(tokens(p)) - stopwords]
    parts = [
        piece for part in parts for piece in re.split(r"以及|还有|和|与", part) if piece.strip()
    ]
    parts = [
        side
        for part in parts
        for side in (
            [re.sub(r"正反面|正背面|两面", name, part) for name in ("正面", "背面")]
            if re.search(r"正反面|正背面|两面", part)
            else [part]
        )
        if set(tokens(side)) - stopwords
    ]

    transfer_pattern = (
        r"交接|交给|递给|转交|流转|交付|(?:怎么|怎样|如何).{0,16}(?:手里|手中|拿到|得到)|"
        r"谁.{0,8}(?:给|交)"
    )
    transfer_query = bool(re.search(transfer_pattern, query))
    asks_state = bool(re.search(r"状态|(?:是否|已经|有没有).{0,6}(?:打开|开启|关闭|解锁)", query))
    asks_findings = bool(re.search(r"调查|查明|内容|原文|写|发现|找到|搜索|线索", query))
    history_scenes = {
        r["scene_title"]
        for r in records
        if r.get("scene_title")
        and r["scene_title"] in query
        and re.search(r"经过|经历|先后|尝试|路线|行动|通过", query)
    }
    scope_words = set(tokens(" ".join(history_scenes)))

    def ranking(question):
        wanted = set(tokens(question)) - stopwords
        result_subjects = re.findall(
            r"(?:^|[，,。?？!！;；])([^，,。?？!！;；]+?)(?:的)?(?:实际|真实)(?:的)?结果",
            question,
        )
        result_words = set(tokens(" ".join(result_subjects))) - stopwords
        side = next((s for s in ("正面", "背面") if s in question), None)
        asks_transfer = transfer_query or bool(re.search(transfer_pattern, question))
        ranked = []
        for r in records:
            material = " ".join(
                str(r.get(k) or "")
                for k in (
                    "text",
                    "title",
                    "speaker",
                    "scene_title",
                    "from_scene_title",
                    "action_quote",
                    "check_purpose",
                )
            )
            score = len(wanted & set(tokens(material)))
            # Reserve the outcome of the explicitly named action before generic
            # question fragments (e.g. 是什么) can favor an unrelated check.
            named_result = bool(r["kind"] == "result" and result_words & set(tokens(material)))
            scene_history = bool(
                history_scenes
                and not asks_speech(question)
                and (
                    r.get("scene_title") in history_scenes
                    or r.get("from_scene_title") in history_scenes
                )
            )
            if scene_history:
                # A scoped history question includes outcomes whose literal text
                # does not repeat the scene name or the question's generic verbs.
                score = max(score, 1)
            original = r.get("title", "") + r["text"]
            preferred = bool(
                side
                and r["kind"] == "source_text"
                and side in original
                or asks_state
                and r["kind"] == "result"
                and not r.get("check_purpose")
                and wanted & set(tokens(original))
                or re.search(r"(?:实际|真实)(?:的)?结果", question)
                and r["kind"] == "result"
                and score
            )
            named_source = bool(
                asks_findings
                and not asks_state
                and not asks_speech(question)
                and r["kind"] == "source_text"
                and r.get("source_type") != "scene"
                and wanted & (set(tokens(r.get("title", ""))) - stopwords - scope_words)
            )
            # A question about an investigated document needs its revealed
            # content as well as check outcomes. Generic, longer check purposes
            # must not crowd that content out of the same fixed budget.
            # Evidence type is a constraint before lexical relevance: a long
            # action mentioning both sides cannot displace the requested print,
            # nor can testimony about a key replace an actual unlock receipt.
            if (
                score
                and r["kind"] == "source_text"
                and r.get("action_quote")
                and re.search(r"发现|找到|搜|寻找", question)
            ):
                score += 3
            if (
                asks_transfer
                and r["kind"] == "result"
                and r.get("action_quote")
                and re.search(r"交给|递给|转交|交到|递到", r["action_quote"])
            ):
                score += 8
            if r["kind"] == "npc_statement" and asks_speech(question):
                score += 3
            if (
                score
                and r["kind"] == "result"
                and re.search(r"结果|经过|办法|怎么|怎样|如何|通过|引开|诱导|打开", question)
            ):
                score += 2
            if r["kind"] == "location" and re.search(r"路线|途经|走过", question):
                score += 3
            if score:
                ranked.append(
                    (
                        named_result,
                        preferred,
                        named_source,
                        scene_history,
                        {"result": 2, "location": 1}.get(r["kind"], 0) if scene_history else 0,
                        score,
                        r["kind"] != "location",
                        r["source_event_seq"],
                        r,
                    )
                )
        return [r for *_, r in sorted(ranked, key=lambda x: x[:-1], reverse=True)]

    ranked = ranking(query)
    # Reserve the first relevant original for each explicit question before a
    # verbose question about keys can displace a separate question about travel.
    candidates = []
    if len(parts) > 1:
        for part in parts:
            if history_scenes and re.match(
                r"\s*(?:我们|两人|大家|各自|分别|每人|每次|各次|它们|它|这|那|又)", part
            ):
                # These clauses refer back to the named scene/task. Ranking
                # them alone loses that scope and reserves unrelated evidence;
                # the full question below retains all relevant actors/results.
                continue
            part_ranked = ranking(part)
            candidates.extend(part_ranked[:1])
            if asks_findings and part_ranked and part_ranked[0]["kind"] == "source_text":
                title_words = set(tokens(part_ranked[0].get("title", ""))) - stopwords
                # The revealed text does not establish success on every later
                # investigation of that object (e.g. reading its date). Reserve
                # the relevant original check alongside the content, before
                # generic testimony can fill all remaining evidence slots.
                candidates.extend(
                    [
                        r
                        for r in part_ranked
                        if r.get("check_purpose") and title_words & set(tokens(r["check_purpose"]))
                    ][:1]
                )
    candidates.extend(ranked)
    selected, seen, used = [], set(), 0
    for r in candidates:
        identity = tuple(
            r.get(k)
            for k in ("kind", "text", "action_quote", "check_purpose", "speaker", "scene_id")
        )
        if identity in seen:
            continue
        size = len(json.dumps(r, ensure_ascii=False))
        if used + size > budget:
            continue
        if len(render_facts([*selected, r])) > 680:
            continue
        selected.append(r)
        used += size
        seen.add(identity)
        if len(selected) == limit:
            break
    if wants_speech and not any(r["kind"] == "npc_statement" for r in records):
        selected.append(
            dict(
                id="absence:npc_speech",
                source_event_seq=None,
                searched_event_range=[events[0]["seq"], events[-1]["seq"]] if events else [],
                kind="absence",
                scope="historical",
                text="这段已公开记录中没有NPC实际答话，不能把旁白改称人物说过的话。",
            )
        )
    return selected


def render_facts(records, ids=()):
    chosen = [r for r in records if r["id"] in ids] if ids else records
    blocks = []
    for r in chosen:
        lines = []
        label = (
            (r.get("speaker") or "该人物") + "当时说："
            if r["kind"] == "npc_statement"
            else (
                (r.get("title") or "已公开文字") + "："
                if r["kind"] == "source_text"
                else "实际记录："
            )
        )
        if r["kind"] == "source_text" and r.get("source_type") in {"location", "scene", "item"}:
            label = (r.get("title") or "场景／物件") + "（当时公开的描述）："
        if r.get("action_quote"):
            lines.append(
                r.get("action_actor", "") + "当时提交的尝试（不代表结果）：" + r["action_quote"]
            )
        if r.get("check_purpose"):
            actor = r.get("check_actor", "")
            lines.append((actor + "：" if actor else "") + "该次检定针对：" + r["check_purpose"])
        lines.append(label + r["text"])
        blocks.append("\n".join(lines))
    return (
        "\n".join(dict.fromkeys(blocks)) or "没有检索到与这个问题对应的原始记录，不能确认具体内容。"
    )


def bounded_facts(records, limit=6, text_budget=680):
    settled_actions = {
        r.get("action_event_seq")
        for r in records
        if r.get("source") == "settled_initial_belongings_projection"
        and r.get("action_event_seq") is not None
    }
    # The existing initial inventory projection resolves legacy generic result
    # templates. Keep their original logs, but do not duplicate them as evidence.
    records = [
        r
        for r in records
        if r.get("action_event_seq") not in settled_actions
        or r.get("source") == "settled_initial_belongings_projection"
    ]
    ordered = sorted(records, key=lambda r: r["kind"] not in {"current_state", "absence"})
    result = []
    for r in ordered:
        if len(render_facts([*result, r])) <= text_budget:
            result.append(r)
        if len(result) >= limit:
            break
    return result


def recall_context_view(context, actor=None):
    """Reserve original evidence before the memory builder's first budget gate."""
    result = dict(context)
    inventory = context.get("inventory_state", {})
    if result.get("item_holders") == inventory.get("holders"):
        result.pop("item_holders", None)
    module = dict(context.get("module", {}))
    state = dict(module.get("interaction_state", {}))
    if state.get("held_items") == context.get("inventory_state", {}).get("holders"):
        state.pop("held_items", None)
    if "interaction_state" in module:
        module["interaction_state"] = state
    if context.get("readonly_recall"):
        result["characters"] = []
        module = {
            k: v
            for k, v in module.items()
            if k in {"id", "title", "current_scene", "scene", "interaction_state"}
        }
    else:
        from app.preparation.action_authority import (
            action_kinds,
            requested_action_kinds,
            teammate_request,
        )
        from app.preparation.inventory import held_item_acknowledgement

        trigger = context.get("triggering_action", {})
        raw = trigger.get("payload", {}).get("text", "")
        kinds = action_kinds(raw)
        if (
            actor
            and teammate_request(
                raw,
                context.get("current_participants", {}).get("members", {}),
                trigger.get("actor_member_id"),
            )
            == actor
        ):
            kinds = requested_action_kinds(raw)
        if set(kinds) == {"give"} or (
            trigger.get("type") == "agent.action_proposed"
            and held_item_acknowledgement(
                raw, context.get("inventory_state", {}), trigger.get("actor_member_id")
            )
        ):
            result["characters"] = [
                {k: v for k, v in c.items() if k not in {"skill_values", "effective_attributes"}}
                for c in context.get("characters", [])
            ]
    result["module"] = module
    return result


def recalled_location(state, question):
    if not re.search(r"位置|路线|车厢|到达|通过|穿过|在哪|哪里|哪一|几号", question):
        return []
    title = state.get("scene_title")
    if not title:
        return []
    return [
        dict(
            id="state:location",
            kind="current_state",
            scope="current",
            source_event_seq=None,
            source="current_navigation_projection",
            text="当前位置：" + title + "。",
        )
    ]


def recalled_entity_states(entities, question):
    """Reuse the visible location projection, already updated by real receipts."""
    records = []
    for entity in entities:
        if entity.get("type") != "location" or entity.get("fact_scope") != "current_scene":
            continue
        relevant = [
            clause
            for clause in re.split(r"[，,。?？;；]", question)
            if entity["title"] in clause
            and re.search(r"状态|(?:是否|有没有|已经).{0,6}(?:打开|关闭|解锁)|开着|上锁", clause)
        ]
        relevant = [
            clause
            for clause in relevant
            if not re.search(r"原话|台词|说|讲|告诉", clause)
            and (
                not re.search(r"之前|此前|刚才|曾经|当时|最初", clause)
                or re.search(r"现在|目前|当前", clause)
            )
        ]
        if not relevant or not entity.get("public_summary"):
            continue
        receipts = entity.get("current_state_receipts", [])
        records.append(
            dict(
                id="state:entity:" + entity["id"],
                kind="current_state",
                scope="current",
                source="current_public_entity_projection",
                entity_id=entity["id"],
                source_event_seq=max(
                    (r["source_event_seq"] for r in receipts),
                    default=entity.get("revealed_event_seq"),
                ),
                text=entity["title"] + "：" + entity["public_summary"],
            )
        )
    return records


def summary_sources(events, seqs, budget=1200):
    """Rehydrate a bounded route guide; never recursively compress old prose."""
    selected = set(seqs)
    records = [r for r in fact_records(events) if r["source_event_seq"] in selected]
    result = []
    for r in reversed(records):
        candidate = [r, *result]
        if len(json.dumps(candidate, ensure_ascii=False)) <= budget:
            result = candidate
    return result
