"""Bounded KP selection: choose a prepared method; never approve new rules or numbers."""

import re
import unicodedata
from copy import deepcopy
from typing import Literal

from pydantic import Field, create_model
from sqlalchemy import select

from app.agents.adjudication_schemas import KeeperPlan
from app.agents.check_policy import CheckProposal
from app.agents.combat_runtime import call_model
from app.agents.generation_contracts import utterance_clauses
from app.agents.schemas import PlannedTool
from app.domain.character import DomainModel
from app.persistence.agent_models import AgentRun
from app.persistence.room_models import RoomEvent
from app.preparation.action_authority import (
    action_kinds,
    authority_error,
    freeze_action,
    selected_action_matches,
)
from app.preparation.inventory import held_instance, public_inventory
from app.preparation.runtime_schemas import ModuleInteraction
from app.rooms.combat_service import load_state, store_state


class PreparedDecision(DomainModel):
    scene_facts: dict[str, str] = Field(default_factory=dict)
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
    "scene_facts只裁定options的required_facts，用本次原话中的站位或实际靠近观察作为值；"
    "无距离或站位依据就不填写，不能引用规则说明来证明实际事实。"
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
    if rule.encounter_operation == "sound_once" and scene_id in rule.scene_node_ids:
        authority = getattr(plan, "action_authority", {})
        if "throw" in authority.get("kinds", []) and focus in authority.get("item_instances", {}):
            return True  # A local sound effect can use the actually focused carried item.
    if (
        focus in rule.required_item_ids
        and entity_id in plan.action_authority.get("named_entity_ids", [])
        and selected_action_matches(
            plan.action_authority, plan.action_authority.get("action", ""), rule
        )
    ):
        return True  # Focus may name the tool; capability and actual holder still gate its use.
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


def repair_interaction_transition(plan, facts):
    """An approved method ID in the transition slot grants no party movement."""
    method_id = plan.proposed_transition_id
    if not method_id or method_id in facts.transitions or plan.parsed_intent.type == "move":
        return
    for eid, entity in facts.approved_entities.items():
        if eid not in facts.local_entity_ids:
            continue
        for raw in entity.get("interactions", []):
            rule = ModuleInteraction.model_validate(raw)
            if (
                rule.id == method_id
                and rule.kp_enabled
                and selected_action_matches(
                    plan.action_authority, plan.action_authority["action"], rule
                )
                and matches_action_focus(plan, facts.scene_id, eid, rule)
            ):
                plan.proposed_transition_id = None
                # State changes still require the normal candidate selection,
                # actual holder checks and executor receipt below.
                return


def action_evidence(decision, clauses, raw_text, known_quotes, *, state_verified=False):
    """Resolve current-event clause IDs; model quotations only support extra facts."""
    positions = [i for i, c in enumerate(clauses) if c["id"] in decision.action_clause_ids]
    span = clauses[min(positions) : max(positions) + 1] if positions else []
    # Clause splitting may produce a standalone newline between an action and
    # speech. Preserve those source bytes; never bridge an unselected text clause.
    if any(c["text"].strip() and c["id"] not in decision.action_clause_ids for c in span):
        return None
    action = "".join(c["text"] for c in span)
    supported = [
        q
        for q in decision.evidence_quotes
        if quote_text(q) and any(quote_text(q) in quote_text(text) for text in known_quotes)
    ]
    if not action or action not in raw_text or not action_kinds(action):
        return None
    if len(supported) != len(decision.evidence_quotes) and not state_verified:
        return None
    # Inventory and sound throws have server-owned action, possession, location
    # and distance/check gates. Discarded quotes cannot add authority or facts.
    return action, list(dict.fromkeys([action, *supported]))


def state_verified_method(raw):
    """Mechanical operations rely on current clauses and executor prerequisites."""
    return bool(
        raw.get("inventory_operation")
        or raw.get("acquire_item_ids")
        or raw.get("encounter_operation") == "sound_once"
        or raw.get("required_item_ids")
        and (raw.get("encounter_operation") == "open_door" or raw.get("action_kinds") == ["open"])
    )


def quote_text(value):
    # Typography is not a fact change. Keep words, names, negations and numbers.
    text = unicodedata.normalize("NFKC", value).translate(str.maketrans("", "", "\"'“”‘’"))
    return re.sub(r"\s+", " ", text).strip()


