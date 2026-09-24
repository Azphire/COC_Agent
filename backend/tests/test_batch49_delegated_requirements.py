"""A shortened real-05 child attempt keeps only its own original obligations."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agents.adjudication_schemas import KeeperPlan
from app.agents.narration import current_delegated_requests, response_brief
from app.agents.narration_coverage import prepare_response_contract
from app.memory.events import story_events

FROZEN = Path(__file__).resolve().parents[2] / "data/prepared/batch-49/real-05/delegate-case.json"
CHILD = "60a2705e-1759-43f2-a99d-423d9f46bb99"


def document(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.fixture(scope="module")
def frozen():
    digest = hashlib.sha256(FROZEN.read_bytes()).hexdigest()
    audit = json.loads(FROZEN.read_text(encoding="utf-8-sig"))["audit"]
    cycle = next(row for row in audit["agent_cycles"] if row["id"] == CHILD)
    record = next(row for row in audit["agent_action_plans"] if row["cycle_id"] == CHILD)
    run = next(row for row in audit["agent_runs"] if row["cycle_id"] == CHILD
               and row["graph_node"] == "generate_keeper_narration")
    events = [{**event, "payload": document(event["payload"])} for event in audit["room_events"]
              if event["room_id"] == cycle["room_id"]]
    events, _ = story_events(events)
    yield (KeeperPlan.model_validate(document(record["document"])["plan"]),
           document(cycle["state"]), document(run["context"]), events)
    assert digest == hashlib.sha256(FROZEN.read_bytes()).hexdigest()


def test_frozen_short_attempt_keeps_the_specific_bound_paper_question(frozen):
    plan, state, context, events = deepcopy(frozen)
    assert context["triggering_action"]["payload"]["text"] == "我试着用手检查便签边缘。"
    assert not context["response_brief"].get("delegated_requests")
    assert context["response_brief"]["answer_requirements"][0]["text"] == "便签边缘"
    rows = current_delegated_requests(plan, state, events, context["public_entities"])
    assert len(rows) == 1 and rows[0]["key"] == "25:7"
    request = rows[0]
    assert "看看纸张有没有夹层或折叠" in request["text"]
    assert request["source_event_seq"] == 25 and request["source_action_seq"] == 46
    assert request["cycle_id"] == CHILD
    assert request["executor_member_id"] == context["triggering_action"]["actor_member_id"]
    assert request["operations"] == ["observe", "search"]
    for operation in request["operations"]:
        operand = request["operation_operands"][operation]
        assert operand["target_id"] == plan.focus.action_target_id
        assert operand["target_source"]["request_key"] == "25:7"
        assert operand["target_source"]["request_source_event_seq"] == 25
        assert operand["target_source"]["revealed_event_seq"] == 6
    brief, _ = response_brief(plan, context, context["response_brief"]["completed_results"],
                              delegated_requests=rows)
    assert brief["delegated_requests"] == rows
    assert not context["response_brief"].get("delegated_requests")


def test_authoritative_projection_drives_specific_unknown_and_actual_effect_requirements(frozen):
    plan, state, context, events = deepcopy(frozen)
    bound = current_delegated_requests(plan, state, events, context["public_entities"])
    context["response_brief"], _ = response_brief(
        plan, context, context["response_brief"]["completed_results"], delegated_requests=bound,
    )
    contract = prepare_response_contract(context)
    observations = [row for row in contract["answer_requirements"] if row["kind"] == "observation"]
    assert len(observations) == 1
    assert observations[0]["text"] == "纸张有没有夹层或折叠"
    assert observations[0]["source_ids"] == []
    results = [row for row in contract["answer_requirements"] if row["kind"] == "result"]
    assert len(results) == 1 and results[0]["source_ids"] == ["e68"]


@pytest.mark.parametrize("case", [
    "autonomous", "wrong_key", "wrong_actor", "wrong_addressee", "wrong_original",
    "wrong_original_span", "private_original", "missing_original", "wrong_cycle",
    "wrong_authority_actor", "wrong_authority_action", "wrong_authority_target",
    "wrong_request_target", "wrong_observe_target", "wrong_observe_actor",
    "wrong_observe_key", "wrong_proof_key", "wrong_proof_source", "wrong_proof_target",
    "wrong_proof_reveal", "hidden_target", "historical_target", "other_operation",
    "unattempted_operation", "ambiguous_operand", "unresolved_operand", "question_only",
])
def test_delegated_requirements_cannot_borrow_other_request_actor_target_or_operation(frozen, case):
    plan, state, context, events = deepcopy(frozen)
    request = state["request_operands"]["25:7"]
    operand = request["operation_operands"]["observe"]
    source = next(event for event in events if event["seq"] == 25)
    target = next(entity for entity in context["public_entities"]
                  if entity["id"] == plan.focus.action_target_id)
    if case == "autonomous":
        state["request_keys"] = []
    elif case == "wrong_key":
        state["request_keys"] = ["24:7"]
    elif case == "wrong_actor":
        request["executor_member_id"] = "editor"
    elif case == "wrong_addressee":
        request["addressee_id"] = "editor"
    elif case == "wrong_original":
        request["text"] += "再打开门。"
    elif case == "wrong_original_span":
        request["source_start"] = 0
    elif case == "private_original":
        source["visibility"] = "private"
    elif case == "missing_original":
        events.remove(source)
    elif case == "wrong_cycle":
        state["cycle_id"] = "old-child"
    elif case == "wrong_authority_actor":
        plan.action_authority["actor_member_id"] = "editor"
    elif case == "wrong_authority_action":
        plan.action_authority["source_event_seq"] = 25
    elif case == "wrong_authority_target":
        plan.action_authority["target_id"] = "other-note"
    elif case == "wrong_request_target":
        request["target_id"] = "other-note"
    elif case == "wrong_observe_target":
        operand["target_id"] = "other-note"
    elif case == "wrong_observe_actor":
        operand["executor_member_id"] = "editor"
    elif case == "wrong_observe_key":
        operand["key"] = "24:7"
    elif case == "wrong_proof_key":
        operand["target_source"]["request_key"] = "24:7"
    elif case == "wrong_proof_source":
        operand["target_source"]["request_source_event_seq"] = 24
    elif case == "wrong_proof_target":
        operand["target_source"]["target_id"] = "other-note"
    elif case == "wrong_proof_reveal":
        operand["target_source"]["revealed_event_seq"] = 5
    elif case == "hidden_target":
        context["public_entities"].remove(target)
    elif case == "historical_target":
        target["fact_scope"] = "historical"
    elif case == "other_operation":
        request["operations"] = ["take"]
    elif case == "unattempted_operation":
        plan.action_authority["kinds"] = ["observe"]
    elif case == "ambiguous_operand":
        operand["target_candidates"] = [target["id"], "other-note"]
    elif case == "unresolved_operand":
        operand["unresolved_operands"] = True
    elif case == "question_only":
        request["kind"] = "question"
    assert current_delegated_requests(plan, state, events, context["public_entities"]) == []


def test_other_frozen_or_pending_requests_are_not_imported(frozen):
    plan, state, context, events = deepcopy(frozen)
    expected = current_delegated_requests(plan, state, events, context["public_entities"])
    extra = {**deepcopy(state["request_operands"]["25:7"]),
             "key": "24:7", "text": "请查看另一份文件。"}
    state["request_operands"]["24:7"] = extra
    state["pending_requests"] = [extra]
    assert current_delegated_requests(plan, state, events, context["public_entities"]) == expected
