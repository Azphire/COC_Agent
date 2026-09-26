"""Wire framing cannot change the safe prefix or invent generation-time evidence."""

import asyncio
import json
import time
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from openai.types.chat import ChatCompletionChunk
from pydantic import BaseModel
from test_batch43_model_stream import NDJSONStream, ollama_adapter
from test_batch50_parts_stream import (
    RESULT,
    RESULT_PART,
    UNKNOWN,
    UNKNOWN_PART,
    context,
    controller,
    encoded,
    feed,
    start,
)

from app.agents.model import AgentModelClient, FakeModelAdapter
from app.agents.stream_json import IncrementalJSONObjectArray
from app.config import Settings
from app.models.base import ModelError
from app.models.openai_compatible import OpenAICompatibleClient
from app.rooms.service import RoomError


@pytest.mark.parametrize("suffix", [
    "," + encoded({**UNKNOWN_PART, "text": "纸张没有夹层或折叠。"}) + "]}",
    "," + encoded({**UNKNOWN_PART, "text": "黑色祭坛下藏着秘密契约。"}) + "]}",
    "," + encoded(RESULT_PART) + "," + encoded(UNKNOWN_PART) + "]}",
    ',{"requirement_id":"r-unknown","text":"first","text":"second"}]}',
    ",null]}",
    '],"answer_parts":[]}',
    "]} bad-tail",
])
@pytest.mark.parametrize("split", [None, 1, 7, "object"])
async def test_safe_closed_prefix_identical_for_joined_or_split_invalid_documents(suffix, split):
    stream = controller()
    await start(stream)
    prefix = '{"answer_parts":[' + encoded(RESULT_PART)
    document = prefix + suffix
    if split == "object":
        chunks = [prefix, suffix]
    elif split:
        chunks = [document[i:i + split] for i in range(0, len(document), split)]
    else:
        chunks = [document]
    for chunk in chunks:
        await feed(stream, chunk)
    assert stream.accepted == RESULT
    assert not stream.eligible
    assert stream.buffered_reason in {"invalid_answer_part", "invalid_json"}
    assert len(stream.audit_metadata()["stream_segments"]) == 1


def test_decoder_preserves_closed_prefix_but_document_still_fails():
    raw = '{"answer_parts":[' + encoded(RESULT_PART) + ",null]}"
    decoder = IncrementalJSONObjectArray("answer_parts")
    assert decoder.feed_closed(raw) == [RESULT_PART]
    assert decoder.invalid and not decoder.finish()
    assert decoder.feed_closed(encoded(UNKNOWN_PART)) == []


@pytest.mark.parametrize("split", [False, True])
def test_raw_limit_also_preserves_the_same_closed_prefix(split):
    prefix = '{"answer_parts":[' + encoded(RESULT_PART)
    tail = "," + encoded(UNKNOWN_PART) + "]}"
    decoder = IncrementalJSONObjectArray("answer_parts", max_chars=len(prefix) + 1)
    released = []
    for chunk in [prefix, tail] if split else [prefix + tail]:
        released.extend(decoder.feed_closed(chunk))
    assert released == [RESULT_PART]
    assert decoder.invalid and not decoder.finish()


@pytest.mark.parametrize("split", [True, False])
async def test_later_cumulative_failure_keeps_same_safe_prefix(split):
    stream = controller()
    stream.runtime.validate_narration_output.side_effect = [None, RoomError("矛盾", 422)]
    await start(stream)
    fragments = ['{"answer_parts":[' + encoded(RESULT_PART), "," + encoded(UNKNOWN_PART) + "]}"]
    for text in fragments if split else ["".join(fragments)]:
        await feed(stream, text)
    assert stream.accepted == RESULT and not stream.eligible


@pytest.mark.parametrize("split", [True, False])
async def test_out_of_order_duplicate_cannot_jump_over_missing_prefix(split):
    stream = controller()
    await start(stream)
    fragments = ['{"answer_parts":[' + encoded(UNKNOWN_PART), "," + encoded(UNKNOWN_PART),
                 "," + encoded(RESULT_PART) + "]}"]
    for text in fragments if split else ["".join(fragments)]:
        await feed(stream, text)
    assert not stream.accepted and not stream.eligible


class PartsBody(BaseModel):
    answer_parts: list[dict]


