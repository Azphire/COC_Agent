"""The KP body, not IDs or auxiliary fields, must answer the current request."""

from copy import deepcopy

import pytest

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.generation_contracts import generation_contract
from app.agents.narration import NarrationValidator
from app.agents.narration_coverage import coverage_audit, prepare_response_contract
from app.models.base import ModelFormatError
from app.rooms.service import RoomError


def coverage_context():
    return {
        "current_scene_reference": "room",
        "response_brief": {
            "responder": {"kind": "keeper"},
            "question": "林先生早先估计少了多少件物品？",
            "answer_requirements": [
                {
                    "id": "r-number",
                    "kind": "question",
                    "text": "林先生早先估计少了多少件物品？",
                    "source_ids": ["e12"],
                }
            ],
            "answer_sources": [
                {
                    "id": "e12",
                    "text": "我估计少了九件物品，但不知道具体名称。",
                    "speaker": "林先生",
                    "historical": True,
                    "kind": "npc_statement",
                }
            ],
        },
    }


@pytest.mark.parametrize(
    "output",
    [
        {"public_narration": "橱柜边沿留着浅浅的灰尘。"},
        {
            "public_narration": "橱柜边沿留着浅浅的灰尘。",
            "answer_coverage": [
                {
                    "requirement_id": "r-number",
                    "body_quote": "林先生早先估计少了九件物品。",
                    "source_id": "e12",
                    "source_quote": "我估计少了九件物品",
                    "status": "answered",
                }
            ],
        },
        {
            "public_narration": "物品确定少了九件。",
            "answer_coverage": [
                {
                    "requirement_id": "r-number",
                    "body_quote": "物品确定少了九件。",
                    "source_id": "e12",
                    "source_quote": "我估计少了九件物品",
                    "status": "answered",
                }
            ],
        },
    ],
)
def test_missing_history_forged_map_and_estimate_as_fact_are_rejected(output):
    with pytest.raises(ModelFormatError):
        generation_contract(KeeperNarration, coverage_context()).model_validate(output)


def good_output(text="林先生早先估计物品少了九件。"):
    return {
        "public_narration": text,
        "answer_coverage": [
            {
                "requirement_id": "r-number",
                "body_quote": text,
                "source_id": "e12",
                "source_quote": "我估计少了九件物品",
                "status": "answered",
            }
        ],
    }


@pytest.mark.parametrize(
    "text",
    [
        "据林先生此前的估算，物品约少了9件。",
        "林先生当时说，凭他的印象物品大概少了九件，实际数目还有待核对。",
    ],
)
def test_natural_paraphrases_keep_value_estimate_and_attribution(text):
    output = generation_contract(KeeperNarration, coverage_context()).model_validate(
        good_output(text)
    )
    assert output.public_narration == text


@pytest.mark.parametrize(
    "text",
    [
        "林先生早先估计物品少了七件。",
        "周女士早先估计物品少了九件。",
        "林先生当场发现物品少了九件。",
        "物品少了九件，这是现场已经证实的事实。",
        "林先生早先估计少了多少件物品？",
    ],
)
def test_wrong_value_speaker_certainty_or_question_echo_fails(text):
    with pytest.raises(ModelFormatError):
        generation_contract(KeeperNarration, coverage_context()).model_validate(good_output(text))


@pytest.mark.parametrize("field", ["incidental_details", "npc_speech"])
def test_auxiliary_field_cannot_answer_for_the_keeper(field):
    output = good_output()
    answer = output["public_narration"]
    output["public_narration"] = "柜子边缘有灰尘。"
    output[field] = (
        [answer] if field == "incidental_details" else {"entity_id": "npc", "text": answer}
    )
    audit = coverage_audit(output, coverage_context()["response_brief"])
    assert not audit["complete"] and not audit["valid"]


def parsed_context():
    return {
        "triggering_action": {"seq": 28},
        "public_entities": [],
        "response_brief": {
            "trigger_seq": 28,
            "responder": {"kind": "keeper"},
            "attempt": "我环顾大厅，查看木箱与铁门。",
            "questions": ["林先生早先估计少了多少件展品？", "他知道具体名称吗？"],
            "routed_requests": [
                {"kind": "question", "addressee_id": "peer", "text": "周岚，请说说下一步。"}
            ],
            "player_statement": "我环顾大厅，查看木箱与铁门。林先生早先估计少了多少件展品，"
            "他知道具体名称吗？请分句回答。周岚，请说说下一步。",
            "current_scene": {
                "id": "hall",
                "public_description": "木箱表面有灰尘，铁门上留着划痕。",
            },
            "historical_memory": [
                {
                    "source": {"ref": "e3", "visibility": "public"},
                    "kind": "npc_statement",
                    "historical_only": True,
                    "speaker": "林先生",
                    "text": "我估计少了九件展品，但不清楚具体名称。",
                }
            ],
        },
    }


