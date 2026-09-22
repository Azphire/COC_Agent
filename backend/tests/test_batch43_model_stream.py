import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from openai.types.chat import ChatCompletionChunk
from pydantic import BaseModel, ConfigDict

from app.agents.model import AgentModelClient, FakeModelAdapter
from app.agents.stream_json import IncrementalJSONObjectString
from app.config import Settings
from app.models.base import ModelError, ModelFormatError
from app.models.ollama import OllamaAgentAdapter, generation_schema
from app.models.openai_compatible import OpenAICompatibleClient


class PublicBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observed_detail: str


@pytest.mark.parametrize("observation", [True, False])
def test_schema_generates_existing_public_body_before_detail_copies(observation):
    from app.agents.adjudication_schemas import KeeperNarration
    from app.agents.generation_contracts import generation_contract

    contract = generation_contract(KeeperNarration, {
        "current_scene_reference": "scene", "response_brief": {
            "ordinary_observation": observation, "responder": {"kind": "keeper"},
        },
    })
    source = contract.model_json_schema()
    schema = generation_schema(source)
    body = "observed_detail" if observation else "public_narration"
    assert next(iter(schema["properties"])) == schema["required"][0] == body
    # Reordering saves waiting for incidental copies; it must not broaden the
    # grammar, create new fields, or expose any server-bound metadata.
    assert set(schema["properties"]) == {
        name for name, field in source["properties"].items()
        if not field.get("x-server-bound")
    }
    assert schema["properties"][body]["type"] == "string"


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 1000])
def test_decoder_exact_top_level_field_and_cross_chunk_escapes(chunk_size):
    document = {
        "thinking": '私密\"observed_detail\":\"HO秘密\"',
        "nested": [{"observed_detail": "不可发布"}],
        "count": 1,
        "observed_detail": '看到门上的字："入口"。\n又看到脚印。😀',
        "tail": None,
    }
    raw = json.dumps(document, ensure_ascii=True)
    parser = IncrementalJSONObjectString("observed_detail")
    output = "".join(parser.feed(raw[i:i + chunk_size]) for i in range(0, len(raw), chunk_size))
    assert output == document["observed_detail"]
    assert parser.value == output
    assert parser.closed and parser.complete and parser.finish()
    assert "秘密" not in output and "不可发布" not in output


@pytest.mark.parametrize("raw", [
    '{"observed_detail":12}',
    '{"observed_detail":"first","observed_detail":"second"}',
    '{"observed_detail":"valid"} private-tail',
    '{"observed_detail":"bad\\x"}',
    '{"observed_detail":"bad\\ud800"}',
    '{"observed_detail":"bad\\udc00"}',
    '{"nested":{"observed_detail":"private"}}',
    '{"observed_detail":"valid",}',
    '{"bad":invalid,"observed_detail":"must not emit"}',
    '[{"observed_detail":"private"}]',
])
def test_decoder_rejects_malformed_or_ambiguous_json(raw):
    parser = IncrementalJSONObjectString("observed_detail")
    assert parser.feed(raw) == ""
    assert parser.invalid
    assert not parser.finish()


def test_decoder_incomplete_strings_and_bounded_buffer():
    parser = IncrementalJSONObjectString("observed_detail", max_chars=40)
    assert parser.feed('{"observed_detail":"公开。') == "公开。"
    assert not parser.closed and not parser.complete
    assert not parser.finish()
    parser = IncrementalJSONObjectString("observed_detail", max_chars=5)
    assert parser.feed('{"observed_detail":"私密"}') == ""
    assert parser.invalid


class NDJSONStream(httpx.AsyncByteStream):
    def __init__(self, chunks, gate=None, fail=None):
        self.chunks, self.gate, self.fail = chunks, gate, fail
        self.closed = False

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            if index == 1 and self.gate:
                await self.gate.wait()
            data = (json.dumps(chunk, ensure_ascii=False) + "\n").encode()
            # HTTP byte chunks need not align with JSON or UTF-8 characters.
            for start in range(0, len(data), 3):
                yield data[start:start + 3]
        if self.fail:
            raise self.fail

    async def aclose(self):
        self.closed = True


async def ollama_adapter(stream, settings=None):
    adapter = OllamaAgentAdapter(settings or Settings(_env_file=None))
    await adapter.client.aclose()
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=stream)

    adapter.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(respond),
    )
    return adapter, requests