def prefer_established_sound_methods(candidates, runtime, authority):
    """Do not select an unproved automatic-distance branch over a legal throw."""
    from app.preparation.action_authority import establish_scene_facts

    preview = deepcopy(runtime)
    establish_scene_facts(
        preview,
        authority,
        {key: authority["action"] for c in candidates for key in c["rule"]["required_facts"]},
    )

    def established(candidate):
        return all(
            (fact := preview.scene_facts.get(key, {})).get("established")
            and fact.get("scene_node_id") == authority["scene_node_id"]
            and fact.get("actor_member_id") == authority["actor_member_id"]
            and fact.get("source_event_seq")
            and fact.get("origin") in {"scene_adjudication", "interaction_receipt"}
            for key in candidate["rule"]["required_facts"]
        )

    available = {
        c["entity_id"]
        for c in candidates
        if c["rule"]["encounter_operation"] == "sound_once" and established(c)
    }
    return [
        c
        for c in candidates
        if c["rule"]["encounter_operation"] != "sound_once"
        or c["entity_id"] not in available
        or established(c)
    ]


async def adjudicate_prepared(runtime, state, plan_run_id):
    agents = runtime.service

    async def prepare(session, room):
        run = await session.get(AgentRun, plan_run_id)
        plan = KeeperPlan.model_validate(run.structured_output)
        from app.persistence.agent_models import AgentCycle

        cycle = await session.get(AgentCycle, state["cycle_id"])
        facts = await agents.adjudication.facts(session, room, cycle, run)
        data = load_state(room)
        members = {
            m.id: m.display_name
            for m in await agents.rooms.members(session, room)
            if m.active and m.role == "player"
        }
        from app.preparation.search import repair_local_interaction_target

        repair_local_interaction_target(plan, facts, members)
        plan.action_authority = freeze_action(
            plan,
            facts.raw_text,
            facts.actor_member_id,
            state["triggering_event_seq"],
            facts.scene_id,
            facts.approved_entities,
            data.module_runtime,
            members,
        )
        plan.proposed_tool_calls = [
            t for t in plan.proposed_tool_calls if t.name != "apply_module_action"
        ]
        repair_interaction_transition(plan, facts)
        run.structured_output = plan.model_dump(mode="json")
        if plan.action_authority.get("request_member_id"):
            return None
        from app.preparation.search import guard_initial_reselection

        if guard_initial_reselection(plan, facts, data.module_runtime):
            run.structured_output = plan.model_dump(mode="json")
            return None
        candidates = []
        for eid, entity in facts.approved_entities.items():
            searching = bool(plan.proposed_check and str(plan.proposed_check.clue_id) == eid)
            revealing = (
                eid in plan.proposed_reveal_entity_ids
                and eid in facts.visible_entity_ids
                and entity.get("reveal_conditions", {}).get("access_policy") == "automatic"
                and not facts.reveal_errors.get(eid)
            )
            if eid not in facts.local_entity_ids or (
                eid not in facts.revealed_entity_ids and not searching and not revealing
            ):
                continue
            for raw in entity.get("interactions", []):
                rule = ModuleInteraction.model_validate(raw)
                if (
                    eid not in facts.revealed_entity_ids
                    and not revealing
                    and not rule.acquire_item_ids
                ):
                    continue
                if not rule.kp_enabled or not rule.check_passed:
                    continue
                if not matches_action_focus(plan, facts.scene_id, eid, rule):
                    continue
                # Item selection is checked once the option supplies it. Every
                # other capability is checked before showing a method candidate.
                if rule.encounter_operation != "sound_once" and authority_error(
                    plan.action_authority,
                    rule,
                    data.module_runtime,
                    actor=facts.actor_member_id,
                    seq=state["triggering_event_seq"],
                    scene=facts.scene_id,
                    check_facts=False,
                ):
                    continue
                if (
                    rule.encounter_operation == "sound_once"
                    and "throw" not in plan.action_authority["kinds"]
                ):
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
        candidates = prefer_established_sound_methods(
            candidates, data.module_runtime, plan.action_authority
        )
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
            "action_authority": plan.action_authority,
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
                    "required_facts": c["rule"]["required_facts"],
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
        # The model may quote a fact as "title: summary". Both fields already
        # came from the same approved entity; this adds no invented evidence.
        quotes.extend(f"{fact['title']}：{fact['summary']}" for fact in context["facts"])
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
        evidence = action_evidence(
            decision,
            context["clauses"],
            quotes[0],
            quotes,
            state_verified=state_verified_method(chosen["rule"]),
        )
        if evidence:
            # Action capabilities and item identity are checked deterministically
            # below and again at execution. A second model cannot add authority
            # and used to reject valid aliases after the method had been selected.
            verification = CurrentActionMatch(
                matches=True,
                action_quote=evidence[0],
                reason="执行层核对冻结动作和所用物品实例",
            )

    async def persist(session, room):
        run = await session.get(AgentRun, plan_run_id)
        plan = KeeperPlan.model_validate(run.structured_output)
        plan.proposed_tool_calls = [
            t for t in plan.proposed_tool_calls if t.name != "apply_module_action"
        ]
        chosen = next((c for c in candidates if c["option"] == decision.option), None)
        if chosen and decision.applicable:
            evidence = action_evidence(
                decision,
                context["clauses"],
                quotes[0],
                quotes,
                state_verified=state_verified_method(chosen["rule"]),
            )
            rule = ModuleInteraction.model_validate(chosen["rule"])
            data = load_state(room)
            from app.preparation.action_authority import establish_scene_facts

            establish_scene_facts(data.module_runtime, plan.action_authority, decision.scene_facts)
            error = authority_error(
                plan.action_authority,
                rule,
                data.module_runtime,
                actor=context["actor"],
                seq=state["triggering_event_seq"],
                scene=context["scene"],
                item_id=decision.used_item_id,
                recipient=decision.recipient_member_id,
            )
            if (
                not evidence
                or not verification
                or error
                or not selected_action_matches(plan.action_authority, evidence[0], rule)
            ):
                # Reject only the unrelated method. Preserve the original valid
                # observation/dialogue and any source-required search check.
                if error and error.startswith("尚缺已裁定"):
                    plan.next_decision = decision.clarification or "请说明你的站位和目标的位置。"
                    if "throw" in plan.action_authority["kinds"]:
                        # Publish the unresolved choice through the existing
                        # clarification path; do not narrate an unexecuted throw.
                        plan.needs_clarification = True
                        plan.parsed_intent.requires_clarification = True
                        plan.parsed_intent.clarification_question = plan.next_decision
                run.structured_output = plan.model_dump(mode="json")
                agents.rooms.append(
                    session,
                    room,
                    "module.ruling_rejected",
                    room.host_member_id,
                    {
                        "reason": error or "实际动作与拟执行操作或引用不一致",
                        "decision": decision.model_dump(),
                        "source_event_seq": state["triggering_event_seq"],
                    },
                    "host_only",
                )
                return
            selected_action, evidence_quotes = evidence
            action = plan.action_authority["action"]
            verification.action_quote = action
            rule = ModuleInteraction.model_validate(chosen["rule"])
            plan.needs_host_review = plan.needs_clarification = False
            plan.parsed_intent.requires_clarification = False
            plan.proposed_transition_id = None
            args = {
                "entity_id": chosen["entity_id"],
                "interaction_id": rule.id,
                "evidence_quote": action,
                "recipient_member_id": decision.recipient_member_id
                if rule.inventory_operation == "give"
                else None,
                "npc_instance_id": decision.npc_instance_id,
                "used_item_id": decision.used_item_id,
            }
            plan.proposed_tool_calls.append(PlannedTool(name="apply_module_action", arguments=args))
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
                possession_check = rule.inventory_operation in {"initial", "recover"}
                check_target = rule.item_id if possession_check else chosen["entity_id"]
                check_entity = (
                    await agents.entities.entity(session, room.id, check_target)
                    if possession_check
                    else None
                )
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
                    target_entity_id=check_target,
                    clue_id=check_target
                    if check_entity
                    and check_entity.state == "hidden"
                    and check_entity.snapshot.get("reveal_conditions", {}).get("access_policy")
                    == "requires_check"
                    else None,
                    necessity="required",
                    uncertainty=rule.situation or rule.instruction,
                    success_effect=rule.public_result[:240],
                    failure_consequence="本次尝试未成功，按已配置失败分支结算。",
                    basis_entity_id=check_target,
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
            elif not (
                plan.proposed_check and str(plan.proposed_check.clue_id) == chosen["entity_id"]
            ):
                plan.proposed_check = None
            key = f"{state['triggering_event_seq']}:{chosen['entity_id']}:{rule.id}"
            data.module_runtime.rulings[key] = {
                "action": action,
                "selected_action_quote": selected_action,
                "actor_member_id": context["actor"],
                "source_event_seq": state["triggering_event_seq"],
                "source_block_ids": rule.source_block_ids,
                "evidence_quotes": evidence_quotes,
                "situation": rule.situation,
                "reason": decision.reason,
                "authority": "game_situation_only",
                "numeric_approval": False,
                "current_action_verification": verification.model_dump(),
                "action_authority": plan.action_authority,
            }
            store_state(room, data)
        elif chosen and decision.clarification:
            plan.needs_clarification = plan.parsed_intent.requires_clarification = True
            plan.parsed_intent.clarification_question = decision.clarification
            plan.proposed_check = None
        run.structured_output = plan.model_dump(mode="json")

    await agents.mutate(state["room_id"], persist)
