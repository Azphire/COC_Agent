"""Mixed reports use real receipts; server uncertainty never settles execution."""

import json
from copy import deepcopy

import pytest
from sqlalchemy import select
from test_agent_runtime import game  # noqa: F401
from test_batch50_parts_runtime import (
    BAD,
    RESULT_ID,
    UNKNOWN_ID,
    freeze_contract,
    seed_inspection,
)
from test_rooms import lobby, ok  # noqa: F401

from app.agents.answer_parts import MIXED_CONTRACT_VERSION
from app.agents.model import FakeModelAdapter
from app.agents.server_parts import prepare_server_parts
from app.memory.facts import fact_records
from app.models.base import ModelResponse
from app.persistence.adjudication_models import ActionPlanRecord, AgentBehaviorRecord
from app.persistence.agent_models import AgentRun
from app.persistence.knowledge_models import AgentModelCall
from app.persistence.room_models import RoomEvent


async def seed_mixed_inspection(service, fixture_room, with_check=False):
    return await seed_inspection(service, fixture_room, with_check)


def freeze_mixed_contract(monkeypatch, seeded, with_server=True):
    """Keep the old request fixture, but run actual scoped server assessment."""
    from app.agents import narration_coverage

    freeze_contract(monkeypatch, seeded)
    original = narration_coverage.prepare_response_contract

    def prepare(context):
        brief = original(context)
        request = deepcopy(seeded["request"])
        request.update(cycle_id=seeded["state"]["cycle_id"],
                       source_action_seq=seeded["action_seq"], operation_operands={
                           "observe": {"target_id": seeded["target"], "target_source": {
                               "binding_kind": "same_request_reference",
                               "target_id": seeded["target"], "request_key": request["key"],
                               "request_source_event_seq": request["source_event_seq"],
                               "reference_text": "公告纸张",
                           }},
                       })
        brief["delegated_requests"] = [request]
        # The runtime must capture the public scope before its prompt trimming.
        assert context["answer_evidence_scope"]["complete_public_projection"]
        assert context["answer_evidence_scope"]["visibility"] == "public"
        if not with_server:
            return brief
        brief = prepare_server_parts(context, brief, brief["answer_sources"])
        assert [p["requirement_id"] for p in brief["server_parts"]] == [UNKNOWN_ID], {
            "assessments": [(r["id"], r.get("evidence_assessment", {}).get("reason"))
                            for r in brief["answer_requirements"]],
            "entities": [e["id"] for e in context["answer_evidence_scope"]["public_entities"]],
        }
        assert [r["id"] for r in brief["answer_requirements"]] == [RESULT_ID, UNKNOWN_ID]
        return brief

    monkeypatch.setattr(narration_coverage, "prepare_response_contract", prepare)


async def saved_evidence(service, seeded, actor):
    async with service.rooms.database.sessions() as session:
        state = seeded["state"]
        run = await session.scalar(select(AgentRun).where(
            AgentRun.cycle_id == state["cycle_id"],
            AgentRun.graph_node == "generate_keeper_narration",
        ))
        calls = list(await session.scalars(select(AgentModelCall).where(
            AgentModelCall.run_id == run.id,
        )))
        behavior = await session.get(AgentBehaviorRecord, (state["room_id"], actor))
        record = await session.get(ActionPlanRecord, state["cycle_id"])
        return (deepcopy(run.context), deepcopy(run.structured_output),
                sorted((deepcopy(c.document) for c in calls), key=lambda c: c["attempt"]),
                deepcopy(behavior.document), deepcopy(record.document))


