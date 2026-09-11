"""Load authoritative facts and persist bounded adjudication/recovery audits."""

from sqlalchemy import select

from app.agents.action_policy import ActionFacts, ActionPolicyValidator
from app.agents.adjudication_schemas import AdjudicationRecord, RecoveryDecision, SupplementContent
from app.agents.modules import Module
from app.agents.schemas import CheckRequest, PlannedTool
from app.persistence.adjudication_models import ActionPlanRecord
from app.persistence.agent_models import AgentCycle, AgentRun, CheckRecord
from app.persistence.room_models import RoomEvent
from app.rooms.service import RoomError, require


class ContextSupplementService:
    def __init__(self, agents):
        self.agents = agents

    async def supplement(self, session, room, run, record, gaps):
        document = AdjudicationRecord.model_validate(record.document)
        require(not document.supplement_attempted, "context_missing：本计划已补读一次")
        document.supplement_attempted = True
        document.context_gaps = gaps
        result = {"nodes": [], "entities": [], "transitions": []}
        if await self.agents.navigation.state(session, room.id):
            state, snapshot, ir, local, linked, _ = await self.agents.module_context.allowed(
                session, room
            )
            # Linked metadata is approved, but another scene's body is never supplemented.
            allowed = set(local) | {
                n.node_id
                for n in snapshot.nodes
                if n.node_id in linked and n.approved_type != "scene"
            }
            allowed_entities = {
                b.entity_id for b in snapshot.entity_bindings if b.node_id in allowed
            }
            for gap in gaps:
                gap.allowed_supplement_scope = sorted(allowed)
                if gap.missing_kind == "node" and gap.requested_target in allowed:
                    result["nodes"].append(
                        {
                            "node_id": gap.requested_target,
                            "blocks": [
                                {"block_id": b.block_id, "text": b.text[:420]}
                                for b in ir.blocks
                                if b.node_id == gap.requested_target
                            ][:3],
                        }
                    )
                elif (
                    gap.missing_kind in {"entity", "npc"}
                    and gap.requested_target in allowed_entities
                ):
                    entity = await self.agents.entities.entity(
                        session, room.id, gap.requested_target
                    )
                    result["entities"].append(
                        {
                            "id": entity.source_entity_id,
                            "type": entity.entity_type,
                            "title": entity.snapshot["title"],
                            "public_summary": entity.frozen_public_summary[:400],
                            "keeper_summary": entity.snapshot.get("keeper_summary", "")[:400],
                            "reveal_conditions": entity.snapshot["reveal_conditions"],
                        }
                    )
                elif gap.missing_kind == "transition":
                    result["transitions"].extend(
                        t.model_dump()
                        for t in snapshot.transitions
                        if t.approved
                        and t.source_scene_node_id == state.current_scene_node_id
                        and t.transition_id == gap.requested_target
                    )
        document.supplement = SupplementContent.model_validate(result)
        document.recoveries.append(
            RecoveryDecision(
                error="context_missing", action="supplement", succeeded=any(result.values())
            )
        )
        record.document = document.model_dump(mode="json")
        run.context = {
            **run.context,
            "context_supplement": document.supplement.model_dump(mode="json"),
        }
        self.agents.rooms.append(
            session,
            room,
            "agent.context_supplemented",
            room.host_member_id,
            {
                "cycle_id": run.cycle_id,
                "gaps": [g.model_dump() for g in gaps],
                "scope": "current_scene",
                "succeeded": any(result.values()),
            },
            "host_only",
        )
        return result


