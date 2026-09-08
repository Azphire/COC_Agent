import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import AsyncOpenAI, AsyncStream, DefaultAsyncHttpxClient, OpenAIError
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from app.config import Settings
from app.models.base import Message, ModelError, ModelResponse, ResponseSchema, Tool, ToolCall


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.model_name
        self.local = settings.model_provider == "ollama"
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
            schema = (
                response_schema.model_json_schema()
                if isinstance(response_schema, type)
                else dict(response_schema)
            )
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "model_response", "strict": True, "schema": schema},
            }
        try:
            completion = await self.client.chat.completions.create(**request)
        except OpenAIError as error:
            raise ModelError(f"Model request failed ({type(error).__name__})") from None
        if stream:
            return self._text_stream(completion)
        return self._parse_response(completion, response_schema)

    @staticmethod
    def _parse_response(
        completion: ChatCompletion, response_schema: ResponseSchema | None
    ) -> ModelResponse:
        if not completion.choices:
            raise ModelError("Model returned no choices")
        choice = completion.choices[0]
        message = choice.message
        try:
            calls = []
            for call in message.tool_calls or []:
                if call.type != "function":
                    raise ModelError("Model returned an unsupported tool type")
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
                    raise ModelError("Structured output exceeded the token limit")
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
            )
        except (ValueError, TypeError):
            raise ModelError("Model returned invalid JSON or tool arguments") from None

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
