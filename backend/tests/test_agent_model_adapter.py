import json

import httpx
import pytest

from app.agents.adjudication_schemas import KeeperPlan
from app.agents.model import AgentModelClient, FakeModelAdapter
from app.agents.schemas import AgentDecision, SummaryOutput
from app.config import Settings
from app.models.base import ModelError, ModelResponse, ToolCall
from app.models.ollama import ModelFormatError, OllamaAgentAdapter


async def test_critical_nullable_plan_decisions_are_explicit_in_generation():
    seen = []
    plan = KeeperPlan(
        plan_id="plan",
        cycle_id="cycle",
        current_scene_id="scene",
        parsed_intent={
            "type": "observe",
            "actor_member_id": "actor",
            "actor_character_slot_id": "slot",
            "evidence_quote": "我观察现场",
            "confidence": 1,
        },
    )

    def respond(request):
        seen.append(json.loads(request.content)["format"])
        return httpx.Response(200, json={"message": {"content": plan.model_dump_json()}})

    adapter = OllamaAgentAdapter(Settings(_env_file=None))
    await adapter.client.aclose()
    adapter.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(respond)
    )
    try:
        result = await adapter.generate([], response_schema=KeeperPlan)
    finally:
        await adapter.close()
    assert {"proposed_check", "proposed_transition_id"} <= set(seen[0]["required"])
    assert "x-explicit-output" not in json.dumps(seen[0])
    assert result.structured.proposed_check is None
    assert result.structured.proposed_transition_id is None


async def test_ollama_options_schema_and_hidden_thinking():
    seen = []

    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"content":"公开叙事"}', "thinking": "never store this"},
                "done_reason": "stop",
                "prompt_eval_count": 12,
                "eval_count": 8,
            },
        )

    settings = Settings(_env_file=None, model_context_limit=8192, model_keep_alive="5m")
    adapter = OllamaAgentAdapter(settings)
    await adapter.client.aclose()
    adapter.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(respond)
    )
    result = await adapter.generate(
        [{"role": "user", "content": "test"}], response_schema=SummaryOutput
    )
    await adapter.close()
    assert result.structured.content == "公开叙事"
    assert "never store this" not in result.model_dump_json()
    assert "maxLength" not in json.dumps(seen[0]["format"])
    assert (
        seen[0]["options"]["num_ctx"] == 8192
        and seen[0]["keep_alive"] == "5m"
        and seen[0]["think"] is False
    )
    assert result.token_usage == {"input": 12, "output": 8}


async def test_ollama_still_enforces_pydantic_length():
    def respond(request):
        return httpx.Response(
            200, json={"message": {"content": json.dumps({"content": "x" * 2001})}}
        )

    adapter = OllamaAgentAdapter(Settings(_env_file=None))
    await adapter.client.aclose()
    adapter.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(respond)
    )
    try:
        with pytest.raises(ModelFormatError):
            await adapter.generate([], response_schema=SummaryOutput)
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "error,match",
    [
        ("CUDA error: out of memory private body", "OOM"),
        ("failed to parse grammar private body", "语法"),
        ("private response body", "请求失败"),
    ],
)
async def test_provider_errors_are_safe(error, match):
    adapter = OllamaAgentAdapter(Settings(_env_file=None))
    await adapter.client.aclose()
    adapter.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(lambda _: httpx.Response(500, json={"error": error})),
    )
    try:
        with pytest.raises(ModelError, match=match) as caught:
            await adapter.generate([])
        assert "private" not in str(caught.value)
    finally:
        await adapter.close()


async def test_gateway_accepts_validated_tool_response():
    adapter = FakeModelAdapter(
        [ModelResponse(tool_calls=[ToolCall(id="1", name="inspect_public_state", arguments={})])]
    )
    model = AgentModelClient(Settings(_env_file=None), adapter)
    result, _ = await model.generate([], response_schema=AgentDecision, tools=[])
    assert result.structured.tools[0].name == "inspect_public_state"
