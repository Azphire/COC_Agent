"""A bounded mechanics clarification when a plan omitted an explicit compound attempt."""

import re
from typing import Literal

from pydantic import Field, create_model

from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
from app.agents.check_policy import CheckProposal
from app.agents.combat_runtime import call_model
from app.domain.character import DomainModel
from app.models.base import ModelError
from app.persistence.agent_models import AgentRun
from app.rooms.service import require
from app.rules.compound import CombinedCheck, OpposedCheck, noncombat_name


class ExtendedDecision(DomainModel):
    mode: Literal["none", "opposed", "all", "any"]
    action_quote: str = Field(min_length=1, max_length=240)
    kind: Literal["attribute", "skill"] = Field(
        default="skill", json_schema_extra={"x-server-bound": True}
    )
    name: str
    second_name: str
    opponent_id: str | None
    opponent_kind: Literal["attribute", "skill"] = Field(
        default="skill", json_schema_extra={"x-server-bound": True}
    )
    uncertainty: str = Field(max_length=240)
    success_effect: str = Field(max_length=240)
    failure_consequence: str = Field(max_length=240)


async def clarify_extended(runtime, state, run_id):
    async with runtime.rooms.database.sessions() as session:
        run = await session.get(AgentRun, run_id)
        plan, context = KeeperPlan.model_validate(run.structured_output), run.context
        raw = context["triggering_action"]["payload"]["text"]
        sensory_pair = bool(
            re.search(r"同时|两项|都做到|组合", raw)
            and re.search(r"观察|侦查|看准", raw)
            and re.search(r"辨听|聆听|听准", raw)
        )
        if (
            plan.proposed_check
            and (plan.proposed_check.opposed or plan.proposed_check.combined)
            and not (sensory_pair and plan.proposed_check.opposed)
        ):
            return
        if context.get("conversation_parent") or not re.search(
            r"掰手腕|对抗检定|组合检定|力量较量|两项.{0,10}(必须|都)|同时.{0,35}(观察|辨听|侦查|聆听)",
            raw,
        ):
            return
        compact = {
            "actor_id": context["action_identifiers"]["actor_member_id"],
            "input": raw,
            "characters": context.get("characters", []),
            "opponents": context.get("compound_candidates", {}),
            "people": context.get("current_participants", {}),
            "scene": context.get("module", {}).get("scene")
            or next(
                (
                    s
                    for s in context.get("module", {}).get("scenes", [])
                    if s["id"] == context["action_identifiers"]["current_scene_id"]
                ),
                {},
            ),
        }
    actor = next(
        (c for c in compact["characters"] if c.get("member_id") == compact["actor_id"]), {}
    )
    keys = [
        *actor.get("effective_attributes", {}),
        *[n for n in actor.get("skill_values", {}) if noncombat_name(n)],
    ]
    # Candidate narrowing uses the named task, never a prescribed roll or outcome.
    if re.search(r"掰手腕|力量较量|比比?力气", raw) and "str" in keys:
        keys = ["str"]
    elif (
        re.search(r"观察|侦查|看准", raw)
        and re.search(r"辨听|聆听|听准", raw)
        and {"spot_hidden", "listen"} <= set(keys)
    ):
        keys = ["spot_hidden", "listen"]
    candidates = context.get("compound_candidates", {})
    opponent_ids = [
        *candidates.get("members", []),
        *[n["npc_id"] for n in candidates.get("npcs", [])],
        None,
    ]
    # When the KP already required a check, clarify its mechanics without discarding it.
    modes = ["none", "opposed", "all", "any"]
    if sensory_pair:
        modes = (
            ["全部通过", "任一通过"] if plan.proposed_check else ["全部通过", "任一通过", "不检定"]
        )
        opponent_ids = [None]
        compact["opponents"] = {}
        compact["people"] = {}
        compact["characters"] = [actor]
        compact["already_requires_check"] = bool(plan.proposed_check)
    contract = (
        create_model(
            "ExtendedDecision",
            __base__=ExtendedDecision,
            name=(Literal[tuple(keys)], ...),
            second_name=(Literal[tuple(keys)], ...),
            opponent_id=(Literal[tuple(opponent_ids)], ...),
            mode=(Literal[tuple(modes)], ...),
            action_quote=(
                Literal[
                    tuple(
                        [raw]
                        if len(raw) <= 240
                        else [raw[i : i + 240] for i in range(0, len(raw), 240)]
                    )
                ],
                ...,
            ),
        )
        if keys
        else ExtendedDecision
    )
    try:
        decision = await call_model(
            runtime,
            state,
            contract,
            "你是KP，仅判定此句是否正在尝试扩展检定。返回ExtendedDecision。actor_id是发起者，成功效果必须从发起者角度描述。"
            "非战斗双方争胜、互斥目标选opposed，例如掰手腕力量str对str；不要用斗殴。"
            "同一动作需要两种技能，选all两项都成功或any任一成功，只掷一次骰。"
            "如果mode候选使用中文，则选择全部通过（all）、任一通过（any）或不检定（none）。"
            "观察并辨听共同定位可用spot_hidden与listen；按实际条件选all或any。"
            "仅提问、假设、聊天、没有不确定因素的简单动作选none。机器遮挡、光线暗、运转异响等是实际阻力。"
            "action_quote必须是玩家原话中的实际尝试子串。"
            "ID和技能键复制候选。uncertainty写已有阻力，success_effect和failure_consequence区分成败；不宣布结果。",
            compact,
            "clarify_extended",
        )
    except ModelError:
        return
    decision = decision.model_copy(
        update={
            "mode": {
                "全部通过": "all",
                "任一通过": "any",
                "不检定": "none",
            }.get(decision.mode, decision.mode)
        }
    )
    if decision.mode == "none":
        return

    async def apply(session, room):
        run = await session.get(AgentRun, run_id)
        plan = KeeperPlan.model_validate(run.structured_output)
        require(
            decision.action_quote and decision.action_quote in raw,
            "扩展检定的动作依据必须来自原话",
            422,
        )
        ids = context["action_identifiers"]
        candidates = context.get("compound_candidates", {})
        opposed, combined = None, None
        if decision.mode == "opposed":
            members = candidates.get("members", [])
            npcs = {n["npc_id"] for n in candidates.get("npcs", [])}
            require(decision.opponent_id in set(members) | npcs, "扩展检定对手不在候选范围", 422)
            opponent = next(
                (c for c in compact["characters"] if c.get("member_id") == decision.opponent_id), {}
            )
            npc = next(
                (n for n in candidates.get("npcs", []) if n["npc_id"] == decision.opponent_id), {}
            )
            opposed = OpposedCheck(
                opponent_member_id=decision.opponent_id
                if decision.opponent_id in members
                else None,
                opponent_npc_id=decision.opponent_id if decision.opponent_id in npcs else None,
                kind="attribute"
                if decision.second_name in actor.get("effective_attributes", {})
                or decision.second_name in opponent.get("effective_attributes", {})
                or decision.second_name in npc.get("attributes", [])
                else "skill",
                name=decision.second_name,
            )
        else:
            combined = CombinedCheck(name=decision.second_name, requirement=decision.mode)
        plan.proposed_check = CheckProposal(
            target_member_id=ids["actor_member_id"],
            kind="attribute" if decision.name in actor.get("effective_attributes", {}) else "skill",
            name=decision.name,
            opposed=opposed,
            combined=combined,
            reason=decision.action_quote,
            purpose=decision.success_effect,
            method=decision.action_quote,
            target_entity_id=plan.parsed_intent.target_id or ids["current_scene_id"],
            necessity="required",
            uncertainty=decision.uncertainty,
            success_effect=decision.success_effect,
            failure_consequence=decision.failure_consequence,
            rule_topic_id="coc7.opposed_check" if opposed else "coc7.combined_check",
        )
        plan.focus = (plan.focus or TurnFocus()).model_copy(
            update={
                "action": decision.action_quote,
                "action_target_id": plan.proposed_check.target_entity_id,
                "purpose": decision.success_effect,
                "obstacle": decision.uncertainty,
            }
        )
        plan.parsed_intent.type = "interact"
        plan.parsed_intent.evidence_quote = decision.action_quote
        plan.parsed_intent.target_id = plan.proposed_check.target_entity_id
        # Reserve one of the existing four tool slots for the actual check.
        module = await runtime.service.module(session, room.id)
        if not await runtime.service.entities.binding(session, room.id):
            possible = {c["id"] for c in module.document["clues"]} - set(
                module.state["revealed_clues"]
            )
            plan.proposed_reveal_entity_ids = [
                eid for eid in plan.proposed_reveal_entity_ids if eid in possible
            ]
        budget = max(
            0, 3 - len(plan.proposed_reveal_entity_ids) - int(bool(plan.proposed_transition_id))
        )
        plan.proposed_tool_calls = plan.proposed_tool_calls[:budget]
        run.structured_output = plan.model_dump(mode="json")
        runtime.rooms.append(
            session,
            room,
            "agent.extended_clarified",
            room.host_member_id,
            {"cycle_id": state["cycle_id"], "decision": decision.model_dump(mode="json")},
            "host_only",
        )

    await runtime.service.mutate(state["room_id"], apply)
