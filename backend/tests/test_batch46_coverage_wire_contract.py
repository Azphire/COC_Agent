"""Real-02 exposed optional evidence fields and a misleading mixed-turn body."""

from test_batch46_narration_coverage import parsed_context

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.generation_contracts import generation_contract, narration_body_field
from app.agents.narration_coverage import (
    coverage_audit,
    coverage_repair_message,
    prepare_response_contract,
)
from app.models.ollama import generation_schema


def prepared_context():
    context = parsed_context()
    context["current_scene_reference"] = "hall"
    context["response_brief"]["ordinary_observation"] = True
    context["response_brief"] = prepare_response_contract(context)
    return context


def test_wire_coverage_requires_source_fields_including_unknown_null_and_empty_quote():
    contract = generation_contract(KeeperNarration, prepared_context())
    wire = generation_schema(contract.model_json_schema())
    coverage = wire["$defs"]["AnswerCoverage"]
    assert set(coverage["required"]) == {
        "requirement_id", "body_quote", "source_id", "source_quote", "status",
    }
    assert None in coverage["properties"]["source_id"]["enum"]


def test_mixed_observation_and_question_uses_one_public_body_without_changing_permissions():
    context = prepared_context()
    contract = generation_contract(KeeperNarration, context)
    assert narration_body_field(contract) == "public_narration"
    wire = generation_schema(contract.model_json_schema())
    assert "observed_detail" not in wire["properties"]
    assert next(iter(wire["properties"])) == "public_narration"
    assert context["response_brief"]["ordinary_observation"] is True


def test_pure_observation_preserves_observed_detail_stream_field():
    context = prepared_context()
    context["response_brief"]["questions"] = []
    context["response_brief"] = prepare_response_contract(context)
    contract = generation_contract(KeeperNarration, context)
    assert narration_body_field(contract) == "observed_detail"


def test_repair_requires_missing_answers_in_body_before_mapping():
    context = prepared_context()
    audit = coverage_audit({"public_narration": "木箱表面有灰尘。"}, context["response_brief"])
    instruction = coverage_repair_message(audit)
    assert "先写完整正文" in instruction
    assert "再" in instruction and "body_quote" in instruction
    assert all(r["text"] in instruction for r in context["response_brief"]["answer_requirements"])
