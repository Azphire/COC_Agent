"""Ollama transport for Agent calls requiring per-request context and keep-alive.

Implements the existing model protocol. The graph never sees this transport.
"""

import json

import httpx
from pydantic import BaseModel, ValidationError

from app.models.base import ModelError, ModelResponse, ToolCall
from app.models.factory import local_ollama_origin


class ModelFormatError(ModelError):
    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = issues or []


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
        explicit = [
            name
            for name, prop in value.get("properties", {}).items()
            if prop.get("x-explicit-output")
        ]
        if explicit:
            # Require an explicit object or null for critical action decisions.
            # Pydantic defaults keep old persisted plans readable.
            result["required"] = list(dict.fromkeys([*result.get("required", []), *explicit]))
        return result
    if isinstance(value, list):
        return [generation_schema(v) for v in value]
    return value


class OllamaAgentAdapter:
    def __init__(self, settings):
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
    ):
        if stream:
            raise ModelError("Agent transport uses complete validated outputs")
        request = {
            "model": self.settings.model_name,
            "messages": list(messages),
            "stream": False,
            "think": self.settings.model_think,
            "keep_alive": self.settings.model_keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self.settings.model_context_limit,
            },
        }
        if response_schema:
            request["format"] = generation_schema(
                response_schema.model_json_schema()
                if isinstance(response_schema, type)
                else response_schema
            )
        if tools:
            request["tools"] = list(tools)
        try:
            response = await self.client.post("/api/chat", json=request)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as error:
            message = error.response.text.lower()
            if "out of memory" in message or "cuda error" in message:
                raise ModelError("本地模型显存不足（OOM），请停止真实模型验收") from None
            if "grammar" in message:
                raise ModelError("本地模型不支持此次结构化生成语法") from None
            raise ModelError("本地模型请求失败，请检查 Ollama 状态") from None
        except (httpx.HTTPError, ValueError):
            raise ModelError("本地模型请求失败，请检查 Ollama 状态") from None
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
                token_usage={
                    "input": data.get("prompt_eval_count"),
                    "output": data.get("eval_count"),
                },
            )
        except ValidationError as error:
            raise ModelFormatError(
                "模型输出格式无效", schema_issues(error, response_schema)
            ) from None
        except (KeyError, TypeError, ValueError):
            raise ModelFormatError("模型输出格式无效") from None

    async def close(self):
        await self.client.aclose()
