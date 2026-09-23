"""The real prepare/context transaction retains scoped evidence and failure audit."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.memory.service import MemoryBudgetError, prompt_context_size
from app.persistence.adjudication_models import AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle
from app.persistence.room_models import RoomEvent


@pytest.mark.parametrize("instances, fits", [(280, True), (2000, False)])
def test_prepare_context_allocates_total_budget_and_persists_actual_overflow(
    client, game, instances, fits,  # noqa: F811
):
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    service = client.app.state.agent_service
    room_id = game["room"]["id"]

    async def verify():
        async def setup(session, room):
            original = await service.cycle(session, room.id)
            binding = next(b for b in await service.bindings(session, room.id)
                           if b.member_id == room.host_member_id)
            cycle_id = str(uuid4())
            trigger = service.rooms.append(session, room, "action.submitted", room.host_member_id,
                                           {"text": "继续交接窗锁钥匙", "cycle_id": cycle_id})
            current = {"key": f"{trigger.seq}:0", "source_event_seq": trigger.seq,
                       "kind": "delegate", "text": "继续交接窗锁钥匙", "operations": ["give"],
                       "executor_member_id": binding.member_id, "target_id": "window-key",
                       "remaining_quantity": instances,
                       "remaining_item_instance_ids": [f"key-{i:04}" for i in range(instances)]}
            old_event = service.rooms.append(session, room, "action.submitted", room.host_member_id,
                                             {"text": "无关旧档案"})
            old = [{"key": f"{old_event.seq}:{i}", "source_event_seq": old_event.seq,
                    "kind": "delegate", "text": "无关旧档案密码" * 60,
                    "target_id": f"archive-{i}"} for i in range(60)]
            behavior = {"pending_requests": [*old, current],
                        "current_short_term_goal": "无关旧目标" * 100}
            row = await session.get(AgentBehaviorRecord, (room.id, binding.member_id))
            if row:
                row.document = behavior
            else:
                session.add(AgentBehaviorRecord(room_id=room.id, member_id=binding.member_id,
                                                document=behavior))
            state = {**original.state, "cycle_id": cycle_id, "triggering_event_seq": trigger.seq,
                     "current_node": "decide_teammates"}
            session.add(AgentCycle(id=cycle_id, room_id=room.id, status="running", state=state))
            return deepcopy(state), binding.id, deepcopy(current), deepcopy(behavior)

        state, binding_id, current, behavior = await service.mutate(room_id, setup)
        additions = {"addressed_requests": [current],
                     "continued_requests": behavior["pending_requests"][:-1],
                     "behavior_state": behavior}
        if fits:
            _, _, context, _ = await service.runtime.prepare_run(
                state, binding_id, "decide_teammates", context_additions=additions,
            )
            audit = context["memory_selection_audit"]
            assert audit["required_complete"] and audit["required_chars"] > 3000
            assert len(audit["required_records"]) == 1
            assert sum(t["required"] for t in audit["task_index"]) == 1
            assert prompt_context_size(context) <= service.settings.agent_context_chars
        else:
            with pytest.raises(MemoryBudgetError) as failure:
                await service.runtime.prepare_run(
                    state, binding_id, "decide_teammates", context_additions=additions,
                )
            assert "无关旧档案" not in failure.value.message
            async with service.rooms.database.sessions() as session:
                saved = await session.get(AgentCycle, state["cycle_id"])
                audit = saved.state["memory_budget_failure"]
                assert audit["context_budget"] == service.settings.agent_context_chars
                assert audit["required_omitted"][0]["ref"] == f"e{current['source_event_seq']}"
                assert audit["required_omitted"][0]["chars"] > audit["context_budget"]
                events = list(await session.scalars(select(RoomEvent).where(
                    RoomEvent.room_id == room_id, RoomEvent.type == "memory.context_blocked")))
                assert len(events) == 1 and events[0].visibility == "host_only"
                assert events[0].payload["required_omitted"] == audit["required_omitted"]
                ledger = await session.get(AgentBehaviorRecord,
                                           (room_id, current["executor_member_id"]))
                assert ledger.document == behavior

    client.portal.call(verify)
