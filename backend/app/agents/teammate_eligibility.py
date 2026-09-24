"""Decide whether a teammate has anything to respond to before model inference."""

import re


class TeammateEligibilityPolicy:
    def priority(self, *, events, trigger, profile, member_id, addressed=None, goal=""):
        """Direct answers first; relevant danger/tasks then least recently called."""
        direct = addressed == member_id
        material = " ".join(
            e.payload.get("text", e.payload.get("public_summary", ""))
            for e in events
            if e.seq >= trigger.seq
        )
        specialty = " ".join(
            str(profile.get(k, "")) for k in ("occupation", "goals", "personality")
        )
        terms = {specialty[i : i + 2] for i in range(len(specialty) - 1)}
        relevant = bool(goal and any(goal[i : i + 2] in material for i in range(len(goal) - 1)))
        relevant = relevant or any(t.strip() and t in material for t in terms)
        urgent = bool(re.search(r"受伤|流血|危险|追来|坍塌|抓住|救命", material)) and relevant
        # Choose who gets the existing single unsolicited opportunity using
        # actual trained abilities. This prioritizes a decision, not an action.
        skills = profile.get("skills", {})
        fit = max((skills.get(k, 0) for k in ("first_aid", "medicine")), default=0) if re.search(
            r"受伤|伤口|流血|腿伤|疼", material
        ) else 0
        last = max(
            (
                e.seq
                for e in events
                if e.type == "agent.teammate_decision"
                and e.payload.get("member_id") == member_id
                and not e.payload.get("deterministically_skipped")
            ),
            default=0,
        )
        return (not direct, not urgent, -fit, not relevant, last)

    def evaluate(self, *, events, trigger, profile, member_id, goal="", addressed_ids=None):
        current = [e for e in events if e.seq > trigger.seq]
        text = trigger.payload.get("text", "")
        if addressed_ids is None:
            from app.preparation.action_authority import addressed_spans

            # Compatibility for standalone callers; runtime supplies the exact
            # registered member IDs and never reinterprets names here.
            addressed_ids = {mid for mid, _, _ in addressed_spans(
                text, {member_id: profile.get("name", "")},
            )}
        addressed = member_id in addressed_ids
        if addressed:
            return "direct_conversation"
        if any(e.type in {"entity.revealed", "clue.revealed"} for e in current):
            return "new_public_entity"
        if any(e.type == "npc.spoke" for e in current):
            return "new_testimony"
        if any(e.type == "scene.updated" for e in current):
            return "scene_changed"
        if goal and any(
            e.type in {"check.resolved", "module.interaction", "combat.resolved"} for e in current
        ):
            return "reassess_goal"
        goals = " ".join(filter(None, [goal, profile.get("goals", "")]))
        keywords = [s for s in re.split(r"[，。；、\s]+", goals) if 2 <= len(s) <= 40]
        for event in current:
            # Goals must match actual public event material, not a model's claim
            # that a generic observation is somehow relevant.
            if event.type not in {
                "check.resolved",
                "entity.corrected",
                "chat.message",
                "npc.spoke",
            }:
                continue
            material = " ".join(
                str(event.payload.get(k, ""))
                for k in ("text", "title", "public_summary", "display_name")
            )
            if any(k in material for k in keywords):
                return "goal_triggered"
        return None
