"""Resolve approved local search tasks before generating their required checks."""

from app.preparation.action_authority import action_kinds


def unrelated_item_focus(action, target, context):
    """A selected discovery cannot inherit an unmentioned equipment target."""
    from app.preparation.action_authority import mentions_alias

    entity = next((e for e in context.get("current_targets", []) if e["id"] == target), {})
    if entity.get("type") != "item":
        return False
    names = [entity.get("title", ""), *entity.get("aliases", [])]
    names += [
        name
        for item in context.get("inventory_state", {}).get("known_items", [])
        if item["id"] == target
        for name in item["names"]
    ]
    return bool(any(names)) and not any(mentions_alias(action, name) for name in names)


def search_instrument(action, target, inventory, actor):
    """An actually held tool used to inspect a place is not the search target."""
    import re

    if re.search(r"如果|假如|不用|不要|不借助", action):
        return False
    if not any(
        h["item_id"] == target and h["holder_id"] == actor for h in inventory.get("holders", [])
    ):
        return False
    names = [
        name
        for item in inventory.get("known_items", [])
        if item["id"] == target
        for name in item["names"]
    ]
    return any(
        name
        and re.search(
            r"(?:用|借助)[^，。；！？,;!?]{0,8}"
            + re.escape(name)
            + r"[^，。；！？,;!?]{0,24}(?:检查|搜索|观察|寻找|查看|照着|照向|照明|照亮)",
            action,
        )
        for name in names
    )


def unresolved_search_plan(plan, context):
    """Ask the existing generation retry to adjudicate an omitted real search.

    This never chooses a hidden result or a roll. An ordinary ungated inspection,
    a concrete obstacle, or a clarification can still proceed without a check.
    """
    focus = plan.focus
    return bool(
        focus
        and "search" in action_kinds(focus.action)
        and plan.parsed_intent.type == "investigate"
        and not context.get("readonly_recall")
        and not focus.obstacle
        and not plan.needs_clarification
        and not plan.parsed_intent.requires_clarification
        and not plan.proposed_check
        and not plan.proposed_tool_calls
        and not plan.proposed_reveal_entity_ids
        and not plan.proposed_transition_id
        and any(
            r.get("access_policy") == "requires_check" and r.get("successful_check")
            for r in context.get("check_requirements", [])
        )
    )


def validate_search_plan(plan, context):
    if not unresolved_search_plan(plan, context):
        return
    from app.models.ollama import ModelFormatError

    instruction = (
        "原话是实际搜索，当前有待调查目标，但计划未作裁定。"
        "请按check_requirements中的原稿条件选择调查目标与必要检定；"
        "若不能进行，在focus.obstacle说明当前具体障碍。"
        "不要只复述照明或替换成旧动作。不得直接宣布发现或成功。"
    )
    # The existing bounded retry sends issues, not exception prose. Keep the
    # server-authored repair instruction in that actual transmitted envelope.
    raise ModelFormatError(instruction, [{
        "field": "proposed_check", "code": "search_plan_missing", "instruction": instruction,
    }])


def named_check_requirement(
    requirement, action, selected_reveals=(), *, parent=None, proposal=None
):
    """Recognize an explicitly requested detail split by natural modifiers."""
    names = [requirement["title"], *requirement.get("aliases", [])]
    if any(name and name in action for name in names):
        return True
    proposed_detail = bool(
        proposal
        and requirement["entity_id"]
        in {proposal.get("target_entity_id"), proposal.get("clue_id")}
    )
    if requirement["entity_id"] not in selected_reveals and not proposed_detail:
        return False
    # The planner already selected this approved detail. Allow "报纸的报头，
    # 确认印刷日期" to identify "报纸日期", without revealing its contents or
    # treating a generic inspection of the parent as a request for every detail.
    import re

    if any(
        re.search(re.escape(name[:i]) + r".{0,24}" + re.escape(name[i:]), action)
        for name in names
        for i in range(2, len(name) - 1)
    ):
        return True
    # The selected object can be referred to as "these remains" while the
    # approved detail is named "remains' time of death". Require the actual
    # selected parent's name in the detail title and its distinct requested
    # suffix in the action; a generic parent inspection is still insufficient.
    parent = parent or {}
    if parent.get("type") not in {"clue", "location", "item"}:
        return False
    parent_names = [parent.get("title", ""), *parent.get("aliases", [])]
    if any(
        name[:i] in parent_name and name[i:] in action
        for name in names for i in range(2, len(name) - 1)
        for parent_name in parent_names if parent_name
    ):
        return True
    # A real KP check can identify a detail whose wording the player paraphrased.
    # Bind only that selected check, its actual parent basis and the approved
    # skill. This neither selects other details nor bypasses their prerequisites.
    required = requirement.get("successful_check") or {}
    return bool(
        proposed_detail
        and proposal.get("basis_entity_id") == parent.get("id")
        and required.get("name")
        and all(proposal.get(k) == required.get(k) for k in ("kind", "name"))
        and set(action_kinds(action)) & {"search", "observe"}
        and any(
            name[:i] in parent_name
            for name in names for i in range(2, len(name) - 1)
            for parent_name in parent_names if parent_name
        )
    )


