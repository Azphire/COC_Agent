"""Run new-contract model calls through real publication and task settlement.

The fixture starts after an authorized delegated inspection and public reveal;
it reuses the small stopped-clock room, without character or launch generation.
"""

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_agent_runtime import game  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.adjudication_schemas import AdjudicationRecord, BehaviorState, KeeperPlan
from app.agents.answer_parts import CONTRACT_VERSION
from app.agents.conversation import initial_state
from app.agents.model import FakeModelAdapter
from app.memory.events import story_events
from app.models.base import ModelResponse
from app.models.ollama import generation_schema
from app.persistence.adjudication_models import ActionPlanRecord, AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle, AgentRun
from app.persistence.knowledge_models import AgentModelCall

UNKNOWN = "公告纸张是否有夹层或折叠，目前还不清楚。"
UNKNOWN_ID = "structure"
RESULT_ID = "discovery"
BAD = "公告纸张没有夹层或折叠。"


async def seed_inspection(service, fixture_room, with_check=False):
    """Freeze actual actor/request/target/reveal separately from generated prose."""
    # The room fixture's resume can still have an empty scheduler wakeup. Let
    # that settle before manually entering the real narration stage.
    await asyncio.gather(*list(service.runtime.tasks.values()))
    parent_id, child_id, run_id = (str(uuid4()) for _ in range(3))

    async def seed(session, room):
        module = await service.module(session, room.id)
        target = module.state["scene_id"]
        clue = next(c for c in module.document["clues"] if c["id"] == "notice")
        actor = fixture_room["agent"]
        request_text = "请检查公告，看看公告纸张有没有夹层或折叠。"
        source = service.rooms.append(session, room, "action.submitted", fixture_room["player"], {
            "cycle_id": parent_id, "text": request_text,
        })
        request = {
            "key": f"{source.seq}:0", "kind": "delegate", "text": request_text,
            "source_event_seq": source.seq, "source_start": 0, "source_end": len(request_text),
            "addressee_id": actor, "executor_member_id": actor,
            "requester_member_id": fixture_room["player"], "target_id": target,
            "operations": ["search", "observe"],
        }
        action_text = "我检查公告，看看公告纸张有没有夹层或折叠。"
        action = service.rooms.append(session, room, "agent.action_proposed", actor, {
            "cycle_id": parent_id, "text": action_text, "target_id": target,
        })
        state = initial_state(room.id, child_id, actor, action.seq, [], origin="teammate")
        state.update({"keeper_run_id": run_id, "related_player_cycle_id": parent_id,
                      "request_keys": [request["key"]],
                      "request_operands": {request["key"]: request}})
        session.add(AgentCycle(id=parent_id, room_id=room.id, status="completed", state={}))
        session.add(AgentCycle(id=child_id, room_id=room.id, status="running", state=state))
        slot = next(s for s in await service.rooms.slots(session, room) if s.member_id == actor)
        plan = KeeperPlan(
            plan_id=child_id, cycle_id=child_id, current_scene_id=target,
            parsed_intent={"type": "observe", "actor_member_id": actor,
                           "actor_character_slot_id": slot.id, "target_id": target,
                           "evidence_quote": action_text, "confidence": 1},
            focus={"action": action_text, "action_target_id": target},
            action_authority={"kinds": ["search"], "actor_member_id": actor,
                              "source_event_seq": action.seq, "target_id": target},
        )
        session.add(AgentRun(
            id=run_id, room_id=room.id, cycle_id=child_id,
            profile_id=fixture_room["profiles"][0]["id"], actor_member_id=room.host_member_id,
            graph_node="plan_keeper_action", status="completed",
            input_seq_start=action.seq, input_seq_end=action.seq,
            provider="fake", model="test", context={}, tool_results=[],
            structured_output=plan.model_dump(mode="json"),
        ))
        session.add(ActionPlanRecord(
            cycle_id=child_id, room_id=room.id, run_id=run_id,
            document=AdjudicationRecord(plan=plan, validation={
                "plan_id": child_id, "status": "approved",
            }).model_dump(mode="json"),
        ))
        # The already executed reveal is persisted before generating any prose.
        # No generated field is used to establish this clue or its original event.
        module.state = {**module.state, "revealed_clues": ["notice"]}
        if with_check:
            service.rooms.append(session, room, "check.resolved", actor, {
                "cycle_id": child_id, "id": "current-check", "name": "spot_hidden",
                "display_name": "侦查", "target_member_id": actor,
                "result": {"passed": True, "total": 25, "threshold": 60},
            })
        reveal = service.rooms.append(session, room, "clue.revealed", room.host_member_id, {
            "cycle_id": child_id, "clue_id": "notice", "title": clue["title"],
            "content": clue["content"],
        })
        session.add(AgentBehaviorRecord(room_id=room.id, member_id=actor,
            document=BehaviorState(pending_requests=[request], task_status="proposed",
                                   task_cycle_id=child_id).model_dump(mode="json")))
        return {"state": state, "request": request, "result": clue["content"],
                "reveal_seq": reveal.seq, "action_seq": action.seq, "target": target}

    return await service.mutate(fixture_room["room"]["id"], seed)


