"""Game-condition decisions for already prepared SAN effects; no numeric authority."""

from typing import Literal

from pydantic import Field
from sqlalchemy import select

from app.agents.combat_runtime import call_model
from app.domain.character import DomainModel
from app.persistence.agent_models import AgentCycle, CheckRecord
from app.persistence.room_models import RoomEvent
from app.rooms.combat_service import load_state, store_state
from app.rooms.sanity_schemas import SanityEffect


class SanitySituation(DomainModel):
    applies: Literal["yes", "no", "unknown"]
    evidence_quotes: list[str] = Field(default_factory=list, max_length=6)
    reason: str = Field(min_length=1, max_length=300)
    clarification: str = Field(default="", max_length=200)


INSTRUCTION = (
    "你是CoC的KP，只裁定已批准SAN效果的实际游戏情境，不批准或改动数值。"
    "依据本次实际行动、已发生事件和当前事实判断是否满足condition。"
    "听到声音不等于看清怪物；提问不等于目睹；未记载的动作不能假定发生。"
    "evidence_quotes必须逐字引用input或facts中的原句，不能填事件编号。"
    "事实不够用unknown并给出自然澄清问题；明确不满足用no。输出SanitySituation。"
)


def visual_gate(effect, flags, raw, scene_facts=None):
    if effect.perception != "visual":
        return None
    if effect.visibility_any_flags and not any(flags.get(k) for k in effect.visibility_any_flags):
        return "no"
    if flags.get("phone_light") and not flags.get("torch_light"):
        import re

        located = (scene_facts or {}).get("visual_target_in_phone_light", {}).get("established")
        if not located and not re.search(r"靠近|走近|近处|面前|脚边|贴近", raw):
            return "unknown"
    return None


def completed_consequence(module_runtime, entry, source_seq, scene_id):
    if entry.get("trigger") != "entity_revealed":
        return None
    return next(
        (
            receipt
            for receipt in module_runtime.receipts.values()
            if receipt.get("source_event_seq") == source_seq
            and receipt.get("scene_node_id") == scene_id
            and entry["entity_id"] in receipt.get("consequence_entity_ids", [])
            and receipt.get("text")
        ),
        None,
    )