def test_requirements_derive_from_routed_input_stably_and_only_public_selected_sources():
    context = parsed_context()
    brief = prepare_response_contract(context)
    assert [r["text"] for r in brief["answer_requirements"]] == [
        "木箱",
        "铁门",
        "林先生早先估计少了多少件展品？",
        "他知道具体名称吗？",
    ]
    assert brief["answer_format_requests"] == ["请分句回答"]
    assert brief["routed_requests"] == context["response_brief"]["routed_requests"]
    assert brief["answer_requirements"] == prepare_response_contract(context)["answer_requirements"]
    modified = deepcopy(context)
    modified["response_brief"]["historical_memory"][0]["source"]["visibility"] = "keeper_only"
    without = prepare_response_contract(modified)
    assert [r["id"] for r in brief["answer_requirements"]] == [
        r["id"] for r in without["answer_requirements"]
    ]
    assert all(s["id"] != "e3" for s in without["answer_sources"])
    assert without["answer_requirements"][-1]["source_ids"] == []


def test_specific_unknown_remains_an_answer_and_does_not_erase_known_estimate():
    brief = prepare_response_contract(parsed_context())
    requirement = brief["answer_requirements"][-1]
    body = "林先生早先表示不清楚展品的具体名称。"
    row = {
        "requirement_id": requirement["id"],
        "body_quote": body,
        "source_id": "e3",
        "source_quote": "不清楚具体名称",
        "status": "unknown",
    }
    only = {**brief, "answer_requirements": [requirement]}
    assert coverage_audit({"public_narration": body, "answer_coverage": [row]}, only)["complete"]
    wrong = body.replace("不清楚", "知道")
    assert not coverage_audit(
        {"public_narration": wrong, "answer_coverage": [{**row, "body_quote": wrong}]}, only
    )["complete"]
    quantity = brief["answer_requirements"][-2]
    unknown = "林先生早先没有说清楚展品的数量。"
    assert not coverage_audit(
        {
            "public_narration": unknown,
            "answer_coverage": [
                {
                    **row,
                    "requirement_id": quantity["id"],
                    "body_quote": unknown,
                    "source_quote": "我估计少了九件展品",
                    "status": "unknown",
                }
            ],
        },
        {**brief, "answer_requirements": [quantity]},
    )["complete"]


def test_all_four_requirements_are_grounded_in_one_natural_body():
    brief = prepare_response_contract(parsed_context())
    parts = [
        "木箱边沿有灰尘。",
        "铁门上留着划痕。",
        "林先生此前估计少了九件展品。",
        "林先生当时表示不清楚具体名称。",
    ]
    quotes = ["木箱表面有灰尘", "铁门上留着划痕", "我估计少了九件展品", "不清楚具体名称"]
    rows = [
        {
            "requirement_id": req["id"],
            "body_quote": part,
            "source_id": "scene:hall" if i < 2 else "e3",
            "source_quote": quote,
            "status": "unknown" if i == 3 else "answered",
        }
        for i, (req, part, quote) in enumerate(zip(brief["answer_requirements"], parts, quotes))
    ]
    output = {"observed_detail": "".join(parts), "answer_coverage": rows}
    assert coverage_audit(output, brief)["complete"]
    partial = coverage_audit({**output, "answer_coverage": rows[:2]}, brief)
    assert len(partial["covered"]) == 2 and len(partial["missing"]) == 2
    assert partial["verified"] == rows[:2]


@pytest.mark.parametrize("attempt", ["我看看四周。", "我观察周围。"])
def test_generic_observation_uses_current_scene_predicates(attempt):
    context = parsed_context()
    context["response_brief"].update(attempt=attempt, questions=[])
    brief = prepare_response_contract(context)
    requirement = brief["answer_requirements"][0]
    assert requirement["generic_scope"] and requirement["source_ids"] == ["scene:hall"]
    body = "木箱边沿有灰尘。铁门上留着划痕。"
    source = brief["answer_sources"][0]
    output = {
        "public_narration": body,
        "answer_coverage": [
            {
                "requirement_id": requirement["id"],
                "body_quote": body,
                "source_id": source["id"],
                "source_quote": source["text"],
                "status": "answered",
            }
        ],
    }
    assert coverage_audit(output, brief)["complete"]
    assert not coverage_audit({"public_narration": body}, brief)["complete"]


def test_format_instruction_attached_to_factual_question_keeps_question():
    context = parsed_context()
    question = "请分别回答林先生早先估计少了多少件展品？"
    context["response_brief"]["questions"] = [question]
    assert question in [
        r["text"] for r in prepare_response_contract(context)["answer_requirements"]
    ]


@pytest.mark.parametrize(
    "question",
    [
        "能用完整句子分别回答吗？",
        "请用完整句子回答好吗？",
        "可以分点回答吗？",
    ],
)
def test_pure_format_question_is_not_a_factual_answer_requirement(question):
    context = parsed_context()
    fact = "请用完整句子回答林先生早先估计少了多少件展品？"
    context["response_brief"]["questions"] = [question, fact]
    brief = prepare_response_contract(context)
    assert question not in [r["text"] for r in brief["answer_requirements"]]
    assert question in brief["answer_format_requests"]
    assert fact in [r["text"] for r in brief["answer_requirements"]]