def freeze_contract(monkeypatch, seeded):
    from app.agents import narration_coverage

    def prepare(context):
        brief = context["response_brief"]
        # Prove this contract is triggered by an actual current receipt, never
        # by a test success flag or a previous draft's coverage.
        actual = [f for f in context["public_tool_results"]["current_result_facts"]
                  if f["source_event_seq"] == seeded["reveal_seq"]]
        assert len(actual) == 1 and actual[0]["effect"] == seeded["result"]
        source_id = f"e{seeded['reveal_seq']}"
        return {**brief, "responder": {"kind": "keeper"},
                "current_action_results": actual,
                "answer_requirements": [
                    {"id": UNKNOWN_ID, "kind": "observation",
                     "text": "公告纸张有没有夹层或折叠", "source_ids": []},
                    {"id": RESULT_ID, "kind": "result", "text": "本次实际结果：公告背面",
                     "source_ids": [source_id], "result_effect": seeded["result"],
                     "verbatim": True},
                ], "answer_sources": [{"id": source_id, "kind": "receipt",
                                       "text": seeded["result"]}]}

    monkeypatch.setattr(narration_coverage, "prepare_response_contract", prepare)


@pytest.mark.parametrize("mode", ["native", "repaired", "failed", "repaired_result"])
def test_actual_runtime_parts_publication_and_original_task_settlement(
    client, game, monkeypatch, mode,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded = client.portal.call(seed_inspection, service, game, mode == "repaired_result")
    freeze_contract(monkeypatch, seeded)
    parts = [{"requirement_id": UNKNOWN_ID, "text": UNKNOWN},
             {"requirement_id": RESULT_ID, "text": seeded["result"]}]
    first = {"answer_parts": parts if mode == "native" else [
        {**parts[0], "text": BAD}, parts[1],
    ]}
    repaired = {"answer_parts": [parts[0] if mode == "repaired" else {
        **parts[0], "text": BAD,
    }]}
    if mode == "repaired_result":
        first = {"answer_parts": [parts[0], {**parts[1], "text": "我试着检查公告。"}]}
        repaired = {"answer_parts": [parts[1]]}
    originals = deepcopy([first] if mode == "native" else [first, repaired])
    requests, snapshots = [], []

    def respond(messages, kwargs):
        assert kwargs["response_schema"].__name__ == "KeeperNarration"
        wire = generation_schema(kwargs["response_schema"].model_json_schema())
        assert "answer_parts" in wire["properties"]
        assert not {"public_narration", "answer_coverage", "incidental_details"} & (
            wire["properties"].keys()
        )
        requests.append({"messages": deepcopy(messages), "schema": wire})
        raw = originals[len(requests) - 1]
        return ModelResponse(text=json.dumps(raw, ensure_ascii=False))

    class StreamingParts(FakeModelAdapter):
        async def generate(self, messages, **kwargs):
            response = respond(messages, kwargs)
            for index, part in enumerate(json.loads(response.text)["answer_parts"]):
                prefix = '{"answer_parts":[' if index == 0 else ","
                await kwargs["on_delta"](prefix + json.dumps(part, ensure_ascii=False))
                stream = next(iter(service.runtime.narration_streams.values()))
                assert not stream.parser.complete
                snapshots.append({"attempt": len(requests), "text": stream.accepted,
                                  "snapshot": deepcopy(service.rooms.hub.stream_snapshot(
                                      seeded["state"]["room_id"],
                                  ))})
                async with service.rooms.database.sessions() as session:
                    behavior = await session.get(AgentBehaviorRecord,
                        (seeded["state"]["room_id"], game["agent"]))
                    assert behavior.document["task_status"] == "proposed"
                    assert behavior.document["pending_requests"][0]["key"] == (
                        seeded["request"]["key"]
                    )
            await kwargs["on_delta"]("]}")
            return response

    service.model.adapter = StreamingParts()

    async def execute():
        state = await service.runtime.generate_keeper_narration(seeded["state"])
        # Generation/publish alone does not settle a still-running task.
        async with service.rooms.database.sessions() as session:
            behavior = await session.get(AgentBehaviorRecord, (state["room_id"], game["agent"]))
            assert behavior.document["task_status"] == "proposed"
            assert behavior.document["pending_requests"][0]["key"] == seeded["request"]["key"]
        return await service.runtime.finish_cycle(state)

    state = client.portal.call(execute)
    assert state["status"] == "completed"
    successful = mode != "failed"
    origin = "native" if mode == "native" else "repaired"
    assert len(requests) == (1 if mode == "native" else 2)
    if mode != "native":
        wire = requests[1]["schema"]
        ref = wire["properties"]["answer_parts"]["items"]["$ref"].split("/")[-1]
        missing = RESULT_ID if mode == "repaired_result" else UNKNOWN_ID
        assert wire["$defs"][ref]["properties"]["requirement_id"]["const"] == missing
        retained_text = UNKNOWN if mode == "repaired_result" else seeded["result"]
        assert retained_text in requests[1]["messages"][-1]["content"]

    events = ok(client.get(game["prefix"] + "/events"))["events"]
    official = [e for e in events if e["type"] == "keeper.narration"
                and e["payload"].get("cycle_id") == state["cycle_id"]]
    assert len(official) == 1
    payload = official[0]["payload"]
    expected = "\n\n".join(p["text"] for p in parts)
    assert payload["answer_origin"] == (origin if successful else "server_fallback")
    assert payload["safe_fallback"] is not successful
    assert BAD not in payload["text"] and seeded["result"] in payload["text"]
    if successful:
        assert payload["text"] == expected
        final_stream = [s for s in snapshots if s["attempt"] == len(requests)]
        assert final_stream[-1]["text"] == expected
        assert final_stream[-1]["snapshot"]["data"]["text"] == expected
        if mode == "native":
            assert final_stream[0]["text"] == UNKNOWN
            assert final_stream[0]["snapshot"]["data"]["status"] == "responding"
    else:
        assert "未完整生成" in payload["text"]
    assert all(BAD not in snapshot["text"] for snapshot in snapshots)
    assert "answer_parts" not in payload
    story, _ = story_events(events)
    assert next(e for e in story if e["seq"] == official[0]["seq"])["payload"]["text"] == (
        payload["text"]
    )

    validation = ok(client.get(game["prefix"] + f"/cycles/{state['cycle_id']}/validation"))
    assert validation["narration"]["public_narration"] == payload["text"]
    assert "answer_parts" not in validation["narration"]
    assert validation["narration_validation"]["answer_complete"] is successful
    assert validation["narration_validation"]["repair_count"] == len(requests) - 1

    async def evidence():
        async with service.rooms.database.sessions() as session:
            run = await session.scalar(select(AgentRun).where(
                AgentRun.cycle_id == state["cycle_id"],
                AgentRun.graph_node == "generate_keeper_narration",
            ))
            calls = [c.document for c in await session.scalars(
                select(AgentModelCall).where(AgentModelCall.run_id == run.id),
            )]
            behavior = await session.get(AgentBehaviorRecord,
                                         (state["room_id"], game["agent"]))
            return deepcopy(run.context), deepcopy(run.structured_output), sorted(
                calls, key=lambda c: c["attempt"],
            ), deepcopy(behavior.document)

    context, stored, calls, behavior = client.portal.call(evidence)
    assert "answer_parts" not in stored
    assert context["answer_contract_version"] == CONTRACT_VERSION
    assert context["answer_render_order"] == [UNKNOWN_ID, RESULT_ID]
    assert [call["raw_output"] for call in calls] == originals
    for index, call in enumerate(calls):
        assert call["answer_contract_version"] == CONTRACT_VERSION
        assert "answer_parts" not in call["derived_narration"]
        assert call["raw_answer_coverage_audit"]["complete"] is False
        accepted = successful and index == len(calls) - 1
        assert bool(call["answer_coverage_audit"]["complete"]) is accepted
        assert call["answer_origin"] == (origin if accepted else "rejected")
    if successful:
        assert stored["public_narration"] == expected
        assert calls[-1]["derived_narration"]["public_narration"] == expected
        assert calls[-1]["effective_answer_coverage"][1]["source_id"] == (
            f"e{seeded['reveal_seq']}"
        )
    else:
        assert context["validated_partial"]["public_narration"] == seeded["result"]
        assert context["validated_partial"]["answer_coverage"][0]["requirement_id"] == RESULT_ID
        assert all(c["retained_answer_parts"] == [parts[1]] for c in calls)

    assert behavior["task_status"] == ("completed" if successful else "attempted")
    assert behavior["last_result"]["narration_complete"] is successful
    task = (behavior["request_history"] if successful else behavior["pending_requests"])[0]
    for key in ("key", "source_event_seq", "source_start", "source_end",
                "executor_member_id", "requester_member_id", "target_id"):
        assert task[key] == seeded["request"][key]
    facts = behavior["last_result"]["result_facts"]
    reveal = next(f for f in facts if f["operation"] == "reveal")
    assert reveal["source_event_seq"] == seeded["reveal_seq"]
    assert reveal["source_action_seq"] == seeded["action_seq"]
    assert reveal["actor_id"] == game["agent"] and reveal["action_target_id"] == seeded["target"]
    assert reveal["cycle_id"] == state["cycle_id"]
    assert any(f["operation"] == "observe" and f["status"] == "success" for f in facts) is (
        successful
    )
    assert not any("纸张没有夹层" in fact.get("effect", "") for fact in facts)
    assert bool(behavior["pending_requests"]) is not successful
