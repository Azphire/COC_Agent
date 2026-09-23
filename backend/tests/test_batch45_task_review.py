"""Follow-up controls exercise normal operand binding and rewind settlement."""

from uuid import uuid4

from test_agent_runtime import game  # noqa: F401
from test_rooms import character, lobby, prepare  # noqa: F401

from app.agents.adjudication_schemas import BehaviorState
from app.agents.conversation import settle_teammate_tasks
from app.agents.task_receipts import bind_task_operands, receipt_progress, requested_quantity
from app.persistence.adjudication_models import AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle


def inventory():
    return {"known_items": [{"id": "key", "names": ["钥匙"]},
                            {"id": "book", "names": ["书"]}],
            "members": [{"id": "bob", "name": "甲"}], "holders": []}


def receipt(operation, item, seq=1, cycle="cycle"):
    return {"operation": operation, "status": "success", "actor_id": "alice",
            "cycle_id": cycle, "source_event_seq": seq, "recipient_member_id": "bob",
            "operated_items": [{"id": item, "instance_id": f"{item}-{seq}", "quantity": 1}]}


def test_chinese_compound_quantity_is_not_truncated_to_last_digit():
    assert requested_quantity("把十二把钥匙交给甲") == 12
    assert requested_quantity("把二十一把钥匙交给甲") == 21


def test_normal_binding_requires_each_named_item_type():
    bound = bind_task_operands({"text": "把钥匙和书交给甲", "operations": ["give"]},
                               inventory(), "alice")
    remaining, done = receipt_progress(bound, [receipt("give", "key")], actor="alice",
                                       cycle_id="cycle", consumed=set())
    assert not done and remaining["operations"] == ["give"]
    # A second key still cannot stand in for the separately requested book.
    again, done = receipt_progress(remaining, [receipt("give", "key", 2, "next")], actor="alice",
                                   cycle_id="next", consumed=set())
    assert not done
    _, done = receipt_progress(again, [receipt("give", "book", 3, "last")], actor="alice",
                               cycle_id="last", consumed=set())
    assert done


def test_normal_compound_binding_keeps_each_operation_item_and_quantity():
    bound = bind_task_operands({"text": "把两把钥匙交给甲，再放下三本书",
                               "operations": ["give", "place"]}, inventory(), "alice")
    assert bound["operation_operands"]["give"]["required_items"] == {"key": 2}
    assert bound["operation_operands"]["place"]["required_items"] == {"book": 3}
    facts = [receipt("give", "key", 1), receipt("place", "book", 2)]
    remaining, done = receipt_progress(bound, facts, actor="alice", cycle_id="cycle",
                                       consumed=set())
    assert not done
    assert remaining["operation_progress"]["give"]["remaining_quantity"] == 1
    assert remaining["operation_progress"]["place"]["remaining_quantity"] == 2


def test_aliases_share_one_item_occurrence_but_ambiguous_entities_stay_pending():
    view = {**inventory(), "known_items": [{"id": "key", "names": ["铜钥匙", "钥匙"]}]}
    request = {"text": "把铜钥匙交给甲", "operations": ["give"]}
    bound = bind_task_operands(request, view, "alice")
    assert not bound.get("unresolved_operands") and bound["required_items"] == {"key": 1}
    view["known_items"].append({"id": "other-key", "names": ["铜钥匙"]})
    bound = bind_task_operands(request, view, "alice")
    assert bound["unresolved_operands"]
    _, done = receipt_progress(bound, [receipt("give", "key")], actor="alice",
                               cycle_id="cycle", consumed=set())
    assert not done


def test_legacy_missing_operands_cannot_infer_entire_request_from_partial_receipt():
    request = {"text": "把钥匙和书交给甲", "operations": ["give"], "target_id": "bob"}
    fact = receipt("give", "key")
    fact["operated_items"][0]["names"] = ["钥匙"]
    _, done = receipt_progress(request, [fact], actor="alice", cycle_id="cycle", consumed=set())
    assert not done


def test_settlement_ignores_success_receipt_from_abandoned_same_cycle(client, game):  # noqa: F811
    svc, rid, cid = client.app.state.agent_service, game["room"]["id"], str(uuid4())

    async def verify():
        async def seed(session, room):
            member = game["agent"]
            source = svc.rooms.append(session, room, "action.submitted", member,
                                      {"text": "交给甲钥匙", "cycle_id": cid})
            session.add(AgentCycle(id=cid, room_id=rid, status="completed", state={
                "request_keys": ["give"], "related_player_cycle_id": cid,
                "triggering_event_seq": source.seq,
            }))
            session.add(AgentBehaviorRecord(room_id=rid, member_id=member, document=BehaviorState(
                task_status="proposed", task_cycle_id=cid, current_short_term_goal="交出钥匙",
                pending_requests=[{"key": "give", "kind": "delegate", "operations": ["give"],
                                   "target_id": "bob", "item_instance_ids": ["key-1"],
                                   "source_event_seq": source.seq}],
            ).model_dump(mode="json")))
            svc.rooms.append(session, room, "module.interaction", member, {
                "cycle_id": cid, "operation": "give", "passed": True,
                "recipient_member_id": "bob", "operated_items": [{"id": "key-1"}],
                "text": "旧分支已经交接",
            })
            svc.rooms.append(session, room, "snapshot.loaded", room.host_member_id,
                             {"source_seq": source.seq})
            svc.rooms.append(session, room, "check.resolved", member, {
                "cycle_id": cid, "result": {"passed": False}, "display_text": "新分支检定失败",
            })
            await session.flush()
            await settle_teammate_tasks(svc, session, room)
            state = (await session.get(AgentBehaviorRecord, (rid, member))).document
            assert state["pending_requests"] and not state["request_history"]
            assert state["task_status"] != "completed"

        await svc.mutate(rid, seed)

    client.portal.call(verify)
