"""Real task settlement must distinguish dice success from operation completion."""

from uuid import uuid4

import pytest
from test_agent_runtime import game  # noqa: F401
from test_rooms import character, lobby, prepare  # noqa: F401

from app.agents.adjudication_schemas import BehaviorState
from app.agents.conversation import settle_teammate_tasks
from app.persistence.adjudication_models import AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle


@pytest.mark.parametrize("executed", [False, True])
def test_successful_check_requires_actual_transfer_receipt_to_finish_task(client, game, executed):  # noqa: F811
    svc, rid = client.app.state.agent_service, game["room"]["id"]
    cid = str(uuid4())

    async def verify():
        async def seed(session, room):
            member = game["agent"]
            request = svc.rooms.append(session, room, "action.submitted", member,
                                       {"text": "把钥匙交给同伴。", "cycle_id": cid})
            session.add(AgentCycle(id=cid, room_id=room.id, status="completed", state={
                "request_keys": ["transfer"], "related_player_cycle_id": cid,
                "triggering_event_seq": request.seq, "combat_decision": {"operation": "give"},
            }))
            state = BehaviorState(task_status="proposed", task_cycle_id=cid,
                                  current_short_term_goal="把钥匙交给同伴", pending_requests=[{
                                      "key": "transfer", "kind": "delegate", "operations": ["give"],
                                      "text": "把钥匙交给同伴", "source_event_seq": request.seq,
                                      "target_id": "companion",
                                      "item_instance_ids": ["key-instance"],
                                  }])
            session.add(AgentBehaviorRecord(room_id=room.id, member_id=member,
                                            document=state.model_dump(mode="json")))
            svc.rooms.append(session, room, "check.resolved", member, {
                "id": "transfer-check", "cycle_id": cid, "result": {"passed": True},
                "display_text": "敏捷检定成功，但尚未交接钥匙。",
            })
            if executed:
                svc.rooms.append(session, room, "module.interaction", member, {
                    "cycle_id": cid, "operation": "give", "passed": True,
                    "recipient_member_id": "companion",
                    "text": "钥匙已交给同伴。", "operated_items": [{"id": "key-instance"}],
                })
            await session.flush()
            await settle_teammate_tasks(svc, session, room)
            row = await session.get(AgentBehaviorRecord, (room.id, member))
            if executed:
                assert row.document["task_status"] == "completed"
                assert not row.document["pending_requests"]
                assert not row.document["current_short_term_goal"]
            else:
                assert row.document["task_status"] == "attempted"
                assert row.document["pending_requests"][0]["operations"] == ["give"]
                assert row.document["current_short_term_goal"] == "把钥匙交给同伴"
                assert not row.document["request_history"]

        await svc.mutate(rid, seed)

    client.portal.call(verify)
