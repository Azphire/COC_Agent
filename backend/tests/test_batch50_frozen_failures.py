"""Original real-09/10 prose remains a failure independently of the new envelope."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.generation_contracts import generation_contract
from app.agents.narration_coverage import coverage_audit
from app.models.base import ModelFormatError

DIRECTORY = Path(__file__).parent / "fixtures/batch50"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


@pytest.fixture(params=["real-09", "real-10"])
def frozen_failure(request):
    name = request.param + "-delegate-failure.json"
    manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    data = (DIRECTORY / name).read_bytes()
    proof = manifest["files"][name]
    assert hashlib.sha256(data).hexdigest() == proof["sha256"]
    frozen = json.loads(data.decode("utf-8"))
    assert frozen["source_sha256"] == proof["source_sha256"]
    assert hashlib.sha256(canonical(frozen["run"]["context"])).hexdigest() == (
        proof["context_sha256"]
    )
    assert [hashlib.sha256(canonical(row["document"])).hexdigest() for row in frozen["calls"]] == (
        proof["call_document_sha256"]
    )
    before = copy.deepcopy(frozen)
    yield frozen
    assert frozen == before
    assert (DIRECTORY / name).read_bytes() == data


@pytest.mark.parametrize("attempt_index", [0, 1])
def test_frozen_prose_fails_semantics_even_without_new_generation_envelope(
    frozen_failure, attempt_index,
):
    context = frozen_failure["run"]["context"]
    call = frozen_failure["calls"][attempt_index]["document"]
    raw = call["generated_output"]
    assert "answer_parts" not in raw
    assert raw == call["raw_output"]
    audit = coverage_audit(raw, context["response_brief"])
    assert audit["checked"] and not audit["complete"]
    unknown = next(row for row in context["response_brief"]["answer_requirements"]
                   if not row["source_ids"])
    assert unknown["id"] not in audit["covered"]
    assert any(row["id"] == unknown["id"] for row in audit["errors"])
    # The old side field is intact, and does not become a successful body.
    coverage = next(row for row in raw["answer_coverage"]
                    if row["requirement_id"] == unknown["id"])
    assert coverage["body_quote"] not in raw["public_narration"]
    assert coverage["status"] == "unknown"


@pytest.mark.parametrize("attempt_index", [0, 1])
def test_frozen_original_is_not_adopted_as_new_success(frozen_failure, attempt_index):
    context = copy.deepcopy(frozen_failure["run"]["context"])
    raw = copy.deepcopy(frozen_failure["calls"][attempt_index]["document"]["generated_output"])
    before = copy.deepcopy(raw)
    with pytest.raises((ModelFormatError, ValidationError)):
        generation_contract(KeeperNarration, context).model_validate(raw)
    assert raw == before
    assert frozen_failure["formal_events"]
    formal = [event for event in frozen_failure["formal_events"]
              if event["type"] == "keeper.narration"]
    assert formal and all(event["payload"].get("safe_fallback") for event in formal)
