"""Repeated answers remain natural; every factual assertion remains checked."""

from copy import deepcopy

import pytest
from test_batch46_coverage_normalization import mixed_answer
from test_batch46_narration_coverage import coverage_context, good_output

from app.agents.narration_coverage import (
    coverage_audit,
    freeze_verified_fact,
    normalize_coverage_spans,
    retained_fact_errors,
)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("extra", [
    "铁门上没有划痕。",
    "林先生早先估计展品少了七件。",
    "展品已经证实正好少了九件。",
    "周女士早先估计展品少了九件。",
    "林先生当时说具体名称是午夜之门。",
    "林先生当时说具体名称是午夜之门但丢失时间不清楚。",
    "建议检查那扇没有划痕的铁门。",
    "下一步核对已证实少了七件展品的记录。",
    "这些已经是准确事实。",
    "周女士早先估计展品少了九件，林先生当时在场。",
])
def test_mapping_cannot_hide_contradictory_assertions_anywhere(native, extra):
    context, raw = mixed_answer()
    if native:
        raw, _ = normalize_coverage_spans(raw, context["response_brief"])
        assert coverage_audit(raw, context["response_brief"])["complete"]
    raw["public_narration"] += extra
    effective, _ = normalize_coverage_spans(raw, context["response_brief"])
    assert not coverage_audit(effective, context["response_brief"])["complete"]


@pytest.mark.parametrize("extra", [
    "下一步可以核对铁门划痕是否与展品失踪有关，同时留意木箱。",
    "铁门上留着划痕。林先生此前说不清楚展品具体名称。",
    "建议核对展品的具体名称是否已记入目录。",
    "下一步可以核对展品是否少了九件。",
    "你注意到木箱与铁门。",
])
def test_consistent_repetition_and_suggestions_do_not_require_one_unique_span(extra):
    context, raw = mixed_answer()
    raw["public_narration"] += extra
    preserved = deepcopy(raw)
    effective, changes = normalize_coverage_spans(raw, context["response_brief"])
    assert coverage_audit(effective, context["response_brief"])["complete"]
    assert raw == preserved and effective["public_narration"] == raw["public_narration"]
    assert changes


@pytest.mark.parametrize("body", [
    "木箱与铁门。",
    "建议检查木箱与铁门。",
    "木箱有什么，铁门上有什么？",
])
def test_subject_or_suggestion_alone_never_becomes_an_answer(body):
    context, raw = mixed_answer()
    raw["public_narration"] = body
    effective, _ = normalize_coverage_spans(raw, context["response_brief"])
    assert not coverage_audit(effective, context["response_brief"])["complete"]


def test_condition_context_is_retained_across_sentence_boundaries():
    brief = {
        "answer_requirements": [
            {"id": "r", "kind": "observation", "text": "铁门", "source_ids": ["s"]},
        ],
        "answer_sources": [
            {"id": "s", "kind": "observation", "text": "只有电源接通。铁门才可以打开。"},
        ],
    }
    body = "只有电源接通。铁门才可以打开。"
    output = {"public_narration": body, "answer_coverage": [{
        "requirement_id": "r", "source_id": "s", "source_quote": "wrong", "body_quote": "铁门",
        "status": "answered",
    }]}
    effective, changes = normalize_coverage_spans(output, brief)
    assert changes and effective["answer_coverage"][0]["body_quote"] == body
    assert coverage_audit(effective, brief)["complete"]
    output["public_narration"] = "铁门可以打开。"
    assert not coverage_audit(normalize_coverage_spans(output, brief)[0], brief)["complete"]


def test_repair_revalidates_facts_and_source_instead_of_exact_wording():
    brief = coverage_context()["response_brief"]
    original = good_output()
    frozen = freeze_verified_fact(original["answer_coverage"][0], brief)
    paraphrase = good_output("据林先生此前的估算，物品约少了9件。")
    assert not retained_fact_errors(paraphrase, brief, [frozen])
    for text in ("林先生早先估计物品少了七件。", "林先生早先估计物品多了九件。"):
        assert retained_fact_errors(good_output(text), brief, [frozen])
    changed = deepcopy(brief)
    changed["answer_sources"][0]["text"] = "我估计少了七件物品。"
    assert retained_fact_errors(good_output("林先生早先估计物品少了七件。"), changed, [frozen])


def test_unproven_paraphrase_does_not_get_retention_credit_just_from_matching_id():
    brief = {
        "answer_requirements": [
            {"id": "r", "kind": "observation", "text": "铁门", "source_ids": ["s"]},
        ],
        "answer_sources": [{"id": "s", "kind": "observation", "text": "铁门有灰尘和划痕。"}],
    }
    original = {"requirement_id": "r", "source_id": "s", "source_quote": "铁门有灰尘和划痕。",
                "body_quote": "铁门有灰尘和划痕。", "status": "answered"}
    frozen = freeze_verified_fact(original, brief)
    changed = {**original, "body_quote": "铁门有灰尘。"}
    assert retained_fact_errors(
        {"public_narration": changed["body_quote"], "answer_coverage": [changed]}, brief, [frozen],
    )


def test_same_source_cannot_swap_predicates_between_different_objects():
    context, raw = mixed_answer()
    brief = context["response_brief"]
    original, _ = normalize_coverage_spans(raw, brief)
    retained = [freeze_verified_fact(row, brief) for row in original["answer_coverage"]]
    raw["public_narration"] = raw["public_narration"].replace(
        "木箱表面有灰尘，铁门上留着划痕。", "木箱表面留着划痕，铁门上有灰尘。",
    )
    effective, _ = normalize_coverage_spans(raw, brief)
    assert not coverage_audit(effective, brief)["complete"]
    assert retained_fact_errors(effective, brief, retained)


def test_condition_operand_without_its_conditional_force_is_not_preserved():
    brief = {
        "answer_requirements": [
            {"id": "r", "kind": "observation", "text": "铁门", "source_ids": ["s"]},
        ],
        "answer_sources": [
            {"id": "s", "kind": "observation", "text": "只有电源接通，铁门才可以打开。"},
        ],
    }
    source = brief["answer_sources"][0]["text"]
    original = {"requirement_id": "r", "source_id": "s", "source_quote": source,
                "body_quote": source, "status": "answered"}
    changed = {"public_narration": "电源接通，铁门可以打开。", "answer_coverage": [original]}
    effective, _ = normalize_coverage_spans(changed, brief)
    assert not coverage_audit(effective, brief)["complete"]
    assert retained_fact_errors(effective, brief, [freeze_verified_fact(original, brief)])
