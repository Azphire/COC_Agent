from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class ModelClient(Protocol):
    """Shared interface for future local and external model adapters."""

    async def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
        response_schema: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...