@pytest.mark.parametrize("mode", ["mixed", "repaired", "failed"])
def test_mixed_formal_report_and_original_task_settlement(
    client, game, monkeypatch, mode,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded = client.portal.call(seed_mixed_inspection, service, game, True)
    freeze_mixed_contract(monkeypatch, seeded)
    good = {"answer_parts": [{"requirement_id": RESULT_ID, "text": seeded["result"]}]}
    bad = {"answer_parts": [{"requirement_id": RESULT_ID,
                              "text": seeded["result"] + BAD}]}
    originals = ([good] if mode == "mixed" else
                 [bad, good] if mode == "repaired" else [bad, bad])
    service.model.adapter = FakeModelAdapter([
        ModelResponse(text=json.dumps(raw, ensure_ascii=False)) for raw in originals
    ])

    async def execute():
        state = await service.runtime.generate_keeper_narration(seeded["state"])
        evidence = await saved_evidence(service, seeded, game["agent"])
        # A final report alone cannot settle a still-running execution cycle.
        assert evidence[3]["task_status"] == "proposed"
        assert evidence[3]["pending_requests"][0]["key"] == seeded["request"]["key"]
        return await service.runtime.finish_cycle(state)

    final = client.portal.call(execute)
    assert final["status"] == "completed"
    context, stored, calls, behavior, record = client.portal.call(
        saved_evidence, service, seeded, game["agent"],
    )
    success = mode != "failed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    formal = [e for e in events if e["type"] == "keeper.narration"
              and e["payload"].get("cycle_id") == final["cycle_id"]]
    assert len(formal) == 1
    payload = formal[0]["payload"]
    unknown = context["response_brief"]["server_parts"][0]["text"]
    expected = seeded["result"] + "\n\n" + unknown
    assert payload["safe_fallback"] is not success
    assert payload["answer_origin"] == ("mixed" if success else "server_fallback")
    assert BAD not in payload["text"]
    assert record["narration"]["public_narration"] == payload["text"]
    assert record["narration_validation"]["answer_complete"] is success
    assert record["narration_validation"]["repair_count"] == len(originals) - 1
    assert context["answer_contract_version"] == MIXED_CONTRACT_VERSION
    assert context["answer_render_order"] == [RESULT_ID, UNKNOWN_ID]
    assert [c["raw_output"] for c in calls] == originals
    assert len(calls) == (1 if mode == "mixed" else 2)
    assert all(c["answer_contract_version"] == MIXED_CONTRACT_VERSION for c in calls)
    assert all(p["requirement_id"] != UNKNOWN_ID
               for c in calls for p in c.get("retained_answer_parts", []))
    assert not context.get("_answer_parts_retained")
    for call in calls:
        wire = call["output_contract"]
        definitions = wire.get("$defs", {})
        requirement_ids = {row["properties"]["requirement_id"].get("const")
                           for row in definitions.values()
                           if "requirement_id" in row.get("properties", {})}
        assert requirement_ids == {RESULT_ID}
    if success:
        assert payload["text"] == expected == stored["public_narration"]
        coverage = stored["answer_coverage"]
        assert coverage[0]["source_id"] == f"e{seeded['reveal_seq']}"
        assert coverage[1]["status"] == "unknown" and coverage[1]["source_id"] is None
        assert behavior["task_status"] == "completed", behavior
        assert not behavior["pending_requests"]
        task = behavior["request_history"][0]
        for key in ("key", "source_event_seq", "executor_member_id", "requester_member_id",
                    "target_id", "source_start", "source_end"):
            assert task[key] == seeded["request"][key]
    else:
        assert "未完整生成" in payload["text"]
        assert behavior["task_status"] != "completed"
        assert behavior["pending_requests"][0]["key"] == seeded["request"]["key"]
    assert behavior["last_result"]["narration_complete"] is success
    # Neither the formal server sentence nor the unknown attribute is a source
    # text / world fact. The immutable clue text remains the evidence source.
    memories = fact_records(events)
    assert not any(r["source_event_seq"] == formal[0]["seq"] for r in memories)
    assert not any(unknown in r.get("text", "") or BAD in r.get("text", "")
                   for r in memories)
    assert any(r["source_event_seq"] == seeded["reveal_seq"]
               and r["text"] == seeded["result"] for r in memories)
    facts = behavior["last_result"]["result_facts"]
    assert any(f["operation"] == "observe" and f["status"] == "success" for f in facts) is success
    assert sum(e["type"] == "clue.revealed" and e["seq"] == seeded["reveal_seq"]
               for e in events) == 1
    check = next(e for e in events if e["type"] == "check.resolved"
                 and e["payload"].get("cycle_id") == final["cycle_id"])
    assert check["payload"]["result"]["total"] == 25


@pytest.mark.parametrize("mismatch", [
    "no_execution", "actor", "target", "cycle", "request", "temporary",
])
def test_server_unknown_cannot_settle_without_bound_execution_and_formal_report(
    client, game, monkeypatch, mismatch,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded = client.portal.call(seed_mixed_inspection, service, game)
    freeze_mixed_contract(monkeypatch, seeded)
    service.model.adapter = FakeModelAdapter([{
        "answer_parts": [{"requirement_id": RESULT_ID, "text": seeded["result"]}],
    }])
    state = client.portal.call(service.runtime.generate_keeper_narration, seeded["state"])
    original_results = service.runtime.public_results

    async def inconsistent_results(session, room, cycle):
        results = deepcopy(await original_results(session, room, cycle))
        if mismatch == "no_execution":
            results["current_result_facts"] = []
        elif mismatch in {"actor", "target", "cycle"}:
            field = {"actor": "actor_id", "target": "action_target_id",
                     "cycle": "cycle_id"}[mismatch]
            for fact in results["current_result_facts"]:
                fact[field] = "unrelated-" + mismatch
        return results

    # Isolate the settlement boundary after a valid mixed report is published:
    # even a previously generated server sentence cannot substitute for these
    # current, authoritative receipt bindings.
    monkeypatch.setattr(service.runtime, "public_results", inconsistent_results)

    async def alter_report_or_request(session, room):
        if mismatch == "request":
            row = await session.get(AgentBehaviorRecord, (room.id, game["agent"]))
            row.document = {**row.document, "pending_requests": [
                {**row.document["pending_requests"][0], "key": "another-request"},
            ]}
        elif mismatch == "temporary":
            report = await session.scalar(select(RoomEvent).where(
                RoomEvent.room_id == room.id, RoomEvent.type == "keeper.narration",
                RoomEvent.payload["cycle_id"].as_string() == state["cycle_id"],
            ))
            # A temporary stream is deliberately absent from the event ledger.
            await session.delete(report)

    client.portal.call(service.mutate, state["room_id"], alter_report_or_request)
    client.portal.call(service.runtime.finish_cycle, state)
    _, _, calls, behavior, _ = client.portal.call(saved_evidence, service, seeded, game["agent"])
    assert len(calls) == 1
    assert behavior["task_status"] != "completed"
    assert behavior["pending_requests"]
    assert not behavior["request_history"]
