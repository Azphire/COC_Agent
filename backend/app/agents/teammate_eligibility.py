"""Decide whether a teammate has anything to respond to before model inference."""

import re


class TeammateEligibilityPolicy:
    def evaluate(self, *, events, trigger, profile, member_id, goal=""):
        current = [e for e in events if e.seq > trigger.seq]
        if any(e.type in {"entity.revealed", "clue.revealed"} for e in current):
            return "new_public_entity"
        if any(e.type == "scene.updated" for e in current):
            return "scene_changed"
        text = trigger.payload.get("text", "")
        addressed = trigger.payload.get("target_member_id") == member_id or (
            profile.get("name")
            and profile["name"] in text
            and re.search(r"问|交谈|说|告诉|请|帮|你", text)
        )
        if addressed:
            return "direct_conversation"
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
