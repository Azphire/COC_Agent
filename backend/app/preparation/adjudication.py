"""Bounded KP selection: choose a prepared method; never approve new rules or numbers."""

import re
import unicodedata
from typing import Literal

from pydantic import Field, create_model
from sqlalchemy import select

from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
from app.agents.check_policy import CheckProposal
from app.agents.combat_runtime import call_model
from app.agents.generation_contracts import utterance_clauses
from app.agents.schemas import PlannedTool
from app.domain.character import DomainModel
from app.persistence.agent_models import AgentRun
from app.persistence.room_models import RoomEvent
from app.preparation.inventory import held_instance, public_inventory
from app.preparation.runtime_schemas import ModuleInteraction
from app.rooms.combat_service import load_state, store_state


class PreparedDecision(DomainModel):
    used_item_id: str | None = Field(default=None, json_schema_extra={"x-explicit-output": True})
    option: str | None = Field(default=None, json_schema_extra={"x-explicit-output": True})
    action_clause_ids: list[str] = Field(
        default_factory=list, max_length=12, json_schema_extra={"x-explicit-output": True}
    )
    recipient_member_id: str | None = Field(
        default=None, json_schema_extra={"x-explicit-output": True}
    )
    npc_instance_id: str | None = Field(default=None, json_schema_extra={"x-explicit-output": True})
    applicable: bool = Field(default=False, json_schema_extra={"x-explicit-output": True})
    evidence_quotes: list[str] = Field(
        default_factory=list, max_length=4, json_schema_extra={"x-explicit-output": True}
    )
    reason: str = Field(default="", max_length=400, json_schema_extra={"x-explicit-output": True})
    clarification: str | None = Field(
        default=None, max_length=240, json_schema_extra={"x-explicit-output": True}
    )


class CurrentActionMatch(DomainModel):
    matches: bool
    action_quote: str = Field(default="", max_length=1000)
    reason: str = Field(min_length=1, max_length=300)
    clarification: str = Field(default="", max_length=240)


async def verify_current_action(runtime, state, action, rule, *, selected_item=None):
    # No historical facts in this pass: they cannot authorize an unperformed act.
    decision = await call_model(
        runtime,
        state,
        CurrentActionMatch,
        "只核对本轮玩家实际动作是否授权拟执行的操作，不裁定效果和成功率。"
        "只依据current_action；操作描述不是玩家的话，不能用它补造行动。"
        "selected_item非空时，必须是本轮实际交出、丢弃、消耗或投掷的物品；不能用别的持有物替换。"
        "观察或照明不会授权丢弃、投掷、交出物品；建议、假设和提问不会授权执行。"
        "动作不一致用false，clarification自然询问本轮要做的动作。",
        {
            "current_action": action,
            "proposed_operation": rule.inventory_operation
            or rule.encounter_operation
            or "prepared_method",
            "operation_description": rule.instruction,
            "selected_item": selected_item,
        },
        "prepared_action_authority",
    )
    # The current text already comes from validated event clause IDs. Do not ask
    # the model to reproduce it a second time just to authorize the same act.
    decision.action_quote = action
    return decision if decision.matches else None


INSTRUCTION = (
    "你是CoC的AI KP，裁定本次实际行动能否应用一个已批准方法。"
    "只选择options中的方法，不能批准新增数值或规则。"
    "只处理本轮clauses；问题、建议、假设、否定不是实际行动，action_clause_ids必须是本人正在做的动作。"
    "寻找、拿取、交出、插钥匙、推拉控制器分别按本次目标匹配。合法物品交接无需检定，交出者是actor。"
    "只有situation被本次动作和已有事实支持才applicable=true，evidence_quotes引用本次或已有事件的原话，reason解释条件和来源。"
    "距离、持续声源、实际抓住的人数、关门时机不能臆造。无法确定时applicable=false并自然询问clarification。"
    "规则已要求检定时，选择方法表示尝试，不表示成功，后端会叫骰。不要要求主机批准正常游戏判断。"
    "不能把敌人总数当成抓住actor的数量，npc_instance_id只选本次对抗的一只。"
    "若本轮没有适用操作，option=null。一次只选择一个方法；关门不是移动到另一个场景。"
    "declared_focus是本轮已解析的动作焦点，不要把照明等背景条件改成新的操作。"
    "当前行动依据由action_clause_ids对应的本轮原话生成；evidence_quotes补充已有事实，过去动作不能授权本次操作。"
)


