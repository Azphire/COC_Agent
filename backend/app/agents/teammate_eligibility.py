"""Decide whether a teammate has anything to respond to before model inference."""

import re


class TeammateEligibilityPolicy:
    def priority(self, *, events, trigger, profile, member_id, addressed=None, goal=""):
        """Direct answers first; relevant danger/tasks then least recently called."""
        text = trigger.payload.get("text", "")
        direct = addressed == member_id or bool(profile.get("name") and profile["name"] in text)
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
        return (not direct, not urgent, not relevant, last)

    def evaluate(self, *, events, trigger, profile, member_id, goal=""):
        current = [e for e in events if e.seq > trigger.seq]
        text = trigger.payload.get("text", "")
        addressed = trigger.payload.get("target_member_id") == member_id or (
            profile.get("name")
            and profile["name"] in text
            and re.search(r"问|交谈|说|告诉|请|帮|你", text)
        )
        if addressed:
            return "direct_conversation"
        if any(e.type in {"entity.revealed", "clue.revealed"} for e in current):
            return "new_public_entity"
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
