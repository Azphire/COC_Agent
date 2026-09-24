"""Complete bound answer parts become visible while the same model call continues."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import create_model

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.narration import NarrationValidator
from app.agents.narration_stream import NarrationStream, private_fragments
from app.agents.stream_json import IncrementalJSONObjectArray
from app.rooms.realtime import RoomHub

RESULT = '便签背面写着：“第三个箱子里有藏着钥匙。”'
UNKNOWN = "纸张是否有夹层或折叠，目前还不清楚。"
RESULT_PART = {"requirement_id": "r-result", "text": RESULT}
UNKNOWN_PART = {"requirement_id": "r-unknown", "text": UNKNOWN}


def context():
    return {
        "response_brief": {
            "responder": {"kind": "keeper"},
            "current_action_results": [{"effect": RESULT}],
            "answer_requirements": [
                {"id": "r-result", "kind": "result", "text": "本次实际结果：便签背面",
                 "source_ids": ["e96"], "result_effect": RESULT, "verbatim": True},
                {"id": "r-unknown", "kind": "observation", "text": "纸张有没有夹层或折叠",
                 "source_ids": []},
            ],
            "answer_sources": [{"id": "e96", "kind": "current_result", "text": RESULT}],
        },
    }


def controller(*, secret="黑色祭坛下藏着秘密契约", ctx=None):
    hub = RoomHub(None)
    runtime = SimpleNamespace(rooms=SimpleNamespace(hub=hub),
                              validate_narration_output=AsyncMock())
    ctx = ctx or context()
    snapshot = {
        "run": SimpleNamespace(context=ctx), "room": None, "cycle": None,
        "private": private_fragments([secret], RESULT), "sources": RESULT,
        "internal_ids": {"r-result", "r-unknown", "e96"},
        "results": {"events": [], "result_facts": []},
    }
    contract = create_model("PartsNarration", __base__=KeeperNarration,
                            answer_parts=(list[dict], ...))
    return NarrationStream(runtime, {"room_id": "r", "cycle_id": "c"}, "run",
                           contract, snapshot)


async def start(stream, attempt=1):
    await stream({"type": "start", "attempt": attempt})


async def feed(stream, raw, attempt=1):
    await stream({"type": "delta", "attempt": attempt, "text": raw})


def encoded(part):
    return json.dumps(part, ensure_ascii=False)


@pytest.mark.parametrize("size", [1, 2, 3, 7, 101, 10000])
def test_array_decoder_cross_chunk_escapes_and_only_top_level_field(size):
    parts = [{"requirement_id": "r1", "text": '字样："入口"。\n😀\\门'},
             {"requirement_id": "r2", "text": "仍不清楚。"}]
    raw = json.dumps({"private": {"answer_parts": [{"text": "秘密"}]},
                      "thinking": '"answer_parts":[{"text":"秘密"}]',
                      "answer_parts": parts, "tail": None}, ensure_ascii=True)
    parser = IncrementalJSONObjectArray("answer_parts")
    released = []
    for i in range(0, len(raw), size):
        released += parser.feed(raw[i:i + size])
    assert released == parts and parser.value == parts and parser.finish()


def test_array_decoder_releases_only_closed_object_before_document_finishes():
    parser = IncrementalJSONObjectArray("answer_parts")
    assert parser.feed('{"answer_parts":[' + encoded(RESULT_PART)[:-1]) == []
    assert parser.feed("}") == [RESULT_PART]
    assert not parser.closed and not parser.complete
    assert parser.feed("," + encoded(UNKNOWN_PART)) == [UNKNOWN_PART]
    assert not parser.complete
    assert parser.feed("]}") == [] and parser.finish()


@pytest.mark.parametrize("raw", [
    '{"answer_parts":"private"}',
    '{"answer_parts":["private"]}',
    '{"answer_parts":[null]}',
    '{"answer_parts":[{"text":"bad\\x"}]}',
    '{"answer_parts":[{"text":"bad\\ud800"}]}',
    '{"answer_parts":[{"text":"bad\\udc00"}]}',
    '{"answer_parts":[{"text":"first","text":"second"}]}',
    '{"answer_parts":[{}],"answer_parts":[]}',
    '{"answer_parts":[{},]}',
    '{"answer_parts":[{} {}]}',
    '{"answer_parts":[{]}',
    '{"answer_parts":[]} private',
    '{"nested":{"answer_parts":[{}]}}',
])
def test_array_decoder_rejects_ambiguous_or_malformed_chunk(raw):
    parser = IncrementalJSONObjectArray("answer_parts")
    assert parser.feed(raw) == [] and parser.invalid and not parser.finish()


def test_array_decoder_caps_raw_input_and_count():
    parser = IncrementalJSONObjectArray("answer_parts", max_chars=5)
    assert parser.feed('{"answer_parts":[]}') == [] and parser.invalid
    parser = IncrementalJSONObjectArray("answer_parts", max_items=1)
    assert parser.feed('{"answer_parts":[{},{}]}') == [] and parser.invalid


@pytest.mark.asyncio
async def test_first_bound_part_streams_then_reconnect_snapshot_and_same_attempt_append():
    stream = controller()
    await start(stream)
    await feed(stream, '{"answer_parts":[' + encoded(RESULT_PART))
    assert stream.accepted == RESULT and not stream.parser.complete
    first_time = stream.first_display_at
    snapshot = deepcopy(stream.hub.stream_snapshot("r"))
    assert snapshot["data"]["text"] == RESULT
    assert snapshot["data"]["status"] == "responding"
    await feed(stream, "," + encoded(UNKNOWN_PART))
    expected = RESULT + "\n\n" + UNKNOWN
    assert stream.accepted == expected and not stream.parser.complete
    assert stream.first_display_at == first_time
    await feed(stream, "]}")
    assert stream.parser.complete and stream.accepted == expected
    outputs = [call.args[4] for call in stream.runtime.validate_narration_output.await_args_list]
    assert [len(output.answer_coverage) for output in outputs] == [1, 2]
    assert outputs[-1].answer_coverage[-1].source_id is None
    assert outputs[-1].answer_coverage[-1].body_quote == UNKNOWN
    assert outputs[0].answer_coverage[0].source_id == "e96"
    assert outputs[0].answer_coverage[0].source_quote == RESULT


@pytest.mark.asyncio
async def test_out_of_order_parts_buffer_until_frozen_order_can_be_released():
    stream = controller()
    await start(stream)
    await feed(stream, '{"answer_parts":[' + encoded(UNKNOWN_PART))
    assert stream.accepted == "" and not stream.parser.complete
    await feed(stream, "," + encoded(RESULT_PART))
    assert stream.accepted == RESULT + "\n\n" + UNKNOWN
    assert not stream.parser.complete


@pytest.mark.asyncio
async def test_unknown_first_streams_only_when_escaped_object_closes():
    ctx = context()
    ctx["response_brief"]["answer_requirements"].reverse()
    stream = controller(ctx=ctx)
    await start(stream)
    raw = '{"answer_parts":[' + json.dumps(UNKNOWN_PART, ensure_ascii=True)
    for char in raw[:-1]:
        await feed(stream, char)
        assert stream.accepted == ""
    await feed(stream, raw[-1])
    assert stream.accepted == UNKNOWN and not stream.parser.complete
    await feed(stream, "," + encoded(RESULT_PART))
    assert stream.accepted == UNKNOWN + "\n\n" + RESULT


@pytest.mark.parametrize("part", [
    {"requirement_id": "r-unknown", "text": "纸张没有夹层或折叠。"},
    {"requirement_id": "r-unknown", "text": "纸张没有夹层。是否折叠目前不清楚。"},
    {"requirement_id": "r-unknown", "text": "纸张没有夹层，夹层和折叠还不清楚。"},
    {"requirement_id": "r-unknown", "text": "你仔细检查纸张有没有夹层或折叠。"},
    {"requirement_id": "r-unknown", "text": UNKNOWN, "source_id": "e96"},
    {"requirement_id": "not-frozen", "text": RESULT},
    {"requirement_id": "r-result", "text": RESULT, "source_id": "other"},
])
@pytest.mark.asyncio
async def test_invalid_part_never_reaches_stream_delta(part):
    stream = controller()
    stream.hub.stream_delta = AsyncMock(wraps=stream.hub.stream_delta)
    await start(stream)
    await feed(stream, '{"answer_parts":[' + encoded(part))
    assert not stream.accepted and stream.hub.stream_delta.await_count == 0
    assert stream.buffered_reason == "invalid_answer_part"


@pytest.mark.parametrize("addition", ["纸张没有夹层。", "黑色祭坛下藏着秘密契约。",
                                       "内部编号r-unknown。"])
@pytest.mark.asyncio
async def test_result_part_cannot_hide_unknown_denial_private_text_or_ids(addition):
    stream = controller()
    await start(stream)
    await feed(stream, '{"answer_parts":[' + encoded({**RESULT_PART, "text": RESULT + addition}))
    assert not stream.accepted


@pytest.mark.asyncio
async def test_duplicate_part_stops_append_without_losing_verified_prefix():
    stream = controller()
    await start(stream)
    await feed(stream, '{"answer_parts":[' + encoded(RESULT_PART))
    await feed(stream, "," + encoded(RESULT_PART) + "," + encoded(UNKNOWN_PART))
    assert stream.accepted == RESULT and not stream.eligible


@pytest.mark.asyncio
async def test_repair_attempt_restores_only_verified_parts_then_appends_missing():
    stream = controller()
    await start(stream)
    await feed(stream, '{"answer_parts":[' + encoded(RESULT_PART))
    old_stream = stream.stream_id
    stream.snapshot["run"].context["_answer_parts_retained"] = [RESULT_PART]
    await start(stream, 2)
    assert stream.stream_id != old_stream and stream.accepted == RESULT
    await feed(stream, '{"answer_parts":[' + encoded(UNKNOWN_PART), 1)
    assert stream.accepted == RESULT
    await feed(stream, '{"answer_parts":[' + encoded(UNKNOWN_PART), 2)
    assert stream.accepted == RESULT + "\n\n" + UNKNOWN
    assert stream.hub.stream_snapshot("r")["data"]["attempt"] == 2


@pytest.mark.asyncio
async def test_retained_later_part_waits_for_missing_first_part():
    ctx = context()
    ctx["_answer_parts_retained"] = [UNKNOWN_PART]
    stream = controller(ctx=ctx)
    await start(stream, 2)
    assert stream.accepted == ""
    await feed(stream, '{"answer_parts":[' + encoded(RESULT_PART), 2)
    assert stream.accepted == RESULT + "\n\n" + UNKNOWN


@pytest.mark.asyncio
async def test_bound_parts_also_use_frozen_current_check_validation():
    stream = controller()
    stream.snapshot["results"]["events"] = [{
        "seq": 1, "type": "check.resolved", "payload": {
            "id": "check-current", "display_name": "侦查", "result": {"passed": False},
        },
    }]

    async def validate_output(_session, _room, _cycle, _run, output, *, prefix_snapshot):
        assert output.check_result_reference == "check-current"
        return NarrationValidator().validate_prefix(
            output, documents=[], public_ids=set(), scene_id="scene",
            results=prefix_snapshot["results"], brief=context()["response_brief"],
        )

    stream.runtime.validate_narration_output.side_effect = validate_output
    await start(stream)
    await feed(stream, '{"answer_parts":[' + encoded(
        {**RESULT_PART, "text": RESULT + "本次检定成功。"},
    ))
    assert not stream.accepted
    assert stream.runtime.validate_narration_output.await_count == 1
