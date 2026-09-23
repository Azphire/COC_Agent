"""Exercise the existing model repair budget and durable publication boundary."""

import json

import pytest
from sqlalchemy import select
from test_action_adjudication import modern_response
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_batch46_narration_coverage import coverage_context
from test_rooms import lobby, ok  # noqa: F401

from app.agents.generation_contracts import narration_body_field
from app.agents.model import FakeModelAdapter
from app.models.base import ModelResponse
from app.persistence.agent_models import AgentRun
from app.persistence.knowledge_models import AgentModelCall


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_one_repair_and_honest_fallback_converge_on_published_text(
    client, game, monkeypatch, repair_succeeds,  # noqa: F811
):
    # Selected visible evidence is fixed at the seam; routing/source selection
    # are exercised separately. Everything after preparation uses the real graph.
    from app.agents import narration_coverage

    selected = coverage_context()["response_brief"]

    def prepare(context):
        return {**context["response_brief"],
                "answer_requirements": selected["answer_requirements"],
                "answer_sources": selected["answer_sources"]}

    monkeypatch.setattr(narration_coverage, "prepare_response_contract", prepare)
    attempts = []
    good = "林先生早先估计少了九件物品。"
    first = "橱柜边沿留着浅浅的灰尘。"

    def respond(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            attempts.append(messages)
            body_field = narration_body_field(kwargs["response_schema"])
            result = {body_field: first}
            if len(attempts) == 2 and repair_succeeds:
                result[body_field] = good
                result.update(answer_coverage=[{
                    "requirement_id": "r-number", "body_quote": good, "source_id": "e12",
                    "source_quote": "我估计少了九件物品", "status": "answered",
                }])
            return result
        return modern_response(messages, kwargs)

    service = client.app.state.agent_service
    service.model.adapter = FakeModelAdapter(responder=respond)
    ok(submit(client, game, "我看看四周"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    assert len(attempts) == 2
    assert "r-number" in attempts[1][-1]["content"]
    assert "九件物品" in attempts[1][-1]["content"]
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    narrations = [e for e in events if e["type"] == "keeper.narration"
                  and e["payload"].get("cycle_id") == cycle["id"]]
    assert len(narrations) == 1
    payload = narrations[0]["payload"]
    assert payload["safe_fallback"] is not repair_succeeds
    assert payload["answer_origin"] == ("repaired" if repair_succeeds else "server_fallback")
    if repair_succeeds:
        assert payload["text"] == good
    else:
        assert "未完整生成" in payload["text"]
    validation = ok(client.get(game["prefix"] + f"/cycles/{cycle['id']}/validation"))
    audit = validation["narration_validation"]
    assert audit["answer_complete"] is repair_succeeds
    assert audit["repair_count"] == 1
    assert validation["narration"]["public_narration"] == payload["text"]

    async def calls():
        async with service.rooms.database.sessions() as session:
            run = await session.scalar(select(AgentRun).where(
                AgentRun.cycle_id == cycle["id"],
                AgentRun.graph_node == "generate_keeper_narration",
            ))
            return [call.document for call in await session.scalars(
                select(AgentModelCall).where(AgentModelCall.run_id == run.id)
            )]

    recorded = sorted(client.portal.call(calls), key=lambda c: c["attempt"])
    assert [c["answer_origin"] for c in recorded] == ["native", "repaired"]
    assert first in recorded[0]["generated_output"].values()
    assert recorded[0]["answer_coverage_audit"]["complete"] is False
    assert recorded[1]["answer_coverage_audit"]["complete"] is repair_succeeds


def test_complete_native_body_keeps_raw_mapping_and_normalized_evidence(
    client, game, monkeypatch,  # noqa: F811
):
    from app.agents import narration_coverage

    selected = coverage_context()["response_brief"]
    monkeypatch.setattr(narration_coverage, "prepare_response_contract", lambda context: {
        **context["response_brief"], "answer_requirements": selected["answer_requirements"],
        "answer_sources": selected["answer_sources"],
    })
    body = "林先生早先估计少了九件物品。"
    raw_map = [{
        "requirement_id": "r-number", "body_quote": selected["question"],
        "source_id": "e12", "source_quote": "我估计少了九件物品，但不知道具体名y。",
        "status": "answered",
    }]
    original_map = json.loads(json.dumps(raw_map))
    attempts = []

    def respond(messages, kwargs):
        if kwargs["response_schema"].__name__ != "KeeperNarration":
            return modern_response(messages, kwargs)
        attempts.append(messages)
        raw = {narration_body_field(kwargs["response_schema"]): body,
               "answer_coverage": raw_map}
        # Ollama parses its schema before the model-call recorder sees a result.
        # The original JSON text must survive this same adapter boundary.
        parsed = kwargs["response_schema"].model_validate(raw)
        return ModelResponse(text=json.dumps(raw, ensure_ascii=False), structured=parsed)

    service = client.app.state.agent_service
    service.model.adapter = FakeModelAdapter(responder=respond)
    ok(submit(client, game, "我看看四周"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed"
    assert len(attempts) == 1
    validation = ok(client.get(game["prefix"] + f"/cycles/{cycle['id']}/validation"))
    assert validation["narration"]["public_narration"] == body
    assert validation["narration_validation"]["answer_origin"] == "native"
    assert validation["narration_validation"]["answer_complete"] is True
    assert validation["narration_validation"]["repair_count"] == 0
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    official = [e for e in events if e["type"] == "keeper.narration"
                and e["payload"].get("cycle_id") == cycle["id"]]
    assert len(official) == 1
    assert official[0]["payload"]["text"] == body
    assert official[0]["payload"]["safe_fallback"] is False

    async def calls():
        async with service.rooms.database.sessions() as session:
            run = await session.scalar(select(AgentRun).where(
                AgentRun.cycle_id == cycle["id"],
                AgentRun.graph_node == "generate_keeper_narration",
            ))
            return [call.document for call in await session.scalars(
                select(AgentModelCall).where(AgentModelCall.run_id == run.id)
            )]

    recorded = client.portal.call(calls)
    assert len(recorded) == 1
    assert raw_map == original_map
    assert recorded[0]["raw_output"]["answer_coverage"] == original_map
    assert recorded[0]["raw_answer_coverage_audit"]["complete"] is False
    assert recorded[0]["answer_coverage_audit"]["complete"] is True
    assert recorded[0]["answer_coverage_normalizations"]
    assert recorded[0]["effective_answer_coverage"][0]["body_quote"] == body
