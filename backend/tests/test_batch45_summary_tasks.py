"""Bound summary prose and request-specific settlement through the real service path."""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_agent_runtime import game  # noqa: F401
from test_batch44_segments import chunks, event
from test_rooms import character, lobby, prepare  # noqa: F401

from app.agents.adjudication_schemas import BehaviorState, TurnRequest
from app.agents.conversation import settle_teammate_tasks
from app.memory.segments import active_segments, make_segment, validate_segment
from app.persistence.adjudication_models import AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle


@pytest.mark.parametrize("wrong", [
    "事件#2：密码是 7300-红。",
    "事件#3：钥匙已交给 actor-c。",
    "事件#4：检定失败，交接成功。",
])
def test_summary_prose_is_source_bound_even_when_exact_ledger_is_correct(wrong):
    events = [event(1, "game.started"),
              event(2, "clue.revealed", {"content": "密码是 0073-蓝。"}),
              event(3, "module.interaction", {"text": "钥匙已交给 actor-b。",
                    "operation": "give", "to_member_id": "actor-b", "instance_id": "key-1"}),
              event(4, "check.resolved", {"display_text": "检定成功，但尚未完成交接。",
                    "result": {"passed": True, "total": 17}})]
    doc = make_segment(wrong, events, chunks(events[1:], all_events=events))
    assert wrong not in doc["summary"]["content"]
    assert "0073-蓝" in doc["summary"]["content"]
    assert "actor-b" in doc["summary"]["content"]
    assert doc["summary"]["generated_content"] == wrong
    assert doc["summary"]["fact_references"]
    broken = deepcopy(doc)
    broken["summary"]["content"] = wrong
    with pytest.raises(ValueError, match="Summary"):
        validate_segment(broken, events)
    memory = SimpleNamespace(id="broken", kind="summary_segment", active=True,
                             content=json.dumps(broken))
    assert not active_segments([memory], events)


@pytest.mark.parametrize("difference", ["item", "recipient", "actor", "partial", "exact"])
def test_actual_settlement_binds_request_operands_and_keeps_remainder(client, game, difference):  # noqa: F811
    svc, rid = client.app.state.agent_service, game["room"]["id"]
    cid = str(uuid4())

    async def verify():
        async def seed(session, room):
            member = game["agent"]
            request_event = svc.rooms.append(session, room, "action.submitted", member,
                {"text": "把两把钥匙交给甲。", "cycle_id": cid})
            request = {"key": "keys-to-a", "kind": "delegate", "operations": ["give"],
                       "text": "把两把钥匙交给甲", "source_event_seq": request_event.seq,
                       "addressee_id": member, "target_id": "recipient-a",
                       "item_instance_ids": ["key-1", "key-2"], "quantity": 2}
            other = {**request, "key": "book-to-b", "target_id": "recipient-b",
                     "text": "把书交给乙", "item_instance_ids": ["book-1"], "quantity": 1}
            session.add(AgentCycle(id=cid, room_id=room.id, status="completed", state={
                "request_keys": ["keys-to-a", "book-to-b"], "related_player_cycle_id": cid,
                "triggering_event_seq": request_event.seq, "combat_decision": {"operation": "give"},
            }))
            state = BehaviorState(task_status="proposed", task_cycle_id=cid,
                current_short_term_goal="把钥匙和书分别交给甲乙", pending_requests=[request, other])
            session.add(AgentBehaviorRecord(room_id=room.id, member_id=member,
                                            document=state.model_dump(mode="json")))
            instances = ["book-1"] if difference == "item" else ["key-1"]
            if difference in {"exact", "recipient", "actor"}:
                instances.append("key-2")
            receipt = svc.rooms.append(session, room, "module.interaction", member, {
                "cycle_id": cid, "operation": "give", "passed": True,
                "actor_id": "someone-else" if difference == "actor" else member,
                "target_id": "recipient-b" if difference == "recipient" else "recipient-a",
                "recipient_member_id": (
                    "recipient-b" if difference == "recipient" else "recipient-a"
                ),
                "source_event_seq": request_event.seq,
                "text": "实际交接回执。", "operated_items": [
                    {"id": "book" if i == "book-1" else "key", "instance_id": i,
                     "names": ["书" if i == "book-1" else "钥匙"], "quantity": 1}
                    for i in instances],
            })
            await session.flush()
            await settle_teammate_tasks(svc, session, room)
            row = await session.get(AgentBehaviorRecord, (room.id, member))
            current = row.document
            pending = {r["key"]: r for r in current["pending_requests"]}
            assert "book-to-b" in pending
            assert current["task_status"] != "completed"
            assert current["current_short_term_goal"]
            if difference == "exact":
                assert "keys-to-a" not in pending
                assert current["request_history"][0]["status"] == "completed"
            else:
                assert "keys-to-a" in pending
                assert pending["keys-to-a"]["operations"] == ["give"]
                assert not current["request_history"]
                if difference == "partial":
                    assert pending["keys-to-a"]["remaining_quantity"] == 1
                    assert pending["keys-to-a"]["remaining_item_instance_ids"] == ["key-2"]
                    assert pending["keys-to-a"]["completion_event_seqs"] == [receipt.seq]
            before = deepcopy(current)
            await settle_teammate_tasks(svc, session, room)
            assert row.document == before

        await svc.mutate(rid, seed)

    client.portal.call(verify)


