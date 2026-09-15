import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AsyncStream,
    DefaultAsyncHttpxClient,
    OpenAIError,
)
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from pydantic import ValidationError

from app.config import Settings
from app.models.base import (
    Message,
    ModelError,
    ModelFormatError,
    ModelResponse,
    ResponseSchema,
    Tool,
    ToolCall,
)
from app.models.ollama import generation_schema, schema_issues


def strict_schema(value):
    """Translate closed game objects; never silently close an arbitrary dictionary."""
    if isinstance(value, list):
        return [strict_schema(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {
        k: strict_schema(v)
        for k, v in value.items()
        if k not in {"default", "discriminator"} and not k.startswith("x-")
    }
    if result.get("type") == "object":
        if "properties" not in result and result.get("additionalProperties") is not False:
            raise ModelError("此动态结构包含开放字典，请在模型设置选择 JSON 模式")
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    if "oneOf" in result:
        result["anyOf"] = result.pop("oneOf")
    return result


def request_error(error):
    if isinstance(error, APITimeoutError):
        return "模型请求超时，请稍后重试"
    if isinstance(error, APIConnectionError):
        return "无法连接模型服务，请检查地址与网络"
    if isinstance(error, APIStatusError):
        status = error.status_code
        if status in {401, 403}:
            return "模型服务认证失败，请检查密钥及模型访问权限"
        if status == 429:
            return "模型服务限流或额度不足，请稍后重试并检查平台额度"
        if status == 404:
            return "模型或接口不存在，请检查服务地址与模型名称"
        if status in {400, 422}:
            return "模型服务不接受请求参数，请核对模型与结构化输出模式"
    return "模型服务请求失败，请稍后重试"


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.model_name
        self.local = settings.model_provider == "ollama"
        self.output_mode = "json_schema" if self.local else settings.model_output_mode
        self.client = AsyncOpenAI(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key.get_secret_value() or ("ollama" if self.local else ""),
            max_retries=0,
            timeout=settings.model_timeout_seconds,
            http_client=DefaultAsyncHttpxClient(
                trust_env=not self.local,
                follow_redirects=False,
            ),
        )

    async def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        response_schema: ResponseSchema | None = None,
        stream: bool = False,
        *,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> ModelResponse | AsyncIterator[str]:
        if stream and (tools or response_schema is not None):
            raise ModelError("Streaming supports text only; use non-streaming for tools or JSON")
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.local:
            # Keep the initial local integration short and deterministic.
            request["reasoning_effort"] = "none"
        if tools:
            request["tools"] = [dict(tool) for tool in tools]
        if response_schema is not None:
            schema = generation_schema(
                response_schema.model_json_schema()
                if isinstance(response_schema, type)
                else dict(response_schema)
            )
            if self.output_mode == "json_object":
                request["response_format"] = {"type": "json_object"}
                request["messages"] = [
                    {
                        "role": "system",
                        "content": "Return only a JSON object matching this schema. "
                        "Do not output reasoning. JSON schema: "
                        + json.dumps(schema, ensure_ascii=False),
                    },
                    *request["messages"],
                ]
            else:
                request["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "model_response",
                        "strict": True,
                        "schema": strict_schema(schema),
                    },
                }
        try:
            completion = await self.client.chat.completions.create(**request)
        except OpenAIError as error:
            raise ModelError(request_error(error)) from None
        if stream:
            return self._text_stream(completion)
        return self._parse_response(completion, response_schema)

    @staticmethod
    def _parse_response(
        completion: ChatCompletion, response_schema: ResponseSchema | None
    ) -> ModelResponse:
        usage = (
            {
                "input": completion.usage.prompt_tokens,
                "output": completion.usage.completion_tokens,
                "total": completion.usage.total_tokens,
            }
            if completion.usage
            else None
        )
        if not completion.choices:
            raise ModelFormatError("模型未返回候选结果", token_usage=usage)
        choice = completion.choices[0]
        message = choice.message
        if message.refusal or choice.finish_reason == "content_filter":
            error = ModelError("模型服务拒绝此次生成，请调整输入后重试")
            error.token_usage = usage
            raise error
        try:
            calls = []
            for call in message.tool_calls or []:
                if call.type != "function":
                    raise ModelFormatError("模型返回了不支持的工具类型", token_usage=usage)
                calls.append(
                    ToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=json.loads(call.function.arguments),
                    )
                )
            structured = None
            if response_schema is not None:
                if choice.finish_reason == "length":
                    raise ModelFormatError("结构化输出超过 token 上限", token_usage=usage)
                structured = (
                    response_schema.model_validate_json(message.content or "")
                    if isinstance(response_schema, type)
                    else json.loads(message.content or "")
                )
            return ModelResponse(
                text=message.content or "",
                tool_calls=calls,
                structured=structured,
                finish_reason=choice.finish_reason,
                token_usage=usage,
            )
        except ValidationError as error:
            raise ModelFormatError(
                "模型 JSON 或工具参数不符合约束", schema_issues(error, response_schema), usage
            ) from None
        except (ValueError, TypeError):
            raise ModelFormatError("模型 JSON 或工具参数格式无效", token_usage=usage) from None

    @staticmethod
    async def _text_stream(chunks: AsyncStream[ChatCompletionChunk]) -> AsyncIterator[str]:
        try:
            async with chunks:
                async for chunk in chunks:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
        except Exception as error:
            # Transport/decoding errors can occur after the SDK yields the stream.
            # Cancellation and GeneratorExit remain unmodified (BaseException).
            raise ModelError(f"Model stream failed ({type(error).__name__})") from None

    async def close(self) -> None:
        await self.client.close()
