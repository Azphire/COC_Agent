"""Source-limited NPC answers and actual ownership of teammate actions."""

from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
from app.persistence.agent_models import AgentCycle, AgentRun
from app.preparation.action_authority import teammate_request


async def prepare_dialogue(runtime, state, run_id):
    service = runtime.service

    async def operation(session, room):
        run = await session.get(AgentRun, run_id)
        plan = KeeperPlan.model_validate(run.structured_output)
        cycle = await session.get(AgentCycle, state["cycle_id"])
        facts = await service.adjudication.facts(session, room, cycle, run)
        if not facts.actor_authorized:
            return
        members = {
            m.id: m.display_name
            for m in await service.rooms.members(session, room)
            if m.active and m.role == "player"
        }
        request = teammate_request(facts.raw_text, members, facts.actor_member_id)
        if request:
            plan.focus = TurnFocus(
                question=facts.raw_text, addressee_id=request, answer_basis="teammate"
            )
            plan.parsed_intent.type = "converse"
            plan.parsed_intent.target_id = request
            plan.addressed_member_id = request
            plan.proposed_check = None
            plan.proposed_tool_calls = []
            plan.proposed_transition_id = None
            plan.proposed_reveal_entity_ids = []
        # Only a specifically addressed NPC and topic can disclose this clue.
        for eid, entity in facts.approved_entities.items():
            if entity.get("type") != "npc" or eid not in facts.local_entity_ids:
                continue
            focus = plan.focus
            if not focus or not (focus.question or plan.parsed_intent.type == "converse"):
                continue
            if focus.addressee_id != eid and entity["title"] not in facts.raw_text:
                continue
            for topic in entity.get("dialogue_topics", []):
                if not any(word in facts.raw_text for word in topic.get("keywords", [])):
                    continue
                if not set(topic.get("required_entity_ids", [])) <= facts.revealed_entity_ids:
                    continue
                plan.focus.addressee_id = eid
                plan.focus.question = facts.raw_text
                plan.focus.answer_basis = "facts"
                # The NPC is publicly described in the current scene, and the
                # topic's own source-approved disclosure gates remain in force.
                if eid in facts.visible_entity_ids or eid in facts.revealed_entity_ids:
                    for revealed in [eid, *topic.get("reveal_entity_ids", [])]:
                        await service.entities.reveal(
                            session,
                            room,
                            revealed,
                            room.host_member_id,
                            cycle_id=cycle.id,
                        )
                    plan.focus.public_fact_ids = list(
                        dict.fromkeys(
                            [
                                *plan.focus.public_fact_ids,
                                *topic.get("reveal_entity_ids", []),
                            ]
                        )
                    )[:5]
                    if not plan.focus.action:
                        plan.parsed_intent.type = "converse"
                        plan.parsed_intent.target_id = eid
                        plan.proposed_reveal_entity_ids = []
        run.structured_output = plan.model_dump(mode="json")

    await service.mutate(state["room_id"], operation)
