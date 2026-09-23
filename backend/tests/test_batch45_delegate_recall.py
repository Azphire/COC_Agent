"""Historical evidence does not erase a present delegated inspection."""

import pytest
from test_action_adjudication import plan_for

from app.agents.adjudication_schemas import BehaviorState, TeammateDecision
from app.agents.behavior import TeammateBehaviorPolicy
from app.memory.facts import readonly_recall, recall_question
from app.preparation.turn_focus import repair_attribution

TASK = "艾琳，请根据托马斯早先的说法查看书房窗户，核对窗锁是否松动。"


def test_fixed_delegated_inspection_keeps_ordinary_planning_and_request():
    assert recall_question(TASK)
    assert not readonly_recall(TASK)
    plan = plan_for(TASK, "converse", "window")
    repair_attribution(plan, TASK, {"aileen": "艾琳", "thomas": "托马斯"},
                       "actor", npc_ids={"thomas"})
    assert len(plan.focus.requests) == 1
    request = plan.focus.requests[0]
    assert request.kind == "delegate" and request.addressee_id == "aileen"
    assert "observe" in request.operations
    assert "查看书房窗户" in request.text


@pytest.mark.parametrize("text", [
    "艾琳，请复述早先查看书房窗户的原话。",
    "艾琳，请回顾托马斯早先关于书房窗户的说法。",
    "艾琳，请不要根据托马斯早先的说法查看书房窗户。",
    "复述原话：‘艾琳，请根据托马斯早先的说法查看书房窗户。’",
])
def test_recounted_or_negated_inspection_stays_readonly(text):
    assert readonly_recall(text)


def test_teammate_can_attempt_fixed_request_without_claiming_a_result():
    decision = TeammateDecision(
        mode="assist", action_type="observe", action_text="我走近书房窗户，查看窗锁是否松动。",
        target_id="window", confidence=1, related_player_action_seq=243,
    )
    result = TeammateBehaviorPolicy().validate(
        decision, state=BehaviorState(), recent_outputs=[], other_outputs=[],
        player_text=TASK, player_intent="converse", public_ids={"window"},
        action_seq=243, fingerprint="new", explicit_action_request=True,
        requested_operations=["observe"], requested_text=TASK,
        actor_id="aileen", requester_id="actor",
    )
    assert result.accepted, result.reason
