"""Real request envelopes, immutable historical evidence and separate limits."""

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from app.agents.action_runtime import (
    action_messages,
    compact_planning_prose,
    generation_prompt,
)
from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan, TeammateDecision
from app.agents.model import AgentModelClient, FakeModelAdapter
from app.config import Settings
from app.models.base import ModelError, ModelResponse
from app.models.budget import context_character_budget, measure_request, schema_envelope
from app.models.openai_compatible import OpenAICompatibleClient


class Answer(BaseModel):
    answer: str


def test_separate_character_limit_includes_schema_system_tools_and_output():
    settings = Settings(_env_file=None, model_name="batch44-budget-envelope",
                        model_context_limit=2048, agent_context_chars=12000)
    messages = [{"role": "system", "content": "系统" * 200},
                {"role": "user", "content": "密码 000219。"}]
    tools = [{"type": "function", "function": {"name": "read", "description": "来源" * 300}}]
    plain = measure_request(settings, messages, output_limit=128)
    measured = measure_request(settings, messages, Answer, tools, output_limit=1600)
    assert context_character_budget(settings) == 12000
    assert measured["input_tokens"] > plain["input_tokens"]
    assert measured["total_tokens"] == measured["input_tokens"] + 1600
    assert measured["system_chars"] == 400
    assert measured["schema_tools_chars"] > 600
    assert not measured["within_token_limit"]
    assert measured["method"] == "conservative_estimate"