async def test_two_model_parts_have_real_separate_receive_validate_publish_boundaries():
    gate = asyncio.Event()
    stream = controller()
    wire = NDJSONStream([
        {"message": {"content": '{"answer_parts":[' + encoded(RESULT_PART)}},
        {"message": {"content": "," + encoded(UNKNOWN_PART) + "]}"},
         "done": True, "done_reason": "stop"},
    ], gate=gate)
    adapter, _ = await ollama_adapter(wire)
    gateway = AgentModelClient(Settings(_env_file=None), adapter)
    first = asyncio.Event()

    async def on_stream(event):
        await stream(event)
        if stream.accepted == RESULT:
            first.set()

    task = asyncio.create_task(gateway.generate([], response_schema=PartsBody, on_stream=on_stream))
    try:
        await asyncio.wait_for(first.wait(), 1)
        assert not task.done()
        first_snapshot = deepcopy(stream.hub.stream_snapshot("r"))
        assert first_snapshot["data"]["text"] == RESULT
        assert first_snapshot["data"]["status"] == "responding"
        # Windows wall-clock granularity can place successive gate operations
        # at one timestamp; keep this controlled generation window measurable.
        await asyncio.sleep(0.02)
        gate.set()
        await task
        call, audit = gateway.calls[0], stream.audit_metadata()
        segments = audit["stream_segments"]
        assert [s["source"] for s in segments] == ["model", "model"]
        assert [s["chunk_index"] for s in segments] == [1, 2]
        assert segments[0]["published_at"] < call["terminal_received_at"]
        assert segments[1]["received_at"] == call["terminal_received_at"]
        for segment in segments:
            assert segment["received_at"] <= segment["validation_started_at"]
            assert segment["validation_started_at"] <= segment["validated_at"]
            assert segment["validated_at"] <= segment["publish_started_at"]
            assert segment["publish_started_at"] <= segment["published_at"]
        assert segments[-1]["published_at"] <= call["adapter_returned_at"]
    finally:
        gate.set()
        await gateway.close()


async def test_full_text_in_terminal_frame_with_slow_callback_is_not_live_generation_evidence():
    stream = controller()
    callback_entered, callback_release = asyncio.Event(), asyncio.Event()
    raw = json.dumps({"answer_parts": [RESULT_PART, UNKNOWN_PART]}, ensure_ascii=False)
    adapter, _ = await ollama_adapter(NDJSONStream([
        {"message": {"content": raw}, "done": True, "done_reason": "stop"},
    ]))
    gateway = AgentModelClient(Settings(_env_file=None), adapter)
    received = []

    async def slow_callback(event):
        if event["type"] == "delta":
            received.append(event["receive"])
            callback_entered.set()
            await callback_release.wait()
        await stream(event)

    task = asyncio.create_task(gateway.generate(
        [], response_schema=PartsBody, on_stream=slow_callback,
    ))
    try:
        await asyncio.wait_for(callback_entered.wait(), 1)
        assert received[0]["terminal"] and not stream.accepted
        assert not task.done()
        callback_release.set()
        await task
        call, audit = gateway.calls[0], stream.audit_metadata()
        assert call["terminal_received_at"] == received[0]["received_at"]
        assert call["terminal_received_at"] <= audit["first_model_published_at"]
        assert audit["first_model_published_at"] <= call["adapter_returned_at"]
        assert call["model_finished_at"] == call["adapter_returned_at"]
        assert all(s["chunk_index"] == 1 for s in audit["stream_segments"])
        assert len(call["provider_chunks"]) == 1
    finally:
        callback_release.set()
        await gateway.close()


async def test_compatible_provider_records_terminal_before_content_callback():
    class TerminalStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def __aiter__(self):
            yield ChatCompletionChunk.model_validate({
                "id": "terminal", "object": "chat.completion.chunk", "created": 0,
                "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                "delta": {"content": '{"observed_detail":"公开。"}',
                          "reasoning_content": "must never be exposed"}}],
            })

    received, body_callbacks = [], []

    async def callback(text):
        assert received and received[0]["terminal"]
        body_callbacks.append({"text": text, "at": time.time()})

    completion = await OpenAICompatibleClient._structured_stream(
        TerminalStream(), callback, received.append,
    )
    assert completion.choices[0].message.content == body_callbacks[0]["text"]
    assert received[0]["received_at"] <= body_callbacks[0]["at"]
    assert received[0]["source"] == "openai_sdk_chunk"
    assert "reasoning" not in str(received)


