"""New model-authored bodies, independent of the frozen failed coverage copies."""

import copy
import json

import pytest
from pydantic import ValidationError

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.answer_parts import (
    decode_answer_parts_document,
    inspect_answer_parts,
    project_answer_parts,
    uses_answer_parts,
)
from app.agents.generation_contracts import generation_contract, restore_output
from app.agents.model import AgentModelClient, FakeModelAdapter
from app.agents.narration_coverage import coverage_audit
from app.config import Settings
from app.models.base import ModelFormatError
from app.models.ollama import generation_schema


@pytest.fixture
def context():
    return {"current_scene_reference": "carriage", "response_brief": {
        "responder": {"kind": "keeper"}, "current_action_results": [{"source_event_seq": 68}],
        "answer_requirements": [
            {"id": "structure", "kind": "observation", "text": "纸张有没有夹层或折叠",
             "source_ids": []},
            {"id": "discovery", "kind": "result", "text": "本次实际结果：便签背面",
             "source_ids": ["e68"], "verbatim": True},
        ], "answer_sources": [{"id": "e68", "kind": "receipt",
                               "text": "便签背面写着：“第三个箱子里有藏着钥匙。”"}],
    }}


def authored_parts():
    return [
        {"requirement_id": "structure", "text": "纸张是否有夹层或折叠，目前还不清楚。"},
        {"requirement_id": "discovery", "text": "检查便签后，背面写着：“第三个箱子里有藏着钥匙。”"},
    ]


def test_single_body_contract_and_restore_existing_api(context):
    contract = generation_contract(KeeperNarration, context)
    wire = generation_schema(contract.model_json_schema())
    assert next(iter(wire["properties"])) == "answer_parts"
    assert not {"public_narration", "answer_coverage", "claim_ids", "incidental_details"} & (
        wire["properties"].keys()
    )
    for ref in wire["properties"]["answer_parts"]["items"]["oneOf"]:
        fields = wire["$defs"][ref["$ref"].split("/")[-1]]["properties"]
        assert set(fields) == {"requirement_id", "text"}
        assert "pattern" not in fields["text"]
    raw = {"answer_parts": authored_parts()}
    original = copy.deepcopy(raw)
    parsed = contract.model_validate(raw)
    restored = restore_output(parsed, KeeperNarration, context)
    assert restored.public_narration == "\n\n".join(p["text"] for p in raw["answer_parts"])
    assert coverage_audit(restored, context["response_brief"])["complete"]
    assert restored.answer_coverage[1].source_id == "e68"
    assert restored.answer_coverage[0].status == "unknown"
    assert "answer_parts" not in restored.model_dump()
    assert raw == original


@pytest.mark.parametrize("text", [
    "纸张有没有夹层或折叠，现在还不知道。", "纸张是否有夹层或折叠，无法确定。",
    "纸张的夹层或折叠情况仍不明确。", "关于纸张夹层或折叠的情况，眼下没有可靠依据。",
])
def test_natural_unknown_forms(context, text):
    parts = authored_parts()
    parts[0]["text"] = text
    assert project_answer_parts(parts, context).public_narration.startswith(text)


@pytest.mark.parametrize("mutation", [
    "missing", "duplicate", "wrong_id", "wrong_source", "wrong_object", "action_only",
    "negative", "positive", "negative_then_unknown", "cross_part", "missing_unknown_cross_part",
    "wrong_number", "missing_literal", "coverage_only", "extra_copy", "missing_unknown_item",
])
def test_reject_incomplete_unbound_or_false_answers(context, mutation):
    parts = authored_parts()
    if mutation == "missing":
        parts.pop()
    elif mutation == "duplicate":
        parts.append(copy.deepcopy(parts[0]))
    elif mutation == "wrong_id":
        parts[0]["requirement_id"] = "other"
    elif mutation == "wrong_source":
        parts[1]["source_id"] = "old-event"
    elif mutation == "wrong_object":
        parts[0]["text"] = "窗外天气还不清楚。"
    elif mutation == "action_only":
        parts[1]["text"] = "我试着查看便签背面。"
    elif mutation in {"negative", "positive", "negative_then_unknown"}:
        parts[0]["text"] = "纸张没有夹层或折叠。" if mutation != "positive" else "纸张存在夹层。"
        if mutation == "negative_then_unknown":
            parts[0]["text"] += "纸张是否折叠仍不清楚。"
    elif mutation in {"cross_part", "missing_unknown_cross_part"}:
        parts[1]["text"] += "纸张没有夹层。"
        if mutation == "missing_unknown_cross_part":
            parts.pop(0)
    elif mutation == "wrong_number":
        parts[1]["text"] = parts[1]["text"].replace("第三个", "第二个")
    elif mutation == "missing_literal":
        parts[1]["text"] = "便签背面确实有一行文字。"
    elif mutation == "coverage_only":
        parts[0] = {"requirement_id": "structure", "status": "unknown"}
    elif mutation == "extra_copy":
        parts[0]["body_quote"] = parts[0]["text"]
    elif mutation == "missing_unknown_item":
        parts[0]["text"] = "纸张是否有夹层，目前还不清楚。"
    with pytest.raises((ModelFormatError, ValidationError)):
        project_answer_parts(parts, context, partial=mutation == "missing_unknown_cross_part")


def test_out_of_order_uses_frozen_render_order(context):
    expected = project_answer_parts(authored_parts(), context)
    actual = project_answer_parts(list(reversed(authored_parts())), context)
    assert actual == expected