def matches_action_focus(plan, scene_id, entity_id, rule):
    """An auxiliary method decision cannot redirect an already parsed action."""
    if not plan.focus or not plan.focus.action:
        return False
    if plan.parsed_intent.type in {"out_of_character", "wait", "recall"}:
        return False
    focus = plan.focus.action_target_id if plan.focus else plan.parsed_intent.target_id
    if plan.parsed_intent.type == "move" or (
        plan.proposed_transition_id and plan.parsed_intent.type == "unknown"
    ):
        return False
    if rule.inventory_operation == "initial":
        return focus == rule.item_id or (
            focus in {scene_id, entity_id}
            and plan.proposed_check is not None
            and plan.proposed_check.name == "luck"
        )
    return (
        not focus
        or focus in {entity_id, rule.item_id, rule.npc_id}
        or focus == scene_id
        and scene_id in rule.scene_node_ids
    )


def decision_contract(context, candidates):
    owned = [i["item_id"] for i in context["items"] if i["holder_id"] == context["actor"]]
    return create_model(
        "PreparedDecision",
        __base__=PreparedDecision,
        option=(
            Literal[tuple([c["option"] for c in candidates] + [None])],
            Field(default=None, json_schema_extra={"x-explicit-output": True}),
        ),
        used_item_id=(
            Literal[tuple(dict.fromkeys([*owned, None]))],
            Field(default=None, json_schema_extra={"x-explicit-output": True}),
        ),
        recipient_member_id=(
            Literal[tuple([m for m in context["members"] if m != context["actor"]] + [None])],
            Field(default=None, json_schema_extra={"x-explicit-output": True}),
        ),
        npc_instance_id=(
            Literal[tuple([*context["npc_instances"], None])],
            Field(default=None, json_schema_extra={"x-explicit-output": True}),
        ),
        action_clause_ids=(
            list[Literal[tuple(c["id"] for c in context["clauses"])]],
            Field(
                default_factory=list, max_length=12, json_schema_extra={"x-explicit-output": True}
            ),
        ),
    )


def action_evidence(decision, clauses, raw_text, known_quotes):
    """Resolve current-event clause IDs; model quotations only support extra facts."""
    action = "".join(c["text"] for c in clauses if c["id"] in decision.action_clause_ids)
    if (
        not action
        or action not in raw_text
        or not all(
            quote_text(q) and any(quote_text(q) in quote_text(text) for text in known_quotes)
            for q in decision.evidence_quotes
        )
        or re.search(r"是否|能否|可否|假如|假设|不要|并未|没有|[?？]", action)
    ):
        return None
    return action, list(dict.fromkeys([action, *decision.evidence_quotes]))


def quote_text(value):
    # Typography is not a fact change. Keep words, names, negations and numbers.
    text = unicodedata.normalize("NFKC", value).translate(str.maketrans("", "", "\"'“”‘’"))
    return re.sub(r"\s+", " ", text).strip()


