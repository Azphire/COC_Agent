"""Normalize evidence copies only when the unchanged body proves one answer."""

from copy import deepcopy

import pytest
from test_batch46_narration_coverage import coverage_context, good_output, parsed_context

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.generation_contracts import generation_contract
from app.agents.narration_coverage import (
    coverage_audit,
    normalize_coverage_spans,
    prepare_response_contract,
)


def mixed_answer():
    context = parsed_context()
    context["current_scene_reference"] = "hall"
    context["response_brief"]["ordinary_observation"] = True
    brief = prepare_response_contract(context)
    context["response_brief"] = brief
    body = "木箱表面有灰尘，铁门上留着划痕。林先生早先估计展品少了九件，但具体名称他并不清楚。"
    output = {
        "public_narration": body,
        "answer_coverage": [
            {
                "requirement_id": requirement["id"],
                "body_quote": requirement["text"],
                "source_id": "scene:hall" if index < 2 else "e3",
                "source_quote": "来源抄写时出现了错字",
                "status": "answered",
            }
            for index, requirement in enumerate(brief["answer_requirements"])
        ],
    }
    return context, output


def test_unique_body_spans_repair_bad_copies_without_rewriting_raw_or_body():
    context, original = mixed_answer()
    preserved = deepcopy(original)
    effective, changes = normalize_coverage_spans(original, context["response_brief"])
    assert original == preserved
    assert effective["public_narration"] == original["public_narration"]
    assert len(changes) == 4
    assert not coverage_audit(original, context["response_brief"])["complete"]
    assert coverage_audit(effective, context["response_brief"])["complete"]
    for old, new in zip(original["answer_coverage"], effective["answer_coverage"]):
        assert {k: old[k] for k in ("requirement_id", "source_id", "status")} == {
            k: new[k] for k in ("requirement_id", "source_id", "status")
        }
        assert new["body_quote"] in original["public_narration"]
    restored, repeated = normalize_coverage_spans(effective, context["response_brief"])
    assert restored == effective and repeated == []
    contract = generation_contract(KeeperNarration, context)
    assert contract.model_validate(original).public_narration == original["public_narration"]


@pytest.mark.parametrize(
    "change", ["missing_body_answer", "missing_id", "illegal_source", "duplicate_id"]
)
def test_missing_answers_and_undeclared_authority_never_acquire_mapping(change):
    context, original = mixed_answer()
    index = 2
    if change == "missing_body_answer":
        original["public_narration"] = "木箱表面有灰尘，铁门上留着划痕。"
    elif change == "missing_id":
        original["answer_coverage"][index]["requirement_id"] = "not-current"
    elif change == "illegal_source":
        original["answer_coverage"][index]["source_id"] = "hidden-source"
    else:
        original["answer_coverage"].append(deepcopy(original["answer_coverage"][index]))
    effective, _ = normalize_coverage_spans(original, context["response_brief"])
    assert effective["answer_coverage"][index] == original["answer_coverage"][index]
    assert not coverage_audit(effective, context["response_brief"])["complete"]


def test_allowed_id_with_wrong_actual_speaker_is_not_corrected_by_rebinding_source():
    context, original = mixed_answer()
    context["response_brief"]["answer_sources"][-1]["speaker"] = "周女士"
    effective, changes = normalize_coverage_spans(original, context["response_brief"])
    assert effective["answer_coverage"][2:] == original["answer_coverage"][2:]
    assert len(changes) == 2


@pytest.mark.parametrize(
    "index,extra",
    [
        (2, "林先生早先估计展品少了七件。"),
        (2, "林先生早先估计展品没有少九件。"),
        (3, "林先生当时说具体名称是午夜之门。"),
        (1, "铁门上没有划痕。"),
    ],
)
def test_normalization_cannot_pick_good_sentence_and_hide_conflicting_one(index, extra):
    context, original = mixed_answer()
    original["public_narration"] += extra
    effective, _ = normalize_coverage_spans(original, context["response_brief"])
    assert effective["answer_coverage"][index] == original["answer_coverage"][index]
    assert not coverage_audit(effective, context["response_brief"])["complete"]


def test_disjoint_matching_answers_are_ambiguous_even_if_both_sourced():
    context = coverage_context()
    original = good_output("林先生早先估计物品少了九件。林先生之前也说物品大约少了九件。")
    original["answer_coverage"][0]["body_quote"] = "wrong-copy"
    effective, changes = normalize_coverage_spans(original, context["response_brief"])
    assert not changes and effective == original


def test_adjacent_sentences_can_supply_speaker_and_history_context():
    context = coverage_context()
    context["response_brief"]["answer_requirements"][0]["speaker"] = "林先生"
    original = good_output("林先生此前作出过估计。他说物品大概少了九件。")
    original["answer_coverage"][0]["body_quote"] = "wrong-copy"
    effective, changes = normalize_coverage_spans(original, context["response_brief"])
    assert len(changes) == 1
    assert coverage_audit(effective, context["response_brief"])["complete"]
