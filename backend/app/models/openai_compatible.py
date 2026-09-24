import json
import re
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
    ContentCallback,
    Message,
    ModelError,
    ModelFormatError,
    ModelResponse,
    ResponseSchema,
    Tool,
    ToolCall,
)
from app.models.budget import schema_envelope
from app.models.credentials import resolve_credential, usable
from app.models.ollama import schema_issues


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


def safe_identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[\w.:-]{1,200}", value) else None


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.model_name
        self.local = settings.model_provider == "ollama"
        self.output_mode = "json_schema" if self.local else settings.model_output_mode
        credential = resolve_credential(settings)
        if not self.local and not usable(credential.key):
            raise ModelError("模型未配置有效 API 密钥")
        self.client = AsyncOpenAI(
            base_url=settings.model_base_url,
            api_key=credential.key.get_secret_value(),
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
        on_delta: ContentCallback | None = None,
    ) -> ModelResponse | AsyncIterator[str]:
        if stream and (tools or response_schema is not None):
            raise ModelError("Streaming supports text only; use non-streaming for tools or JSON")
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
            "stream": stream or on_delta is not None,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.local:
            # Keep the initial local integration short and deterministic.
            request["reasoning_effort"] = "none"
        request.update(schema_envelope(
            messages, response_schema, tools,
            provider="openai_compatible", output_mode=self.output_mode,
        ))
        try:
            completion = await self.client.chat.completions.create(**request)
        except OpenAIError as error:
            failure = ModelError(request_error(error))
            failure.error_category = type(error).__name__
            failure.request_id = safe_identifier(getattr(error, "request_id", None))
            raise failure from None
        if on_delta is not None:
            completion = await self._structured_stream(completion, on_delta)
        elif stream:
            return self._text_stream(completion)
        try:
            return self._parse_response(completion, response_schema)
        except ModelError as error:
            error.request_id = safe_identifier(getattr(completion, "_request_id", None))
            error.response_model = safe_identifier(completion.model)
            raise

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
                if "answer_parts" in getattr(response_schema, "model_fields", {}):
                    from app.agents.answer_parts import decode_answer_parts_document

                    structured = response_schema.model_validate(
                        decode_answer_parts_document(message.content or ""),
                    )
                else:
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
                request_id=safe_identifier(getattr(completion, "_request_id", None)),
                response_model=safe_identifier(completion.model),
            )
        except (ValidationError, ModelFormatError) as error:
            failure = error if isinstance(error, ModelFormatError) else ModelFormatError(
                "模型 JSON 或工具参数不符合约束", schema_issues(error, response_schema), usage,
            )
            failure.token_usage = usage
            if "answer_parts" in getattr(response_schema, "model_fields", {}):
                content = message.content or ""
                if not any(tag in content.lower() for tag in (
                    "<think", "</think", '"reasoning"', '"chain_of_thought"', '"thinking"',
                )):
                    from app.agents.answer_parts import decode_answer_parts_document

                    failure.raw_output_text = content
                    try:
                        failure.generated_output = decode_answer_parts_document(content)
                    except (ValueError, ModelFormatError):
                        failure.generated_output = {"unparsed_text": content}
            raise failure from None
        except (ValueError, TypeError):
            raise ModelFormatError("模型 JSON 或工具参数格式无效", token_usage=usage) from None

    @staticmethod
    async def _structured_stream(chunks, on_delta):
        """Collect the same response while forwarding only the content channel."""
        content, tool_calls = [], {}
        finish_reason = usage = model = completion_id = refusal = None
        total_chars = 0
        try:
            async with chunks:
                async for chunk in chunks:
                    model, completion_id = chunk.model, chunk.id
                    if chunk.usage is not None:
                        usage = chunk.usage.model_dump()
                    if not chunk.choices:
                        await on_delta("")
                        continue
                    choice = next((c for c in chunk.choices if c.index == 0), None)
                    if choice is None:
                        continue
                    delta = choice.delta
                    text = delta.content or ""
                    total_chars += len(text)
                    if total_chars > 262144:
                        raise ModelFormatError("模型流超过缓冲上限")
                    content.append(text)
                    if delta.refusal:
                        refusal = "refused"
                    for item in delta.tool_calls or []:
                        call = tool_calls.setdefault(item.index, {
                            "id": "", "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if item.id:
                            call["id"] += item.id
                        if item.type and item.type != "function":
                            raise ModelFormatError("模型返回了不支持的工具类型")
                        if item.function:
                            call["function"]["name"] += item.function.name or ""
                            call["function"]["arguments"] += item.function.arguments or ""
                    # SDK extensions such as reasoning_content are never forwarded.
                    await on_delta(text)
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
        except ModelError:
            raise
        except OpenAIError as error:
            failure = ModelError(request_error(error))
            failure.error_category = type(error).__name__
            failure.request_id = safe_identifier(getattr(error, "request_id", None))
            raise failure from None
        except Exception as error:
            raise ModelError(f"Model stream failed ({type(error).__name__})") from None
        if finish_reason is None:
            raise ModelError("模型流在结束状态前中断")
        completion = ChatCompletion.model_validate({
            "id": completion_id or "stream", "model": model or "unknown",
            "created": 0, "object": "chat.completion", "usage": usage,
            "choices": [{
                "index": 0, "finish_reason": finish_reason,
                "message": {
                    "role": "assistant", "content": "".join(content),
                    "refusal": refusal,
                    "tool_calls": [tool_calls[index] for index in sorted(tool_calls)] or None,
                },
            }],
        })
        response = getattr(chunks, "response", None)
        if response is not None:
            completion._request_id = safe_identifier(response.headers.get("x-request-id"))
        return completion

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