async def test_failed_pre_call_records_reached_stage_and_missing_stages_as_none():
    gateway = AgentModelClient(Settings(_env_file=None), FakeModelAdapter())
    pre_call = AsyncMock(side_effect=ModelError("调用前失败"))
    with pytest.raises(ModelError):
        await gateway.generate([], on_call=pre_call)
    call = gateway.calls[0]
    assert call["on_call_started_at"] <= call["on_call_completed_at"]
    assert call["on_call_ms"] is not None and call["queue_wait_ms"] is not None
    assert call["request_preparation_ms"] is None
    assert call["request_started_at"] is None
    assert call["model_elapsed_ms"] is None
    assert call["validation_ms"] is None
    assert call["terminal_received_at"] is None
    assert call["adapter_returned_at"] is None
    assert call["stream_callback_ms"] is None
    assert call["on_result_callback_ms"] is None


async def test_provider_error_frame_preserves_receipt_without_claiming_terminal_or_return():
    adapter, _ = await ollama_adapter(NDJSONStream([{"error": "provider-private-detail"}]))
    gateway = AgentModelClient(Settings(_env_file=None), adapter)
    try:
        with pytest.raises(ModelError):
            await gateway.generate([], response_schema=PartsBody, on_stream=AsyncMock())
        call = gateway.calls[0]
        assert call["first_chunk_at"] is not None
        assert call["provider_chunks"][0]["frame_error"]
        assert call["terminal_received_at"] is None
        assert call["adapter_completed_at"] is not None
        assert call["adapter_returned_at"] is None and call["validation_ms"] is None
        assert "provider-private-detail" not in json.dumps(call)
    finally:
        await gateway.close()


async def test_retained_prefix_does_not_count_as_first_model_display():
    ctx = context()
    ctx["_answer_parts_retained"] = [RESULT_PART]
    stream = controller(ctx=ctx)
    await start(stream, 2)
    audit = stream.audit_metadata()
    assert audit["first_retained_published_at"] is not None
    assert audit["first_model_published_at"] is None
    await feed(stream, '{"answer_parts":[' + encoded(UNKNOWN_PART) + "]}", 2)
    audit = stream.audit_metadata()
    assert audit["first_model_published_at"] >= audit["first_retained_published_at"]
    assert [s["source"] for s in audit["stream_segments"]] == ["retained", "model"]


@pytest.mark.parametrize("retained", [False, True])
async def test_mixed_v2_publishes_known_prefix_then_server_tail_with_separate_origins(retained):
    from app.agents.answer_parts import project_answer_parts

    ctx = context()
    ctx["response_brief"]["server_parts"] = [UNKNOWN_PART]
    if retained:
        ctx["_answer_parts_retained"] = [RESULT_PART]
    stream = controller(ctx=ctx)
    await start(stream, 2 if retained else 1)
    if not retained:
        assert not stream.accepted
        await feed(stream, '{"answer_parts":[' + encoded(RESULT_PART))
        assert not stream.parser.complete
    expected = RESULT + "\n\n" + UNKNOWN
    assert stream.accepted == expected
    assert stream.hub.stream_snapshot("r")["data"]["text"] == expected
    final = project_answer_parts([] if retained else [RESULT_PART], ctx)
    assert final.public_narration == stream.accepted
    assert final.answer_coverage[-1].status == "unknown"
    audit = stream.audit_metadata()
    assert [s["source"] for s in audit["stream_segments"]] == [
        "retained" if retained else "model", "server",
    ]
    assert audit["first_server_published_at"] >= audit["first_published_segment_at"]
    assert (audit["first_model_published_at"] is None) == retained
    assert (audit["first_retained_published_at"] is None) != retained
    assert [s["body_end"] for s in audit["stream_segments"]] == [len(RESULT), len(expected)]


async def test_model_cannot_submit_server_part_or_use_it_to_hide_unbacked_assertion():
    for part in (UNKNOWN_PART, {**RESULT_PART, "text": RESULT + "纸张没有夹层。"}):
        ctx = context()
        ctx["response_brief"]["server_parts"] = [UNKNOWN_PART]
        stream = controller(ctx=ctx)
        await start(stream)
        await feed(stream, '{"answer_parts":[' + encoded(part) + "]}")
        assert not stream.accepted and not stream.eligible
        assert not stream.audit_metadata()["stream_segments"]