def test_same_testimony_from_different_speakers_keeps_both_sources():
    context = parsed_context()
    first = context["response_brief"]["historical_memory"][0]
    second = {
        **deepcopy(first),
        "speaker": "周女士",
        "source": {"ref": "e4", "visibility": "public"},
    }
    context["response_brief"]["historical_memory"].append(second)
    context["response_brief"]["questions"].append("周女士早先估计少了多少件展品？")
    sources = prepare_response_contract(context)["answer_sources"]
    assert {(s["id"], s["speaker"]) for s in sources if s["kind"] == "npc_statement"} == {
        ("e3", "林先生"),
        ("e4", "周女士"),
    }


def test_actual_source_speaker_cannot_be_reassigned_to_requested_speaker():
    context = coverage_context()
    context["response_brief"]["answer_requirements"][0]["speaker"] = "林先生"
    context["response_brief"]["answer_sources"][0]["speaker"] = "周女士"
    assert not coverage_audit(good_output(), context["response_brief"])["complete"]


@pytest.mark.parametrize(
    "index,body,quote,status",
    [
        (1, "铁门上没有划痕。", "铁门上留着划痕", "answered"),
        (
            3,
            "林先生当时表示，展品的具体名称是午夜之门，但丢失时间不清楚。",
            "不清楚具体名称",
            "unknown",
        ),
        (2, "林先生早先估计没有少九件展品。", "我估计少了九件展品", "answered"),
    ],
)
def test_source_polarity_and_specific_unknown_cannot_be_laundered(index, body, quote, status):
    brief = prepare_response_contract(parsed_context())
    requirement = brief["answer_requirements"][index]
    output = {
        "public_narration": body,
        "answer_coverage": [
            {
                "requirement_id": requirement["id"],
                "body_quote": body,
                "source_id": "scene:hall" if index == 1 else "e3",
                "source_quote": quote,
                "status": status,
            }
        ],
    }
    assert not coverage_audit(output, {**brief, "answer_requirements": [requirement]})["complete"]


def test_source_copy_cannot_launder_an_attributed_estimate_into_fact():
    brief = prepare_response_contract(parsed_context())
    requirement = brief["answer_requirements"][-2]
    source = {"id": "copy", "text": "少了九件展品。", "kind": "source_text"}
    brief = {
        **brief,
        "answer_requirements": [{**requirement, "source_ids": ["copy"]}],
        "answer_sources": [source],
    }
    text = "展品确定少了九件。"
    audit = coverage_audit(
        {
            "public_narration": text,
            "answer_coverage": [
                {
                    "requirement_id": requirement["id"],
                    "body_quote": text,
                    "source_id": "copy",
                    "source_quote": source["text"],
                    "status": "answered",
                }
            ],
        },
        brief,
    )
    assert not audit["complete"]
    assert any("说话人" in reason for error in audit["errors"] for reason in error["reasons"])


def test_completed_numeric_prefix_checks_assertion_without_future_coverage():
    brief = coverage_context()["response_brief"]
    missing = {"public_narration": "林先生之前估计物品少了九件。"}
    assert coverage_audit(missing, brief, prefix=True)["valid"]
    assert coverage_audit(missing, brief, prefix=True)["complete"] is None
    wrong = {"public_narration": "物品确定少了七件。"}
    assert not coverage_audit(wrong, brief, prefix=True)["valid"]
    requirement = {**brief["answer_requirements"][0], "speaker": "林先生", "historical": True}
    natural = {"public_narration": "林先生此前谈到失物。他估计物品少了九件。"}
    assert coverage_audit(natural, {**brief, "answer_requirements": [requirement]}, prefix=True)[
        "valid"
    ]


def test_prefix_defers_full_coverage_but_final_and_partial_keep_their_boundaries():
    validator = NarrationValidator()
    kwargs = {
        "documents": [],
        "public_ids": set(),
        "scene_id": "room",
        "results": {"events": []},
        "brief": coverage_context()["response_brief"],
    }
    output = KeeperNarration(public_narration="橱柜边沿有灰尘。")
    assert validator.validate_prefix(output, **kwargs)["answer_coverage"]["complete"] is None
    assert validator.validate(output, partial=True, **kwargs)["answer_coverage"]["complete"] is None
    with pytest.raises(RoomError):
        validator.validate(output, **kwargs)
    wrong = KeeperNarration.model_validate(good_output("物品确定少了九件。"))
    with pytest.raises(RoomError):
        validator.validate(wrong, partial=True, **kwargs)
    # Source/result visibility gates apply before any full-response contract.
    with pytest.raises(RoomError, match="文字内容"):
        validator.validate_prefix(
            KeeperNarration(public_narration="标签写着：伪造密码。"),
            **{**kwargs, "brief": {**kwargs["brief"], "source_quotes": ["标签写着：正确密码。"]}},
        )


@pytest.mark.parametrize(
    "bad",
    [
        True,
        "r-number",
        [None],
        [{"requirement_id": []}],
        [{"requirement_id": "r-number", "body_quote": [], "source_id": {}}],
    ],
)
def test_malformed_original_model_output_can_still_be_audited(bad):
    assert not coverage_audit({"answer_coverage": bad}, coverage_context()["response_brief"])[
        "complete"
    ]
