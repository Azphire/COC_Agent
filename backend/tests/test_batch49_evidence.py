"""Frozen batch-48 first-turn evidence and public answer boundaries."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.agents.action_runtime import generation_prompt
from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan, TeammateDecision
from app.agents.generation_contracts import generation_contract, restore_output
from app.agents.narration import response_brief
from app.agents.narration_coverage import coverage_audit, prepare_response_contract
from app.memory.facts import selected_answer_facts
from app.models.base import ModelFormatError

SOURCE = Path(__file__).resolve().parents[2] / (
    "data/prepared/batch-48/real-01/investigation-state-01.json"
)
QUOTE = "只管前进吧，已经没有退路了。"


def document(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.fixture
def frozen():
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    state = document(SOURCE.read_text(encoding="utf-8-sig"))
    room = state["launch_drafts"][0]["room_id"]
    runs = {r["graph_node"]: r for r in state["agent_runs"] if r["room_id"] == room}
    for run in runs.values():
        run["context"] = document(run["context"])
        run["output"] = document(run["structured_output"])
        run["calls"] = [document(c["document"]) for c in state["agent_model_calls"]
                        if c["run_id"] == run["id"]]
    yield runs
    assert digest == hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def test_frozen_teammate_keeps_selected_original_after_deduplication(frozen):
    run = frozen["decide_teammates"]
    context = run["context"]
    assert context["readonly_recall"] and context["fact_evidence"] == []
    output = TeammateDecision.model_validate(run["calls"][0]["generated_output"])
    assert QUOTE in output.speech_text
    result = restore_output(output, TeammateDecision, context)
    assert QUOTE in result.speech_text
    assert "6号车厢" not in result.speech_text
    assert result.fact_ids == ["event:6"]
    sent = generation_prompt(context, TeammateDecision)
    assert sent["fact_evidence"] == selected_answer_facts(context)
    assert not any(r.get("source", {}).get("seq") == 6 for r in sent["memory_evidence"])


def test_existing_current_state_source_labels_remain_readable():
    record = {"id": "current:location", "kind": "current_state",
              "source": "current_navigation_projection", "text": "当前位于车厢。"}
    context = {"fact_evidence": [record], "triggering_action": {
        "payload": {"text": "我们现在在哪？"},
    }}
    assert selected_answer_facts(context) == [record]


@pytest.mark.parametrize("mutation", [
    "private", "missing_ref", "wrong_ref", "omitted", "task", "unrelated",
])
def test_quote_cannot_reconstruct_a_source_from_model_draft(frozen, mutation):
    run = frozen["decide_teammates"]
    context = run["context"]
    row = next(r for r in context["memory_evidence"] if r["source"]["seq"] == 6)
    context["memory_evidence"] = [row]
    if mutation == "private":
        row["source"]["visibility"] = "keeper"
    elif mutation == "missing_ref":
        row["source"].pop("ref")
    elif mutation == "wrong_ref":
        row["source"]["ref"] = "e999"
    elif mutation == "task":
        row["kind"] = "pending_task"
    elif mutation == "unrelated":
        row["title"] = "6号车厢"
        row["text"] = "车厢空无他人，门上贴着便签。"
    else:
        context["memory_evidence"] = []
    result = restore_output(TeammateDecision.model_validate(run["calls"][0]["generated_output"]),
                            TeammateDecision, context)
    assert QUOTE not in result.speech_text
    assert not result.fact_ids


def repaired_context(frozen):
    context = copy.deepcopy(frozen["generate_keeper_narration"]["context"])
    plan_run = frozen["plan_keeper_action"]
    plan = restore_output(KeeperPlan.model_validate(plan_run["output"]), KeeperPlan,
                          plan_run["context"])
    context["response_brief"], _ = response_brief(
        plan, context, context["public_tool_results"],
    )
    context["response_brief"] = prepare_response_contract(context)
    return context


def test_frozen_unaddressed_question_belongs_to_keeper(frozen):
    context = repaired_context(frozen)
    brief = context["response_brief"]
    assert "便签具体写着什么？" in brief["questions"]
    question = next(r for r in brief["answer_requirements"] if r["kind"] == "question")
    assert question["verbatim"] and question["source_ids"] == ["e6"]
    assert any(r["kind"] == "observation" for r in brief["answer_requirements"])
    assert not brief["routed_requests"]


def test_frozen_kp_opening_replay_and_auxiliary_quote_still_rejected(frozen):
    context = repaired_context(frozen)
    for call in frozen["generate_keeper_narration"]["calls"]:
        raw = dict(call["generated_output"])
        # The mixed-question grammar now uses public_narration. Preserve the
        # frozen prose and coverage, changing only that old envelope field.
        raw["public_narration"] = raw.pop("observed_detail", raw.get("public_narration", ""))
        with pytest.raises(ModelFormatError, match="KP正文需求覆盖未通过|重复旧叙述"):
            generation_contract(KeeperNarration, context).model_validate(raw)


def test_mixed_answer_must_cover_observation_and_actual_quote(frozen):
    context = repaired_context(frozen)
    brief = context["response_brief"]
    observation = next(r for r in brief["answer_requirements"] if r["kind"] == "observation")
    question = next(r for r in brief["answer_requirements"] if r["kind"] == "question")
    sources = {s["id"]: s for s in brief["answer_sources"]}
    detail = "这节车厢空无他人，列车仍在行进，窗外一片漆黑。"
    answer = '便签写着：“' + QUOTE + '”'
    rows = [
        {"requirement_id": observation["id"], "body_quote": detail,
         "source_id": observation["source_ids"][0], "status": "answered",
         "source_quote": sources[observation["source_ids"][0]]["text"]},
        {"requirement_id": question["id"], "body_quote": answer, "source_id": "e6",
         "source_quote": sources["e6"]["text"], "status": "answered"},
    ]
    output = KeeperNarration(public_narration=detail + answer, answer_coverage=rows)
    audit = coverage_audit(output, brief)
    assert audit["complete"], audit
    restored = restore_output(output, KeeperNarration, context)
    assert restored.public_narration == detail + answer
    output.public_narration = answer
    assert not coverage_audit(output, brief)["complete"]
    output.public_narration = detail
    assert not coverage_audit(output, brief)["complete"]
    rows[1]["source_id"] = observation["source_ids"][0]
    output = KeeperNarration(public_narration=detail + answer, answer_coverage=rows)
    assert not coverage_audit(output, brief)["complete"]
