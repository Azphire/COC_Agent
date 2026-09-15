"""Small generation grammars; full public/persisted contracts stay authoritative.

The per-call subclasses keep the same contract names and validation, but bind
server metadata as defaults. These fields never enter the generation grammar.
"""

import re
from typing import Literal

from pydantic import Field, create_model, model_validator

from app.agents.adjudication_schemas import (
    KeeperNarration,
    KeeperPlan,
    NPCSpeech,
    PlayerIntent,
    TeammateDecision,
    TurnFocus,
)
from app.agents.check_policy import CheckProposal


def utterance_clauses(raw):
    pieces = [part for part in re.split(r"(?<=[，。！？；,.!?;\n])", raw) if part]
    pieces = pieces[:11] + ["".join(pieces[11:])] if len(pieces) > 12 else pieces
    return [{"id": f"u{i}", "text": part} for i, part in enumerate(pieces, 1)]


def bound(base, values, **fields):
    return create_model(
        base.__name__,
        __base__=base,
        **{
            k: (
                base.model_fields[k].annotation,
                Field(
                    default=v,
                    json_schema_extra={
                        "x-server-bound": True,
                    },
                ),
            )
            for k, v in values.items()
        },
        **fields,
    )


def generation_contract(schema, context):
    if schema is KeeperPlan:
        ids = context["action_identifiers"]
        people = [t["id"] for t in context.get("current_targets", []) if t["type"] == "npc"]
        people += [
            member_id
            for member_id in context.get("current_participants", {}).get("members", {})
            if member_id != ids["actor_member_id"]
        ]
        targets = [t["id"] for t in context.get("current_targets", [])]
        targets += [t["target_scene_node_id"] for t in context.get("approved_exits", [])]
        focus_base = create_model(
            "TurnFocus",
            __base__=TurnFocus,
            addressee_id=(
                Literal[tuple(dict.fromkeys([*people, None]))],
                Field(
                    default=None,
                    json_schema_extra={"x-explicit-output": True},
                ),
            ),
            action_target_id=(
                Literal[tuple(dict.fromkeys([*targets, ids["current_scene_id"], None]))],
                Field(default=None, json_schema_extra={"x-explicit-output": True}),
            ),
        )
        clauses = utterance_clauses(context["triggering_action"]["payload"]["text"])
        clause_list = list[Literal[tuple(c["id"] for c in clauses)]]
        focus_base = bound(
            focus_base, {k: "" for k in ("action", "question", "suggestion", "hypothesis")}
        )
        focus = create_model(
            "TurnFocus",
            __base__=TurnFocus.__bases__[0],
            **{
                name + "_clause_ids": (
                    clause_list,
                    Field(
                        default_factory=list,
                        max_length=12,
                        json_schema_extra={"x-explicit-output": True},
                    ),
                )
                for name in ("action", "question", "suggestion", "hypothesis")
            },
            **{k: (f.annotation, f) for k, f in focus_base.model_fields.items()},
        )
        trigger = context["triggering_action"]
        from app.preparation.action_authority import (
            action_kinds,
            declared_action,
            speaker_action,
            teammate_request,
        )

        original = trigger.get("payload", {}).get("text", "")
        own_transfers = {
            c["id"]: c["text"] for c in clauses
            if speaker_action(c["text"]) and "give" in action_kinds(c["text"])
        } if teammate_request(original, context.get("current_participants", {}).get("members", {}),
                              trigger.get("actor_member_id")) else {}
        if not context.get("readonly_recall") and (
            trigger.get("type") == "agent.action_proposed"
            and re.search(r"检查|搜索|寻找|拿起|使用|交给", original)
            or declared_action(original)
            and action_kinds(original)
        ):

            def keep_proposed_operation(value):
                if own_transfers and not (
                    set(value.action_clause_ids) & own_transfers.keys()
                    or value.action and any(t in value.action for t in own_transfers.values())
                ):
                    raise ValueError(
                        "本人交出物品与队友请求是两段意图；"
                        "action_clause_ids须保留本人实际交出段落。"
                    )
                if not value.action_clause_ids and not (value.action and value.action in original):
                    raise ValueError(
                        "原话包含实际检查或物品操作；action_clause_ids必须保留原动作。检查有没有文字不是只向人提问。检定仍由KP按条件决定。"
                    )
                return value

            focus = create_model(
                "TurnFocus",
                __base__=focus,
                __validators__={
                    "keep_proposed_operation": model_validator(mode="after")(
                        keep_proposed_operation
                    )
                },
            )
        intent = bound(
            PlayerIntent,
            {
                "schema_version": 1,
                "actor_member_id": ids["actor_member_id"],
                "actor_character_slot_id": ids["actor_character_slot_id"],
                "evidence_quote": context["triggering_action"]["payload"]["text"],
                "confidence": 1,
                "target_kind": None,
                "target_id": None,
                "target_text": None,
                "requested_outcome": "",
                "ambiguity_reason": None,
            },
        )
        check = bound(
            CheckProposal,
            {
                "target_member_id": ids["actor_member_id"],
                "rule_topic_id": None,
                "risk_quote": "",
                "necessity": "unnecessary",
                "purpose": "",
                "method": "",
                "reason": "",
                "uncertainty": "",
            },
        )
        from app.agents.compound_generation import proposal_contract

        check = proposal_contract(check, context)
        return bound(
            KeeperPlan,
            {
                **{
                    k: ids[k]
                    for k in (
                        "plan_id",
                        "cycle_id",
                        "current_scene_id",
                        "expected_navigation_revision",
                    )
                },
                "schema_version": 1,
                "addressed_member_id": None,
                "action_authority": {},
                "target_entity_ids": [],
                "target_node_ids": [],
                "source_entity_ids": [],
                "source_node_ids": [],
                "source_evidence_ids": [],
                "expected_next_phase": "narration",
                "rationale_summary": "",
                **(
                    {"pending_action": "independent"}
                    if not context.get("conversation_parent")
                    else {}
                ),
            },
            parsed_intent=(intent, ...),
            focus=(
                focus,
                Field(
                    # Generation requires an object via x-explicit-output;
                    # legacy providers that omit the field retain their default.
                    default=None,
                    json_schema_extra={"x-explicit-output": True},
                ),
            ),
            proposed_check=(
                check | None,
                Field(
                    default=None,
                    json_schema_extra={
                        "x-explicit-output": True,
                    },
                ),
            ),
            **(
                {
                    "proposed_transition_id": (
                        Literal[
                            tuple([e["transition_id"] for e in context["approved_exits"]] + [None])
                        ],
                        Field(default=None, json_schema_extra={"x-explicit-output": True}),
                    )
                }
                if "approved_exits" in context
                else {}
            ),
        )
    if schema is KeeperNarration:
        responder = context.get("response_brief", {}).get("responder", {})
        fields = {}
        if responder.get("kind") == "npc":
            speech = bound(NPCSpeech, {"entity_id": responder["id"]})
            fields["npc_speech"] = (
                speech,
                Field(
                    ...,
                    json_schema_extra={"x-explicit-output": True},
                ),
            )
        fields["public_narration"] = (
            str,
            Field(
                default="",
                max_length=2000,
                json_schema_extra={"x-explicit-output": True},
            ),
        )
        return bound(
            KeeperNarration,
            {
                "schema_version": 1,
                "grounded_claims": [],
                "current_scene_reference": context["current_scene_reference"],
                "check_result_reference": None,
                "transition_result_reference": None,
                "public_entity_references": [],
                **({"npc_speech": None} if responder.get("kind") != "npc" else {}),
            },
            **fields,
        )
    if schema is TeammateDecision:
        return bound(
            TeammateDecision,
            {
                "schema_version": 1,
                "related_player_action_seq": context["triggering_action"]["seq"],
            },
        )
    return schema


