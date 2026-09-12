"""Small generation grammars; full public/persisted contracts stay authoritative.

The per-call subclasses keep the same contract names and validation, but bind
server metadata as defaults. These fields never enter the generation grammar.
"""

import re
from typing import Literal

from pydantic import Field, create_model

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
        people += list(context.get("current_participants", {}).get("members", {}))
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
        )
    if schema is KeeperNarration:
        responder = context.get("response_brief", {}).get("responder", {})
        fields = {}
        if responder.get("kind") == "npc":
            speech = bound(NPCSpeech, {"entity_id": responder["id"]})
            fields["npc_speech"] = (
                speech | None,
                Field(
                    default=None,
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

        raw = context["triggering_action"]["payload"]["text"]
        named = named_move_exits(raw, context.get("approved_exits", []))
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
        proposal = value.get("proposed_check")
        named_requirements = [
            r
            for r in context.get("check_requirements", [])
            if r.get("access_policy") == "requires_check"
            and r.get("successful_check")
            and r["title"] in focus.get("action", "")
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
            and not proposal
            and focus.get("action")
            and value["parsed_intent"]["type"] in {"investigate", "observe", "interact"}
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
    if schema is KeeperNarration and value.get("claim_ids"):
        options = {c["claim_id"]: c for c in context["PUBLIC_CLAIM_OPTIONS"]}
        if not set(value["claim_ids"]) <= options.keys():
            from app.models.ollama import ModelFormatError

            raise ModelFormatError("未知公开依据", [{"field": "claim_ids", "code": "unknown_id"}])
        value["grounded_claims"] = [options[k] for k in dict.fromkeys(value["claim_ids"])]
    return schema.model_validate(value)
