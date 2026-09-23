"""Ollama transport for Agent calls requiring per-request context and keep-alive.

Implements the existing model protocol. The graph never sees this transport.
"""

import json

import httpx
from pydantic import BaseModel, ValidationError

from app.models.base import ModelError, ModelFormatError, ModelResponse, ToolCall


def schema_issues(error, schema):
    """Schema-defined paths and constraint codes only; never include model values."""
    allowed = set()

    def collect(value):
        if isinstance(value, dict):
            allowed.update(value.get("properties", {}))
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    if isinstance(schema, type) and issubclass(schema, BaseModel):
        collect(schema.model_json_schema())
    return [
        {
            "field": ".".join(
                str(p) if isinstance(p, int) or p in allowed else "unrecognized"
                for p in item["loc"]
            ),
            "code": item["type"],
            "limits": {
                k: v
                for k, v in item.get("ctx", {}).items()
                if k in {"le", "ge", "max_length", "min_length"} and isinstance(v, (int, float))
            },
        }
        for item in error.errors(include_input=False, include_url=False)[:5]
    ]


def generation_schema(value):
    """llama.cpp's bounded string grammar expands exponentially for long fields.

    Keep structural constraints in its grammar; Pydantic still enforces all bounds
    after generation. num_predict independently limits generated response length.
    """
    if isinstance(value, dict):
        result = {
            k: generation_schema(v)
            for k, v in value.items()
            if k not in {"maxLength", "x-explicit-output"}
        }
        if "properties" in result:
            hidden = {k for k, v in value["properties"].items() if v.get("x-server-bound")}
            result["properties"] = {
                k: v for k, v in result["properties"].items() if k not in hidden
            }
            result["required"] = [k for k in result.get("required", []) if k not in hidden]
        explicit = [
            name
            for name, prop in value.get("properties", {}).items()
            if prop.get("x-explicit-output") and not prop.get("x-server-bound")
        ]
        if explicit:
            # Require an explicit object or null for critical action decisions.
            # Pydantic defaults keep old persisted plans readable.
            result["required"] = list(dict.fromkeys([*result.get("required", []), *explicit]))
        properties = result.get("properties", {})
        body = next((name for name in ("observed_detail", "public_narration")
                     if name in properties), None)
        if body:
            # Providers often follow schema order. Produce the public body
            # before its incidental-detail copies, reducing the first-sentence
            # wait without changing any field, requirement or validation.
            result["properties"] = {body: properties[body], **properties}
            if body in result.get("required", []):
                result["required"] = [body, *[k for k in result["required"] if k != body]]
        return result
    if isinstance(value, list):
        return [generation_schema(v) for v in value]
    return value


