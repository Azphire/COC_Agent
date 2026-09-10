"""Deterministic encounter discovery after adjudication, never from selecting a target."""

from sqlalchemy import select

from app.agents.adjudication_schemas import AdjudicationRecord
from app.module_ir.facts import recalling
from app.persistence.adjudication_models import ActionPlanRecord
from app.persistence.agent_models import AgentRun, CheckRecord
from app.persistence.room_models import RoomEvent
from app.rooms.sanity_schemas import SanityEffect
from app.rooms.service import require


class EncounterService:
    def __init__(self, sanity):
        self.sanity, self.agents, self.rooms = sanity, sanity.agents, sanity.rooms

    async def previous(self, session, room, entity_id, effect_id, member_id):
        return [
            c
            for c in await session.scalars(
                select(CheckRecord).where(
                    CheckRecord.room_id == room.id, CheckRecord.target_member_id == member_id
                )
            )
            if c.document.get("sanity")
            and not c.document.get("sanity_rewound")
            and c.document["sanity"]["entity_id"] == entity_id
            and c.document["sanity"]["effect"]["id"] == effect_id
        ]

    async def discover(self, session, room, cycle):
        if cycle.state.get("encounters_scanned"):
            return
        cycle.state = {
            **cycle.state,
            "encounters_scanned": True,
            "encounter_queue": [],
            "ordinary_check_id": cycle.state.get("pending_check_id"),
            "settlement_phase": "sanity",
            "pending_check_id": None,
        }
        record = await session.get(ActionPlanRecord, cycle.id)
        if not record or cycle.state.get("request_category") != "investigation":
            return
        doc = AdjudicationRecord.model_validate(record.document)
        run = await session.get(AgentRun, record.run_id)
        facts = await self.agents.adjudication.facts(session, room, cycle, run)
        intent = doc.plan.parsed_intent
        # Rejecting an unnecessary model roll does not undo a valid ordinary
        # observation. Revalidate the actor's action separately from tool proposals.
        redundant_check = bool(
            doc.validation
            and doc.validation.check_decisions
            and all(
                d.code in {"routine_action", "unnecessary", "already_public"}
                for d in doc.validation.check_decisions
            )
            and all(a.tool == "request_skill_check" for a in doc.validation.rejected_actions)
        )
        intent_error = self.agents.adjudication.validator.validate_intent(intent, doc.plan, facts)
        if (
            not doc.validation
            or intent_error
            or doc.validation.status == "clarification_required"
            or doc.validation.status == "rejected"
            and not redundant_check
            or intent.type in {"recall", "out_of_character", "unknown", "wait"}
            or recalling(facts.raw_text)
            or cycle.state.get("requires_clarification")
            or (cycle.state.get("review_result") or {}).get("status") == "rejected"
        ):
            return
        visible = facts.visible_entity_ids & facts.local_entity_ids
        matches = [
            eid
            for eid in visible
            if facts.approved_entities[eid].get("title")
            and facts.approved_entities[eid]["title"] in facts.raw_text
        ]
        target = facts.trusted_target_id or (matches[0] if len(matches) == 1 else None)
        ambiguous_target = not facts.trusted_target_id and len(matches) > 1
        sources = []
        for candidate in [target] if target else matches if ambiguous_target else []:
            if not (
                candidate in visible
                and (intent.target_id == candidate or ambiguous_target)
                and intent.type in {"observe", "investigate", "interact", "use_item"}
            ):
                continue
            # A failed/pending required check cannot establish that the action took effect.
            check_id = cycle.state.get("ordinary_check_id")
            check = await session.get(CheckRecord, check_id) if check_id else None
            checks_ok = not check or (
                check.status == "resolved" and check.document["result"]["passed"]
            )
            if checks_ok and not facts.reveal_errors.get(candidate):
                sources.append((candidate, "action_target", cycle.state["triggering_event_seq"]))
        for event in await session.scalars(
            select(RoomEvent)
            .where(RoomEvent.room_id == room.id, RoomEvent.type == "entity.revealed")
            .order_by(RoomEvent.seq)
        ):
            if event.payload.get("cycle_id") == cycle.id:
                eid = event.payload.get("entity_id", event.payload.get("id"))
                if eid in facts.local_entity_ids and not facts.reveal_errors.get(eid):
                    sources.append((eid, "entity_revealed", event.seq))
        queue = []
        for eid, trigger, seq in sources:
            effects = [
                SanityEffect.model_validate(e)
                for e in facts.approved_entities[eid].get("sanity_effects", [])
                if e.get("trigger", "action_target") == trigger
                and (
                    trigger != "action_target"
                    or intent.type in e.get("action_types", ["observe", "investigate", "interact"])
                )
            ]
            for effect in effects:
                if trigger == "action_target" and intent.type not in effect.action_types:
                    continue
                prior = await self.previous(session, room, eid, effect.id, facts.actor_member_id)
                # A fresh action does not create a new encounter. Repeats require a
                # separately confirmed host encounter, even under host_confirmed policy.
                if prior:
                    continue
                automatic = (
                    effect.automation == "automatic"
                    and not effect.condition
                    and len(effects) == 1
                    and not (ambiguous_target and trigger == "action_target")
                )
                queue.append(
                    {
                        "entity_id": eid,
                        "effect_id": effect.id,
                        "source_event_seq": seq,
                        "target_member_ids": [facts.actor_member_id],
                        "slot_ids": [facts.actor_slot_id],
                        "trigger": trigger,
                        "status": "approved" if automatic else "review",
                        "origin": "automatic" if automatic else "host_review",
                        "condition": effect.condition
                        or (
                            "自然语言目标不唯一，请主机确认实际遭遇"
                            if ambiguous_target and trigger == "action_target"
                            else ""
                        ),
                        "repeat_confirmed": False,
                    }
                )
        cycle.state = {**cycle.state, "encounter_queue": queue}
        if queue:
            self.rooms.append(
                session,
                room,
                "sanity.encounters_discovered",
                room.host_member_id,
                {"cycle_id": cycle.id, "queue": queue},
                "host_only",
            )

    async def review(self, session, room, cycle, body):
        require(body.reason.strip(), "请说明实际遭遇的核实依据", 422)
        require(
            cycle and cycle.state.get("wait_reason") == "sanity_encounter_review",
            "没有等待主机确认的遭遇",
        )
        queue = [dict(e) for e in cycle.state.get("encounter_queue", [])]
        item = next((e for e in queue if e["status"] == "review"), None)
        require(
            item
            and all(
                item[k] == getattr(body, k) for k in ("source_event_seq", "entity_id", "effect_id")
            ),
            "遭遇阶段已变化",
        )
        if body.approve:
            require(body.target_member_ids, "请确认实际遭遇者", 422)
            slots = [
                await self.sanity.slot(session, room, member_id=m) for m in body.target_member_ids
            ]
            require(len(set(body.target_member_ids)) == len(slots), "遭遇者不能重复", 422)
            item.update(
                target_member_ids=[str(m) for m in body.target_member_ids],
                slot_ids=[s.id for s in slots],
            )
        item.update(
            status="approved" if body.approve else "rejected",
            reason=body.reason,
            repeat_confirmed=body.repeat_confirmed,
            origin="host_review",
        )
        if body.approve:
            for other in queue:
                if (
                    other is not item
                    and other["status"] == "review"
                    and (other["source_event_seq"], other["entity_id"])
                    == (item["source_event_seq"], item["entity_id"])
                ):
                    other.update(status="rejected", reason="主机已选定同一遭遇的其他效果")
        cycle.state = {**cycle.state, "encounter_queue": queue}
        self.rooms.append(
            session,
            room,
            "sanity.encounter_reviewed",
            room.host_member_id,
            {"cycle_id": cycle.id, **item},
            "host_only",
        )

    async def propose(self, session, room, args, run):
        # Model entry records a proposal only. The post-action discovery stage is
        # authoritative and shares the eventual effect/member encounter receipt.
        from app.persistence.agent_models import AgentCycle

        cycle = await session.get(AgentCycle, run.cycle_id)
        require(
            cycle.state.get("request_category") == "investigation"
            and str(args.target_member_id) == cycle.state["triggering_member_id"]
            and args.source_event_seq == cycle.state["triggering_event_seq"],
            "模型 SAN 提案不属于本次行动者的遭遇",
            403,
        )
        self.rooms.append(
            session,
            room,
            "sanity.proposed",
            room.host_member_id,
            {**args.model_dump(mode="json"), "cycle_id": cycle.id},
            "host_only",
        )
        return {"status": "queued_for_encounter_validation"}