async def resolve_sanity_conditions(runtime, state):
    service = runtime.service
    for index, entry in enumerate(state.get("encounter_queue", [])):
        if entry["status"] != "kp_review":
            continue

        async def prepare(session, room):
            entity = await service.entities.entity(session, room.id, entry["entity_id"])
            effect = SanityEffect.model_validate(
                next(e for e in entity.snapshot["sanity_effects"] if e["id"] == entry["effect_id"])
            )
            event = await session.get(RoomEvent, (room.id, state["triggering_event_seq"]))
            nav = await service.navigation.state(session, room.id)
            public = await service.entities.public(session, room.id)
            checks = list(
                await session.scalars(
                    select(CheckRecord).where(
                        CheckRecord.room_id == room.id,
                        CheckRecord.target_member_id == state["triggering_member_id"],
                        CheckRecord.status == "resolved",
                    )
                )
            )
            context = {
                "input": event.payload.get("text", ""),
                "condition": effect.condition or effect.encounter,
                "facts": [
                    {"title": e["title"], "summary": e.get("public_summary", "")} for e in public
                ],
                "flags": room.session_state.get("module_runtime", {}).get("flags", {}),
                "scene_facts": room.session_state.get("module_runtime", {}).get("scene_facts", {}),
                "source_event": entry,
                "scene": nav.current_scene_node_id if nav else None,
                "visited_scenes": nav.visited_scene_node_ids if nav else [],
                "actual_checks": [
                    {
                        "name": c.document.get("name"),
                        "result": c.document.get("result"),
                        "sanity": c.document.get("sanity"),
                    }
                    for c in checks
                ][-12:],
                "observations": [
                    o
                    for o in load_state(room).module_runtime.observations.values()
                    if o.get("entity_id") == entry["entity_id"]
                    and o.get("effect_id") == entry["effect_id"]
                    and o.get("actor_member_id") in entry["target_member_ids"]
                ],
                "completed_consequence": completed_consequence(
                    load_state(room).module_runtime,
                    entry,
                    state["triggering_event_seq"],
                    nav.current_scene_node_id if nav else None,
                ),
            }
            from app.preparation.observation import approved_visual_result

            context["completed_visual"] = await approved_visual_result(
                service, session, room, entry["entity_id"], effect, context["input"]
            )
            return effect, context

        effect, context = await service.mutate(state["room_id"], prepare)
        observed = [o for o in context["observations"] if o.get("established")]
        consequence = context["completed_consequence"]
        if consequence and effect.perception == "other":
            decision = SanitySituation(
                applies="yes",
                evidence_quotes=[consequence["text"]],
                reason="本次已完成交互实际揭示的终幕后果，与公开回执一致",
            )
        elif observed or context["completed_visual"]:
            decision = SanitySituation(
                applies="yes",
                evidence_quotes=[observed[-1]["text"] if observed else context["input"]],
                reason="已完成观察的服务端回执",
            )
        elif gate := visual_gate(
            effect, context["flags"], context["input"], context["scene_facts"]
        ):
            decision = SanitySituation(
                applies=gate,
                evidence_quotes=[context["input"]],
                reason="按已知照明范围和实际站位核对目睹条件",
                clarification="手机灯只能照亮近处；你要靠近观察，还是留在原处？",
            )
        else:
            decision = await call_model(
                runtime, state, SanitySituation, INSTRUCTION, context, "sanity_situation"
            )
        quotes = [context["input"], *[f["summary"] for f in context["facts"]]]
        quotes.extend(o["text"] for o in observed)
        if consequence:
            quotes.append(consequence["text"])
        valid = bool(decision.evidence_quotes) and all(
            q and any(q in text for text in quotes) for q in decision.evidence_quotes
        )

        async def persist(session, room):
            cycle = await session.get(AgentCycle, state["cycle_id"])
            queue = [dict(e) for e in cycle.state["encounter_queue"]]
            item = queue[index]
            approved = valid and decision.applies == "yes"
            unresolved = decision.applies == "unknown" or not valid
            item.update(
                status="approved"
                if approved
                else "awaiting_clarification"
                if unresolved
                else "rejected",
                origin="kp_ruling",
                reason=decision.reason,
                evidence_quotes=decision.evidence_quotes,
                numeric_approval=False,
            )
            if approved and effect.audience == "party":
                slots = [s for s in await service.rooms.slots(session, room) if s.member_id]
                # Repeated observations never repeat SAN or assign it to new bystanders.
                slots = [
                    s
                    for s in slots
                    if not await service.sanity.encounters.previous(
                        session, room, item["entity_id"], item["effect_id"], s.member_id
                    )
                ]
                item.update(
                    target_member_ids=[s.member_id for s in slots], slot_ids=[s.id for s in slots]
                )
                if not slots:
                    item["status"] = "rejected"
            cycle.state = {**cycle.state, "encounter_queue": queue}
            if approved and context["completed_visual"]:
                data = load_state(room)
                observation = {
                    **context["completed_visual"],
                    "actor_member_id": state["triggering_member_id"],
                    "source_event_seq": entry["source_event_seq"],
                    "cycle_id": cycle.id,
                    "scene_node_id": context["scene"],
                    "entity_id": entry["entity_id"],
                    "effect_id": effect.id,
                    "established": True,
                }
                data.module_runtime.observations[f"{cycle.id}:{effect.id}"] = observation
                store_state(room, data)
                service.rooms.append(
                    session,
                    room,
                    "module.observation",
                    state["triggering_member_id"],
                    {"text": observation["text"], "cycle_id": cycle.id},
                )
                await service.entities.reveal(
                    session, room, entry["entity_id"], room.host_member_id, cycle_id=cycle.id
                )
            if unresolved:
                question = decision.clarification or (
                    "你这次是否实际看清了眼前的情形？"
                    if effect.perception == "visual"
                    else "请补充你这次实际遭遇的情况。"
                )
                data = load_state(room)
                key = f"{item['entity_id']}:{item['effect_id']}:{state['triggering_member_id']}"
                prior = data.module_runtime.sanity_clarifications.get(key)
                if not prior or prior.get("status") not in {
                    "awaiting_answer",
                    "settled",
                    "approved",
                }:
                    data.module_runtime.sanity_clarifications[key] = {
                        **item,
                        "id": key,
                        "original_cycle_id": cycle.id,
                        "scene_node_id": context["scene"],
                        "status": "awaiting_answer",
                        "perception": effect.perception,
                        "missing_facts": [effect.condition or effect.encounter],
                        "question": question,
                        "asked_at_seq": room.event_seq
                        if hasattr(room, "event_seq")
                        else state["triggering_event_seq"],
                        "unrelated_count": 0,
                    }
                    service.rooms.append(
                        session,
                        room,
                        "chat.message",
                        room.host_member_id,
                        {"text": question, "cycle_id": cycle.id},
                    )
                store_state(room, data)
            service.rooms.append(
                session,
                room,
                "sanity.situation_ruled",
                room.host_member_id,
                {"cycle_id": cycle.id, **item},
                "host_only",
            )
            return cycle.state

        state = await service.mutate(state["room_id"], persist)
    return state