def restore_output(output, schema, context):
    value = output.model_dump(mode="json")
    if schema is KeeperPlan:
        from app.agents.action_policy import READ_TOOLS, explicit_movement, named_move_exits

        if value.get("proposed_transition_id") in {"null", "None", ""}:
            value["proposed_transition_id"] = None

        raw = context["triggering_action"]["payload"]["text"]
        named = named_move_exits(raw, context.get("approved_exits", []))
        from app.preparation.action_authority import action_kinds

        if (
            len(named) == 1
            and explicit_movement(raw)
            and value["parsed_intent"]["type"]
            in {"observe", "investigate", "wait", "unknown", "converse", "interact"}
            and set(action_kinds(raw)) <= {"observe", "search", "pass"}
            and not re.search(r"潜行|悄悄", raw)
        ):
            # "Enter the hall and look around" cannot lose its explicit move
            # merely because the model called it investigation. The named exit
            # still goes through the normal availability/authority checks.
            focus = value.get("focus") or TurnFocus().model_dump(mode="json")
            focus.update(action=raw, action_target_id=named[0]["target_scene_node_id"])
            if focus.get("question") == raw:
                focus.update(question="", addressee_id=None)
            value["focus"] = focus
            value["parsed_intent"].update(
                type="move", evidence_quote=raw, requires_clarification=False
            )
            value["proposed_transition_id"] = named[0]["transition_id"]
            value["needs_clarification"] = False
            value["proposed_check"] = None
            value["proposed_reveal_entity_ids"] = []
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"] if t["name"] in READ_TOOLS
            ]
        local = named_move_exits(
            raw,
            [
                {**t, "target_public_title": t["title"]}
                for t in context.get("current_targets", [])
                if t.get("type") != "scene"
            ],
        )
        if (
            value["parsed_intent"]["type"] in {"move", "unknown"}
            and value.get("proposed_transition_id")
            and not named
            and len(local) == 1
            and explicit_movement(raw)
        ):
            value["focus"] = TurnFocus(action=raw, action_target_id=local[0]["id"]).model_dump(
                mode="json"
            )
            value["parsed_intent"].update(
                type="interact", evidence_quote=raw, requires_clarification=False
            )
            value["proposed_transition_id"] = None
            value["proposed_check"] = None
            value["needs_clarification"] = False
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"] if t["name"] in READ_TOOLS
            ]
            if local[0]["type"] == "npc" and not any(
                r["entity_id"] == local[0]["id"] for r in context.get("check_requirements", [])
            ):
                value["proposed_reveal_entity_ids"] = [local[0]["id"]]
        if (
            value["parsed_intent"]["type"] in {"move", "unknown"}
            and (not value.get("focus") or value["parsed_intent"]["type"] == "unknown")
            and len(named) == 1
            and value.get("proposed_transition_id") == named[0]["transition_id"]
            and explicit_movement(raw)
        ):
            # Repair an incomplete representation of the model's selected move,
            # corroborated by the existing verb/destination guards. No inferred exit.
            value["focus"] = TurnFocus(
                action=raw, action_target_id=named[0]["target_scene_node_id"]
            ).model_dump(mode="json")
            value["parsed_intent"].update(
                type="move", evidence_quote=raw, requires_clarification=False
            )
            value["needs_clarification"] = False
            value["proposed_check"] = None
            value["proposed_tool_calls"] = [
                tool for tool in value["proposed_tool_calls"] if tool["name"] in READ_TOOLS
            ]
    if schema is KeeperPlan and value.get("focus"):
        focus = value["focus"]
        if focus.get("answer_basis") in {"improvise", "unrecorded"}:
            from app.agents.action_policy import READ_TOOLS

            if (
                not value.get("proposed_check")
                and not value.get("proposed_reveal_entity_ids")
                and not value.get("proposed_transition_id")
                and all(t["name"] in READ_TOOLS for t in value["proposed_tool_calls"])
            ):
                value["needs_host_review"] = False
        clauses = utterance_clauses(context["triggering_action"]["payload"]["text"])
        for name in ("action", "question", "suggestion", "hypothesis"):
            key = name + "_clause_ids"
            chosen = focus.pop(key, [])
            if output.focus is not None and key in output.focus.model_fields_set:
                positions = [i for i, c in enumerate(clauses) if c["id"] in chosen]
                focus[name] = (
                    "".join(c["text"] for c in clauses[min(positions) : max(positions) + 1])
                    if positions
                    else ""
                )
        from app.agents.action_policy import named_move_exits
        from app.preparation.action_authority import NON_ACTION, action_kinds, declared_action

        kinds = set(action_kinds(focus.get("action", "")))
        if (
            (
                value["parsed_intent"]["type"] in {"converse", "wait", "unknown", "assist"}
                or value["parsed_intent"]["type"] == "observe"
                and (
                    kinds & {"take", "give", "open", "close", "place", "control"}
                    or "search" in kinds
                    and (
                        re.search(r"翻看|翻过|翻转|揭下", focus.get("action", ""))
                        or any(
                            entry.get("entity_id") == focus.get("action_target_id")
                            and any("search" in rule.get("action_kinds", [])
                                    for rule in entry.get("interactions", []))
                            for entry in context.get("module_interactions", [])
                        )
                    )
                )
            )
            and kinds
            and declared_action(focus["action"])
        ):
            value["parsed_intent"]["type"] = (
                "observe" if kinds <= {"observe", "light"} else "interact"
            )

        if (
            value["parsed_intent"]["type"] in {"observe", "converse", "wait", "unknown", "move"}
            and "throw" in action_kinds(focus.get("action", ""))
            and (value["parsed_intent"]["type"] != "move" or not explicit_movement(raw))
            and re.search(
                r"(?:^|[，。；])\s*我?(?:(?:把|将|脱下|摘下|取下).{1,24})?(?:扔|抛|掷|投向)",
                focus["action"],
            )
        ):
            # An explicit current throw cannot be downgraded to observation.
            # Keep the source clauses/target; approved methods still decide
            # whether a worn or actually held object is legal and its result.
            value["parsed_intent"]["type"] = "interact"
            value["proposed_transition_id"] = None
            value["needs_clarification"] = False
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
            ]

        if (
            value["parsed_intent"]["type"] == "move"
            and not explicit_movement(raw)
            and set(action_kinds(focus.get("action", "")))
            & {"control", "open", "close", "give", "place", "take", "light", "sound_start"}
            and any(
                t["id"] == focus.get("action_target_id")
                and t["type"] in {"item", "location", "clue"}
                for t in context.get("current_targets", [])
            )
        ):
            # Moving a lever/item is not an investigator scene transition.
            # Keep the selected target and raw clauses, then use normal method
            # adjudication; this repair cannot grant any world operation itself.
            value["parsed_intent"]["type"] = "interact"
            value["proposed_transition_id"] = None
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
            ]

        if (
            NON_ACTION.search(focus.get("action", ""))
            and not action_kinds(focus["action"])
            and not explicit_movement(focus["action"])
            and not (value["parsed_intent"]["type"] == "move" and explicit_movement(raw))
        ):
            focus["question"] = focus.get("question") or focus["action"]
            focus["action"] = ""
            focus["action_target_id"] = None
            value["parsed_intent"]["type"] = "converse" if focus.get("addressee_id") else "wait"
            value["proposed_check"] = None
            value["proposed_tool_calls"] = []
            value["proposed_reveal_entity_ids"] = []
            value["proposed_transition_id"] = None

        named = named_move_exits(
            context["triggering_action"]["payload"]["text"], context.get("approved_exits", [])
        )
        if value["parsed_intent"]["type"] == "move" and len(named) == 1:
            value["proposed_transition_id"] = named[0]["transition_id"]
            # Discard a model-authored movement call with a different destination.
            # The approved transition proposal is normalized by the existing planner.
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
            ]
        transition = next(
            (
                t
                for t in context.get("approved_exits", [])
                if t["transition_id"] == value.get("proposed_transition_id")
            ),
            None,
        )
        if transition and value["parsed_intent"]["type"] == "move":
            # A selected move has one destination. Restore it from the approved
            # candidate; the ordinary intent/exit/ownership guards still apply.
            focus["action_target_id"] = transition["target_scene_node_id"]
            # Preserve the actual movement clause when the model selected only
            # its preceding door-opening clause. The destination still comes
            # from the approved exit and the complete utterance must authorize it.
            if len(named) == 1 and explicit_movement(raw):
                movement_positions = [
                    i
                    for i, clause in enumerate(clauses)
                    if explicit_movement(clause["text"])
                    and named_move_exits(clause["text"], [transition])
                ]
                if movement_positions and not explicit_movement(focus.get("action", "")):
                    focus["action"] = "".join(
                        c["text"]
                        for c in clauses[min(movement_positions) : max(movement_positions) + 1]
                    )
                if "converse" not in action_kinds(raw):
                    focus.update(question="", addressee_id=None)
        proposal = value.get("proposed_check")
        from app.preparation.search import (
            named_check_requirement,
            repair_belongings_action,
            repair_control_target,
            repair_observation_target,
            repair_search_target,
        )

        repair_belongings_action(value, context)
        repair_observation_target(value, context)
        repair_search_target(value, context)
        repair_control_target(value, context)
        named_requirements = [
            r
            for r in context.get("check_requirements", [])
            if r.get("access_policy") == "requires_check"
            and r.get("successful_check")
            and named_check_requirement(
                r, focus.get("action", ""), value.get("proposed_reveal_entity_ids", []),
                parent=next(
                    (t for t in context.get("current_targets", [])
                     if t["id"] == focus.get("action_target_id")), None
                ),
                proposal=proposal,
            )
        ]
        from app.preparation.search import search_instrument, unrelated_item_focus

        if (
            not named_requirements
            and (
                focus.get("action_target_id")
                == context.get("action_identifiers", {}).get("current_scene_id")
                or any(
                    t.get("type") == "scene" and t["id"] == focus.get("action_target_id")
                    for t in context.get("current_targets", [])
                )
                or unrelated_item_focus(
                    focus.get("action", ""), focus.get("action_target_id"), context
                )
                or search_instrument(
                    focus.get("action", ""),
                    focus.get("action_target_id"),
                    context.get("inventory_state", {}),
                    context.get("triggering_action", {}).get("actor_member_id"),
                )
            )
            and set(action_kinds(focus.get("action", ""))) & {"search", "observe"}
            and value["parsed_intent"]["type"] in {"investigate", "observe"}
        ):
            # A scene search need not name the object it has not discovered yet.
            # The KP already chose a single gated discovery; bind that attempt
            # to its approved real check, retaining all normal prerequisites.
            named_requirements = [
                r
                for r in context.get("check_requirements", [])
                if (
                    r["entity_id"] in value.get("proposed_reveal_entity_ids", [])
                    or r["entity_id"] == (proposal or {}).get("target_entity_id")
                )
                and r.get("access_policy") == "requires_check"
                and r.get("successful_check")
            ]
        if len(named_requirements) == 1 and value["parsed_intent"]["type"] in {
            "investigate",
            "observe",
            "interact",
        }:
            # Resolve an explicitly named gated detail instead of its parent object.
            focus["action_target_id"] = named_requirements[0]["entity_id"]
        required = next(
            (
                r
                for r in context.get("check_requirements", [])
                if r["entity_id"] == focus.get("action_target_id")
                and r.get("access_policy") == "requires_check"
                and r.get("successful_check")
            ),
            None,
        )
        if (
            required
            and focus.get("action")
            and not focus.get("obstacle")
            and value["parsed_intent"]["type"] in {"investigate", "observe", "interact"}
        ):
            focus["obstacle"] = "模组已批准条件要求先完成检定，目标尚未确认。"
        if (
            required
            and focus.get("action")
            and value["parsed_intent"]["type"] in {"investigate", "observe", "interact"}
            and (
                not proposal
                or proposal.get("target_entity_id") != required["entity_id"]
                or not proposal.get("alternative_basis")
                and any(
                    proposal.get(k) != required["successful_check"][k]
                    for k in ("kind", "name", "difficulty")
                )
            )
        ):
            # A source-approved mandatory search is a rule, not optional model
            # judgement. This requests a real roll; it never supplies its result.
            focus["obstacle"] = "模组已批准条件要求先完成检定，目标尚未确认。"
            proposal = CheckProposal(
                **required["successful_check"],
                target_member_id=context["action_identifiers"]["actor_member_id"],
                reason=focus["action"],
                clue_id=required["entity_id"],
                target_entity_id=required["entity_id"],
                basis_entity_id=required["entity_id"],
                success_effect="完成模组配置的调查目标。",
                failure_consequence="本次未取得模组配置的发现，不自动扣除资源。",
            ).model_dump(mode="json")
            value["proposed_check"] = proposal
            value["rationale_summary"] = "按已批准的目标条件补齐必需检定；使用真实骰。"
        if not focus.get("action") or not focus.get("obstacle"):
            value["proposed_check"] = None
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"] if t["name"] != "request_skill_check"
            ]
        elif proposal:
            if required and proposal.get("target_entity_id") == required["entity_id"]:
                # A model may propose the correct check but omit its discovery
                # binding. Bind the existing proposal before method candidates
                # and deferred acquisition are collected, just as for a new one.
                proposal.update(
                    clue_id=required["entity_id"],
                    basis_entity_id=required["entity_id"],
                    target_member_id=context["action_identifiers"]["actor_member_id"],
                )
            actor = next(
                (
                    c
                    for c in context.get("characters", [])
                    if c.get("member_id") == context["action_identifiers"]["actor_member_id"]
                ),
                {},
            )
            # Resolve an unambiguous attribute name, e.g. luck, without letting
            # the model choose numeric values or invent a new skill.
            if proposal.get("name") in actor.get("effective_attributes", {}) and proposal.get(
                "name"
            ) not in actor.get("skill_values", {}):
                proposal["kind"] = "attribute"
            proposal.update(
                rule_topic_id="coc7.skill_check",
                necessity="required",
                purpose=(focus.get("purpose") or focus["action"])[:240],
                method=focus["action"][:240],
                reason=focus["action"],
                uncertainty=focus["obstacle"],
            )
    acknowledgement = False
    if schema is KeeperPlan:
        from app.preparation.inventory import held_item_acknowledgement

        trigger = context["triggering_action"]
        acknowledgement = trigger.get(
            "type"
        ) == "agent.action_proposed" and held_item_acknowledgement(
            raw, context.get("inventory_state", {}), trigger.get("actor_member_id")
        )
    if schema is KeeperPlan and (context.get("readonly_recall") or acknowledgement):
        value["parsed_intent"].update(
            type="converse" if acknowledgement else "recall",
            target_id=None,
            evidence_quote=context["triggering_action"]["payload"]["text"],
            requires_clarification=False,
        )
        value.update(
            proposed_check=None,
            proposed_tool_calls=[],
            proposed_transition_id=None,
            proposed_reveal_entity_ids=[],
            needs_clarification=False,
        )
        value["focus"] = TurnFocus(
            question=context["triggering_action"]["payload"]["text"],
            addressee_id=(value.get("focus") or {}).get("addressee_id"),
            answer_basis="facts",
        ).model_dump()
    elif schema is KeeperPlan:
        from app.agents.action_policy import local_scene_movement

        if local_scene_movement(
            raw, context.get("current_targets", []), context.get("approved_exits", [])
        ):
            scene_id = context["action_identifiers"]["current_scene_id"]
            focus = value.get("focus") or TurnFocus().model_dump()
            focus.update(action=raw, action_target_id=scene_id)
            value.update(focus=focus, proposed_transition_id=None, needs_clarification=False)
            # The navigation node is already the current scene. It is not an
            # unrevealed prepared entity that a local move needs to discover.
            value["proposed_reveal_entity_ids"] = [
                eid for eid in value["proposed_reveal_entity_ids"] if eid != scene_id
            ]
            value["parsed_intent"].update(
                type="interact",
                target_id=scene_id,
                evidence_quote=raw,
                requires_clarification=False,
            )
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
                and not (
                    t["name"] == "reveal_entity" and t["arguments"].get("entity_id") == scene_id
                )
            ]
            if value.get("proposed_check"):
                value["proposed_check"].update(target_entity_id=scene_id, clue_id=None)
    if schema is KeeperNarration and value.get("claim_ids"):
        options = {c["claim_id"]: c for c in context["PUBLIC_CLAIM_OPTIONS"]}
        if not set(value["claim_ids"]) <= options.keys():
            from app.models.ollama import ModelFormatError

            raise ModelFormatError("未知公开依据", [{"field": "claim_ids", "code": "unknown_id"}])
        value["grounded_claims"] = [options[k] for k in dict.fromkeys(value["claim_ids"])]
    if schema in {KeeperNarration, TeammateDecision} and context.get("readonly_recall"):
        from app.memory.facts import render_facts

        evidence = context.get("fact_evidence", [])
        ids = value.get("fact_ids", [])
        # The model may order retrieved excerpts. The original text is always
        # rendered by the server, including evidence the model forgot to select.
        known = {r["id"]: r for r in evidence}
        ids = list(dict.fromkeys([i for i in ids if i in known] + list(known)))
        content = render_facts([known[i] for i in ids])
        if schema is KeeperNarration:
            value.update(
                public_narration=content,
                incidental_details=[],
                npc_speech=None,
                grounded_claims=[],
                claim_ids=[],
                fact_ids=ids,
            )
        else:
            value.update(
                mode="speak",
                action_type="recall",
                action_text=None,
                speech_text=content,
                target_id=None,
                related_public_entity_ids=[],
                fact_ids=[r["id"] for r in evidence],
            )
    elif schema is KeeperNarration:
        brief = context.get("response_brief", {})
        if value.get("npc_speech") and brief.get("dialogue_answers"):
            from app.preparation.dialogue import sourced_dialogue_reply

            value["npc_speech"]["text"] = sourced_dialogue_reply(
                brief.get("question", ""), brief["dialogue_answers"]
            )
        results = context.get("public_tool_results", {}).get("events", [])
        transitions = [e for e in results if e["type"] == "scene.updated"]
        interactions = [
            e["payload"]["text"]
            for e in results
            if e["type"] == "module.interaction" and e["payload"].get("text")
        ]
        checks = [e for e in results if e["type"] == "check.resolved"]
        reveals = [
            e["payload"].get("public_summary", e["payload"].get("content", ""))
            for e in results
            if e["type"] in {"entity.revealed", "clue.revealed"}
        ]
        if transitions:
            p = transitions[-1]["payload"]
            # A movement outcome is a receipt, not prose inferred from intent.
            value["public_narration"] = (
                "你已抵达" + p.get("scene_title", "") + "。" + p.get("scene_summary", "")
            )
            value["incidental_details"] = []
        elif interactions:
            # The selected approved operation supplies its actual result text.
            # A correct claim ID cannot authorize a different outcome in prose.
            value["public_narration"] = "\n".join(dict.fromkeys(interactions))
            value["incidental_details"] = []
        elif checks:
            from app.agents.narration import fallback_narration

            # A real roll constrains the achieved result, not only its ID or the
            # words "success/failure". Only actual reveals can supply new clues.
            value["public_narration"] = fallback_narration(
                context.get("intent_type", ""), {"events": results}, "", brief=brief
            )
            value["incidental_details"] = []
        elif context.get("public_tool_results", {}).get("blocked_operations"):
            value["public_narration"] = "这次动作没有完成，当前状态未因这次尝试改变。"
            value.update(incidental_details=[], claim_ids=[], grounded_claims=[])
        elif brief.get("movement_requested"):
            title = brief.get("current_scene", {}).get("title", "原场景")
            value["public_narration"] = "本次没有完成转场；当前位置仍是" + title + "。"
            value.update(incidental_details=[], claim_ids=[], grounded_claims=[])
        elif (
            brief.get("responder", {}).get("kind") == "teammate"
            and not brief.get("attempt")
            and not brief.get("source_quotes")
        ):
            value["public_narration"] = ""
            value.update(incidental_details=[], claim_ids=[], grounded_claims=[])
        elif brief.get("inventory_probe") and brief.get("responder", {}).get("kind") != "npc":
            from app.preparation.inventory import inventory_reply

            view = context.get("inventory_state", {})
            actor = context["triggering_action"].get("actor_member_id")
            name = next(
                (m["name"] for m in view.get("members", []) if m["id"] == actor), "该调查员"
            )
            value["public_narration"] = (
                ""
                if brief.get("responder", {}).get("kind") == "teammate"
                else "请先完成本次随身物检查的检定，物品结果尚未确定。"
                if any(e["type"] == "check.requested" for e in results)
                else inventory_reply(view, actor).replace("我", name, 1)
            )
            value.update(incidental_details=[], claim_ids=[], grounded_claims=[])
        elif reveals:
            value["public_narration"] = "\n".join(filter(None, reveals))
            value["incidental_details"] = []
        elif context.get("public_tool_results", {}).get("blocked_discovery"):
            value["public_narration"] = "这次尚未确认新的线索内容，仍需满足该目标的调查条件。"
            value["incidental_details"] = []
        elif brief.get("unconfirmed_target"):
            value["public_narration"] = "这次检查的目标尚未实际确认，目前无法确定其中的具体内容。"
            value["incidental_details"] = []
        elif brief.get("source_quotes") and brief.get("responder", {}).get("kind") != "npc":
            # Already public writing remains the same on a teammate's repeated
            # inspection; an old improvised absence cannot erase the source.
            value["public_narration"] = "\n".join(brief["source_quotes"])
            value["incidental_details"] = []
    return schema.model_validate(value)
