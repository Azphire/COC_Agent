"""Public narration validation and bounded deterministic Chinese fallback."""

import re

from app.rooms.service import require
from app.rules.display import check_display


def response_brief(plan, context, results, *, withdrawal=None):
    """Project approved public candidates and receipts, never private KP prose."""
    focus = plan.focus
    intent = plan.parsed_intent
    raw = context["triggering_action"].get("payload", {}).get("text", intent.evidence_quote)
    from app.agents.action_policy import explicit_movement, local_scene_movement
    from app.preparation.inventory import inventory_probe

    scene = context.get("module", {}).get("current_scene") or context.get("module", {}).get(
        "scene", {}
    )
    movement_requested = (
        (
            intent.type == "move"
            or bool(
                re.search(
                    r"(?:进入|走进|前往|返回|回到)[^，。！？；,.!?;\n]{0,24}(?:车厢|房间|大厅|厅|室)",
                    raw,
                )
            )
        )
        and explicit_movement(raw)
        and not local_scene_movement(
            raw,
            [{"type": "scene", "title": scene.get("title", "")}],
            context.get("module", {}).get("outgoing_transitions", []),
        )
    )

    attempt = focus.action if focus else intent.evidence_quote
    attempt = attempt if attempt in raw else ""
    question = focus.question if focus and focus.question in raw else raw
    addressee = (
        focus.addressee_id if focus else (intent.target_id if intent.type == "converse" else None)
    )
    people = context.get("current_participants", {}).get("members", {})
    npc = next(
        (
            e
            for e in context.get("public_entities", [])
            if e["id"] == addressee
            and e["type"] == "npc"
            and e.get("fact_scope", "current_scene") == "current_scene"
        ),
        None,
    )
    result_ids = {
        e["payload"].get("entity_id", e["payload"].get("id"))
        for e in results["events"]
        if e["type"] in {"clue.revealed", "entity.revealed"}
    }
    wanted = set(focus.public_fact_ids if focus else []) | result_ids
    if focus and focus.action and focus.action_target_id:
        wanted.add(focus.action_target_id)
    options = context.get("PUBLIC_CLAIM_OPTIONS", [])
    if focus:
        current_ids = {
            e["id"]
            for e in context.get("public_entities", [])
            if e.get("fact_scope", "current_scene") == "current_scene"
        }
        current_ids.add(context.get("module", {}).get("scene", {}).get("id"))
        options = [
            c
            for c in options
            if c["claim_id"] in wanted
            or set(c["entity_ids"]) & (wanted | current_ids)
            or c["category"] == "rule"
        ]
    # A profile is portrayal, not testimony or evidence that a conversation happened.
    facts = [c for c in options if not npc or npc["id"] not in c["entity_ids"]]
    written_sources = []
    if not npc and re.search(r"看|观察|检查|文字|记号|字迹|读|原文|内容|写", raw):
        for entity in context.get("public_entities", []):
            if (
                entity.get("type") == "clue"
                and entity.get("fact_scope", "current_scene") == "current_scene"
                and (
                    any(
                        name and name in raw
                        for name in [entity["title"], *entity.get("aliases", [])]
                    )
                    or focus
                    and focus.action_target_id == entity["id"]
                    or entity["id"] in result_ids
                )
            ):
                if entity.get("public_summary"):
                    written_sources.append(entity["public_summary"])
    return {
        "trigger_seq": context["triggering_action"]["seq"],
        "inventory_probe": inventory_probe(raw),
        "movement_requested": movement_requested,
        "speaker_id": intent.actor_member_id,
        "question": question,
        "purpose": focus.action if focus and focus.action in raw and focus.action else question,
        "answer_basis": "improvise"
        if focus and focus.answer_basis == "unrecorded"
        else focus.answer_basis
        if focus
        else "social",
        "attempt": attempt,
        "responder": {
            "id": npc["id"],
            "name": npc["title"],
            "kind": "npc",
            "portrayal": npc["public_summary"],
        }
        if npc
        else {"id": addressee, "name": people[addressee], "kind": "teammate"}
        if addressee in people
        else {"kind": "keeper"},
        "allowed_facts": [{"id": c["claim_id"], "text": c["statement"]} for c in facts],
        "source_quotes": list(dict.fromkeys(written_sources))[:3],
        "current_scene": scene,
        "current_inventory": context.get("item_holders", []),
        "current_state": [
            r
            for e in context.get("public_entities", [])
            if e.get("fact_scope", "current_scene") == "current_scene"
            for r in e.get("current_state_receipts", [])
        ],
        "incidental_memories": context.get("incidental_memories", []),
        "completed_results": results,
        "withdrawal": withdrawal,
        "pending_decisions": list(
            filter(
                None,
                [
                    plan.next_decision
                    if plan.next_decision in raw
                    else "后续行动仍需你决定。"
                    if plan.next_decision
                    else "",
                    focus.suggestion if focus and focus.suggestion in raw else "",
                    focus.hypothesis if focus and focus.hypothesis in raw else "",
                ],
            )
        ),
    }, facts


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
        text = "\n".join(
            [
                output.public_narration,
                output.npc_speech.text if output.npc_speech else "",
                *output.incidental_details,
            ]
        )
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
        if not transitions and results.get("failed_tools"):
            require(
                not re.search(r"(?:已经|已|成功)(?:到达|抵达|进入|穿过)", text),
                "失败的转场不能叙述为已经到达",
                422,
            )
        if checks:
            passed = checks.get(output.check_result_reference, next(iter(checks.values())))[
                "result"
            ]["passed"]
            contradictory = r"检定失败|未通过|没有成功" if passed else r"检定成功|检定通过|成功地"
            require(not re.search(contradictory, text), "叙事与真实检定结果冲突", 422)
        require(
            not re.search(
                r"(?:受到|扣除|损失|恢复|获得).{0,6}[一二三四五六七八九十百\d]+点?(?:伤害|生命|HP|MP|幸运)",
                text,
            ),
            "资源变化必须使用实际结算记录",
            422,
        )
        # These are structural and literal checks, not proof of semantic truth.
        # The public narrator never receives private KP material. Its prose and
        # incidental details do not create entities, resources, exits or results.
        return {
            "valid": True,
            "checks": len(checks),
            "scene_id": scene_id,
            "scope": "visibility_and_result_references; semantic correctness not proven",
        }