def named_local_interaction_ids(facts, action=None):
    """Address named local objects whose approved access conditions allow contact.

    This exposes an identifier, not contents or a successful operation. Gated
    discoveries remain on the separate search/check path.
    """
    from app.agents.action_policy import local_scene_movement
    from app.agents.check_policy import entity_access
    from app.preparation.action_authority import aliases, mentions_alias, required_kinds
    from app.preparation.runtime_schemas import ModuleInteraction

    action = facts.raw_text if action is None else action
    kinds = set(action_kinds(action))
    local_stealth = "潜行" in action and local_scene_movement(
        action,
        [
            e
            for eid, e in facts.approved_entities.items()
            if eid in facts.local_entity_ids and e.get("type") == "scene"
        ],
    )
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
        and (
            any(mentions_alias(action, name) for name in aliases(entity))
            or local_stealth
            and entity.get("type") == "location"
            and any(
                r.get("kp_enabled")
                and r.get("check_name") == "stealth"
                and r.get("action_kinds") == ["pass"]
                for r in entity.get("interactions", [])
            )
        )
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
        or teammate_request(facts.raw_text, members, facts.actor_member_id, action=focus.action)
    ):
        return
    targets = named_local_interaction_ids(facts, focus.action)
    if focus.action_target_id in targets:
        target = focus.action_target_id
    elif len(targets) == 1:
        target = targets.pop()
    else:
        return
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


def repair_belongings_action(value, context):
    """Keep an explicit self-inspection when the selected span only says why."""
    import re

    from app.agents.generation_contracts import utterance_clauses
    from app.preparation.action_authority import declared_action, teammate_request
    from app.preparation.inventory import inventory_probe

    focus = value.get("focus") or {}
    if "search" in action_kinds(focus.get("action", "")):
        return
    trigger = context.get("triggering_action", {})
    raw = trigger.get("payload", {}).get("text", "")
    if context.get("readonly_recall") or teammate_request(
        raw, context.get("current_participants", {}).get("members", {}),
        trigger.get("actor_member_id"),
    ):
        return
    probes = [
        c["text"] for c in utterance_clauses(raw)
        if inventory_probe(c["text"]) and declared_action(c["text"])
        and "search" in action_kinds(c["text"])
        and re.search(r"我|自己", c["text"])
    ]
    if (
        len(probes) == 1 and focus.get("action")
        and re.search(r"随身|口袋|衣袋|保留|留下", focus["action"])
        and not set(action_kinds(focus["action"])) - {"observe", "converse"}
    ):
        focus["action"] = probes[0]
        value["parsed_intent"]["type"] = "investigate"


def repair_search_target(value, context):
    focus = value.get("focus") or {}
    action = focus.get("action", "")
    kinds = set(action_kinds(action))
    previous_target = next(
        (t for t in context.get("current_targets", []) if t["id"] == focus.get("action_target_id")),
        {},
    )
    observation_without_adjudication = bool(
        kinds == {"observe"}
        and previous_target.get("type") in {"clue", "location"}
        and not any(
            name and name in action
            for name in [previous_target.get("title", ""), *previous_target.get("aliases", [])]
        )
        and not context.get("readonly_recall")
        and not value.get("proposed_check")
        and not value.get("proposed_tool_calls")
        and not value.get("proposed_reveal_entity_ids")
        and not value.get("proposed_transition_id")
        and not focus.get("obstacle")
        and not value.get("needs_clarification")
    )
    if not ("search" in kinds or observation_without_adjudication) or kinds & {
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
        if observation_without_adjudication:
            if not requirement.get("successful_check") or (
                requirement.get("access_policy") != "requires_check"
            ):
                continue
            # Approved detail names may contain an object followed by a hidden
            # property. Match a distinctive object prefix without requiring the
            # player to name the undiscovered property. Ties stay with the KP.
            matched += [
                name[:size]
                for name in names if name
                for size in range(3, len(name))
                if all("\u4e00" <= char <= "\u9fff" for char in name[:size])
                if name[:size] in action
            ]
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
