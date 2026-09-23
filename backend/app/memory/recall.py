"""Source-grouped, permission-filtered memory for ordinary actions as well as recall.

The caller supplies the visible active branch. Selection never mutates world state;
the full source ledger stays on the server and only selected short references travel.
"""

import json
import re

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


def select_memory(events, query, *, scene_id=None, tasks=(), memories=(), budget=2600):
    """Select exact facts and related segments without a read-only question gate."""
    events, _ = story_events(events, include_initial_reveals=True)
    from app.memory.segments import source_metadata_map

    source_metadata = source_metadata_map(events)
    by_seq = {e["seq"]: e for e in events}
    wanted = terms(query + " " + " ".join(t.get("text", "") for t in tasks))
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
            "text", "title", "speaker", "scene_title", "action_quote", "check_purpose"
        ))
        score = len(wanted & terms(material))
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
        ) if k in p}
        if fields:
            record["fields"] = fields
        if "exact_payload" in fact:
            record["fields"] = fact["exact_payload"]
        for chunk in quote_chunks(record, query):
            candidates.append((score, event["seq"], chunk))

    # A promise remains attributed speech; no inferred completed operation or new task.
    for event in events:
        if event["type"] not in {"agent.spoke", "chat.message"}:
            continue
        text = event["payload"].get("text", "")
        if re.search(r"答应|承诺|我(?:会|来|负责)|等.{0,12}(?:再|就)", text):
            score = len(wanted & terms(text))
            if score:
                record = {"kind": "commitment_candidate", "text": text,
                          "source": source(event, source_metadata[event["seq"]]["scene_id"]),
                          "epistemic": "attributed_intent_not_execution",
                          "historical_only": True}
                candidates.extend((score, event["seq"], r) for r in quote_chunks(record, query))

    selected, omitted, used = [], [], 2
    # Existing pending requests, including their source, reserve room first.
    ordered = [r for _, _, r in sorted(candidates, key=lambda c: c[:2], reverse=True)]
    for record in [*tasks, *ordered]:
        size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":"))) + 1
        ref = record.get("source", {}).get("ref", "task")
        if used + size <= budget:
            selected.append(record)
            used += size
        else:
            omitted.append({"ref": ref, "reason": "character_budget", "kind": record["kind"]})

    from app.memory.segments import active_segments

    ranked = []
    by_id = {m.id: m for m in memories}
    for doc in active_segments(memories, events):
        memory = by_id[doc["segment_id"]]
        summary = doc.get("summary", {})
        text = summary.get("content", "") if isinstance(summary, dict) else str(summary)
        score = len(wanted & terms(text + json.dumps(doc.get("index", {}), ensure_ascii=False)))
        if score:
            ranked.append((score, memory.coverage_end or 0, memory, doc, summary))
    for _, _, memory, doc, summary in sorted(ranked, key=lambda r: r[:2], reverse=True):
        # Full exact positions/tasks live in the segment ledger and selected
        # fact/task records above. Do not resend that ledger inside prose.
        prose = {"content": summary.get("content", "")} if isinstance(summary, dict) else summary
        record = {"kind": "segment", "summary": prose, "historical_only": True,
                  "epistemic": "derived_summary_not_fact_authority",
                  "source": {"ref": f"s{memory.coverage_start}-{memory.coverage_end}",
                             "range": [memory.coverage_start, memory.coverage_end],
                             "visibility": memory.scope},
                  "scenes": doc.get("index", {}).get("scenes", [])}
        size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":"))) + 1
        if used + size <= budget and len([r for r in selected if r["kind"] == "segment"]) < 2:
            selected.append(record)
            used += size
        else:
            omitted.append({"ref": record["source"]["ref"], "reason": "character_budget",
                            "kind": "segment"})
    return selected, {"selected_refs": [r["source"]["ref"] for r in selected],
                      "omitted": omitted, "source_group_count": len(by_seq),
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