@pytest.mark.parametrize("text,target", [
    ("把书交给甲", "recipient-a"), ("把钥匙交给乙", "recipient-b"),
    ("把两把钥匙交给甲", "recipient-a"),
])
def test_distinct_handover_requests_do_not_supersede_each_other(text, target):
    from app.preparation.turn_focus import reconcile_requests

    first = {"key": "old", "kind": "delegate", "text": "把钥匙交给甲",
             "operations": ["give"], "target_id": "recipient-a", "scene_id": "hall"}
    state = BehaviorState(pending_requests=[first])
    next_request = TurnRequest(kind="delegate", addressee_id="alice", text=text,
                              operations=["give"], target_id=target)
    reconcile_requests(state, [next_request], seq=9, scene_id="hall",
                       reachable_ids={"recipient-a", "recipient-b"})
    assert [r["key"] for r in state.pending_requests] == ["old", "9:0"]
    assert not state.request_history


@pytest.mark.parametrize("first_target", ["door-a", None])
def test_actual_observation_receipt_only_completes_its_bound_target(client, game, first_target):  # noqa: F811
    svc, rid, cid = client.app.state.agent_service, game["room"]["id"], str(uuid4())

    async def verify():
        async def seed(session, room):
            member = game["agent"]
            source = svc.rooms.append(session, room, "action.submitted", member,
                                      {"text": "检查前门和后门", "cycle_id": cid})
            requests = [{"key": name, "kind": "delegate", "operations": ["observe"],
                         "text": "检查前门" if name == "a" else "检查后门",
                         "source_event_seq": source.seq, "target_id": target}
                        for name, target in [("a", first_target), ("b", None)]]
            session.add(AgentCycle(id=cid, room_id=rid, status="completed", state={
                "request_keys": ["a", "b"], "related_player_cycle_id": cid,
                "triggering_event_seq": source.seq,
                "request_operands": {"b": {"target_id": "door-b"}},
            }))
            session.add(AgentBehaviorRecord(room_id=rid, member_id=member, document=BehaviorState(
                pending_requests=requests, task_status="proposed", task_cycle_id=cid,
                current_short_term_goal="检查两扇门",
            ).model_dump(mode="json")))
            svc.rooms.append(session, room, "module.interaction", member, {
                "operation": "observe", "passed": True, "target_id": "door-b",
                "text": "后门观察完毕", "cycle_id": cid,
            })
            await session.flush()
            await settle_teammate_tasks(svc, session, room)
            state = (await session.get(AgentBehaviorRecord, (rid, member))).document
            assert [r["key"] for r in state["pending_requests"]] == ["a"]
            assert state["request_history"][0]["key"] == "b"
            assert state["request_history"][0]["status"] == "completed"
            assert state["task_status"] != "completed"

        await svc.mutate(rid, seed)

    client.portal.call(verify)


def test_compound_item_operations_track_quantities_independently():
    from app.agents.task_receipts import receipt_progress

    request = {"operations": ["give", "drop"], "text": "交两把钥匙，再放下两把钥匙",
               "quantity": 2, "item_ids": ["key"],
               "operation_operands": {"give": {"target_id": "bob"}}}
    facts = [{"operation": op, "status": "success", "actor_id": "alice",
              "cycle_id": "cycle", "source_event_seq": seq,
              "recipient_member_id": "bob" if op == "give" else None,
              "operated_items": [{"id": "key", "instance_id": f"key-{seq}", "quantity": 1}]}
             for seq, op in enumerate(["give", "drop"], 1)]
    remaining, done = receipt_progress(request, facts, actor="alice", cycle_id="cycle",
                                       consumed=set())
    assert not done and remaining["operations"] == ["give", "drop"]
    assert {op: p["remaining_quantity"] for op, p in remaining["operation_progress"].items()} == {
        "give": 1, "drop": 1,
    }
    replay = [{**facts[0], "source_event_seq": 3, "cycle_id": "next"}]
    after, done = receipt_progress(remaining, replay, actor="alice", cycle_id="next",
                                   consumed=set())
    assert not done and after["operation_progress"]["give"]["remaining_quantity"] == 1


def test_observation_operand_binding_uses_unique_visible_name_without_guessing():
    from app.agents.task_receipts import bind_task_operands

    request = {"text": "检查前门", "operations": ["observe"]}
    targets = [{"id": "front", "title": "前门"}, {"id": "back", "title": "后门"}]
    assert bind_task_operands(request, {}, "alice", targets=targets)["target_id"] == "front"
    ambiguous = [*targets, {"id": "other-front", "title": "前门"}]
    assert not bind_task_operands(request, {}, "alice", targets=ambiguous).get("target_id")
