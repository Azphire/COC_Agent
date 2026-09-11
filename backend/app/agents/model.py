"""One serialized model gateway, including repairs in the cycle's call budget."""

import asyncio
import json
import time
from collections import deque
from weakref import WeakKeyDictionary

from pydantic import BaseModel, ValidationError

from app.models.base import ModelError, ModelResponse
from app.models.factory import create_model
from app.models.ollama import ModelFormatError, OllamaAgentAdapter, schema_issues

_semaphores = WeakKeyDictionary()


def model_semaphore():
    loop = asyncio.get_running_loop()
    if loop not in _semaphores:
        _semaphores[loop] = asyncio.Semaphore(1)
    return _semaphores[loop]


class AgentModelClient:
    def __init__(self, settings, adapter=None):
        self.settings, self.adapter = settings, adapter
        self.calls = []

    async def generate(
        self,
        messages,
        response_schema=None,
        tools=None,
        on_call=None,
        on_result=None,
        output_limit=None,
        validate_output=None,
        max_attempts=2,
    ):
        started = time.monotonic()
        if self.adapter is None:
            self.adapter = (
                OllamaAgentAdapter(self.settings)
                if self.settings.model_provider == "ollama"
                else create_model(self.settings)
            )
        prompt = list(messages)
        for attempt in range(max_attempts):
            async with model_semaphore():
                if on_call:
                    await on_call()
                call_started = time.monotonic()
                call_prompt = list(prompt)
                usage = None
                issues = []
                try:
                    async with asyncio.timeout(self.settings.model_timeout_seconds):
                        result = await self.adapter.generate(
                            prompt,
                            response_schema=response_schema,
                            tools=tools,
                            temperature=self.settings.model_temperature,
                            max_tokens=output_limit or self.settings.model_output_limit,
                        )
                    if not isinstance(result, ModelResponse):
                        raise ModelFormatError("模型输出格式无效")
                    usage = result.token_usage
                    if response_schema:
                        value = result.structured
                        if isinstance(value, BaseModel):
                            value = value.model_dump()
                        if value is None and result.tool_calls:
                            value = {
                                "tools": [
                                    {"name": c.name, "arguments": c.arguments}
                                    for c in result.tool_calls
                                ]
                            }
                        if value is None:
                            value = json.loads(result.text)
                        result.structured = response_schema.model_validate(value)
                    # Hidden reasoning is never accepted as content or as a tool argument.
                    serialized = result.model_dump_json().lower()
                    if any(
                        tag in serialized
                        for tag in ("<think", "</think", '"reasoning"', '"chain_of_thought"')
                    ):
                        raise ModelFormatError("模型输出包含不支持的字段")
                    if validate_output:
                        await validate_output(result.structured)
                    return result, int((time.monotonic() - started) * 1000)
                except (ValidationError, ValueError, ModelFormatError) as error:
                    issues = (
                        schema_issues(error, response_schema)
                        if isinstance(error, ValidationError)
                        else getattr(error, "issues", [])
                    )
                    if attempt + 1 >= max_attempts:
                        raise ModelFormatError("模型结构化输出在一次修复后仍无效") from None
                    prompt = list(messages) + [
                        {
                            "role": "user",
                            "content": "上次格式无效。只返回所给 JSON schema 的对象，"
                            "不输出推理。字段约束："
                            + json.dumps(
                                schema_issues(error, response_schema)
                                if isinstance(error, ValidationError)
                                else getattr(error, "issues", []),
                                ensure_ascii=False,
                            ),
                        }
                    ]
                except TimeoutError:
                    raise ModelError("模型请求超时") from None
                finally:
                    call = {
                        "provider": self.settings.model_provider,
                        "model": self.settings.model_name,
                        "latency_ms": int((time.monotonic() - call_started) * 1000),
                        "attempt": attempt + 1,
                        "token_usage": usage,
                        "validation_issues": issues,
                        "schema": response_schema.__name__ if response_schema else None,
                        "input_chars": len(json.dumps(call_prompt, ensure_ascii=False)),
                        "input_messages": call_prompt,
                    }
                    self.calls.append(call)
                    if on_result:
                        await on_result(call)

    async def close(self):
        if self.adapter:
            await self.adapter.close()


class FakeModelAdapter:
    """Explicitly injected only by tests/smoke launchers. Never contacts a provider."""

    def __init__(self, responses=(), responder=None):
        self.responses = deque(responses)
        self.responder = responder
        self.prompts = []
        self.active = 0
        self.max_active = 0

    async def generate(self, messages, **kwargs):
        self.prompts.append(list(messages))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            response = (
                self.responses.popleft()
                if self.responses
                else self.responder(messages, kwargs)
                if self.responder
                else {"tools": []}
            )
            if callable(response):
                response = response()
            if hasattr(response, "__await__"):
                response = await response
            if isinstance(response, Exception):
                raise response
            return (
                response
                if isinstance(response, ModelResponse)
                else ModelResponse(structured=response)
            )
        finally:
            self.active -= 1

    async def close(self):
        pass