async def test_actual_json_provider_envelope_equals_measurement():
    settings = Settings(_env_file=None, model_provider="openai_compatible",
                        model_api_key="test-key", model_name="batch44-envelope",
                        model_base_url="https://example.com/v1/", model_output_mode="json_object")
    client = OpenAICompatibleClient(settings)
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "batch44", "object": "chat.completion",
            "created": 0, "model": settings.model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content":
                '{"answer":"密码 000219"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 15, "total_tokens": 215}})

    await client.client.close()
    from openai import AsyncOpenAI

    client.client = AsyncOpenAI(api_key="test-key", base_url=settings.model_base_url,
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    gateway = AgentModelClient(settings, client)
    messages = [{"role": "system", "content": "读取历史证据。"},
                {"role": "user", "content": "密码？"}]
    try:
        await gateway.generate(messages, Answer, output_limit=128)
    finally:
        await gateway.close()
    actual = captured[0]
    expected = schema_envelope(messages, Answer, None, provider=settings.model_provider,
                               output_mode=settings.model_output_mode)
    assert actual["messages"] == expected["messages"]
    assert actual["response_format"] == expected["response_format"]
    call = gateway.calls[0]
    assert call["transmitted_messages"] == actual["messages"]
    assert "JSON schema:" in actual["messages"][0]["content"]
    assert call["request_budget"]["actual_input_tokens"] == 200
    assert call["request_budget"]["output_reserve"] == 128


async def test_gateway_rejects_complete_overflow_and_calibrates_actual_usage():
    settings = Settings(_env_file=None, model_name="batch44-calibration",
                        model_context_limit=2048)
    adapter = FakeModelAdapter([ModelResponse(structured={"answer": "已记录"},
                                              token_usage={"input": 500, "output": 12})])
    gateway = AgentModelClient(settings, adapter)
    with pytest.raises(ModelError, match="上下文预算"):
        await gateway.generate([{"role": "system", "content": "长" * 3000}], Answer)
    assert not adapter.prompts
    await gateway.generate([{"role": "user", "content": "密码"}], Answer, output_limit=128)
    measured = measure_request(settings, [{"role": "user", "content": "密码"}], Answer,
                               output_limit=128)
    assert measured["calibration_samples"] == 1
    assert measured["calibration_factor"] > 1
    assert measured["input_tokens"] >= 500


async def test_explicit_8192_remains_strict_after_default_capacity_increase():
    assert Settings(_env_file=None).model_context_limit == 16384
    settings = Settings(_env_file=None, model_context_limit=8192,
                        model_name="batch44-explicit-small")
    adapter = FakeModelAdapter()
    client = AgentModelClient(settings, adapter)
    with pytest.raises(ModelError, match="上下文预算"):
        await client.generate([{"role": "user", "content": "原始记录" * 2000}], Answer)
    assert not adapter.prompts
    assert client.calls[0]["request_budget"]["token_limit"] == 8192


def test_available_cached_tokenizer_is_used_and_schema_keeps_constraints(monkeypatch):
    from app.models import budget

    class CachedTokenizer:
        def encode(self, text):
            return list(range(len(text) // 2))

    monkeypatch.setattr(budget, "_tokenizer", lambda _: CachedTokenizer())
    settings = Settings(_env_file=None, model_name="batch44-cached-tokenizer")
    measured = measure_request(settings, [{"role": "user", "content": "原文"}], Answer)
    assert measured["method"] == "cached_model_tokenizer"
    repeated = {"type": "string", "enum": ["必须保留的完整密码000219" * 8, "99.95"]}
    original = {"type": "object", "properties": {"a": repeated, "b": repeated},
                "required": ["a", "b"], "$defs": {"Unused": {"type": "string"}}}
    compacted = budget.compact_schema(original)
    assert "Unused" not in compacted["$defs"]
    assert compacted["required"] == ["a", "b"]
    assert compacted["properties"]["a"] == compacted["properties"]["b"]
    shared = compacted["properties"]["a"]["$ref"].removeprefix("#/$defs/")
    assert compacted["$defs"][shared] == repeated
    fields = budget.compact_schema({"type": "object", "properties": {
        "title": {"type": "string", "title": "Display title"},
        "default": {"type": "integer", "default": 17},
    }, "required": ["title", "default"]})
    assert fields["properties"] == {"title": {"type": "string"},
                                    "default": {"type": "integer"}}
    properties = {"type": {"type": "string", "enum": ["an-extremely-long-enum-value" * 8]},
                  "title": {"type": "string"}, "default": {"type": "integer", "minimum": 17}}
    nested = {"type": "object", "properties": properties, "required": list(properties)}
    duplicate = {"type": "object", "properties": {"first": nested, "second": nested},
                 "$defs": {"BudgetShared0": {"type": "boolean"}},
                 "allOf": [{"$ref": "#/$defs/BudgetShared0"}]}
    compacted = budget.compact_schema(duplicate)

    def expand(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(compacted["$defs"][value["$ref"].removeprefix("#/$defs/")])
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    assert compacted["$defs"]["BudgetShared0"] == {"type": "boolean"}
    assert expand(compacted["properties"]["first"])["properties"] == properties
    assert expand(compacted["properties"]["second"])["properties"] == properties


def test_final_role_messages_keep_exact_early_evidence_and_only_public_narration():
    public = {"kind": "clue", "text": "密码为 000219，阈值为 99.95。",
              "source": {"ref": "e3", "seq": 3, "scene": "旧站台", "visibility": "public"},
              "historical_only": True}
    private = {"kind": "hypothesis", "text": "私人HO暗号 112233。",
               "source": {"ref": "e4", "scene": "密室", "visibility": "private"}}
    task = {"kind": "pending_task", "text": "见到站长时询问钥匙。",
            "source": {"ref": "e5", "scene": "旧站台", "visibility": "public"}}
    context = {"memory_evidence": [public, private, task], "memory_audit": {"all_refs": ["secret"]},
               "triggering_action": {"type": "action.submitted", "payload": {"text": "输入密码"}},
               "response_brief": {"player_statement": "输入密码"},
               "prompt_budget_audit": {"omitted": ["secret audit"]}}
    for schema in (KeeperPlan, TeammateDecision):
        sent = json.loads(action_messages(context, schema, "处理当前行动")[1]["content"])
        assert sent["memory_evidence"] == [public, private, task]
        assert "memory_audit" not in sent and "prompt_budget_audit" not in sent
    sent = json.loads(action_messages(context, KeeperNarration, "公开回应")[1]["content"])
    assert "memory_evidence" not in sent
    assert sent["response_brief"]["historical_memory"] == [public, task]
    assert "112233" not in json.dumps(sent, ensure_ascii=False)
    assert "旧站台" in json.dumps(sent, ensure_ascii=False)
    assert "不是本轮执行回执" in action_messages(context, KeeperNarration, "公开回应")[0]["content"]


def test_compaction_keeps_or_omits_complete_source_never_truncates_numbers():
    full = "普通经过。" * 80 + "密码000219；阈值99.95；交给队员甲。"
    context = {"module": {"current_scene": {"summary": full}},
               "triggering_action": {"payload": {"text": "输入000219"}},
               "memory_evidence": [{"kind": "pending_task", "text": "询问站长",
                                    "source": {"ref": "e3"}}]}
    compacted = compact_planning_prose(context, 300)
    summary = compacted["module"]["current_scene"].get("summary")
    assert summary is None or summary == full
    assert compacted["triggering_action"] == context["triggering_action"]
    assert generation_prompt(compacted, KeeperPlan)["memory_evidence"] == context["memory_evidence"]


def test_segment_task_sources_outside_chunk_recheck_permissions_and_branch():
    from app.memory.segments import active_segments, make_segment, select_chunks, validate_segment

    def event(seq, text, visibility="public"):
        return {"seq": seq, "type": "chat.message", "payload": {"text": text},
                "visibility": visibility, "actor_member_id": "alice"}

    old, request = event(1, "经过"), event(5, "等列车到站后检查密码")
    events = [old, request]
    task = {"kind": "pending_task", "text": request["payload"]["text"],
            "source": {"seq": 5, "ref": "e5", "visibility": "public"}}
    _, chunks = select_chunks([old], {}, lambda _: True, all_events=events)
    doc = make_segment("经过；列车到站后仍需检查密码。", events, chunks, tasks=[task])
    memory = SimpleNamespace(kind="summary_segment", id="s1", active=True, scope="public",
                             content=json.dumps(doc))
    assert active_segments([memory], events)
    assert not active_segments([memory], [old])
    changed = [old, event(5, "废弃分支的新任务")]
    assert not active_segments([memory], changed)
    assert not active_segments([memory], [old, event(5, request["payload"]["text"], "host_only")])
    with pytest.raises(ValueError, match="Task source"):
        validate_segment(doc, changed)
    doc["summary"]["unfinished"].append({"kind": "short_term_goal", "text": "秘密目标",
                                         "source": {"visibility": "agent_private"}})
    memory.content = json.dumps(doc)
    assert not active_segments([memory], events)
