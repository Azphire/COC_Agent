"""Resolve approved local search tasks before generating their required checks."""

from app.preparation.action_authority import action_kinds


def named_local_interaction_ids(facts, action=None):
    """Address named local objects whose approved access conditions allow contact.

    This exposes an identifier, not contents or a successful operation. Gated
    discoveries remain on the separate search/check path.
    """
    from app.agents.check_policy import entity_access
    from app.preparation.action_authority import aliases, required_kinds
    from app.preparation.runtime_schemas import ModuleInteraction

    action = facts.raw_text if action is None else action
    kinds = set(action_kinds(action))
    return {
        eid
        for eid, entity in facts.approved_entities.items()
        if eid in facts.local_entity_ids
        and entity.get("type") != "npc"
        and (
            eid in facts.revealed_entity_ids
            or entity_access(entity) == "automatic"
            and not facts.reveal_errors.get(eid)
        )
        and any(name and name in action for name in aliases(entity))
        and any(
            r.get("kp_enabled") and kinds & required_kinds(ModuleInteraction.model_validate(r))
            for r in entity.get("interactions", [])
        )
    }


def repair_local_interaction_target(plan, facts, members):
    """Revalidate a uniquely named operation target before freezing authority."""
    from app.preparation.action_authority import teammate_request

    focus = plan.focus
    if (
        not focus
        or not focus.action
        or focus.action not in facts.raw_text
        or plan.parsed_intent.type not in {"interact", "use_item"}
        or teammate_request(facts.raw_text, members, facts.actor_member_id)
    ):
        return
    targets = named_local_interaction_ids(facts, focus.action)
    if len(targets) != 1:
        return
    target = targets.pop()
    focus.action_target_id = target
    plan.parsed_intent.target_id = target
    plan.parsed_intent.target_kind = facts.approved_entities[target]["type"]
    if target not in facts.revealed_entity_ids:
        plan.proposed_reveal_entity_ids = list(
            dict.fromkeys([*plan.proposed_reveal_entity_ids, target])
        )
    plan.rationale_summary = "按原话中唯一的本地交互对象重新校验目标，再核对揭示条件及实际操作。"


def guard_initial_reselection(plan, facts, runtime):
    """A resolved starting choice cannot reveal a replacement through a new plan."""
    from app.preparation.inventory import held_instance

    actor = facts.actor_member_id
    target = plan.action_authority.get("target_id")
    if (
        actor not in runtime.initial_belongings
        or "search" not in plan.action_authority.get("kinds", [])
        or held_instance(runtime, target, actor)
    ):
        return False
    initial_items = {
        r.get("item_id")
        for eid, e in facts.approved_entities.items()
        if eid in facts.local_entity_ids
        for r in e.get("interactions", [])
        if r.get("inventory_operation") == "initial"
        and r.get("kp_enabled")
        and (not r.get("scene_node_ids") or facts.scene_id in r["scene_node_ids"])
    }
    if target not in initial_items:
        return False
    plan.action_authority["rejection_code"] = "initial_choice_settled"
    plan.needs_clarification = plan.parsed_intent.requires_clarification = True
    plan.parsed_intent.clarification_question = (
        "你的起始随身物已由先前的幸运检定确定，不能重新选择或重骰。"
        "这次没有取得新的物品，可以继续调查现场或前往其他车厢。"
    )
    plan.proposed_check = None
    plan.proposed_tool_calls = []
    plan.proposed_reveal_entity_ids = []
    plan.proposed_transition_id = None
    return True


def searchable_entity_ids(entities, local, scene):
    """A configured possession check permits searching, never early revelation."""
    result = {
        eid
        for eid, e in entities.items()
        if eid in local
        and e.get("type") in {"item", "clue", "location"}
        and e.get("reveal_conditions", {}).get("access_policy") == "requires_check"
    }
    result.update(
        rule["item_id"]
        for eid, e in entities.items()
        if eid in local
        for rule in e.get("interactions", [])
        if rule.get("inventory_operation") in {"initial", "recover"}
        and rule.get("check_name")
        and rule.get("kp_enabled")
        and rule.get("item_id") in local
        and entities[rule["item_id"]].get("type") == "item"
        and (not rule.get("scene_node_ids") or scene in rule["scene_node_ids"])
    )
    return result


def repair_observation_target(value, context):
    """Restore explicit visual observation before the primary plan is validated.

    Equipment supplies light; it is not the subject named after 'observe'. The
    current scene remains the target until a sourced observation actually works.
    """
    focus = value.get("focus") or {}
    action = focus.get("action", "")
    kinds = set(action_kinds(action))
    if "observe" not in kinds or kinds - {"observe", "converse"}:
        return
    if not any(
        alias and alias in action
        for target in context.get("observation_targets", [])
        for alias in target["aliases"]
    ):
        return
    focus["action_target_id"] = context["action_identifiers"]["current_scene_id"]
    value["parsed_intent"]["type"] = "observe"
    value["rationale_summary"] = (
        "按原话中的实际观察对象恢复主计划；照明用具作为条件，随后统一验证。"
    )


def repair_search_target(value, context):
    focus = value.get("focus") or {}
    action = focus.get("action", "")
    kinds = set(action_kinds(action))
    if "search" not in kinds or kinds & {
        "open",
        "close",
        "control",
        "give",
        "throw",
        "consume",
        "place",
    }:
        return
    candidates = []
    targets = {
        r["entity_id"]: r
        for r in [*context.get("search_targets", []), *context.get("check_requirements", [])]
    }
    for requirement in targets.values():
        names = [
            requirement["title"],
            *requirement.get("aliases", []),
            *requirement.get("search_aliases", []),
        ]
        matched = [name for name in names if name and name in action]
        if matched:
            candidates.append((max(map(len, matched)), requirement))
    if not candidates:
        return
    candidates.sort(key=lambda c: c[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return
    target = candidates[0][1]["entity_id"]
    previous = focus.get("action_target_id")
    focus["action_target_id"] = target
    value["parsed_intent"].update(type="investigate", target_id=target)
    value["proposed_tool_calls"] = [
        t
        for t in value.get("proposed_tool_calls", [])
        if t["name"] not in {"apply_module_action", "request_skill_check"}
    ]
    if previous != target:
        value["rationale_summary"] = "根据当前搜索对象及容器指代重新校验调查目标。"


def repair_control_target(value, context):
    """Resolve a uniquely described device before freezing the main plan.

    A wrong parent focus with no control method must not hide the only actual
    lever operation. An explicitly named different object, a question, or an
    ambiguous match retains its original target.
    """
    from app.knowledge.text import tokens
    from app.preparation.action_authority import action_kinds

    focus = value.get("focus") or {}
    action = focus.get("action", "")
    if "control" not in action_kinds(action):
        return
    target = focus.get("action_target_id")
    current = next((t for t in context.get("current_targets", []) if t["id"] == target), {})
    if current.get("type") == "npc" or current.get("title") and current["title"] in action:
        return
    terms = set(tokens(action))
    candidates = {
        entity["entity_id"]
        for entity in context.get("module_interactions", [])
        for method in entity["interactions"]
        if "control" in method.get("action_kinds", [])
        and len(terms & set(tokens(method["instruction"]))) >= 2
    }
    if len(candidates) == 1 and target not in candidates:
        focus["action_target_id"] = candidates.pop()
        value["rationale_summary"] = "按当前公开装置的唯一匹配操作恢复目标，再走正常裁定。"
