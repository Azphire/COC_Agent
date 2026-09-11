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
                focus | None,
                Field(
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
            if key in output.focus.model_fields_set:
                positions = [i for i, c in enumerate(clauses) if c["id"] in chosen]
                focus[name] = (
                    "".join(c["text"] for c in clauses[min(positions) : max(positions) + 1])
                    if positions
                    else ""
                )
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
        proposal = value.get("proposed_check")
        if not focus.get("action") or not focus.get("obstacle"):
            value["proposed_check"] = None
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"] if t["name"] != "request_skill_check"
            ]
        elif proposal:
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
