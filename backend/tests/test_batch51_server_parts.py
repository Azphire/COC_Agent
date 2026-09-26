"""New mixed composition is independent of the immutable rejected originals."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agents.answer_parts import (
    MIXED_CONTRACT_VERSION,
    contract_version,
    inspect_answer_parts,
    parts_field,
    project_answer_parts,
)
from app.agents.narration_coverage import prepare_response_contract, unknown_assertion_errors
from app.agents.server_parts import capture_evidence_scope
from app.models.base import ModelFormatError


@pytest.fixture
def context():
    frozen = json.loads((Path(__file__).parent / "fixtures/batch50/real-01-current-failure.json")
                        .read_text(encoding="utf-8"))
    context = deepcopy(frozen["run"]["context"])
    context["answer_evidence_scope"] = capture_evidence_scope(context, context["public_entities"])
    context.pop("_answer_parts_retained", None)
    context["response_brief"] = prepare_response_contract(context)
    return context


def result_part(context):
    result = next(r for r in context["response_brief"]["answer_requirements"]
                  if r["kind"] == "result")
    return {"requirement_id": result["id"], "text": '便签背面写着：“第三个箱子里有藏着钥匙。”'}


def test_mixed_projection_and_readonly_server_scope(context):
    brief = context["response_brief"]
    assert contract_version(context) == MIXED_CONTRACT_VERSION
    assert brief["answer_requirements"][0]["kind"] == "result"
    unknown = brief["answer_requirements"][-1]
    assert unknown["evidence_assessment"]["state"] == "not_confirmed"
    assert unknown["evidence_assessment"]["checked_scope"]
    assert unknown["evidence_assessment"]["receipt_seqs"] == [68, 69]
    part = result_part(context)
    projection = project_answer_parts([part], context)
    assert projection.public_narration == (
        part["text"] + "\n\n纸张有没有夹层或折叠，目前还不能确定。"
    )
    assert [c.status for c in projection.answer_coverage] == ["answered", "unknown"]
    assert projection.answer_coverage[-1].source_id is None
    assert projection.answer_coverage[-1].source_quote == ""
    assert project_answer_parts([part], context, partial=True).public_narration == part["text"]
    audit = inspect_answer_parts({"answer_parts": [part]}, context)
    assert audit["valid"]
    assert [p["origin"] for p in audit["part_origins"]] == ["model", "server"]
    assert len(audit["verified_parts"]) == 1
    field, _ = parts_field(context)
    assert unknown["id"] not in json.dumps(field.__args__[0].model_json_schema())
    with pytest.raises(ModelFormatError):
        project_answer_parts([part, {"requirement_id": unknown["id"], "text": "未知"}], context)


@pytest.mark.parametrize("cause", ["recall_gap", "budget_omission", "recall_failure",
                                   "target_or_question_unclear", "execution_blocked_or_unbound"])
def test_missing_recall_or_execution_cannot_become_controlled_unknown(context, cause):
    if cause == "recall_gap":
        # Full target text, including text outside a bounded model projection.
        target = next(e for e in context["answer_evidence_scope"]["public_entities"]
                      if e["id"] == context["fact_target"])
        target["public_summary"] += "\n" + "公开背景。" * 400 + "纸张有夹层。"
    elif cause == "budget_omission":
        context["memory_omission"] = {"count": 1, "reason": "request_budget"}
    elif cause == "recall_failure":
        context["memory_selection_audit"]["unloaded_event_seqs"] = [6]
    elif cause == "target_or_question_unclear":
        context["fact_target"] = "other-target"
    else:
        context["public_tool_results"]["current_result_facts"] = []
    context["response_brief"] = prepare_response_contract(context)
    brief = context["response_brief"]
    assert brief["server_parts"] == []
    unknown = next(r for r in brief["answer_requirements"] if not r["source_ids"])
    assert unknown["evidence_assessment"]["reason"] == cause
    with pytest.raises(ModelFormatError):
        project_answer_parts([result_part(context), {"requirement_id": unknown["id"],
                             "text": "纸张有没有夹层或折叠，目前还不能确定。"}], context)


def test_model_denial_is_not_cancelled_by_server_unknown(context):
    part = result_part(context)
    part["text"] += "纸张没有夹层或折叠。"
    with pytest.raises(ModelFormatError):
        project_answer_parts([part], context)


@pytest.mark.parametrize("field,value", [("actor_id", "other-actor"),
                                         ("operation", "check"),
                                         ("cycle_id", "other-cycle"),
                                         ("source_action_seq", 999),
                                         ("action_target_id", "other-target")])
def test_unrelated_receipts_do_not_authorize_server_completion(context, field, value):
    for receipt in context["public_tool_results"]["current_result_facts"]:
        receipt[field] = value
    context["response_brief"] = prepare_response_contract(context)
    assert context["response_brief"]["server_parts"] == []


@pytest.mark.parametrize("text,valid", [
    ("不能确定纸张存在夹层或折叠。", True),
    ("目前无法确认纸张存在夹层或折叠。", True),
    ("纸张有没有夹层或折叠，目前还不能确定。", True),
    ("不能确定纸张存在夹层或折叠，但纸张确实没有夹层。", False),
    ("纸张存在夹层，不过还不能确定有折叠。", False),
    ("没有发现纸张有夹层或折叠。", False),
    ("目前尚未发现纸张有夹层或折叠。", False),
])
def test_local_epistemic_scope(text, valid):
    requirement = {"text": "纸张有没有夹层或折叠", "source_ids": []}
    assert bool(unknown_assertion_errors(text, requirement)) is not valid


def test_question_about_another_object_cannot_borrow_the_inspection_receipt(context):
    from app.agents.server_parts import prepare_server_parts

    brief = deepcopy(context["response_brief"])
    requirement = next(r for r in brief["answer_requirements"] if not r["source_ids"])
    requirement["text"] = "盒子有没有夹层"
    result = prepare_server_parts(context, brief, brief["answer_sources"])
    assert result["server_parts"] == []
    assessment = next(r["evidence_assessment"] for r in result["answer_requirements"]
                      if r["id"] == requirement["id"])
    assert assessment["state"] == "unresolved"
    assert assessment["reason"] == "target_or_question_unclear"
