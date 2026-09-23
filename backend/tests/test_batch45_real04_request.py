"""Fixed real-04 regressions: scene identity and a response constraint."""

import pytest
from test_action_adjudication import facts_for, plan_for

from app.agents.action_policy import ActionPolicyValidator
from app.agents.adjudication_schemas import BehaviorState, TurnFocus
from app.preparation.turn_focus import reconcile_requests, repair_attribution

ACTION = "我环顾道格拉斯的书房，查看书架上的空档和通向屋外的窗户。"
PEER = "艾琳，请根据这些旧证词说明下一步还需要核对什么，不要声称已经查明窃贼。"
RAW = (ACTION + "托马斯早先估计少了多少本书，他知道具体书名吗？"
       "请把眼前的两项观察和早先的证词分句回答，明确哪些是托马斯的估计。" + PEER)
SCENE_NODE = "node_8bcf7cbdbcd39a291ff2720ded27b3e9"
SCENE_ENTITY = "c8f347c6-6d18-46ff-8a0e-e17efd52797c"
WINDOW = "038a9b20-b029-4855-be10-c5fb8994907a"


def observation_facts():
    facts = facts_for(RAW)
    facts.scene_id = SCENE_NODE
    # Titles and aliases are the approved public fields in real-04, not a new
    # fictional target. The module node and prepared scene entity differ.
    facts.approved_entities = {
        SCENE_ENTITY: {"id": SCENE_ENTITY, "type": "scene", "title": "道格拉斯的书房"},
        WINDOW: {"id": WINDOW, "type": "clue", "title": "松动的窗锁",
                 "aliases": ["窗户", "窗锁"]},
    }
    facts.local_entity_ids = facts.visible_entity_ids = {SCENE_ENTITY, WINDOW}
    return facts


def test_fixed_multiobject_observation_accepts_prepared_scene_identity():
    facts = observation_facts()
    plan = plan_for(RAW, "investigate", SCENE_ENTITY)
    plan.current_scene_id = SCENE_NODE
    plan.focus = TurnFocus(action=ACTION, question=RAW[len(ACTION):],
                           action_target_id=SCENE_ENTITY)
    # This is intent validation only: no reveal or search method is authorized.
    assert ActionPolicyValidator().validate_intent(plan.parsed_intent, plan, facts) is None


def test_historical_scene_cannot_use_current_scene_identity_exception():
    facts = observation_facts()
    facts.approved_entities["old-hall"] = {
        "id": "old-hall", "title": "旧大厅", "type": "scene",
    }
    facts.visible_entity_ids = {*facts.visible_entity_ids, "old-hall"}
    plan = plan_for(RAW, "investigate", "old-hall")
    plan.focus = TurnFocus(action=ACTION, action_target_id="old-hall")
    assert ActionPolicyValidator().validate_intent(plan.parsed_intent, plan, facts)[0] == (
        "clarification_required"
    )


def routed_requests(raw):
    plan = plan_for(raw, target=SCENE_ENTITY)
    plan.focus = TurnFocus(action=ACTION if raw == RAW else "", question=raw,
                           addressee_id="aileen", action_target_id=SCENE_ENTITY)
    repair_attribution(plan, raw, {"aileen": "艾琳", "thomas": "托马斯·金博尔"},
                       "actor", npc_ids={"thomas"})
    return plan.focus.requests


def pending_window_task():
    return BehaviorState(pending_requests=[{
        "key": "window-task", "kind": "delegate", "text": "艾琳，请查看窗户。",
        "operations": ["observe"], "target_id": WINDOW, "scene_id": SCENE_NODE,
    }])


def test_fixed_response_constraint_preserves_question_and_existing_task():
    requests = routed_requests(RAW)
    peer = next(r for r in requests if r.addressee_id == "aileen")
    assert peer.kind == "question"
    assert peer.text == PEER
    behavior = pending_window_task()
    reconcile_requests(behavior, requests, seq=189, scene_id=SCENE_NODE,
                       reachable_ids={WINDOW}, name="艾琳")
    assert behavior.pending_requests[0]["key"] == "window-task"
    assert not behavior.request_history


@pytest.mark.parametrize("raw", ["艾琳，取消查看窗户的任务。", "艾琳，别检查窗户。"])
def test_explicit_cancellation_still_cancels_existing_task(raw):
    requests = routed_requests(raw)
    assert len(requests) == 1 and requests[0].kind == "cancel"
    behavior = pending_window_task()
    reconcile_requests(behavior, requests, seq=190, scene_id=SCENE_NODE,
                       reachable_ids={WINDOW}, name="艾琳")
    assert not any(r["key"] == "window-task" for r in behavior.pending_requests)
    assert behavior.request_history[0]["status"] == "cancelled"
