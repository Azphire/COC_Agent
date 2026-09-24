"""Preserve the actual first failure; normalize maps only over unchanged prose."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.generation_contracts import generation_contract
from app.agents.narration_coverage import coverage_audit, normalize_coverage_spans

SOURCE = Path(__file__).resolve().parents[2] / "data/prepared/batch-49/real-02/read-case.json"


def frozen():
    state = json.loads(SOURCE.read_text(encoding="utf-8"))["audit"]
    context = next(r["context"] for r in state["agent_runs"]
                   if r["graph_node"] == "generate_keeper_narration")
    calls = [c["document"] for c in state["agent_model_calls"]
             if c["document"]["schema"] == "KeeperNarration"]
    return context, calls


@pytest.mark.parametrize("index", [0, 1])
def test_actual_body_answers_survive_quote_mapping_copy_errors(index):
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    context, calls = frozen()
    original = calls[index]["generated_output"]
    saved = copy.deepcopy(original)
    assert calls[index]["error_category"] == "ModelFormatError"
    effective, changes = normalize_coverage_spans(original, context["response_brief"])
    assert effective["public_narration"] == original["public_narration"]
    assert original == saved and changes
    assert coverage_audit(effective, context["response_brief"])["complete"]
    validated = generation_contract(KeeperNarration, context).model_validate(original)
    assert validated.public_narration == original["public_narration"]
    assert digest == hashlib.sha256(SOURCE.read_bytes()).hexdigest()


@pytest.mark.parametrize("replacement", [
    "门上贴着两张便签", "门上只有一张便签", "门上总共贴着一张便签",
    "门上恰好贴着一张便签", "门上贴着一张地图",
])
def test_article_exception_cannot_forge_totals_or_other_objects(replacement):
    context, calls = frozen()
    original = copy.deepcopy(calls[1]["generated_output"])
    original["public_narration"] = original["public_narration"].replace(
        "门上贴着一张便签", replacement,
    )
    effective, _ = normalize_coverage_spans(original, context["response_brief"])
    assert not coverage_audit(effective, context["response_brief"])["complete"]


def test_unquoted_literal_source_still_requires_its_actual_content():
    context, calls = frozen()
    brief = context["response_brief"]
    source = next(s for s in brief["answer_sources"] if s["id"] == "e6")
    source["text"] = "便签写着：只管前进吧，已经没有退路了。"
    original = copy.deepcopy(calls[1]["generated_output"])
    original["public_narration"] = "这节车厢空无他人，列车仍在行进。便签上写着文字。"
    effective, _ = normalize_coverage_spans(original, brief)
    assert not coverage_audit(effective, brief)["complete"]


def test_numeric_question_does_not_treat_one_as_an_article():
    context, calls = frozen()
    brief = context["response_brief"]
    requirement = next(r for r in brief["answer_requirements"] if r.get("verbatim"))
    requirement["text"] = "门上贴着几张便签，具体写着什么？"
    effective, _ = normalize_coverage_spans(calls[1]["generated_output"], brief)
    assert not coverage_audit(effective, brief)["complete"]