async def resume_sanity_clarification(runtime, state, run_id):
    """Consume only related answers before any prepared item method is selected."""
    import re

    from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
    from app.persistence.agent_models import AgentRun

    service = runtime.service

    async def prepare(session, room):
        data = load_state(room)
        event = await session.get(RoomEvent, (room.id, state["triggering_event_seq"]))
        nav = await service.navigation.state(session, room.id)
        raw = event.payload.get("text", "")
        candidates = [
            p
            for p in data.module_runtime.sanity_clarifications.values()
            if p["status"] == "awaiting_answer"
            and state["triggering_member_id"] in p["target_member_ids"]
        ]
        visual_answer = bool(re.search(r"看清|看见|看到了|灯光|照明|靠近|走近", raw))
        candidates.sort(key=lambda p: (p.get("perception") == "visual") != visual_answer)
        pending = next(iter(candidates), None)
        if not pending:
            return None
        changed_scene = nav and nav.current_scene_node_id != pending["scene_node_id"]
        redirected = bool(re.search(r"不看|不再看|转身离开|改为|先不|不观察", raw))
        related = bool(
            re.search(
                r"看清|看见|看到了|照明|灯光|站在|走近|靠近|角度|只听|没看|没有看|是的|确实", raw
            )
        )
        if changed_scene or redirected or not related:
            pending["unrelated_count"] = pending.get("unrelated_count", 0) + 1
            if changed_scene or redirected or pending["unrelated_count"] >= 3:
                pending.update(status="expired", reason="尚未成立的观察已转向或过期")
            store_state(room, data)
            return None
        entity = await service.entities.entity(session, room.id, pending["entity_id"])
        effect = SanityEffect.model_validate(
            next(e for e in entity.snapshot["sanity_effects"] if e["id"] == pending["effect_id"])
        )
        from app.preparation.observation import approved_visual_result

        completed = await approved_visual_result(
            service, session, room, pending["entity_id"], effect, raw
        )
        return dict(pending), effect, raw, dict(data.module_runtime.flags), completed

    prepared = await service.mutate(state["room_id"], prepare)
    if not prepared:
        return False
    pending, effect, raw, flags, completed = prepared
    if completed:
        decision = SanitySituation(
            applies="yes",
            evidence_quotes=[raw],
            reason="缺失的观察选择已补全，批准方法的照明与站位条件满足",
        )
    elif gate := visual_gate(effect, flags, raw):
        decision = SanitySituation(applies=gate, evidence_quotes=[raw], reason="照明或站位仍不满足")
    else:
        decision = await call_model(
            runtime,
            state,
            SanitySituation,
            INSTRUCTION,
            {
                "input": raw,
                "condition": effect.condition,
                "pending": pending,
                "flags": flags,
                "instruction": "回答只补充站位或观察选择，不能推翻已知照明与位置限制。",
            },
            "sanity_clarification",
        )
    valid = bool(decision.evidence_quotes) and all(q and q in raw for q in decision.evidence_quotes)

    async def persist(session, room):
        data = load_state(room)
        item = data.module_runtime.sanity_clarifications[pending["id"]]
        run = await session.get(AgentRun, run_id)
        plan = KeeperPlan.model_validate(run.structured_output)
        plan.focus = TurnFocus(question=raw)
        plan.parsed_intent.type = "wait"
        plan.parsed_intent.target_id = None
        plan.proposed_tool_calls = []
        plan.proposed_check = None
        plan.proposed_transition_id = None
        plan.proposed_reveal_entity_ids = []
        plan.needs_clarification = plan.parsed_intent.requires_clarification = False
        run.structured_output = plan.model_dump(mode="json")
        item.update(answer_event_seq=state["triggering_event_seq"], reason=decision.reason)
        cycle = await session.get(AgentCycle, state["cycle_id"])
        if valid and decision.applies == "yes":
            item["status"] = "approved"
            resumed = {
                **item,
                "status": "approved",
                "origin": "kp_ruling",
                "repeat_confirmed": False,
            }
            if effect.audience == "party":
                slots = [
                    s
                    for s in await service.rooms.slots(session, room)
                    if s.member_id
                    and not await service.sanity.encounters.previous(
                        session, room, item["entity_id"], item["effect_id"], s.member_id
                    )
                ]
                resumed.update(
                    target_member_ids=[s.member_id for s in slots], slot_ids=[s.id for s in slots]
                )
                if not slots:
                    resumed["status"] = "rejected"
            cycle.state = {**cycle.state, "sanity_resumed": [resumed]}
            observation = {
                "actor_member_id": state["triggering_member_id"],
                "source_event_seq": pending["source_event_seq"],
                "answer_event_seq": state["triggering_event_seq"],
                "cycle_id": cycle.id,
                "scene_node_id": pending["scene_node_id"],
                "entity_id": pending["entity_id"],
                "effect_id": pending["effect_id"],
                "established": True,
                "origin": "scene_adjudication",
                "text": effect.encounter,
                **(completed or {}),
            }
            data.module_runtime.observations[pending["id"]] = observation
            service.rooms.append(
                session,
                room,
                "module.observation",
                state["triggering_member_id"],
                {"text": observation["text"], "cycle_id": cycle.id},
            )
        else:
            # One relevant answer gets one adjudication. Unknown remains visible
            # as unresolved, without an endless automatic question loop.
            item["status"] = (
                "not_applicable" if valid and decision.applies == "no" else "unresolved"
            )
        store_state(room, data)
        if completed and item["status"] == "approved":
            await service.entities.reveal(
                session, room, item["entity_id"], room.host_member_id, cycle_id=cycle.id
            )
        service.rooms.append(
            session,
            room,
            "sanity.clarification_resolved",
            room.host_member_id,
            dict(item),
            "host_only",
        )

    await service.mutate(state["room_id"], persist)
    return True
