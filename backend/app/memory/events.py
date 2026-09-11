"""Shared story selection. Input must already be filtered for the reader's permissions."""

import re
from collections import Counter


def published_incidental_details(details, text):
    """Extract short verbatim quotes; a draft field is never publication."""
    return list(
        dict.fromkeys(
            d.strip()
            for d in details
            if isinstance(d, str) and 0 < len(d.strip()) <= 160 and d.strip() in text
        )
    )[:2]


def incidental_records(event):
    payload = event["payload"]
    if (
        event.get("visibility") != "public"
        or event["type"] not in {"keeper.narration", "npc.spoke"}
        or payload.get("safe_fallback")
        or payload.get("incidental_source") != "kp_improvisation"
    ):
        return []
    return [
        {
            "text": detail,
            "source": "kp_improvisation",
            "source_event_seq": event["seq"],
            "speaker_id": payload.get("entity_id") or event.get("actor_member_id"),
            "speaker": payload.get("actor_name") or "KP",
            "scene_id": payload.get("scene_id"),
        }
        for detail in published_incidental_details(
            payload.get("incidental_details", []), payload.get("text", "")
        )
    ]


def relevant_incidental_memories(visible_events, query, scene_id, *, limit=6, budget=1400):
    """Retrieve public quotes on the active story branch, independently of summaries."""
    events, _ = story_events(visible_events)

    def terms(text):
        # Chinese questions rarely contain spaces. Bigrams also find short
        # follow-ups without introducing an embedding model or another store.
        tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
        return {t for t in tokens if len(t) > 1} | {
            t[i : i + 2] for t in tokens for i in range(len(t) - 1)
        }

    wanted = terms(query)
    ranked = []
    for event in events:
        for record in incidental_records(event):
            current = bool(scene_id and record["scene_id"] == scene_id)
            score = len(wanted & terms(record["text"] + record["speaker"]))
            if not current and not score:
                continue
            record["fact_scope"] = "current_scene" if current else "historical"
            ranked.append((score, current, event["seq"], record))
    selected, used = [], 0
    import json

    for _, _, _, record in sorted(ranked, key=lambda r: r[:3], reverse=True):
        size = len(json.dumps(record, ensure_ascii=False))
        if used + size > budget:
            continue
        selected.append(record)
        used += size
        if len(selected) >= limit:
            break
    return selected


STORY_TYPES = {
    "check.choice_made",
    "check.rolled",
    "check.luck_spent",
    "check.push_requested",
    "check.push_reviewed",
    "check.consequence_pending",
    "check.consequence_applied",
    "sanity.progressed",
    "sanity.involuntary",
    "sanity.state_changed",
    "action.submitted",
    "action.clarification_requested",
    "keeper.narration",
    "npc.spoke",
    "agent.spoke",
    "agent.action_proposed",
    "agent.needs_host_ruling",
    "chat.message",
    "check.requested",
    "check.resolved",
    "check.cancelled",
    "dice.rolled",
    "entity.revealed",
    "entity.corrected",
    "clue.revealed",
    "scene.updated",
    "module.completed",
}
INITIAL_TYPES = {"entity.revealed", "entity.corrected", "clue.revealed", "scene.updated"}


def epistemic_event(event):
    kind = event["type"]
    return {
        **event,
        **({"incidental_sources": incidental_records(event)} if incidental_records(event) else {}),
        "epistemic_status": (
            "intent_not_result"
            if kind in {"action.submitted", "agent.action_proposed"}
            else "attributed_testimony"
            if kind in {"npc.spoke", "agent.spoke", "chat.message"}
            else "narration_not_evidence"
            if kind == "keeper.narration"
            else "authoritative_result"
        ),
    }


def story_events(visible_events):
    selected, excluded = [], Counter()
    started = False
    previous_type = None
    for event in visible_events:
        kind, payload = event["type"], event["payload"]
        if kind == "game.started" or kind == "action.submitted" or payload.get("cycle_id"):
            started = True
        if kind == "snapshot.loaded":
            source = payload.get("source_seq")
            if isinstance(source, int):
                retained = [e for e in selected if e["seq"] <= source]
                excluded["abandoned_after_snapshot"] += len(selected) - len(retained)
                selected = retained
        if kind not in STORY_TYPES:
            excluded[kind] += 1
        elif kind in INITIAL_TYPES and (
            not started
            or payload.get("initialization")
            or kind == "scene.updated"
            and previous_type == "module.bound"
        ):
            excluded["initialization:" + kind] += 1
        else:
            selected.append(event)
        previous_type = kind
    return selected, {
        "policy": "story_v1",
        "visible_count": len(visible_events),
        "candidate_count": len(selected),
        "excluded_count": sum(excluded.values()),
        "excluded_by_type": dict(excluded),
        "coverage_basis": "visible eligible story events; original seq; gaps are excluded events",
    }


async def current_participants(service, session, room):
    # Public roster and actual assignments only. Published but unassigned cards
    # have no member and are deliberately absent from assignments.
    members = await service.rooms.members(session, room)
    return {
        "members": {m.id: m.display_name for m in members if m.active},
        "assignments": {
            s.member_id: s.public_summary.get("name", "")
            for s in await service.rooms.slots(session, room)
            if s.member_id
        },
    }
