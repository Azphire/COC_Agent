"""The actual new-contract failure remains rejected, with only valid prose kept."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.agents.answer_parts import inspect_answer_parts, project_answer_parts
from app.agents.narration_coverage import coverage_audit
from app.models.base import ModelFormatError

DIRECTORY = Path(__file__).parent / "fixtures/batch50"


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


@pytest.fixture
def frozen():
    manifest = json.loads((DIRECTORY / "real-01-current-manifest.json").read_text(encoding="utf-8"))
    data = (DIRECTORY / manifest["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == manifest["sha256"]
    value = json.loads(data.decode("utf-8"))
    assert value["source_sha256"] == manifest["source_sha256"]
    assert digest(value["run"]["context"]) == manifest["context_sha256"]
    assert [digest(row["document"]) for row in value["calls"]] == manifest["call_document_sha256"]
    before = copy.deepcopy(value)
    yield value
    assert value == before


@pytest.mark.parametrize("index", [0, 1])
def test_real01_no_source_denial_still_fails_before_publication(frozen, index):
    call = frozen["calls"][index]["document"]
    context = copy.deepcopy(frozen["run"]["context"])
    if index == 1:
        # This is the actual server-retained result from attempt one, not a
        # reconstructed answer from old coverage or a newly written success.
        context["_answer_parts_retained"] = frozen["calls"][0]["document"]["retained_answer_parts"]
    raw = call["generated_output"]
    assert raw == call["raw_output"]
    assert "answer_parts" in call["output_contract"]["properties"]
    assert "public_narration" not in call["output_contract"]["properties"]
    assert not call["answer_parts_audit"]["valid"]
    assert call["first_validated_segment_at"] is None
    with pytest.raises(ModelFormatError, match="KP逐段正文未通过") as raised:
        project_answer_parts(raw["answer_parts"], context)
    assert "本轮来源未确认的检查结论" in json.dumps(raised.value.issues, ensure_ascii=False)
    assert not inspect_answer_parts(raw, context)["valid"]


def test_real01_valid_result_is_preserved_without_marking_the_request_complete(frozen):
    first = frozen["calls"][0]["document"]
    context = frozen["run"]["context"]
    valid = first["generated_output"]["answer_parts"][1]
    retained = first["retained_answer_parts"]
    assert retained == [valid] == context["retained_answer_parts"]
    partial = project_answer_parts(retained, context, partial=True)
    assert partial.public_narration == valid["text"]
    assert partial.model_dump(mode="json") == context["validated_partial"]
    assert partial.answer_coverage[0].source_id == "e68"
    assert not coverage_audit(partial, context["response_brief"])["complete"]
    formal = next(e["payload"] for e in frozen["formal_events"] if e["type"] == "keeper.narration")
    validation = next(e["payload"] for e in frozen["formal_events"]
                      if e["type"] == "agent.narration_validated")
    assert formal["text"].startswith(valid["text"])
    assert formal["safe_fallback"] and formal["answer_origin"] == "server_fallback"
    assert not validation["valid"] and not validation["answer_complete"]