class OllamaAgentAdapter:
    def __init__(self, settings):
        from app.models.factory import local_ollama_origin

        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=local_ollama_origin(settings.model_base_url),
            timeout=settings.model_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )

    async def generate(
        self,
        messages,
        tools=None,
        response_schema=None,
        stream=False,
        *,
        temperature=0.0,
        max_tokens=256,
        on_delta=None,
    ):
        if stream:
            raise ModelError("Agent 流式生成请使用 on_delta 回调")
        request = {
            "model": self.settings.model_name,
            "messages": list(messages),
            "stream": on_delta is not None,
            "think": self.settings.model_think,
            "keep_alive": self.settings.model_keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self.settings.model_context_limit,
            },
        }
        from app.models.budget import schema_envelope

        request.update(schema_envelope(
            messages, response_schema, tools, provider="ollama", output_mode="json_schema",
        ))
        if on_delta is not None:
            data = await self._stream_response(request, on_delta)
        else:
            data = await self._request_response(request)
        return self._parse_response(data, response_schema)

    async def _request_response(self, request):
        try:
            response = await self.client.post("/api/chat", json=request)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            message = error.response.text.lower()
            if "out of memory" in message or "cuda error" in message:
                raise ModelError("本地模型显存不足（OOM），请停止真实模型验收") from None
            if "grammar" in message:
                raise ModelError("本地模型不支持此次结构化生成语法") from None
            raise ModelError("本地模型请求失败，请检查 Ollama 状态") from None
        except (httpx.HTTPError, ValueError):
            raise ModelError("本地模型请求失败，请检查 Ollama 状态") from None

    async def _stream_response(self, request, on_delta):
        """Consume one NDJSON response; cancellation exits and closes its HTTP stream."""
        content, calls = [], []
        total_chars = 0
        terminal = None
        try:
            async with self.client.stream("POST", "/api/chat", json=request) as response:
                if response.is_error:
                    await response.aread()
                    response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        raise ModelFormatError("模型流格式无效")
                    if data.get("error"):
                        # The server may put errors in NDJSON after HTTP 200.
                        detail = str(data["error"]).lower()
                        if "out of memory" in detail or "cuda error" in detail:
                            raise ModelError("本地模型显存不足（OOM），请停止真实模型验收")
                        raise ModelError("本地模型流式请求失败")
                    message = data.get("message", {})
                    if not isinstance(message, dict):
                        raise ModelFormatError("模型流格式无效")
                    text = message.get("content", "")
                    if not isinstance(text, str):
                        raise ModelFormatError("模型流格式无效")
                    total_chars += len(text)
                    if total_chars > 262144:
                        raise ModelFormatError("模型流超过缓冲上限")
                    content.append(text)
                    calls.extend(message.get("tool_calls") or [])
                    # An empty callback marks the first wire chunk, including thinking-only
                    # chunks, without copying their private payload into any return value.
                    await on_delta(text)
                    if data.get("done") is True:
                        terminal = data
                        break
        except httpx.HTTPStatusError as error:
            detail = error.response.text.lower()
            if "out of memory" in detail or "cuda error" in detail:
                raise ModelError("本地模型显存不足（OOM），请停止真实模型验收") from None
            if "grammar" in detail:
                raise ModelError("本地模型不支持此次结构化生成语法") from None
            raise ModelError("本地模型请求失败，请检查 Ollama 状态") from None
        except (httpx.HTTPError, ValueError):
            raise ModelError("本地模型流式请求失败，请检查 Ollama 状态") from None
        if terminal is None:
            raise ModelError("本地模型流在结束标记前中断")
        return {
            **terminal,
            "message": {"content": "".join(content), "tool_calls": calls},
        }

    @staticmethod
    def _parse_response(data, response_schema):
        usage = {"input": data.get("prompt_eval_count"), "output": data.get("eval_count")}
        usage = usage if any(v is not None for v in usage.values()) else None
        try:
            message = data["message"]
            content = message.get("content", "")
            if data.get("done_reason") == "length" and response_schema:
                raise ValueError("truncated")
            structured = None
            if response_schema:
                structured = (
                    response_schema.model_validate_json(content)
                    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel)
                    else json.loads(content)
                )
            # Deliberately never copy message.thinking into any returned object.
            return ModelResponse(
                text=content,
                structured=structured,
                tool_calls=[
                    ToolCall(
                        id=f"call-{i}",
                        name=c["function"]["name"],
                        arguments=c["function"]["arguments"],
                    )
                    for i, c in enumerate(message.get("tool_calls", []))
                ],
                finish_reason=data.get("done_reason"),
                token_usage=usage,
            )
        except (ValidationError, ModelFormatError, KeyError, TypeError, ValueError) as error:
            failure = (
                error
                if isinstance(error, ModelFormatError)
                else ModelFormatError(
                    "模型输出格式无效",
                    schema_issues(error, response_schema)
                    if isinstance(error, ValidationError)
                    else [],
                )
            )
            failure.token_usage = usage
            # Preserve the answer for private auditing, including rejected JSON.
            # The separate thinking channel and reasoning-tagged content remain excluded.
            content = data.get("message", {}).get("content", "")
            if not any(
                tag in content.lower()
                for tag in ("<think", "</think", '"reasoning"', '"chain_of_thought"', '"thinking"')
            ):
                try:
                    failure.generated_output = json.loads(content)
                except ValueError:
                    failure.generated_output = {"unparsed_text": content}
            raise failure from None

    async def close(self):
        await self.client.aclose()