async def adjudicate_prepared(runtime, state, plan_run_id):
    agents = runtime.service

    async def prepare(session, room):
        run = await session.get(AgentRun, plan_run_id)
        plan = KeeperPlan.model_validate(run.structured_output)
        from app.persistence.agent_models import AgentCycle

        cycle = await session.get(AgentCycle, state["cycle_id"])
        facts = await agents.adjudication.facts(session, room, cycle, run)
        data = load_state(room)
        candidates = []
        for eid, entity in facts.approved_entities.items():
            if eid not in facts.local_entity_ids or eid not in facts.revealed_entity_ids:
                continue
            for raw in entity.get("interactions", []):
                rule = ModuleInteraction.model_validate(raw)
                if not rule.kp_enabled or not rule.check_passed:
                    continue
                if not matches_action_focus(plan, facts.scene_id, eid, rule):
                    continue
                if rule.scene_node_ids and facts.scene_id not in rule.scene_node_ids:
                    continue
                if rule.inventory_operation == "recover":
                    initial = data.module_runtime.initial_belongings.get(facts.actor_member_id, {})
                    if (
                        initial.get("chosen_item_id") != rule.item_id
                        or initial.get("item_ids")
                        or initial.get("recovered_item_id")
                    ):
                        continue
                if rule.encounter_operation in {"put_down", "carry_check"}:
                    if data.module_runtime.carriers.get(rule.npc_id) != facts.actor_member_id:
                        continue
                if rule.encounter_operation == "carry_check":
                    last = data.module_runtime.carry_checks.get(rule.npc_id, {})
                    if (
                        last.get("scene_node_id") == facts.scene_id
                        and data.game_minute - last.get("minute", 0) < 5
                    ):
                        continue
                if any(
                    not held_instance(data.module_runtime, item, facts.actor_member_id)
                    for item in rule.required_item_ids
                ):
                    continue
                if rule.inventory_operation in {"give", "drop", "consume"} and not held_instance(
                    data.module_runtime, rule.item_id or eid, facts.actor_member_id
                ):
                    continue
                if rule.inventory_operation == "pickup" and not any(
                    data.module_runtime.item_instances.get(key, key) == (rule.item_id or eid)
                    and node == facts.scene_id
                    for key, node in data.module_runtime.dropped_items.items()
                ):
                    continue
                if rule.encounter_operation == "sound_stop" and not any(
                    s.get("active") and s.get("actor_id") == facts.actor_member_id
                    for s in data.module_runtime.sounds.values()
                ):
                    continue
                if not all(
                    data.module_runtime.flags.get(k, False) == v
                    for k, v in rule.required_flags.items()
                ):
                    continue
                if not set(rule.required_entity_ids) <= facts.revealed_entity_ids:
                    continue
                candidates.append(
                    {"entity_id": eid, "title": entity["title"], "rule": rule.model_dump()}
                )
        if not candidates:
            return None
        # Rank by the actual focus plus lexical relevance, without excluding synonyms.
        from app.knowledge.text import tokens

        terms = set(tokens(facts.raw_text))
        candidates.sort(
            key=lambda c: (
                c["entity_id"] != (plan.focus.action_target_id if plan.focus else None),
                -len(
                    terms
                    & set(tokens(c["title"] + c["rule"]["instruction"] + c["rule"]["situation"]))
                ),
            )
        )
        candidates = candidates[:16]
        for i, c in enumerate(candidates):
            c["option"] = str(i + 1)
        events = list(
            await session.scalars(
                select(RoomEvent)
                .where(
                    RoomEvent.room_id == room.id,
                    RoomEvent.seq < state["triggering_event_seq"],
                    RoomEvent.type.in_(
                        [
                            "action.submitted",
                            "module.interaction",
                            "agent.action_proposed",
                            "scene.updated",
                        ]
                    ),
                )
                .order_by(RoomEvent.seq.desc())
                .limit(8)
            )
        )
        quotes = [facts.raw_text, *[e.payload.get("text", "") for e in events]]
        context = {
            "actor": facts.actor_member_id,
            "declared_focus": plan.focus.model_dump() if plan.focus else None,
            "clauses": utterance_clauses(facts.raw_text),
            "scene": facts.scene_id,
            "options": [
                {
                    "option": c["option"],
                    "entity": c["title"],
                    "method": c["rule"]["instruction"],
                    "situation": c["rule"]["situation"],
                    "check": c["rule"]["check_name"],
                    "source_block_ids": c["rule"]["source_block_ids"],
                    "uses_sound_item": c["rule"]["encounter_operation"] == "sound_once",
                    "allows_worn_item": c["rule"]["allow_worn_sound_item"],
                }
                for c in candidates
            ],
            "members": {
                m.id: m.display_name
                for m in await agents.rooms.members(session, room)
                if m.active and m.role == "player"
            },
            "items": await public_inventory(agents, session, room),
            "facts": [
                {"title": e["title"], "summary": e.get("public_summary", "")[:300]}
                for eid, e in facts.approved_entities.items()
                if eid in facts.local_entity_ids & facts.revealed_entity_ids
            ],
            "recent_events": [
                {"seq": e.seq, "text": e.payload.get("text", "")} for e in reversed(events)
            ],
            "encounter": {
                k: getattr(data.module_runtime, k)
                for k in ("sounds", "doors", "grapples", "carriers", "flags")
            },
            "npc_instances": [
                f"module:{eid}:{i + 1}"
                for eid, count in data.module_runtime.npc_counts.items()
                if eid in facts.local_entity_ids
                for i in range(count)
            ],
        }
        # The KP may cite the same approved public facts/rules that it was given,
        # in addition to event dialogue. They do not supply action authority.
        quotes.extend(value for fact in context["facts"] for value in fact.values())
        quotes.extend(c["rule"][key] for c in candidates for key in ("instruction", "situation"))
        return context, candidates, quotes

    prepared = await agents.mutate(state["room_id"], prepare)
    if not prepared:
        return
    context, candidates, quotes = prepared
    schema = decision_contract(context, candidates)
    decision = await call_model(runtime, state, schema, INSTRUCTION, context, "prepared_situation")
    chosen = next((c for c in candidates if c["option"] == decision.option), None)
    verification = None
    if chosen and decision.applicable:
        evidence = action_evidence(decision, context["clauses"], quotes[0], quotes)
        if evidence:
            rule = ModuleInteraction.model_validate(chosen["rule"])
            selected_id = (
                decision.used_item_id
                if rule.encounter_operation == "sound_once"
                else rule.item_id
                if rule.inventory_operation in {"give", "drop", "consume"}
                else None
            )
            selected_item = next(
                (i["title"] for i in context["items"] if i["item_id"] == selected_id), None
            )
            verification = await verify_current_action(
                runtime, state, evidence[0], rule, selected_item=selected_item
            )

    async def persist(session, room):
        run = await session.get(AgentRun, plan_run_id)
        plan = KeeperPlan.model_validate(run.structured_output)
        plan.proposed_tool_calls = [
            t for t in plan.proposed_tool_calls if t.name != "apply_module_action"
        ]
        chosen = next((c for c in candidates if c["option"] == decision.option), None)
        if chosen and decision.applicable:
            evidence = action_evidence(decision, context["clauses"], quotes[0], quotes)
            if not evidence or not verification:
                plan.proposed_check = None
                plan.proposed_reveal_entity_ids = []
                plan.proposed_transition_id = None
                plan.needs_clarification = plan.parsed_intent.requires_clarification = True
                plan.parsed_intent.clarification_question = (
                    decision.clarification or "你这次具体要操作哪件物品、做什么动作？"
                )
                run.structured_output = plan.model_dump(mode="json")
                agents.rooms.append(
                    session,
                    room,
                    "module.ruling_rejected",
                    room.host_member_id,
                    {
                        "reason": "实际动作与拟执行操作不一致"
                        if evidence
                        else "实际行动或事实引用无效",
                        "decision": decision.model_dump(),
                        "source_event_seq": state["triggering_event_seq"],
                    },
                    "host_only",
                )
                return
            action, evidence_quotes = evidence
            rule = ModuleInteraction.model_validate(chosen["rule"])
            plan.focus = plan.focus or TurnFocus()
            plan.focus.action, plan.focus.action_target_id = action, chosen["entity_id"]
            plan.parsed_intent.type = "use_item" if rule.inventory_operation else "interact"
            plan.parsed_intent.target_id = chosen["entity_id"]
            plan.parsed_intent.evidence_quote = action
            plan.needs_host_review = plan.needs_clarification = False
            plan.parsed_intent.requires_clarification = False
            plan.proposed_transition_id = None
            args = {
                "entity_id": chosen["entity_id"],
                "interaction_id": rule.id,
                "evidence_quote": action,
                "recipient_member_id": decision.recipient_member_id,
                "npc_instance_id": decision.npc_instance_id,
                "used_item_id": decision.used_item_id,
            }
            plan.proposed_tool_calls.append(PlannedTool(name="apply_module_action", arguments=args))
            data = load_state(room)
            needs_check = bool(rule.check_name)
            if rule.carry_attribute:
                slot = next(
                    (
                        s
                        for s in await agents.rooms.slots(session, room)
                        if s.member_id == context["actor"]
                    ),
                    None,
                )
                value = (
                    slot.character_snapshot.get("effective_attributes", {}).get(
                        rule.carry_attribute
                    )
                    if slot
                    else None
                )
                needs_check = value is None or value < 70
            if (
                rule.inventory_operation == "initial"
                and context["actor"] in data.module_runtime.initial_belongings
            ):
                needs_check = False
            if needs_check:
                plan.focus.obstacle = rule.situation or rule.instruction
                plan.proposed_check = CheckProposal(
                    target_member_id=context["actor"],
                    kind="attribute"
                    if rule.check_name in {"str", "dex", "con", "luck"}
                    else "skill",
                    name=rule.check_name,
                    difficulty=rule.check_difficulty,
                    reason=rule.instruction,
                    purpose=rule.instruction,
                    method=action[:240],
                    target_entity_id=chosen["entity_id"],
                    necessity="required",
                    uncertainty=rule.situation or rule.instruction,
                    success_effect=rule.public_result[:240],
                    failure_consequence="本次尝试未成功，按已配置失败分支结算。",
                    basis_entity_id=chosen["entity_id"],
                    rule_topic_id="coc7.opposed_check"
                    if rule.opposed_npc_id
                    else "coc7.skill_check",
                    opposed={
                        "opponent_npc_id": rule.opposed_npc_id,
                        "kind": "attribute",
                        "name": rule.check_name,
                    }
                    if rule.opposed_npc_id
                    else None,
                )
            else:
                plan.proposed_check = None
            key = f"{state['triggering_event_seq']}:{chosen['entity_id']}:{rule.id}"
            data.module_runtime.rulings[key] = {
                "action": action,
                "actor_member_id": context["actor"],
                "source_event_seq": state["triggering_event_seq"],
                "source_block_ids": rule.source_block_ids,
                "evidence_quotes": evidence_quotes,
                "situation": rule.situation,
                "reason": decision.reason,
                "authority": "game_situation_only",
                "numeric_approval": False,
                "current_action_verification": verification.model_dump(),
            }
            store_state(room, data)
        elif chosen and decision.clarification:
            plan.needs_clarification = plan.parsed_intent.requires_clarification = True
            plan.parsed_intent.clarification_question = decision.clarification
            plan.proposed_check = None
        run.structured_output = plan.model_dump(mode="json")

    await agents.mutate(state["room_id"], persist)
