"""One serialized model gateway, including repairs in the cycle's call budget."""

import asyncio
import json
import time
from collections import deque
from contextlib import nullcontext
from weakref import WeakKeyDictionary

from pydantic import BaseModel, ValidationError

from app.models.base import ModelError, ModelFormatError, ModelResponse
from app.models.budget import (
    calibrate_usage,
    compact_schema,
    measure_request,
    require_request_fit,
    schema_envelope,
)
from app.models.factory import create_model
from app.models.ollama import OllamaAgentAdapter, generation_schema, schema_issues

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
        self.configuration = None

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
        on_stream=None,
        prepare_retry=None,
    ):
        guard = (
            self.configuration.operation("模型生成（含排队及格式修复）")
            if self.configuration
            else nullcontext()
        )
        with guard:
            if self.configuration and not self.configuration.ready():
                raise ModelError("模型未配置，请打开系统状态完成模型设置")
            try:
                return await self._generate(
                    messages,
                    response_schema,
                    tools,
                    on_call,
                    on_result,
                    output_limit,
                    validate_output,
                    max_attempts,
                    on_stream,
                    prepare_retry,
                )
            except ModelError as error:
                if self.configuration:
                    self.configuration.error = str(error)
                    self.configuration.verification = "failed"
                raise

    async def _generate(
        self,
        messages,
        response_schema,
        tools,
        on_call,
        on_result,
        output_limit,
        validate_output,
        max_attempts,
        on_stream,
        prepare_retry,
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
            queued_at = time.monotonic()
            async with model_semaphore():
                queue_ms = int((time.monotonic() - queued_at) * 1000)
                if on_call:
                    await on_call()
                call_started = time.monotonic()
                request_started_at = time.time()
                first_chunk_at = first_chunk_started = None
                model_finished_at = None
                call_prompt = list(prompt)
                usage = None
                issues = []
                request_id = response_model = error_category = None
                model_finished = None
                generated_output = None
                raw_output = None
                raw_output_text = None
                request_budget = measure_request(
                    self.settings, call_prompt, response_schema, tools, output_limit,
                )

                async def stream_event(kind, **values):
                    if on_stream:
                        await on_stream({
                            "type": kind, "attempt": attempt + 1, "at": time.time(), **values,
                        })

                async def content_delta(text):
                    nonlocal first_chunk_at, first_chunk_started
                    if first_chunk_at is None:
                        first_chunk_at = time.time()
                        first_chunk_started = time.monotonic()
                    if text:
                        await stream_event("delta", text=text)

                try:
                    require_request_fit(request_budget)
                    await stream_event("start")
                    async with asyncio.timeout(self.settings.model_timeout_seconds):
                        result = await self.adapter.generate(
                            prompt,
                            response_schema=response_schema,
                            tools=tools,
                            temperature=self.settings.model_temperature,
                            max_tokens=output_limit or self.settings.model_output_limit,
                            **({"on_delta": content_delta} if on_stream else {}),
                        )
                    model_finished = time.monotonic()
                    model_finished_at = time.time()
                    if not isinstance(result, ModelResponse):
                        raise ModelFormatError("模型输出格式无效")
                    usage = result.token_usage
                    if result.text and not any(
                        tag in result.text.lower()
                        for tag in ("<think", "</think", '"reasoning"', '"chain_of_thought"')
                    ):
                        raw_output_text = result.text
                        try:
                            if "answer_parts" in getattr(response_schema, "model_fields", {}):
                                from app.agents.answer_parts import decode_answer_parts_document

                                raw_output = decode_answer_parts_document(result.text)
                            else:
                                raw_output = json.loads(result.text)
                        except ValueError:
                            pass
                    generated_output = (
                        result.structured.model_dump(mode="json")
                        if isinstance(result.structured, BaseModel)
                        else result.structured
                    )
                    request_id, response_model = result.request_id, result.response_model
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
                    if self.configuration:
                        self.configuration.error = None
                        self.configuration.verification = "available"
                    await stream_event("finish")
                    return result, int((time.monotonic() - started) * 1000)
                except (ValidationError, ValueError, ModelFormatError) as error:
                    error_category = type(error).__name__
                    request_id = request_id or getattr(error, "request_id", None)
                    response_model = response_model or getattr(error, "response_model", None)
                    usage = usage or getattr(error, "token_usage", None)
                    generated_output = generated_output or getattr(error, "generated_output", None)
                    raw_output = raw_output or getattr(error, "generated_output", None)
                    raw_output_text = raw_output_text or getattr(error, "raw_output_text", None)
                    issues = (
                        schema_issues(error, response_schema)
                        if isinstance(error, ValidationError)
                        else getattr(error, "issues", [])
                    )
                    await stream_event(
                        "error", category=error_category, retrying=attempt + 1 < max_attempts,
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
                except ModelError as error:
                    error_category = getattr(error, "error_category", type(error).__name__)
                    request_id = request_id or getattr(error, "request_id", None)
                    response_model = response_model or getattr(error, "response_model", None)
                    usage = usage or getattr(error, "token_usage", None)
                    await stream_event("error", category=error_category, retrying=False)
                    raise
                except TimeoutError:
                    error_category = "TimeoutError"
                    await stream_event("error", category=error_category, retrying=False)
                    raise ModelError("模型请求超时") from None
                except asyncio.CancelledError:
                    error_category = "CancelledError"
                    await stream_event("error", category=error_category, retrying=False)
                    raise
                finally:
                    calibrate_usage(self.settings, request_budget, usage)
                    envelope = schema_envelope(
                        call_prompt, response_schema, tools,
                        provider=self.settings.model_provider,
                        output_mode=self.settings.model_output_mode,
                    )
                    call = {
                        "provider": self.settings.model_provider,
                        "model": self.settings.model_name,
                        "config_revision": self.configuration.revision if self.configuration else 0,
                        "latency_ms": int((time.monotonic() - call_started) * 1000),
                        "queue_wait_ms": queue_ms,
                        "model_elapsed_ms": int(
                            ((model_finished or time.monotonic()) - call_started) * 1000
                        ),
                        "request_started_at": request_started_at,
                        "first_chunk_at": first_chunk_at,
                        "first_chunk_ms": int((first_chunk_started - call_started) * 1000)
                        if first_chunk_started is not None else None,
                        "model_finished_at": model_finished_at,
                        "validation_ms": int((time.monotonic() - model_finished) * 1000)
                        if model_finished
                        else 0,
                        "attempt": attempt + 1,
                        "token_usage": usage,
                        "request_id": request_id,
                        "response_model": response_model,
                        "error_category": error_category,
                        "validation_issues": issues,
                        "schema": response_schema.__name__ if response_schema else None,
                        "output_contract": compact_schema(generation_schema(
                            response_schema.model_json_schema()))
                        if response_schema else None,
                        "input_chars": len(json.dumps(call_prompt, ensure_ascii=False)),
                        "input_messages": call_prompt,
                        "transmitted_messages": envelope["messages"],
                        "request_budget": request_budget,
                        "generated_output": generated_output,
                        "raw_output": raw_output,
                        "raw_output_text": raw_output_text,
                    }
                    self.calls.append(call)
                    if on_result:
                        await on_result(call)
                if prepare_retry and attempt + 1 < max_attempts:
                    response_schema, prompt = await prepare_retry(call, prompt)

    async def close(self):
        adapter, self.adapter = self.adapter, None
        if adapter:
            await adapter.close()


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
