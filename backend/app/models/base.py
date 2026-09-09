from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field, SerializeAsAny

type Message = Mapping[str, Any]
type Tool = Mapping[str, Any]
type ResponseSchema = type[BaseModel] | Mapping[str, Any]


class ModelError(Exception):
    """A short, provider-independent configuration or model request failure."""


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ModelResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    structured: dict[str, Any] | SerializeAsAny[BaseModel] | None = None
    finish_reason: str | None = None
    token_usage: dict[str, int | None] | None = None


class ModelClient(Protocol):
    """Stateless generation; streaming returns text chunks, never executed tools."""

    async def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        response_schema: ResponseSchema | None = None,
        stream: bool = False,
        *,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> ModelResponse | AsyncIterator[str]: ...

    async def close(self) -> None: ...
