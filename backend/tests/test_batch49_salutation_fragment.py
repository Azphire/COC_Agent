"""The frozen real-04 parent must not turn its addressee into a player action."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
from app.preparation.turn_focus import repair_attribution, salutation_only_fragment

FROZEN = (Path(__file__).resolve().parents[2]
          / "data/prepared/batch-49/real04-parent-salutation.json")


@pytest.fixture(scope="module")
def frozen():
    digest = hashlib.sha256(FROZEN.read_bytes()).hexdigest()
    yield json.loads(FROZEN.read_text(encoding="utf-8"))
    assert digest == hashlib.sha256(FROZEN.read_bytes()).hexdigest()


@pytest.mark.parametrize("selected_action", ["frozen_residue", "whole_utterance"])
def test_frozen_parent_delegate_has_no_self_action_or_observation(frozen, selected_action):
    plan = KeeperPlan.model_validate(deepcopy(frozen["plan"]))
    raw = frozen["triggering_action"]["payload"]["text"]
    people = frozen["current_participants"]["members"]
    actor = frozen["triggering_action"]["actor_member_id"]
    assert plan.focus.action == "林修远·猎人，"
    assert plan.parsed_intent.type == "investigate"
    if selected_action == "whole_utterance":
        plan.focus.action = raw
    repair_attribution(plan, raw, people, actor)
    assert plan.focus.action == "" and plan.focus.action_target_id is None
    assert plan.parsed_intent.type == "converse"
    assert plan.parsed_intent.target_id == plan.focus.requests[0].addressee_id
    assert len(plan.focus.requests) == 1
    request = plan.focus.requests[0]
    assert request.kind == "delegate" and request.source_start == 7
    assert request.operations == ["search", "observe"]
    assert not plan.proposed_tool_calls and not plan.proposed_check
    assert not plan.proposed_reveal_entity_ids and not plan.proposed_transition_id


@pytest.mark.parametrize("own", [
    "我检查窗框，", "我查看便签正面，", "我踮起脚伸长脖子，",
    "我走到林修远·猎人身旁，", "我读出“林修远·猎人，”，",
])
def test_real_self_action_stays_when_a_separate_delegate_request_follows(frozen, own):
    plan = KeeperPlan.model_validate(deepcopy(frozen["plan"]))
    raw = own + frozen["triggering_action"]["payload"]["text"]
    people = frozen["current_participants"]["members"]
    actor = frozen["triggering_action"]["actor_member_id"]
    plan.focus = TurnFocus(action=own, action_target_id=plan.focus.action_target_id)
    repair_attribution(plan, raw, people, actor)
    assert plan.focus.action == own
    assert plan.focus.action_target_id is not None
    assert len(plan.focus.requests) == 1
    assert plan.focus.requests[0].text.endswith(frozen["plan"]["focus"]["requests"][0]["text"])


@pytest.mark.parametrize("text", [
    "林修远·猎人，", "请林修远·猎人，", "我请林修远·猎人：", "林修远·猎人先生，", "，； ",
])
def test_only_resolved_salutation_and_punctuation_are_empty(text, frozen):
    assert salutation_only_fragment(text, frozen["current_participants"]["members"])


@pytest.mark.parametrize("text", [
    "林修远·猎人检查便签，", "向林修远·猎人展示便签，", "我踮起脚伸长脖子，",
    "我走到林修远·猎人身旁并询问：", "“林修远·猎人，”", "林修远·猎人说：“检查便签。”",
])
def test_explicit_action_and_quoted_names_are_not_erased(text, frozen):
    assert not salutation_only_fragment(text, frozen["current_participants"]["members"])


def test_genuinely_ambiguous_salutation_still_requires_clarification(frozen):
    plan = KeeperPlan.model_validate(deepcopy(frozen["plan"]))
    people = {"hunter": "林修远·猎人", "editor": "林修远·编辑", "player": "玩家"}
    raw = "林修远，请检查便签。"
    plan.focus = TurnFocus(action=raw)
    repair_attribution(plan, raw, people, "player")
    assert plan.needs_clarification and plan.parsed_intent.requires_clarification
    assert plan.focus.requests == []


def test_quoted_request_remains_unowned_context(frozen):
    plan = KeeperPlan.model_validate(deepcopy(frozen["plan"]))
    raw = "林修远·猎人说：“请你检查便签。”"
    plan.focus = TurnFocus(question=raw)
    repair_attribution(plan, raw, frozen["current_participants"]["members"],
                       frozen["triggering_action"]["actor_member_id"])
    assert plan.focus.requests == [] and plan.focus.action == ""
