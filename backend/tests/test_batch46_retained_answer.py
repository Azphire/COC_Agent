"""A repair cannot discard a public, source-checked span from the first draft."""

from copy import deepcopy

import pytest
from sqlalchemy import select
from test_action_adjudication import modern_response
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_batch46_narration_coverage import coverage_context
from test_rooms import lobby, ok  # noqa: F401

from app.agents.generation_contracts import narration_body_field
from app.agents.model import FakeModelAdapter
from app.persistence.agent_models import AgentRun
from app.persistence.knowledge_models import AgentModelCall


def test_changed_verified_quote_is_rejected_and_first_observed_detail_is_retained(
    client, game, monkeypatch,  # noqa: F811
):
    from app.agents import narration_coverage

    selected = deepcopy(coverage_context()["response_brief"])
    selected["answer_requirements"].append({
        "id": "r-name", "kind": "question", "text": "林先生知道物品的具体名称吗？",
        "speaker": "林先生", "historical": True, "source_ids": ["e12"],
    })

    def prepare(context):
        return {**context["response_brief"],
                "ordinary_observation": True,
                "answer_requirements": selected["answer_requirements"],
                "answer_sources": selected["answer_sources"]}

    monkeypatch.setattr(narration_coverage, "prepare_response_contract", prepare)
    original_quote = "林先生早先估计少了九件物品。"
    changed_quote = "据林先生此前的估算，物品约少了9件。"
    missing_answer = "林先生此前表示不知道物品的具体名称。"
    original_mapping = {
        "requirement_id": "r-number", "body_quote": original_quote,
        "source_id": "e12", "source_quote": "我估计少了九件物品", "status": "answered",
    }
    attempts = []
    body_fields = []

    def respond(messages, kwargs):
        schema = kwargs["response_schema"]
        if schema.__name__ != "KeeperNarration":
            return modern_response(messages, kwargs)
        body_field = narration_body_field(schema)
        assert body_field == "public_narration"  # Mixed historical questions use the full body.
        body_fields.append(body_field)
        attempts.append(messages)
        if len(attempts) == 1:
            return {body_field: original_quote, "answer_coverage": [original_mapping]}
        return {body_field: changed_quote + missing_answer, "answer_coverage": [
            {**original_mapping, "body_quote": changed_quote},
            {"requirement_id": "r-name", "body_quote": missing_answer,
             "source_id": "e12", "source_quote": "不知道具体名称", "status": "unknown"},
        ]}

    service = client.app.state.agent_service
    service.model.adapter = FakeModelAdapter(responder=respond)
    validate = service.runtime.validate_narration_output
    validated_partials = []

    async def capture_validation(*args, **kwargs):
        audit = await validate(*args, **kwargs)
        if kwargs.get("partial"):
            validated_partials.append((args[-1].public_narration, audit))
        return audit

    monkeypatch.setattr(service.runtime, "validate_narration_output", capture_validation)
    ok(submit(client, game, "我看看四周"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    assert len(attempts) == 2
    assert original_quote in attempts[1][-1]["content"]
    assert "r-name" in attempts[1][-1]["content"]
    assert any(text == original_quote and audit["answer_coverage"]["valid"]
               for text, audit in validated_partials)

    events = ok(client.get(game["prefix"] + "/events"))["events"]
    narrations = [e for e in events if e["type"] == "keeper.narration"
                  and e["payload"].get("cycle_id") == cycle["id"]]
    assert len(narrations) == 1
    payload = narrations[0]["payload"]
    assert payload["safe_fallback"] and payload["answer_origin"] == "server_fallback"
    assert original_quote in payload["text"] and "未完整生成" in payload["text"]
    assert changed_quote not in payload["text"] and missing_answer in payload["text"]

    validation = ok(client.get(game["prefix"] + f"/cycles/{cycle['id']}/validation"))
    audit = validation["narration_validation"]
    assert audit["answer_complete"] is False and audit["repair_count"] == 1
    assert audit["answer_coverage"]["covered"] == ["r-number", "r-name"]
    assert audit["answer_coverage"]["missing"] == []
    assert validation["narration"]["public_narration"] == payload["text"]
    assert validation["narration"]["answer_coverage"][0] == original_mapping

    async def evidence():
        async with service.rooms.database.sessions() as session:
            run = await session.scalar(select(AgentRun).where(
                AgentRun.cycle_id == cycle["id"],
                AgentRun.graph_node == "generate_keeper_narration",
            ))
            calls = [call.document for call in await session.scalars(
                select(AgentModelCall).where(AgentModelCall.run_id == run.id)
            )]
            return run.context["validated_partial"], sorted(calls, key=lambda c: c["attempt"])

    retained, calls = client.portal.call(evidence)
    assert retained["public_narration"] == original_quote + "\n" + missing_answer
    assert retained["answer_coverage"][0] == original_mapping
    # Both independently safe answers can survive in a server fallback. The
    # rejected repair remains rejected and must not be labelled complete/native.
    assert calls[0]["generated_output"][body_fields[0]] == original_quote
    assert calls[0]["answer_coverage_audit"]["complete"] is False
    # The second draft covers the sources. Its separate runtime error is the
    # dropped exact span, proving this is retention enforcement, not missing IDs.
    assert calls[1]["answer_coverage_audit"]["complete"] is True
    assert calls[1]["error_category"] == "ModelFormatError"
    assert any("已验证正文片段" in issue["code"]
               for issue in calls[1]["validation_issues"])


@pytest.mark.parametrize("bad_subject", ["historical", "observation"])
def test_failed_mixed_body_retains_only_its_individually_validated_observation(
    client, game, monkeypatch, bad_subject,  # noqa: F811
):
    from app.agents import narration_coverage

    selected = deepcopy(coverage_context()["response_brief"])
    if bad_subject == "observation":
        selected["answer_requirements"] = [{
            "id": "r-scratches", "kind": "observation", "text": "铁门划痕",
            "source_ids": ["door-source"],
        }]
        selected["answer_sources"] = [{
            "id": "door-source", "text": "铁门表面留着一道划痕。", "kind": "observation",
        }]
    selected["answer_requirements"].insert(0, {
        "id": "r-cabinet", "kind": "observation", "text": "橱柜边沿",
        "source_ids": ["cabinet-source"],
    })
    selected["answer_sources"].append({
        "id": "cabinet-source", "text": "橱柜边沿有灰尘。", "kind": "observation",
    })

    def prepare(context):
        return {**context["response_brief"], "ordinary_observation": True,
                "answer_requirements": selected["answer_requirements"],
                "answer_sources": selected["answer_sources"]}

    monkeypatch.setattr(narration_coverage, "prepare_response_contract", prepare)
    safe_quote = "橱柜边沿有灰尘。"
    bad_quote = ("林先生早先估计少了七件物品。" if bad_subject == "historical"
                 else "铁门表面没有划痕。")
    missing_id = "r-number" if bad_subject == "historical" else "r-scratches"
    safe_mapping = {"requirement_id": "r-cabinet", "body_quote": safe_quote,
                    "source_id": "cabinet-source", "source_quote": "橱柜边沿有灰尘。",
                    "status": "answered"}
    bad_mapping = {"requirement_id": "r-number", "body_quote": bad_quote,
                   "source_id": "e12", "source_quote": "我估计少了九件物品",
                   "status": "answered"}
    if bad_subject == "observation":
        bad_mapping.update(requirement_id="r-scratches", source_id="door-source",
                           source_quote="铁门表面留着一道划痕。")
    attempts = []

    def respond(messages, kwargs):
        schema = kwargs["response_schema"]
        if schema.__name__ != "KeeperNarration":
            return modern_response(messages, kwargs)
        body_field = narration_body_field(schema)
        assert body_field == ("public_narration" if bad_subject == "historical"
                              else "observed_detail")
        attempts.append(messages)
        return {body_field: safe_quote + bad_quote,
                "answer_coverage": [safe_mapping, bad_mapping]}

    service = client.app.state.agent_service
    service.model.adapter = FakeModelAdapter(responder=respond)
    validate = service.runtime.validate_narration_output
    validated_partials = []

    async def capture_validation(*args, **kwargs):
        audit = await validate(*args, **kwargs)
        if kwargs.get("partial"):
            validated_partials.append((args[-1].public_narration, audit))
        return audit

    monkeypatch.setattr(service.runtime, "validate_narration_output", capture_validation)
    ok(submit(client, game, "我看看四周"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    assert len(attempts) == 2
    # Real public/source/result validation succeeded for this exact span even
    # though validating either entire generated paragraph failed.
    assert any(text == safe_quote and audit["answer_coverage"]["valid"]
               for text, audit in validated_partials)
    assert not any(bad_quote in text for text, _ in validated_partials)

    async def evidence():
        async with service.rooms.database.sessions() as session:
            run = await session.scalar(select(AgentRun).where(
                AgentRun.cycle_id == cycle["id"],
                AgentRun.graph_node == "generate_keeper_narration",
            ))
            calls = [call.document for call in await session.scalars(
                select(AgentModelCall).where(AgentModelCall.run_id == run.id)
            )]
            return run.context["validated_partial"], calls

    retained, calls = client.portal.call(evidence)
    assert len(calls) == 2
    assert all(c["error_category"] == "ModelFormatError" for c in calls)
    assert all(c["answer_coverage_audit"]["verified"] == [safe_mapping] for c in calls)

    events = ok(client.get(game["prefix"] + "/events"))["events"]
    narrations = [e for e in events if e["type"] == "keeper.narration"
                  and e["payload"].get("cycle_id") == cycle["id"]]
    assert len(narrations) == 1
    payload = narrations[0]["payload"]
    assert payload["safe_fallback"] and payload["answer_origin"] == "server_fallback"
    assert safe_quote in payload["text"], "fallback dropped an individually verified span"
    assert bad_quote not in payload["text"] and "未完整生成" in payload["text"]
    assert retained["public_narration"] == safe_quote
    assert retained["answer_coverage"] == [safe_mapping]
    validation = ok(client.get(game["prefix"] + f"/cycles/{cycle['id']}/validation"))
    assert validation["narration_validation"]["answer_complete"] is False
    assert validation["narration_validation"]["answer_coverage"]["covered"] == ["r-cabinet"]
    assert validation["narration_validation"]["answer_coverage"]["missing"] == [missing_id]
    assert validation["narration"]["public_narration"] == payload["text"]
    assert validation["narration"]["answer_coverage"] == [safe_mapping]
