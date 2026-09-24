"""Replay the real-03 child without model calls or changes to its frozen evidence."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agents.adjudication_schemas import KeeperPlan
from app.agents.narration import current_action_results, response_brief
from app.agents.task_receipts import bind_task_operands, receipt_progress

FROZEN = Path(__file__).resolve().parents[2] / "data/prepared/batch-49/real-03/delegate-case.json"
CHILD = "65039a29-bb2a-4b25-9589-59681ec9a9b4"


def document(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.fixture(scope="module")
def frozen():
    digest = hashlib.sha256(FROZEN.read_bytes()).hexdigest()
    audit = json.loads(FROZEN.read_text(encoding="utf-8-sig"))["audit"]
    narration = next(r for r in audit["agent_runs"]
                     if r["id"] == "a4bcc301-034d-4adc-8c18-30e24ac9e801")
    context = document(narration["context"])
    decision = next(r for r in audit["agent_runs"]
                    if r["id"] == "0b0b9af7-ef5d-49e4-8535-fa97dad9220b")
    decision = document(decision["context"])
    record = next(r for r in audit["agent_action_plans"] if r["cycle_id"] == CHILD)
    plan = KeeperPlan.model_validate(document(record["document"])["plan"])
    yield plan, context, decision
    assert digest == hashlib.sha256(FROZEN.read_bytes()).hexdigest()


def actual_results(context):
    return {**deepcopy(context["response_brief"]["completed_results"]), "cycle_id": CHILD}


def test_frozen_fallback_had_actual_reveal_but_no_result_requirement(frozen):
    plan, context, _ = frozen
    brief = context["response_brief"]
    assert not brief.get("current_action_results")
    assert [r["kind"] for r in brief["answer_requirements"]] == ["observation"]
    assert brief["answer_requirements"][0]["text"] == "纸张有没有夹层或折叠"
    rows = current_action_results(plan, context, actual_results(context))
    assert [row["source_event_seq"] for row in rows] == [96, 97]
    assert {row["operation"] for row in rows} == {"reveal", "search"}
    assert {row["effect"] for row in rows} == {"便签背面写着：“第三个箱子里有藏着钥匙。”"}
    assert all(row["actor_id"] == context["triggering_action"]["actor_member_id"]
               and row["cycle_id"] == CHILD and row["source_action_seq"] == 74
               and row["action_target_id"] == plan.focus.action_target_id for row in rows)
    replay, _ = response_brief(plan, context, actual_results(context))
    assert replay["current_action_results"] == rows


@pytest.mark.parametrize("case", [
    "old_cycle", "wrong_actor", "wrong_action", "wrong_target", "wrong_effect",
    "wrong_event", "not_executed", "private_event", "already_revealed", "wrong_event_actor",
])
def test_current_effects_cannot_borrow_an_unrelated_or_private_receipt(frozen, case):
    plan, context, _ = frozen
    results = actual_results(context)
    fact = results["current_result_facts"][0]
    event = results["events"][0]
    results["current_result_facts"] = [fact]
    results["events"] = [event]
    if case == "old_cycle":
        fact["cycle_id"] = event["payload"]["cycle_id"] = "old-cycle"
    elif case == "wrong_actor":
        fact["actor_id"] = "other-investigator"
    elif case == "wrong_action":
        fact["source_action_seq"] = 73
    elif case == "wrong_target":
        fact["action_target_id"] = "other-target"
    elif case == "wrong_effect":
        fact["effect"] = "纸张没有夹层或折叠。"
    elif case == "wrong_event":
        fact["source_event_seq"] = 6
    elif case == "not_executed":
        fact["status"] = "not_executed"
    elif case == "private_event":
        event["visibility"] = "private"
    elif case == "already_revealed":
        event["payload"]["already_revealed"] = True
    elif case == "wrong_event_actor":
        event["payload"]["actor_id"] = "other-investigator"
    assert current_action_results(plan, context, results) == []


def task_inputs(decision):
    request = deepcopy(decision["addressed_requests"][0])
    targets = deepcopy(decision["public_entities"])
    for target in targets:
        item = next((item for item in decision["inventory_state"]["known_items"]
                     if item["id"] == target["id"]), {})
        target["aliases"] = item.get("names", [])
    return request, targets


def test_frozen_paper_reference_binds_original_request_without_new_entity_alias(frozen):
    _, _, decision = frozen
    request, targets = task_inputs(decision)
    original_targets = deepcopy(targets)
    assert request["operation_operands"]["observe"]["target_id"] is None
    actor = request["executor_member_id"]
    bound = bind_task_operands(request, decision["inventory_state"], actor, targets=targets)
    source = bound["operation_operands"]["observe"]["target_source"]
    assert source["request_key"] == "53:7" and source["request_source_event_seq"] == 53
    assert source["target_id"] == request["target_id"]
    assert source["binding_kind"] == "same_request_reference"
    assert source["reference_text"] == "看看纸张"
    assert targets == original_targets
    # Recovery of the same partially settled request preserves its original key.
    reduced = {**deepcopy(request), "operations": ["observe"]}
    repaired = bind_task_operands(reduced, decision["inventory_state"], actor, targets=targets)
    assert repaired["operation_operands"]["observe"]["target_source"] == source
    assert request["operation_operands"]["observe"]["target_id"] is None


@pytest.mark.parametrize("text", [
    "看看另一张纸张", "看看桌上的纸张", "看看远处的纸张", "看看纸张和箱子",
    "看看它和箱子", "不看看纸张", "建议看看纸张", "“看看纸张”",
])
def test_unsupported_paper_reference_does_not_inherit_note_target(frozen, text):
    _, _, decision = frozen
    request, targets = task_inputs(decision)
    request["text"] = request["text"].replace("看看纸张", text)
    request["operation_operands"]["observe"]["text"] = text
    bound = bind_task_operands(request, decision["inventory_state"],
                               request["executor_member_id"], targets=targets)
    assert bound["operation_operands"]["observe"]["target_id"] is None


@pytest.mark.parametrize("case", ["wrong_key", "wrong_source", "wrong_actor", "historical",
                                  "ambiguous", "nonpaper", "new_sentence"])
def test_paper_reference_needs_same_request_and_unique_current_paper_antecedent(frozen, case):
    _, _, decision = frozen
    request, targets = task_inputs(decision)
    front = next(target for target in targets if target["id"] == request["target_id"])
    if case == "wrong_key":
        request["operation_operands"]["observe"]["key"] = "52:7"
    elif case == "wrong_source":
        request["operation_operands"]["observe"]["source_event_seq"] = 52
    elif case == "wrong_actor":
        request["operation_operands"]["observe"]["executor_member_id"] = "other-investigator"
    elif case == "historical":
        front["fact_scope"] = "historical"
    elif case == "ambiguous":
        targets.append({**front, "id": "second-note"})
    elif case == "nonpaper":
        front["title"], front["aliases"] = "门锁", []
        request["text"] = request["text"].replace("门上便签正面", "门锁")
    elif case == "new_sentence":
        request["text"] = request["text"].replace("边缘，", "边缘。")
    bound = bind_task_operands(request, decision["inventory_state"],
                               request["executor_member_id"], targets=targets)
    assert bound["operation_operands"]["observe"]["target_id"] is None


def test_new_reveal_alone_still_cannot_complete_observation_but_own_valid_answer_can(frozen):
    _, context, decision = frozen
    request, targets = task_inputs(decision)
    actor = request["executor_member_id"]
    bound = bind_task_operands(request, decision["inventory_state"], actor, targets=targets)
    actual = actual_results(context)["current_result_facts"]
    pending, completed = receipt_progress(
        bound, actual, actor=actor, cycle_id=CHILD, consumed=set(),
    )
    assert not completed and pending["operations"] == ["observe"]
    assert pending["completion_event_seqs"] == [97]
    # The only observation receipt permitted by settlement is a validated formal
    # answer; a reveal alone did not create one above.
    formal_answer = {**actual[1], "operation": "observe", "source_event_seq": 104}
    remaining, completed = receipt_progress(
        pending, [formal_answer], actor=actor, cycle_id=CHILD, consumed=set(),
    )
    assert completed and remaining["completion_event_seqs"] == [97, 104]
    for changed in ({"actor_id": "editor"}, {"cycle_id": "old-cycle"},
                    {"target_id": "other", "action_target_id": "other"},
                    {"operation": "reveal"}):
        _, completed = receipt_progress(pending, [{**formal_answer, **changed}], actor=actor,
                                        cycle_id=CHILD, consumed=set())
        assert not completed
