"""Public narration validation and bounded deterministic Chinese fallback."""

import re

from app.rooms.service import require
from app.rules.display import check_display


def action_lead(intent, results):
    if intent == "recall":
        return "你回顾了先前获知的信息。"
    if intent == "converse":
        return "你向对方询问了情况。"
    if intent == "move":
        return (
            "你已抵达新的位置。"
            if any(e["type"] == "scene.updated" for e in results["events"])
            else "你仍在原处，可以继续确认通行条件。"
        )
    if intent == "investigate":
        return "你仔细检查了眼前的目标。"
    if intent == "observe":
        return "你环顾四周，确认了眼前的情况。"
    if intent in {"interact", "use_item", "assist"}:
        return "你尝试了这次动作。"
    return ""


class NarrationValidator:
    def validate(self, output, *, documents, public_ids, scene_id, results):
        text = output.public_narration
        require(
            not re.search(r"[a-z][a-z0-9]*_[a-z0-9_]+", text, re.I), "公开叙事包含内部标识", 422
        )
        require(
            not re.search(r"Traceback|Exception|HTTPError|工具调用|系统错误", text),
            "公开叙事包含系统内容",
            422,
        )
        require(
            not output.current_scene_reference or output.current_scene_reference == scene_id,
            "叙事引用了非当前场景",
            422,
        )
        require(set(output.public_entity_references) <= public_ids, "叙事引用了不可见实体", 422)
        for claim in documents:
            require(claim["visibility"] == "public", "叙事泄漏私密内容", 422)
            require(set(claim["entity_ids"]) <= public_ids, "叙事引用了非当前公开实体", 422)
        checks = {
            e["payload"].get("id", e["payload"].get("check_id")): e["payload"]
            for e in results["events"]
            if e["type"] == "check.resolved"
        }
        transitions = {str(e["seq"]) for e in results["events"] if e["type"] == "scene.updated"}
        require(
            not output.check_result_reference or output.check_result_reference in checks,
            "检定尚未完成",
            422,
        )
        require(
            not output.transition_result_reference
            or output.transition_result_reference in transitions,
            "转场尚未发生",
            422,
        )
        if checks:
            passed = checks.get(output.check_result_reference, next(iter(checks.values())))[
                "result"
            ]["passed"]
            contradictory = r"检定失败|未通过|没有成功" if passed else r"检定成功|检定通过|成功地"
            require(not re.search(contradictory, text), "叙事与真实检定结果冲突", 422)
        require(
            not text or text == "\n".join(d["statement"] for d in documents),
            "叙事超出验证过的公开依据",
            422,
        )
        # Exact public claims prevent invented clues, damage and tool effects;
        # server-authored result text is appended only after this check.
        require(
            bool(documents) or bool(checks) or output.needs_host_ruling,
            "叙事没有回应行动的公开依据",
            422,
        )
        return {"valid": True, "checks": len(checks), "scene_id": scene_id}


def fallback_narration(intent_type, results, public_scene, *, rejected=False):
    checks = [e["payload"] for e in results["events"] if e["type"] == "check.resolved"]
    transitions = [e["payload"] for e in results["events"] if e["type"] == "scene.updated"]
    reveals = [
        e["payload"].get("public_summary", e["payload"].get("content", ""))
        for e in results["events"]
        if e["type"] in {"entity.revealed", "clue.revealed"}
    ]
    if checks:
        check = checks[-1]
        outcome = (
            "检定成功，行动达到了本次检定的目标。"
            if check["result"]["passed"]
            else "检定失败，这次尝试未能达到目标。"
        )
        return "\n".join(
            filter(
                None,
                [
                    outcome,
                    check.get("display_text") or check_display(check)["display_text"],
                    *reveals,
                ],
            )
        )
    if transitions:
        return transitions[-1].get("scene_summary") or "你已抵达当前场景，可以继续查看周围。"
    if reveals:
        return "你查看了眼前的目标。\n" + "\n".join(reveals)
    if rejected:
        return "你暂时无法完成这项行动，可以根据眼前的情况选择下一步。"
    if intent_type == "converse":
        return "你向对方询问了情况，目前没有得到更多可以确认的信息。"
    if intent_type in {"observe", "investigate"}:
        return "你仔细查看了周围，目前没有新的发现。" + (
            "\n" + public_scene if public_scene else ""
        )
    return "你留意着眼前的情况，可以继续决定下一步行动。"
