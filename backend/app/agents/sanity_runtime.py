"""KP symptom selection; all insanity state and dice stay in SanityService."""

from uuid import UUID

from pydantic import Field
from sqlalchemy import select

from app.agents.combat_runtime import call_model
from app.domain.character import DomainModel
from app.persistence.agent_models import AgentCycle, CheckRecord
from app.persistence.room_models import RoomEvent
from app.rooms.combat_service import load_state
from app.rooms.sanity_schemas import SanityManagement


class SanitySymptom(DomainModel):
    symptom: str = Field(min_length=1, max_length=400)
    reason: str = Field(min_length=1, max_length=300)


async def complete_keeper_bouts(service, session, room):
    """Play out a KP-assigned psychological bout using its already fixed rounds.

    No actions, recovery, resource effects, or new dice are inferred. Combat,
    unresolved checks/injuries and physical restraint keep their existing flow.
    Summary-mode hours and manually assigned bouts still need scene adjudication.
    """
    data = load_state(room)
    if (
        not any(c.sanity.phase == "bout" for c in data.characters.values())
        or room.status != "running"
        or data.module_runtime.outcome
        or data.combat.active
        or any(data.module_runtime.grapples.values())
        or any(
            c.injury.dying or c.injury.con_pending
            for c in [*data.characters.values(), *data.combat.participants.values()]
        )
        or await service.cycle(session, room.id, active=True)
        or await session.scalar(
            select(CheckRecord.id).where(
                CheckRecord.room_id == room.id, CheckRecord.status == "pending"
            )
        )
        or not any(
            b.member_id == room.host_member_id for b in await service.bindings(session, room.id)
        )
    ):
        return
    assignments = {
        e.payload.get("slot_id"): e.payload
        for e in await session.scalars(
            select(RoomEvent)
            .where(RoomEvent.room_id == room.id, RoomEvent.type == "sanity.managed")
            .order_by(RoomEvent.seq)
        )
        if e.payload.get("operation") == "symptom"
    }
    assigned = {sid for sid, p in assignments.items() if p.get("reason", "").startswith("AI KP：")}
    names = {m.id: m.display_name for m in await service.rooms.members(session, room)}
    bouts = []
    for slot in await service.rooms.slots(session, room):
        character = data.characters.get(UUID(slot.id))
        if not character or str(slot.id) not in assigned:
            continue
        sanity = character.sanity
        if sanity.phase != "bout" or sanity.kind not in {"temporary", "indefinite"}:
            continue
        record = await session.get(CheckRecord, sanity.trigger_id)
        progress = record.document.get("sanity", {}) if record else {}
        if (
            not record
            or record.status != "resolved"
            or progress.get("origin") != "kp_ruling"
            or not progress.get("effect", {}).get("kp_enabled")
            or sanity.bout_end_round is None
            or not 0 <= sanity.bout_end_round - data.game_round <= 10
            or not progress.get("rolls", {}).get("bout_duration")
        ):
            continue
        bouts.append((sanity.bout_end_round, slot, sanity, progress))
    for end_round, slot, sanity, progress in sorted(bouts, key=lambda b: b[0]):
        duration = progress["rolls"]["bout_duration"]["total"]
        reason = f"AI KP：已确认的{sanity.symptom}心理发作，按既有发作骰{duration}轮经过。"
        if load_state(room).game_round < end_round:
            await service.sanity.manage(
                session,
                room,
                SanityManagement(
                    expected_revision=room.revision,
                    operation="advance",
                    round=end_round,
                    reason=reason,
                ),
            )
        await service.sanity.manage(
            session,
            room,
            SanityManagement(
                expected_revision=room.revision,
                operation="end_bout",
                slot_id=slot.id,
                reason=reason,
            ),
        )
        service.rooms.append(
            session,
            room,
            "sanity.bout_elapsed",
            room.host_member_id,
            {
                "text": f"{names.get(slot.member_id, '调查员')}经历了{duration}轮"
                f"{sanity.symptom}的发作。"
                "这段发作已结束，潜在的疯狂状态仍然持续。",
                "slot_id": slot.id,
                "check_id": sanity.trigger_id,
                "round": end_round,
                "source": "existing_sanity_service",
                "resource_recovery": False,
            },
        )


