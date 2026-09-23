"""An observation proposal or reused reveal is not this delegated task's result."""

from copy import deepcopy
from uuid import uuid4

import pytest
from test_agent_runtime import game  # noqa: F401
from test_rooms import character, lobby, prepare  # noqa: F401

from app.agents.adjudication_schemas import AdjudicationRecord, BehaviorState, KeeperPlan
from app.agents.conversation import settle_teammate_tasks
from app.persistence.adjudication_models import ActionPlanRecord, AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle, AgentRun


@pytest.mark.parametrize("case", [
    "failed_narration_old_reveal", "validated_current_observation", "old_narration_only",
    "unvalidated_narration", "actual_success_failed_narration", "actual_failure_failed_narration",
])
def test_only_current_validated_observation_or_actual_operation_can_settle(client, game, case):  # noqa: F811
    svc, rid = client.app.state.agent_service, game["room"]["id"]
    parent_id, child_id, run_id = (str(uuid4()) for _ in range(3))

    async def verify():
        async def seed(session, room):
            actor = game["agent"]
            module = await svc.module(session, room.id)
            target = module.state["scene_id"]
            session.add(AgentCycle(id=parent_id, room_id=rid, status="completed", state={}))
            # Same failure shape as real-02: this reveal belongs to the earlier
            # main cycle; the child tool returns its original sequence unchanged.
            old_reveal = svc.rooms.append(session, room, "entity.revealed", room.host_member_id, {
                "cycle_id": parent_id, "entity_id": target, "public_summary": "窗锁早已松动。",
            })
            action = svc.rooms.append(session, room, "agent.action_proposed", actor, {
                "cycle_id": parent_id, "text": "我查看窗锁。", "target_id": target,
            })
            request = {"key": "window-request", "kind": "delegate", "operations": ["observe"],
                       "text": "请查看窗锁。", "target_id": target,
                       "source_event_seq": action.seq}
            state = {"triggering_member_id": actor, "triggering_event_seq": action.seq,
                     "request_keys": [request["key"]], "related_player_cycle_id": parent_id,
                     "request_operands": {request["key"]: request},
                     "teammate_attempt": {"target_id": target, "operations": ["observe"]}}
            child = AgentCycle(id=child_id, room_id=rid, status="completed", state=state)
            session.add(child)
            session.add(AgentBehaviorRecord(room_id=rid, member_id=actor, document=BehaviorState(
                pending_requests=[request], task_status="proposed", task_cycle_id=child_id,
                current_short_term_goal="核对窗锁当前状态",
            ).model_dump(mode="json")))
            plan = KeeperPlan(
                plan_id=child_id, cycle_id=child_id, current_scene_id=target,
                parsed_intent={"type": "observe", "actor_member_id": actor,
                               "actor_character_slot_id": "slot", "target_id": target,
                               "evidence_quote": "我查看窗锁。", "confidence": 1},
                focus={"action": "我查看窗锁。", "action_target_id": target},
                action_authority={"kinds": ["observe"]},
            )
            tool_result = {"ok": True, "tool": "reveal_entity", "data": {
                "entity_id": target, "already_revealed": True, "event_seq": old_reveal.seq,
            }}
            session.add(AgentRun(
                id=run_id, room_id=rid, cycle_id=child_id, profile_id=game["profiles"][0]["id"],
                actor_member_id=room.host_member_id, graph_node="plan_keeper_action",
                status="completed", input_seq_start=action.seq, input_seq_end=action.seq,
                provider="fake", model="test", context={}, tool_results=[tool_result],
            ))
            valid = case in {"validated_current_observation", "old_narration_only"}
            text = "窗锁目前仍然松动。" if valid else "窗锁早已松动。\n本次答复未完整生成。"
            document = AdjudicationRecord(
                plan=plan, narration={"public_narration": text},
                narration_validation={} if case == "unvalidated_narration" else {
                    "valid": valid, "answer_complete": valid,
                },
            ).model_dump(mode="json")
            session.add(ActionPlanRecord(cycle_id=child_id, room_id=rid, run_id=run_id,
                                         document=document))
            narration = svc.rooms.append(session, room, "keeper.narration", room.host_member_id, {
                "cycle_id": parent_id if case == "old_narration_only" else child_id,
                "text": text, "safe_fallback": not valid,
            })
            operation = None
            if case.startswith("actual_"):
                operation = svc.rooms.append(session, room, "module.interaction", actor, {
                    "cycle_id": child_id, "target_id": target, "operation": "observe",
                    "passed": case == "actual_success_failed_narration",
                    "text": "本次观察操作已实际结算。",
                })
            await session.flush()
            # Exercise the real projection: the old bug treated this plan-derived
            # flag as a receipt despite failed generation and an unchanged reveal.
            projected = await svc.runtime.public_results(session, room, child)
            assert projected["observation_completed"]
            await settle_teammate_tasks(svc, session, room)
            row = await session.get(AgentBehaviorRecord, (rid, actor))
            saved = row.document
            should_complete = case in {
                "validated_current_observation", "actual_success_failed_narration",
            }
            assert (saved["task_status"] == "completed") is should_complete
            if should_complete:
                assert not saved["pending_requests"]
                expected_seq = operation.seq if operation else narration.seq
                history = saved["request_history"][0]
                assert expected_seq in history["result_event_seqs"]
                assert {f["source_event_seq"] for f in saved["last_result"]["result_facts"]
                        if f["operation"] == "observe" and f["status"] == "success"} == {
                    expected_seq,
                }
            else:
                assert saved["pending_requests"][0]["target_id"] == target
                assert saved["pending_requests"][0]["key"] == request["key"]
                assert saved["current_short_term_goal"] == "核对窗锁当前状态"
                assert not saved["request_history"]
                assert not any(f["operation"] == "observe" and f["status"] == "success"
                               for f in saved["last_result"]["result_facts"])
            if case == "failed_narration_old_reveal":
                assert saved["task_status"] == "generation_failed"
            if case == "actual_failure_failed_narration":
                assert saved["task_status"] != "generation_failed"
                assert saved["last_attempt_result"]["result_facts"][0]["status"] == "failure"
            before = deepcopy(saved)
            await settle_teammate_tasks(svc, session, room)
            assert row.document == before

        await svc.mutate(rid, seed)

    client.portal.call(verify)
