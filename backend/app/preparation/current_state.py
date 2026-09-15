"""Public executed state outranks initial descriptions and remembered narration."""

import re


def validate_access_prose(text, action, entities, runtime):
    """A prepared key gate is not opened by an improvised inspection sentence."""
    from app.preparation.action_authority import aliases
    from app.rooms.service import require

    topic = (action + text).replace("的", "")
    for entity in entities:
        methods = [
            r
            for r in entity.get("interactions", [])
            if r.get("encounter_operation") == "open_door" and r.get("required_item_ids")
        ]
        if not methods or not any(n and n.replace("的", "") in topic for n in aliases(entity)):
            continue
        claims = [
            c
            for c in re.split(r"[。；\n]", text)
            if re.search(
                r"没(?:有)?上锁|未上锁|不用钥匙|无需钥匙|(?:门|锁).{0,20}(?:打开|开启|开着|开了|敞开)",
                c,
            )
            and not re.search(
                r"尝试|试着|打算|准备|如果|能否|是否|没有打开|未打开|没能|打不开|不能|无法|未能|[?？]",
                c,
            )
        ]
        if not claims:
            continue
        require(
            any(
                r.get("entity_id") == entity["id"]
                and r.get("interaction_id") in {m["id"] for m in methods}
                for r in runtime.receipts.values()
            ),
            "门的通行状态必须来自实际开门回执，查看或旁白不能代替开锁",
            422,
        )


def current_results(runtime, entities):
    definitions = {e.source_entity_id: e.snapshot for e in entities}
    latest = {}
    for receipt in sorted(
        runtime.get("receipts", {}).values(), key=lambda r: r.get("source_event_seq", 0)
    ):
        eid = receipt.get("entity_id")
        definition = definitions.get(eid, {})
        rule = next(
            (
                r
                for r in definition.get("interactions", [])
                if r["id"] == receipt.get("interaction_id")
            ),
            {},
        )
        if receipt.get("use_result") and not receipt["use_result"].get("passed"):
            continue
        keys = [f"flag:{k}" for k in rule.get("set_flags", {})]
        if rule.get("door_id") and rule.get("encounter_operation") in {"open_door", "close_door"}:
            keys.append("door:" + rule["door_id"])
        if keys and receipt.get("text"):
            for key in keys:
                latest[key] = receipt
    unique = {(r["entity_id"], r["source_event_seq"]): r for r in latest.values()}
    return [
        {k: r.get(k) for k in ("entity_id", "source_event_seq", "actor_member_id", "text")}
        for r in unique.values()
    ]


def bind_terminal_confirmation(plan, facts, runtime):
    """Bind an explicit settlement response to an already executed terminal task."""
    from app.agents.adjudication_schemas import TurnFocus

    if not runtime.pending_outcome or re.search(r"不|别|是否|能否|如果|假如|[?？]", facts.raw_text):
        return
    if not re.search(r"确认.{0,12}(?:结束|终幕|结算)|继续结算|结束调查", facts.raw_text):
        return
    origins = []
    for receipt in runtime.receipts.values():
        entity = facts.approved_entities.get(receipt.get("entity_id"), {})
        rule = next(
            (r for r in entity.get("interactions", []) if r["id"] == receipt.get("interaction_id")),
            {},
        )
        if (
            rule.get("prepare_outcome") == runtime.pending_outcome
            and receipt.get("scene_node_id") == facts.scene_id
        ):
            origins.append(receipt)
    if not origins:
        return
    candidates = [
        (eid, rule)
        for eid, entity in facts.approved_entities.items()
        if eid in facts.local_entity_ids & facts.revealed_entity_ids
        for rule in entity.get("interactions", [])
        if rule.get("outcome") == runtime.pending_outcome and rule.get("kp_enabled")
    ]
    if len(candidates) != 1:
        return
    eid, rule = candidates[0]
    plan.parsed_intent.type = "interact"
    plan.parsed_intent.target_id = eid
    plan.parsed_intent.target_kind = facts.approved_entities[eid]["type"]
    plan.focus = TurnFocus(action=facts.raw_text, action_target_id=eid, answer_basis="facts")
    plan.proposed_check = None
    plan.proposed_transition_id = None
    return {
        "outcome": runtime.pending_outcome,
        "entity_id": eid,
        "interaction_id": rule["id"],
        "source_event_seq": max(origins, key=lambda r: r["source_event_seq"])["source_event_seq"],
    }