async def test_ollama_stream_emits_before_completion_and_never_thinking():
    gate, received = asyncio.Event(), asyncio.Event()
    wire = NDJSONStream([
        {"message": {"content": '{"observed_detail":"公开。', "thinking": "HO-secret"}},
        {"message": {"content": '还有一句。"}'}, "done": True, "done_reason": "stop",
         "prompt_eval_count": 10, "eval_count": 20},
    ], gate=gate)
    adapter, requests = await ollama_adapter(wire)
    gateway = AgentModelClient(Settings(_env_file=None), adapter)
    events = []

    async def event(value):
        events.append(value)
        if value["type"] == "delta":
            received.set()

    task = asyncio.create_task(gateway.generate([], response_schema=PublicBody, on_stream=event))
    try:
        await asyncio.wait_for(received.wait(), 1)
        assert not task.done()
        gate.set()
        response, _ = await task
        assert response.structured.observed_detail == "公开。还有一句。"
        assert response.token_usage == {"input": 10, "output": 20}
        assert "HO-secret" not in json.dumps(events)
        assert [e["type"] for e in events] == ["start", "delta", "delta", "finish"]
        assert len(requests) == 1 and requests[0]["stream"] is True
        call = gateway.calls[0]
        assert call["request_started_at"] <= call["first_chunk_at"] <= call["model_finished_at"]
        assert wire.closed
    finally:
        await gateway.close()


@pytest.mark.parametrize("termination", ["cancel", "timeout"])
async def test_cancel_and_timeout_close_model_connection_without_retry(termination):
    received = asyncio.Event()
    settings = Settings(_env_file=None, model_timeout_seconds=0.1)
    wire = NDJSONStream([
        {"message": {"content": '{"observed_detail":"公开。'}},
        {"message": {"content": '不能迟到。"}'}, "done": True},
    ], gate=asyncio.Event())
    adapter, requests = await ollama_adapter(wire, settings)
    gateway = AgentModelClient(settings, adapter)
    events = []

    async def event(value):
        events.append(value)
        if value["type"] == "delta":
            received.set()

    task = asyncio.create_task(gateway.generate([], response_schema=PublicBody, on_stream=event))
    await received.wait()
    if termination == "cancel":
        task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError if termination == "cancel" else ModelError):
            await task
        assert wire.closed and len(requests) == 1
        assert events[-1]["type"] == "error" and not events[-1]["retrying"]
        assert "finish" not in [e["type"] for e in events]
        assert "不能迟到" not in json.dumps(events, ensure_ascii=False)
    finally:
        await gateway.close()


@pytest.mark.parametrize("ending", ["missing", "length", "failure"])
async def test_ollama_rejects_incomplete_or_failed_stream(ending):
    chunks = [{"message": {"content": '{"observed_detail":"公开。"}'}}]
    if ending == "length":
        chunks.append({"done": True, "done_reason": "length", "message": {"content": ""}})
    wire = NDJSONStream(
        chunks, fail=httpx.ReadError("private transport") if ending == "failure" else None,
    )
    adapter, _ = await ollama_adapter(wire)
    try:
        with pytest.raises(ModelError) as error:
            await adapter.generate([], response_schema=PublicBody, on_delta=AsyncMock())
        assert "private" not in str(error.value)
        assert wire.closed
    finally:
        await adapter.close()


class OpenAIStream:
    def __init__(self, texts, finish="stop"):
        self.texts, self.finish = texts, finish
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def __aiter__(self):
        for text in self.texts:
            yield ChatCompletionChunk.model_validate({
                "id": "stream-test", "object": "chat.completion.chunk", "created": 0,
                "model": "model-test", "choices": [{
                    "index": 0, "delta": {"content": text, "reasoning_content": "HO-secret"},
                    "finish_reason": None,
                }],
            })
        if self.finish:
            yield ChatCompletionChunk.model_validate({
                "id": "stream-test", "object": "chat.completion.chunk", "created": 0,
                "model": "model-test", "choices": [{
                    "index": 0, "delta": {}, "finish_reason": self.finish,
                }], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            })


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
async def test_openai_stream_reuses_structured_contract(monkeypatch, mode):
    client = OpenAICompatibleClient(Settings(
        _env_file=None, model_provider="openai", model_api_key="test-only", model_output_mode=mode,
    ))
    stream = OpenAIStream(['{"observed_detail":"第一句。', '第二句。"}'])
    create = AsyncMock(return_value=stream)
    monkeypatch.setattr(client.client.chat.completions, "create", create)
    delta = AsyncMock()
    try:
        response = await client.generate([], response_schema=PublicBody, on_delta=delta)
        assert response.structured.observed_detail == "第一句。第二句。"
        assert response.finish_reason == "stop"
        assert response.token_usage["total"] == 30
        assert "HO-secret" not in str(delta.call_args_list)
        assert create.await_count == 1 and create.call_args.kwargs["stream"] is True
        assert create.call_args.kwargs["response_format"]["type"] == mode
        assert stream.closed
    finally:
        await client.close()