class ActionAdjudicationService:
    def __init__(self, agents):
        self.agents = agents
        self.validator = ActionPolicyValidator()
        self.supplements = ContextSupplementService(agents)

    async def facts(self, session, room, cycle, run):
        agents = self.agents
        trigger = await session.get(RoomEvent, (room.id, cycle.state["triggering_event_seq"]))
        members = await agents.rooms.members(session, room)
        actor = next((m for m in members if m.id == cycle.state["triggering_member_id"]), None)
        slots = await agents.rooms.slots(session, room)
        slot = next((s for s in slots if s.member_id == cycle.state["triggering_member_id"]), None)
        module = await agents.module(session, room.id)
        facts = ActionFacts(
            room_id=room.id,
            cycle_id=cycle.id,
            raw_text=trigger.payload["text"],
            actor_member_id=cycle.state["triggering_member_id"],
            actor_slot_id=slot.id if slot else "",
            actor_authorized=bool(
                actor
                and actor.active
                and actor.role == "player"
                and (actor.controller_type == "human" or cycle.state.get("origin") == "teammate")
                and slot
            ),
            scene_id=module.state["scene_id"],
            member_ids={m.id for m in members if m.active},
            can_move_party=cycle.state.get("origin") != "teammate",
            characters={
                s.member_id: s.character_snapshot
                for s in slots
                if s.member_id and any(m.id == s.member_id and m.active for m in members)
            },
            trusted_target_id=trigger.payload.get("target_entity_id"),
            host_review_approved=(cycle.state.get("review_result") or {}).get("status")
            in {"approved", "edited"},
        )
        prepared = await agents.entities.binding(session, room.id)
        if prepared:
            rows = await agents.entities.rows(session, room.id)
            facts.approved_entities = {
                e.source_entity_id: {**e.snapshot, "id": e.source_entity_id}
                for e in rows
                if e.snapshot.get("status") == "approved"
            }
            facts.visible_entity_ids = {e.source_entity_id for e in rows if e.state != "hidden"}
            facts.revealed_entity_ids = set(facts.visible_entity_ids)
            facts.local_entity_ids = set(facts.approved_entities)
            for entity in rows:
                for for_check, errors in (
                    (False, facts.reveal_errors),
                    (True, facts.reveal_check_errors),
                ):
                    try:
                        await agents.entities.check_conditions(
                            session, room, entity, cycle.id, for_check=for_check
                        )
                    except RoomError as error:
                        errors[entity.source_entity_id] = error.message
        else:
            definition = Module.model_validate(module.document)
            for scene in definition.scenes:
                facts.approved_entities[scene.id] = {
                    "id": scene.id,
                    "type": "scene",
                    "title": scene.title,
                    "public_summary": scene.public_description,
                }
                if scene.id != facts.scene_id:
                    tid = "legacy:" + scene.id
                    facts.transitions[tid] = {
                        "transition_id": tid,
                        "source_scene_node_id": facts.scene_id,
                        "target_scene_node_id": scene.id,
                        "approved": True,
                    }
                    facts.available_transition_ids.add(tid)
            facts.local_entity_ids.add(facts.scene_id)
            facts.visible_entity_ids.add(facts.scene_id)
            for npc in definition.npcs:
                facts.approved_entities[npc.id] = {
                    "id": npc.id,
                    "type": "npc",
                    "title": npc.name,
                    "public_summary": npc.public_description,
                }
                facts.local_entity_ids.add(npc.id)
                facts.visible_entity_ids.add(npc.id)
            checks = list(
                await session.scalars(
                    select(CheckRecord).where(
                        CheckRecord.cycle_id == cycle.id, CheckRecord.status == "resolved"
                    )
                )
            )
            for clue in definition.clues:
                if clue.visibility == "keeper_only":
                    continue
                facts.approved_entities[clue.id] = {
                    "id": clue.id,
                    "type": "clue",
                    "title": clue.title,
                    "public_summary": clue.content,
                    "reveal_conditions": {
                        "scene_id": clue.prerequisites.scene_id,
                        "required_entity_ids": clue.prerequisites.clue_ids,
                        "successful_check": clue.prerequisites.successful_check.model_dump()
                        if clue.prerequisites.successful_check
                        else None,
                    },
                }
                pre = clue.prerequisites
                if not pre.scene_id or pre.scene_id == facts.scene_id:
                    facts.local_entity_ids.add(clue.id)
                if clue.id in module.state["revealed_clues"]:
                    facts.visible_entity_ids.add(clue.id)
                    facts.revealed_entity_ids.add(clue.id)
                if clue.id not in facts.local_entity_ids or not set(pre.clue_ids) <= set(
                    module.state["revealed_clues"]
                ):
                    facts.reveal_errors[clue.id] = facts.reveal_check_errors[clue.id] = (
                        "线索场景或前置实体不满足"
                    )
                elif pre.successful_check and not any(
                    c.document.get("clue_id") == clue.id and c.document["result"]["passed"]
                    for c in checks
                ):
                    facts.reveal_errors[clue.id] = "必须通过关联的真实检定"
        nav = await agents.navigation.state(session, room.id)
        if nav:
            state, snapshot, _, local, linked, _ = await agents.module_context.allowed(
                session, room
            )
            facts.structure = True
            facts.scene_id = state.current_scene_node_id
            facts.navigation_revision = state.navigation_revision
            facts.node_ids = {n.node_id for n in snapshot.nodes}
            facts.allowed_node_ids = set(local) | {
                n.node_id
                for n in snapshot.nodes
                if n.node_id in linked and n.approved_type != "scene"
            }
            facts.local_entity_ids = {
                b.entity_id for b in snapshot.entity_bindings if b.node_id in local
            }
            facts.visible_entity_ids &= facts.local_entity_ids
            facts.transitions = {
                t.transition_id: {
                    **t.model_dump(),
                    "target_entity_id": next(
                        (
                            b.entity_id
                            for b in snapshot.entity_bindings
                            if b.node_id == t.target_scene_node_id
                            and facts.approved_entities.get(b.entity_id, {}).get("type") == "scene"
                        ),
                        None,
                    ),
                }
                for t in snapshot.transitions
                if t.approved and t.source_scene_node_id == facts.scene_id
            }
            await agents.navigation.refresh(session, room, state, snapshot)
            facts.available_transition_ids = set(state.available_transition_ids)
        # An object named in the public scene can be addressed before revealing its content.
        public_scene_text = room.session_state.get("scene_summary", "")
        facts.visible_entity_ids.update(
            eid
            for eid, e in facts.approved_entities.items()
            if eid in facts.local_entity_ids and e.get("title") and e["title"] in public_scene_text
        )
        supplied = run.context.get("module", {})
        supplement = run.context.get("context_supplement", {})
        facts.observed_entity_ids = {
            e["id"] for e in supplied.get("approved_entities", []) + supplement.get("entities", [])
        }
        if not prepared:
            facts.observed_entity_ids = set(facts.approved_entities)
        facts.observed_node_ids = set(
            run.context.get("module_context_audit", {}).get("selected_node_ids", [])
        ) | {n["node_id"] for n in supplement.get("nodes", [])}
        facts.observed_evidence_ids = set(
            await agents.knowledge.evidence_for_run(session, run.id, room.id, run.profile_id)
        )
        facts.completed_checks = [
            {**c.document, "cycle_id": c.cycle_id}
            for c in await session.scalars(
                select(CheckRecord).where(
                    CheckRecord.room_id == room.id, CheckRecord.status == "resolved"
                )
            )
        ]
        facts.check_state = {
            "module_state": module.state,
            "entities": {
                e["id"]: e.get("public_summary", "")
                for e in await agents.entities.public(session, room.id)
            },
        }
        return facts

    @staticmethod
    def actions(plan, facts):
        from app.agents.check_policy import entity_access

        proposal = plan.proposed_check
        if proposal and proposal.clue_id is None:
            target = proposal.target_entity_id
            if (
                target
                and target not in facts.revealed_entity_ids
                and proposal.basis_entity_id == target
                and entity_access(facts.approved_entities.get(target, {})) == "requires_check"
            ):
                # The approved entity is authoritative; this is an interface alias,
                # never an inferred check requirement or invented target.
                proposal.clue_id = target
        actions = list(plan.proposed_tool_calls)
        if plan.proposed_check and not any(t.name == "request_skill_check" for t in actions):
            actions.append(
                PlannedTool(
                    name="request_skill_check",
                    arguments={
                        k: v
                        for k, v in plan.proposed_check.model_dump(mode="json").items()
                        if k in CheckRequest.model_fields
                    },
                )
            )
        reveals = list(plan.proposed_reveal_entity_ids)
        if plan.proposed_check and plan.proposed_check.clue_id:
            eid = plan.proposed_check.clue_id
            if entity_access(facts.approved_entities.get(eid, {})) == "requires_check":
                reveals = list(dict.fromkeys([*reveals, eid]))
        for eid in reveals:
            if not any(
                t.name in {"reveal_entity", "reveal_clue"} and eid in t.arguments.values()
                for t in actions
            ):
                actions.append(
                    PlannedTool(
                        name="reveal_entity" if facts.structure else "reveal_clue",
                        arguments={"entity_id" if facts.structure else "clue_id": eid},
                    )
                )
        if plan.proposed_transition_id and not any(
            t.name in {"transition_scene", "update_scene"} for t in actions
        ):
            t = facts.transitions.get(plan.proposed_transition_id)
            if t:
                actions.append(
                    PlannedTool(
                        name="transition_scene" if facts.structure else "update_scene",
                        arguments={
                            "target_scene_node_id": t["target_scene_node_id"],
                            "expected_revision": plan.expected_navigation_revision,
                            "request_id": plan.plan_id + ":move",
                        }
                        if facts.structure
                        else {"scene_id": t["target_scene_node_id"]},
                    )
                )
        return actions

    async def validate(self, session, room, cycle, *, after_check=False):
        record = await session.get(ActionPlanRecord, cycle.id)
        doc = AdjudicationRecord.model_validate(record.document)
        run = await session.get(AgentRun, record.run_id)
        facts = await self.facts(session, room, cycle, run)
        actions = self.actions(doc.plan, facts)
        doc.validation = self.validator.validate(
            doc.plan.parsed_intent, doc.plan, facts, actions, after_check=after_check
        )
        record.document = doc.model_dump(mode="json")
        return record, doc, facts, actions

    async def guard_transition(self, session, room, run, target):
        require(run is not None, "permission_denied：转场需要行动计划", 403)
        cycle = await session.get(AgentCycle, run.cycle_id)
        record = await session.get(ActionPlanRecord, cycle.id)
        require(record, "precondition_failed：转场缺少玩家移动计划")
        doc = AdjudicationRecord.model_validate(record.document)
        facts = await self.facts(session, room, cycle, run)
        require(
            self.validator.validate_intent(doc.plan.parsed_intent, doc.plan, facts) is None,
            "precondition_failed：玩家移动意图未通过验证",
        )
        require(doc.plan.parsed_intent.type == "move", "precondition_failed：玩家没有明确移动")
        transition = facts.transitions.get(doc.plan.proposed_transition_id)
        require(
            transition and transition["target_scene_node_id"] == target,
            "precondition_failed：转场目标不匹配玩家计划",
        )

    async def recover_revision(self, session, room, cycle):
        record = await session.get(ActionPlanRecord, cycle.id)
        if not record:
            return False
        doc = AdjudicationRecord.model_validate(record.document)
        if doc.revision_recovery_attempted:
            return False
        doc.revision_recovery_attempted = True
        nav = await self.agents.navigation.require_available(session, room.id)
        # A changed scene invalidates the old intent; only a same-scene revision can be retried.
        safe = nav and nav.current_scene_node_id == doc.plan.current_scene_id
        doc.recoveries.append(
            RecoveryDecision(
                error="revision_conflict",
                action="revalidate" if safe else "stop",
                succeeded=bool(safe),
            )
        )
        if safe:
            doc.plan.expected_navigation_revision = nav.navigation_revision
            for tool in doc.plan.proposed_tool_calls:
                if tool.name == "transition_scene" and "expected_revision" in tool.arguments:
                    tool.arguments = {
                        **tool.arguments,
                        "expected_revision": nav.navigation_revision,
                    }
            cycle.state = {**cycle.state, "navigation_revision": nav.navigation_revision}
        record.document = doc.model_dump(mode="json")
        if safe:
            _, checked, _, _ = await self.validate(session, room, cycle)
            return checked.validation.status not in {"rejected", "clarification_required"}
        return False