def test_unknown_noun_phrase_cannot_deny_then_claim_unknown(context):
    context["response_brief"]["answer_requirements"][0]["text"] = "纸张夹层或折叠"
    parts = authored_parts()
    parts[0]["text"] = "纸张没有夹层。" + parts[0]["text"]
    with pytest.raises(ModelFormatError):
        project_answer_parts(parts, context)


@pytest.mark.parametrize("raw", [
    '{"answer_parts":[{"requirement_id":"structure","text":"bad","text":"good"}]}',
    '{"answer_parts":[],"answer_parts":[]}',
])
def test_final_decode_rejects_duplicate_json_keys_just_like_stream(context, raw):
    from openai.types.chat import ChatCompletion

    from app.models.ollama import OllamaAgentAdapter
    from app.models.openai_compatible import OpenAICompatibleClient

    with pytest.raises(ModelFormatError):
        decode_answer_parts_document(raw)
    with pytest.raises(ModelFormatError) as failure:
        OllamaAgentAdapter._parse_response(
            {"message": {"content": raw}}, generation_contract(KeeperNarration, context),
        )
    assert failure.value.raw_output_text == raw
    assert failure.value.generated_output == {"unparsed_text": raw}
    completion = ChatCompletion.model_validate({
        "id": "local-test", "object": "chat.completion", "created": 0, "model": "test",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": raw,
        }}],
    })
    with pytest.raises(ModelFormatError) as compatible:
        OpenAICompatibleClient._parse_response(
            completion, generation_contract(KeeperNarration, context),
        )
    assert compatible.value.raw_output_text == raw
    assert compatible.value.generated_output == {"unparsed_text": raw}


def test_multiple_sources_require_explicit_legal_choice(context):
    brief = context["response_brief"]
    brief["answer_sources"].append({**brief["answer_sources"][0], "id": "e69"})
    brief["answer_requirements"][1]["source_ids"].append("e69")
    parts = authored_parts()
    with pytest.raises(ModelFormatError):
        project_answer_parts(parts, context)
    parts[1]["source_id"] = "e69"
    assert project_answer_parts(parts, context).answer_coverage[1].source_id == "e69"
    parsed = generation_contract(KeeperNarration, context).model_validate({"answer_parts": parts})
    assert parsed.answer_coverage[1].source_id == "e69"


def test_long_source_keeps_existing_quote_limit_without_changing_prose(context):
    source = context["response_brief"]["answer_sources"][0]
    source["text"] = "其他公开背景记录。" * 170 + source["text"]
    projected = project_answer_parts(authored_parts(), context)
    assert len(projected.answer_coverage[1].source_quote) <= 1200
    assert projected.answer_coverage[1].source_quote in source["text"]
    assert projected.public_narration == "\n\n".join(p["text"] for p in authored_parts())


@pytest.mark.parametrize("mode", ["npc", "teammate", "recall", "pure_observation", "known_only"])
def test_activation_is_narrow(context, mode):
    if mode in {"npc", "teammate"}:
        context["response_brief"]["responder"]["kind"] = mode
    elif mode == "recall":
        context["readonly_recall"] = True
    elif mode == "pure_observation":
        context["response_brief"]["current_action_results"] = []
    else:
        context["response_brief"]["answer_requirements"].pop(0)
    assert not uses_answer_parts(context)


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_ok", [True, False])
async def test_one_repair_only_missing_part_preserves_model_body(context, repair_ok):
    parts = authored_parts()
    first = {"answer_parts": [parts[0], {**parts[1], "text": "我检查了便签背面。"}]}
    second = {"answer_parts": [parts[1] if repair_ok else {
        **parts[1], "text": "我再次检查了便签背面。",
    }]}
    adapter = FakeModelAdapter([first, second])
    client = AgentModelClient(Settings(), adapter=adapter)
    calls, restored = [], []

    async def record(call):
        calls.append(copy.deepcopy(call))

    async def retry(call, prompt):
        audit = inspect_answer_parts(call["raw_output"] or call["generated_output"], context)
        assert audit["verified_parts"] == [parts[0]]
        context["_answer_parts_retained"] = audit["verified_parts"]
        schema = generation_contract(KeeperNarration, context)
        wire = generation_schema(schema.model_json_schema())
        ref = wire["properties"]["answer_parts"]["items"]["$ref"].split("/")[-1]
        assert wire["$defs"][ref]["properties"]["requirement_id"]["const"] == "discovery"
        return schema, prompt

    async def validate(output):
        restored.append(restore_output(output, KeeperNarration, context))

    arguments = dict(response_schema=generation_contract(KeeperNarration, context),
                     prepare_retry=retry, on_result=record, validate_output=validate)
    if repair_ok:
        result, _ = await client.generate([], **arguments)
        final = restore_output(result.structured, KeeperNarration, context)
        assert final.public_narration == "\n\n".join(p["text"] for p in parts)
        assert len(restored) == 1 and coverage_audit(final, context["response_brief"])["complete"]
    else:
        with pytest.raises(ModelFormatError):
            await client.generate([], **arguments)
        assert restored == []
    assert len(calls) == 2 and calls[0]["error_category"]
    assert bool(calls[1]["error_category"]) is not repair_ok
    assert context["_answer_parts_retained"] == [parts[0]]
    assert json.loads(json.dumps(first)) == first
