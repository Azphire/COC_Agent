from unittest.mock import AsyncMock

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.models.base import ModelError, ModelResponse
from app.models.factory import create_model
from app.models.openai_compatible import OpenAICompatibleClient


@pytest.mark.parametrize("provider", ["ollama", "openai"])
async def test_factory_builds_compatible_provider(provider: str) -> None:
    settings = Settings(_env_file=None, model_provider=provider, model_api_key="test-only")
    client = create_model(settings)
    try:
        assert isinstance(client, OpenAICompatibleClient)
        assert client.model == settings.model_name
        assert str(client.client.base_url) == settings.model_base_url
        assert client.client.max_retries == 0
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("configuration", "error"),
    [
        ({"model_provider": "unsupported"}, "Unknown model provider"),
        ({"model_base_url": "http://0.0.0.0:11434/v1/"}, "localhost"),
        ({"model_base_url": "https://example.com/v1/"}, "localhost"),
    ],
)
def test_factory_rejects_invalid_configuration(configuration: dict, error: str) -> None:
    with pytest.raises(ModelError, match=error):
        create_model(Settings(_env_file=None, **configuration))


@pytest.mark.parametrize("mode", ["plain", "tools", "structured"])
async def test_adapter_parses_responses(monkeypatch, mode: str) -> None:
    class Decision(BaseModel):
        model_config = ConfigDict(extra="forbid")
        action: str
        target: str | None

    with_tool = mode == "tools"
    content = '{"action":"观察","target":null}' if mode == "structured" else "连接成功"
    calls = [
        {
            "id": "call_test",
            "type": "function",
            "function": {"name": "roll_dice", "arguments": '{"sides":100,"count":1}'},
        }
    ] if with_tool else []
    completion = ChatCompletion.model_validate(
        {
            "id": "test-completion",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen3:8b",
            "choices": [{
                "index": 0,
                "finish_reason": "tool_calls" if with_tool else "stop",
                "message": {
                    "role": "assistant", "content": None if with_tool else content,
                    "tool_calls": calls,
                },
            }],
        }
    )
    client = create_model(Settings(_env_file=None))
    create = AsyncMock(return_value=completion)
    monkeypatch.setattr(client.client.chat.completions, "create", create)
    tools = [{"type": "function", "function": {"name": "roll_dice"}}] if with_tool else None
    try:
        result = await client.generate(
            [{"role": "user", "content": "测试"}], tools=tools,
            response_schema=Decision if mode == "structured" else None,
        )
        assert isinstance(result, ModelResponse)
        assert create.await_count == 1
        assert create.call_args.kwargs["reasoning_effort"] == "none"
        if with_tool:
            assert create.call_args.kwargs["tools"] == tools
            assert result.tool_calls[0].id == "call_test"
            assert result.tool_calls[0].name == "roll_dice"
            assert result.tool_calls[0].arguments == {"sides": 100, "count": 1}
        elif mode == "structured":
            assert isinstance(result.structured, Decision)
            assert result.model_dump()["structured"] == {"action": "观察", "target": None}
            specification = create.call_args.kwargs["response_format"]["json_schema"]
            assert specification["schema"] == Decision.model_json_schema()
            assert specification["strict"] is True
        else:
            assert result.text == "连接成功"
            assert result.tool_calls == []
    finally:
        await client.close()


@pytest.mark.parametrize("fail", [False, True])
async def test_text_stream_closes_and_normalizes_errors(monkeypatch, fail: bool) -> None:
    class FakeStream:
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def __aiter__(self):
            yield ChatCompletionChunk.model_validate({
                "id": "stream-test", "object": "chat.completion.chunk", "created": 0,
                "model": "qwen3:8b",
                "choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}],
            })
            if fail:
                raise RuntimeError("sensitive provider detail")

    chunks = FakeStream()
    client = create_model(Settings(_env_file=None))
    create = AsyncMock(return_value=chunks)
    monkeypatch.setattr(client.client.chat.completions, "create", create)
    try:
        stream = await client.generate([{"role": "user", "content": "测试"}], stream=True)
        if fail:
            with pytest.raises(ModelError, match="Model stream failed") as error:
                async for _ in stream:
                    pass
            assert "sensitive" not in str(error.value)
        else:
            assert [chunk async for chunk in stream] == ["你好"]
        assert chunks.closed
        assert create.await_count == 1
        assert create.call_args.kwargs["stream"] is True
    finally:
        await client.close()
