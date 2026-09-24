"""Freeze the actual delegated child failure, keeping effects and prose separate."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.action_runtime import NARRATION_INSTRUCTION, action_messages, generation_prompt
from app.agents.adjudication_schemas import KeeperNarration
from app.agents.answer_parts import bind_part
from app.agents.generation_contracts import generation_contract, narration_body_field
from app.agents.narration_coverage import (
    coverage_audit,
    coverage_repair_message,
    prepare_response_contract,
)
from app.models.base import ModelFormatError
from app.models.ollama import generation_schema

SOURCE = Path(__file__).resolve().parents[2] / "data/prepared/batch-49/real-03/delegate-case.json"
CHILD = "65039a29-bb2a-4b25-9589-59681ec9a9b4"
UNKNOWN = "纸张是否有夹层或折叠，目前尚未确认。"
RESULT = "便签背面写着：“第三个箱子里有藏着钥匙。”"


@pytest.fixture
def frozen():
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    audit = json.loads(SOURCE.read_text(encoding="utf8"))["audit"]
    run = next(r for r in audit["agent_runs"]
               if r["cycle_id"] == CHILD and r["graph_node"] == "generate_keeper_narration")
    context = run["context"]
    context["response_brief"]["current_action_results"] = [
        row for row in context["public_tool_results"]["current_result_facts"]
        if row["cycle_id"] == CHILD and row["source_action_seq"] == 74
    ]
    context["response_brief"] = prepare_response_contract(context)
    calls = [r["document"]["generated_output"] for r in audit["agent_model_calls"]
             if r["run_id"] == run["id"]]
    yield context, calls
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before


def corrected(context, original):
    value = copy.deepcopy(original)
    value["public_narration"] = "你仔细检查便签后，" + RESULT + UNKNOWN
    value.pop("observed_detail", None)
    value["incidental_details"] = []
    value["claim_ids"] = []
    rows = []
    for requirement in context["response_brief"]["answer_requirements"]:
        if requirement["kind"] == "result":
            source = next(s for s in context["response_brief"]["answer_sources"]
                          if s["id"] == requirement["source_ids"][0])
            rows.append({"requirement_id": requirement["id"], "body_quote": RESULT,
                         "source_id": source["id"], "source_quote": source["text"],
                         "status": "answered"})
        else:
            rows.append({"requirement_id": requirement["id"], "body_quote": UNKNOWN,
                         "source_id": None, "source_quote": "", "status": "unknown"})
    value["answer_coverage"] = rows
    return value


def test_current_effect_is_required_and_unknown_structure_has_no_old_source(frozen):
    context, _ = frozen
    requirements = context["response_brief"]["answer_requirements"]
    observation = next(r for r in requirements if r["kind"] == "observation")
    assert observation["source_ids"] == []
    results = [r for r in requirements if r["kind"] == "result"]
    assert len(results) == 1 and results[0]["source_ids"] == ["e96"]
    assert results[0]["result_effect"] == RESULT


@pytest.mark.parametrize("index", [0, 1])
def test_frozen_wrong_structure_and_duplicate_mapping_still_rejected(frozen, index):
    context, calls = frozen
    assert not coverage_audit(calls[index], context["response_brief"])["complete"]
    with pytest.raises((ModelFormatError, ValidationError)):
        generation_contract(KeeperNarration, context).model_validate(calls[index])


@pytest.mark.parametrize("lead", ["你仔细检查便签后，", "你仔细查看有无夹层或折叠，",
                                 "你试图寻找夹层或折叠，"])
def test_action_leadin_does_not_erase_valid_actual_result_and_unknown_answer(frozen, lead):
    context, calls = frozen
    value = corrected(context, calls[0])
    value["public_narration"] = lead + RESULT + UNKNOWN
    # Legacy persisted/API prose still validates as prose. It is deliberately
    # not converted from old coverage to the new generation-only answer_parts.
    result = KeeperNarration.model_validate(value)
    assert result.public_narration == value["public_narration"]
    assert coverage_audit(result, context["response_brief"])["complete"]


@pytest.mark.parametrize("assertion", [
    "纸张没有夹层但折叠尚未确认。",
    "纸张没有夹层而是否折叠还不清楚。",
    "折叠尚未确认且纸张没有夹层。",
    "你仔细检查发现没有夹层。",
    "你仔细检查没看到夹层。",
])
def test_same_sentence_unknown_cannot_hide_another_unconfirmed_assertion(frozen, assertion):
    context, calls = frozen
    value = corrected(context, calls[0])
    value["public_narration"] = assertion + value["public_narration"]
    assert not coverage_audit(value, context["response_brief"])["complete"]
    with pytest.raises((ModelFormatError, ValidationError)):
        generation_contract(KeeperNarration, context).model_validate(value)


@pytest.mark.parametrize("mutation", ["invented_negative", "omit_effect", "action_only",
                                     "old_source", "duplicate", "wrong_effect"])
def test_actual_effect_does_not_excuse_missing_or_invented_formal_answers(frozen, mutation):
    context, calls = frozen
    value = corrected(context, calls[0])
    if mutation == "invented_negative":
        value["public_narration"] = "纸张没有夹层或折叠。" + value["public_narration"]
    elif mutation == "omit_effect":
        value["public_narration"] = UNKNOWN
    elif mutation == "action_only":
        value["public_narration"] = "你仔细检查便签的边缘，试图找到夹层和折叠。"
    elif mutation == "old_source":
        value["answer_coverage"][-1]["source_id"] = "e6"
    elif mutation == "duplicate":
        value["answer_coverage"].append(copy.deepcopy(value["answer_coverage"][-1]))
    else:
        value["public_narration"] = value["public_narration"].replace("第三个", "第二个")
    assert not coverage_audit(value, context["response_brief"])["complete"]
    with pytest.raises((ModelFormatError, ValidationError)):
        generation_contract(KeeperNarration, context).model_validate(value)


def test_unknown_source_and_status_are_server_bound_outside_generation_grammar(frozen):
    context, _ = frozen
    contract = generation_contract(KeeperNarration, context)
    wire = generation_schema(contract.model_json_schema())
    variants = wire["properties"]["answer_parts"]["items"]["oneOf"]
    rows = [wire["$defs"][v["$ref"].split("/")[-1]] for v in variants]
    unknown, result = rows
    assert set(unknown["properties"]) == {"requirement_id", "text"}
    assert set(result["properties"]) == {"requirement_id", "text"}
    unknown_id = unknown["properties"]["requirement_id"]["const"]
    result_id = result["properties"]["requirement_id"]["const"]
    bound_unknown = bind_part({"requirement_id": unknown_id, "text": UNKNOWN}, context)
    assert bound_unknown["source_id"] is None and bound_unknown["status"] == "unknown"
    assert bound_unknown["source_quote"] == ""
    bound_result = bind_part({"requirement_id": result_id, "text": RESULT}, context)
    assert bound_result["source_id"] == "e96" and bound_result["status"] == "answered"
    with pytest.raises(ModelFormatError):
        bind_part({"requirement_id": unknown_id, "text": UNKNOWN, "source_id": "e96"}, context)


@pytest.mark.parametrize("has_result", [False, True])
def test_current_effect_uses_full_body_while_pure_observation_keeps_detail(frozen, has_result):
    context, _ = frozen
    if not has_result:
        brief = context["response_brief"]
        brief["current_action_results"] = []
        brief["answer_requirements"] = [
            r for r in brief["answer_requirements"] if r["kind"] != "result"
        ]
    contract = generation_contract(KeeperNarration, context)
    expected = None if has_result else "observed_detail"
    assert narration_body_field(contract) == expected
    wire = generation_schema(contract.model_json_schema())
    assert ("answer_parts" if has_result else expected) in wire["properties"]
    assert ("observed_detail" in wire["properties"]) is not has_result
    if has_result:
        assert "public_narration" not in wire["properties"]
        assert "answer_coverage" not in wire["properties"]
        assert "pattern" not in json.dumps(wire)


@pytest.mark.parametrize("index", [0, 1])
def test_real04_source_borrow_and_opening_replay_stay_rejected(index):
    path = SOURCE.parent.parent / "real-04/delegate-case.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    audit = json.loads(path.read_text(encoding="utf8"))["audit"]
    run = next(r for r in audit["agent_runs"]
               if r["cycle_id"] == "ff88db86-4870-470e-8aa3-a08a5bbfc0f6"
               and r["graph_node"] == "generate_keeper_narration")
    context = run["context"]
    context["response_brief"] = prepare_response_contract(context)
    calls = [r["document"]["generated_output"] for r in audit["agent_model_calls"]
             if r["run_id"] == run["id"]]
    assert not coverage_audit(calls[index], context["response_brief"])["complete"]
    with pytest.raises((ModelFormatError, ValidationError)):
        generation_contract(KeeperNarration, context).model_validate(calls[index])
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_original_bound_probe_survives_real05_abbreviated_teammate_action():
    path = SOURCE.parent.parent / "real-05/delegate-case.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    audit = json.loads(path.read_text(encoding="utf8"))["audit"]
    child = next(c for c in audit["agent_cycles"]
                 if c["id"] == "60a2705e-1759-43f2-a99d-423d9f46bb99")
    run = next(r for r in audit["agent_runs"] if r["cycle_id"] == child["id"]
               and r["graph_node"] == "generate_keeper_narration")
    context = run["context"]
    assert context["triggering_action"]["payload"]["text"] == "我试着用手检查便签边缘。"
    # Runtime binding itself is covered by the frozen request/actor/target tests.
    # Here feed its retained original request into the public answer contract.
    context["response_brief"]["delegated_requests"] = [
        child["state"]["request_operands"]["25:7"],
    ]
    context["response_brief"] = prepare_response_contract(context)
    brief = context["response_brief"]
    observation = [r for r in brief["answer_requirements"] if r["kind"] == "observation"]
    assert len(observation) == 1
    assert observation[0]["text"] == "纸张有没有夹层或折叠"
    assert observation[0]["source_ids"] == []
    calls = [r["document"]["generated_output"] for r in audit["agent_model_calls"]
             if r["run_id"] == run["id"]]
    for original in calls:
        assert not coverage_audit(original, brief)["complete"]
    value = corrected(context, calls[0])
    result = KeeperNarration.model_validate(value)
    assert result.public_narration == value["public_narration"]
    assert coverage_audit(result, brief)["complete"]
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("mixed", [False, True])
def test_real06_prompt_and_claim_grammar_use_final_selected_sources(mixed):
    path = SOURCE.parent.parent / "real-06/delegate-case.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    audit = json.loads(path.read_text(encoding="utf8"))["audit"]
    run = next(r for r in audit["agent_runs"] if r["graph_node"] == "generate_keeper_narration")
    context = run["context"]
    brief = context["response_brief"]
    opening = brief["current_scene"]["public_description"]
    assert brief["answer_basis"] == "improvise"
    if mixed:
        brief["answer_sources"].append({"id": "e4", "text": opening, "kind": "source_text"})
        brief["answer_requirements"].append({
            "id": "mixed_scene", "kind": "observation", "text": "这节车厢", "source_ids": ["e4"],
        })
    before = copy.deepcopy(context)
    prompt = generation_prompt(context, KeeperNarration)
    assert context == before
    assert prompt["response_brief"]["answer_basis"] == "facts"
    assert (opening in json.dumps(prompt, ensure_ascii=False)) is mixed
    assert "只管前进吧，已经没有退路了。" not in json.dumps(prompt, ensure_ascii=False)
    assert prompt["response_brief"]["answer_sources"] == brief["answer_sources"]
    allowed = {c["text"] for c in prompt["response_brief"]["allowed_facts"]}
    selected = {s["text"] for s in brief["answer_sources"]}
    assert allowed <= selected and RESULT in allowed
    wire = generation_schema(generation_contract(KeeperNarration, context).model_json_schema())
    assert "claim_ids" not in wire["properties"]
    assert "answer_parts" in wire["properties"]
    assert "entity_a630d572-fb0f-4fdb-9a14-7931258cb862" not in json.dumps(wire)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_real08_missing_evidence_hints_do_not_supply_facts_or_validate_failed_body():
    path = SOURCE.parent.parent / "real-08/delegate-case.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    audit = json.loads(path.read_text(encoding="utf8"))["audit"]
    run = next(r for r in audit["agent_runs"] if r["graph_node"] == "generate_keeper_narration")
    context = run["context"]
    before = copy.deepcopy(context)
    messages = action_messages(context, KeeperNarration, NARRATION_INSTRUCTION)
    assert "普通杂物可补充材质" not in messages[0]["content"]
    assert "answer_parts是唯一正文" in messages[0]["content"]
    assert "尚未确认" not in messages[0]["content"]
    assert "操作成功不代表" in messages[0]["content"]
    assert json.loads(messages[1]["content"])["response_brief"]["answer_sources"] == (
        context["response_brief"]["answer_sources"]
    )
    for row in audit["agent_model_calls"]:
        if row["run_id"] != run["id"]:
            continue
        output = row["document"]["generated_output"]
        checked = coverage_audit(output, context["response_brief"])
        assert not checked["complete"]
        with pytest.raises((ModelFormatError, ValidationError)):
            generation_contract(KeeperNarration, context).model_validate(output)
        feedback = coverage_repair_message(checked)
        details = json.loads(feedback[feedback.index("{"):])
        assert details["required_unknown_answers"] == [{
            "requirement_id": "rb6ced70f4c",
            "answer": "关于纸张有没有夹层或折叠，目前尚未确认。",
        }]
        assert all(r["source_id"] == "e68" for r in details["preserve"])
    assert context == before
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("mode", ["custom", "npc", "pure_observation"])
def test_result_instruction_replacement_leaves_other_instruction_modes_intact(frozen, mode):
    context, _ = frozen
    instruction = NARRATION_INSTRUCTION
    if mode == "custom":
        instruction = "原自定义指令"
    elif mode == "npc":
        context["response_brief"]["responder"] = {"kind": "npc"}
    else:
        context["response_brief"]["current_action_results"] = []
        context["response_brief"]["answer_requirements"] = [
            r for r in context["response_brief"]["answer_requirements"] if r["kind"] != "result"
        ]
    assert action_messages(context, KeeperNarration, instruction)[0]["content"].startswith(
        instruction
    )


@pytest.mark.parametrize("mutation", ["original", "wrong_subject", "denial_then_unknown"])
@pytest.mark.parametrize("run_name", ["real-09", "real-10"])
def test_frozen_missing_answer_needs_object_and_fact_validation(mutation, run_name):
    path = SOURCE.parent.parent / run_name / "delegate-case.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    audit = json.loads(path.read_text(encoding="utf8"))["audit"]
    run = next(r for r in audit["agent_runs"] if r["graph_node"] == "generate_keeper_narration")
    context = run["context"]
    contract = generation_contract(KeeperNarration, context)
    for row in audit["agent_model_calls"]:
        if row["run_id"] != run["id"]:
            continue
        value = copy.deepcopy(row["document"]["generated_output"])
        if mutation == "wrong_subject":
            value["public_narration"] += "窗外的天气尚未确认。"
        elif mutation == "denial_then_unknown":
            value["public_narration"] += (
                "纸张没有夹层或折叠。关于纸张有没有夹层或折叠，目前尚未确认。"
            )
        before = copy.deepcopy(value)
        with pytest.raises((ModelFormatError, ValidationError)):
            contract.model_validate(value)
        assert value == before
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_known_only_result_does_not_require_unknown_wording(frozen):
    context, _ = frozen
    context["response_brief"]["answer_requirements"] = [
        r for r in context["response_brief"]["answer_requirements"] if r["source_ids"]
    ]
    wire = generation_schema(generation_contract(KeeperNarration, context).model_json_schema())
    assert "pattern" not in wire["properties"]["public_narration"]
