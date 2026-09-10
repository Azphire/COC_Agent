"""Shared story selection. Input must already be filtered for the reader's permissions."""

from collections import Counter

STORY_TYPES = {
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
