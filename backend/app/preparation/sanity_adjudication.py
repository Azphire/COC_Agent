"""Game-condition decisions for already prepared SAN effects; no numeric authority."""

from typing import Literal

from pydantic import Field
from sqlalchemy import select

from app.agents.combat_runtime import call_model
from app.domain.character import DomainModel
from app.persistence.agent_models import AgentCycle, CheckRecord
from app.persistence.room_models import RoomEvent
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
            }
            return effect, context

        effect, context = await service.mutate(state["room_id"], prepare)
        decision = await call_model(
            runtime, state, SanitySituation, INSTRUCTION, context, "sanity_situation"
        )
        quotes = [context["input"], *[f["summary"] for f in context["facts"]]]
        valid = bool(decision.evidence_quotes) and all(
            q and any(q in text for text in quotes) for q in decision.evidence_quotes
        )

        async def persist(session, room):
            cycle = await session.get(AgentCycle, state["cycle_id"])
            queue = [dict(e) for e in cycle.state["encounter_queue"]]
            item = queue[index]
            approved = valid and decision.applies == "yes"
            item.update(
                status="approved" if approved else "rejected",
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
            if decision.applies == "unknown" or not valid:
                question = decision.clarification or "你这次是否实际看清了眼前的情形？"
                service.rooms.append(
                    session, room, "chat.message", room.host_member_id, {"text": question}
                )
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