def fallback_narration(
    intent_type, results, public_scene, *, rejected=False, brief=None, inventory_state=None
):
    interactions = [
        e["payload"].get("text")
        for e in results["events"]
        if e["type"] == "module.interaction" and e["payload"].get("text")
    ]
    if interactions:
        return "\n".join(dict.fromkeys(interactions))
    if (brief or {}).get("resource_gate") == "unconfirmed_item" and not any(
        e["type"] == "check.resolved" for e in results["events"]
    ):
        return "你检查了随身物品，但还没有完成确认物品所需的检定。"
    checks = [e["payload"] for e in results["events"] if e["type"] == "check.resolved"]
    transitions = [e["payload"] for e in results["events"] if e["type"] == "scene.updated"]
    reveals = [
        e["payload"].get("public_summary", e["payload"].get("content", ""))
        for e in results["events"]
        if e["type"] in {"entity.revealed", "clue.revealed"}
    ]
    if checks:
        check = checks[-1]
        if check.get("opposed"):
            return "\n".join([check["display_text"], *reveals])
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
    if results.get("blocked_operations"):
        return "这次动作没有完成，当前状态未因这次尝试改变。"
    if (brief or {}).get("movement_requested"):
        return (
            "本次没有完成转场；当前位置仍是"
            + brief.get("current_scene", {}).get("title", "原场景")
            + "。"
        )
    if reveals:
        return "你查看了眼前的目标。\n" + "\n".join(reveals)
    if results.get("blocked_discovery"):
        return "这次尚未确认新的线索内容，仍需满足该目标的调查条件。"
    brief = brief or {}
    if brief.get("responder", {}).get("kind") == "teammate" and not brief.get("attempt"):
        return "你向" + brief["responder"].get("name", "队友") + "说出了这番话。"
    topic = " ".join(str(brief.get(k, "")) for k in ("attempt", "question", "purpose"))
    if inventory_state is not None and (
        re.search(r"照明|屏幕|点亮|光|交接|递给|交给|随身|口袋", topic)
        or any(
            name and name in topic
            for item in inventory_state.get("known_items", []) for name in item["names"]
        )
    ):
        from app.preparation.inventory import recalled_inventory

        current = recalled_inventory(inventory_state, "当前持有物品")
        return "这次没有完成新的物品操作。\n" + "\n".join(r["text"] for r in current)
    facts = [f["text"] for f in brief.get("allowed_facts", []) if f.get("text")]
    # A failed generation cannot recover by republishing an unchecked old
    # improvisation. Ordinary generation still receives those attributed memories.
    available = list(dict.fromkeys(facts[:3])) or (
        [public_scene] if public_scene else []
    )
    directions = (
        "你可以继续追问具体细节，或查看这些信息提到的地方；接下来怎么做由你决定。"
        if intent_type == "converse"
        else "你可以继续查看眼前的人或物，也可以换个方法尝试；接下来怎么做由你决定。"
    )
    return "\n".join([*available, directions])