async def resolve_keeper_symptom(runtime, state, check_id):
    service = runtime.service

    async def prepare(session, room):
        cycle = await session.get(AgentCycle, state["cycle_id"])
        record = await session.get(CheckRecord, check_id)
        progress = record.document.get("sanity") if record else None
        if (
            not record
            or record.cycle_id != cycle.id
            or record.status != "pending"
            or not progress
            or progress["stage"] != "symptom"
            or not progress["effect"].get("kp_enabled")
            or cycle.state.get("request_category") not in {"investigation", "dialogue"}
            or check_id in cycle.state.get("sanity_symptom_errors", {})
            or room.status != "running"
            or check_id in getattr(runtime, "sanity_symptom_inflight", set())
            or not any(
                b.member_id == room.host_member_id for b in await service.bindings(session, room.id)
            )
        ):
            return None
        runtime.sanity_symptom_inflight = {
            *getattr(runtime, "sanity_symptom_inflight", set()),
            check_id,
        }
        cycle.status = "running"
        cycle.state = {**cycle.state, "status": "running", "wait_reason": None}
        service.cycle_event(session, room, cycle)
        return {
            "check_id": record.id,
            "slot_id": record.document["slot_id"],
            "encounter": progress["effect"]["encounter"],
            "before": progress["before"],
            "after": progress["after"],
            "insanity_kind": progress["insanity_kind"],
        }

    context = await service.mutate(state["room_id"], prepare)
    if not context:
        return False
    try:
        decision = await call_model(
            runtime,
            state,
            SanitySymptom,
            "你是本场CoC的KP。疯狂已经由规则服务确定，选择与实际遭遇有关的具体心理症状。"
            "只描述恐惧、偏执、畏缩等心理表现；不能替玩家决定行动，不能宣告物品变化、"
            "额外伤害、资源损失、恢复、移动或持续时间。数值和时长全部由既有服务处理。",
            context,
            "sanity_symptom",
        )

        async def apply(session, room):
            from app.agents.tools import ensure_public_text

            record = await session.get(CheckRecord, check_id)
            if record.status == "resolved":
                return True
            if record.document["sanity"]["stage"] != "symptom":
                return False
            ensure_public_text(await service.module(session, room.id), decision.symptom)
            cycle = await session.get(AgentCycle, state["cycle_id"])
            cycle.status = "waiting_for_roll"
            cycle.state = {
                **cycle.state,
                "status": "waiting_for_roll",
                "wait_reason": "sanity_symptom",
            }
            await service.sanity.manage(
                session,
                room,
                SanityManagement(
                    expected_revision=room.revision,
                    operation="symptom",
                    slot_id=context["slot_id"],
                    symptom=decision.symptom,
                    reason="AI KP：" + decision.reason,
                ),
            )
            return record.status == "resolved"

        return await service.mutate(state["room_id"], apply)
    except Exception:

        async def failed(session, room):
            cycle = await session.get(AgentCycle, state["cycle_id"])
            cycle.status = "waiting_for_roll"
            cycle.state = {
                **cycle.state,
                "status": "waiting_for_roll",
                "wait_reason": "sanity_symptom",
                "sanity_symptom_errors": {
                    **cycle.state.get("sanity_symptom_errors", {}),
                    check_id: "KP 症状生成失败，保留既有症状选择入口",
                },
            }
            service.cycle_event(session, room, cycle)

        await service.mutate(state["room_id"], failed)
        return False
    finally:
        runtime.sanity_symptom_inflight.discard(check_id)