@pytest.mark.parametrize("finish", [None, "length", "content_filter"])
async def test_openai_stream_checks_finish_status(monkeypatch, finish):
    client = OpenAICompatibleClient(Settings(_env_file=None))
    stream = OpenAIStream(['{"observed_detail":"公开。"}'], finish=finish)
    monkeypatch.setattr(client.client.chat.completions, "create", AsyncMock(return_value=stream))
    try:
        with pytest.raises(ModelError):
            await client.generate([], response_schema=PublicBody, on_delta=AsyncMock())
        assert stream.closed
    finally:
        await client.close()


async def test_gateway_retry_budget_and_fake_does_not_fabricate_deltas():
    adapter = FakeModelAdapter([
        ModelFormatError("bad"), {"observed_detail": "公开。"},
    ])
    gateway = AgentModelClient(Settings(_env_file=None), adapter)
    callback = AsyncMock()
    response, _ = await gateway.generate([], response_schema=PublicBody, on_stream=callback)
    assert response.structured.observed_detail == "公开。"
    assert len(gateway.calls) == 2 and len(adapter.prompts) == 2
    events = [call.args[0] for call in callback.call_args_list]
    assert [e["type"] for e in events] == ["start", "error", "start", "finish"]
    assert [e["attempt"] for e in events] == [1, 1, 2, 2]
    assert events[1]["retrying"]
    assert gateway.calls[0]["first_chunk_at"] is None


async def test_ollama_thinking_only_first_chunk_records_time_without_content_event():
    wire = NDJSONStream([
        {"message": {"thinking": "private thinking only"}},
        {"message": {"content": '{"observed_detail":"公开正文。"}'},
         "done": True, "done_reason": "stop"},
    ])
    adapter, _ = await ollama_adapter(wire)
    gateway = AgentModelClient(Settings(_env_file=None), adapter)
    callback = AsyncMock()
    try:
        await gateway.generate([], response_schema=PublicBody, on_stream=callback)
        events = [call.args[0] for call in callback.call_args_list]
        assert [event["type"] for event in events] == ["start", "delta", "finish"]
        assert gateway.calls[0]["first_chunk_at"] <= events[1]["at"]
        assert "private thinking" not in json.dumps(events)
    finally:
        await gateway.close()


async def test_native_stream_length_repair_uses_same_two_attempt_budget():
    wires = [NDJSONStream([{
        "message": {"content": '{"observed_detail":"公开正文。"}'},
        "done": True, "done_reason": reason,
    }]) for reason in ("length", "stop")]
    settings = Settings(_env_file=None)
    adapter = OllamaAgentAdapter(settings)
    await adapter.client.aclose()
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=wires[len(requests) - 1])

    adapter.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(respond),
    )
    gateway = AgentModelClient(settings, adapter)
    callback, budget = AsyncMock(), AsyncMock()
    try:
        result, _ = await gateway.generate(
            [], response_schema=PublicBody, on_stream=callback, on_call=budget,
        )
        assert result.structured.observed_detail == "公开正文。"
        assert len(requests) == len(gateway.calls) == budget.await_count == 2
        assert all(request["stream"] for request in requests)
        assert all(wire.closed for wire in wires)
        events = [call.args[0] for call in callback.call_args_list]
        assert [event["type"] for event in events] == [
            "start", "delta", "error", "start", "delta", "finish",
        ]
        assert events[2]["retrying"]
    finally:
        await gateway.close()
