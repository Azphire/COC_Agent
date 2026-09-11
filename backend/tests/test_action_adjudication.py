import json
from uuid import uuid4

import pytest
from adjudication_helpers import proposal_for
from test_agent_runtime import accept_original, game, submit, wait_cycle  # noqa: F401
from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_module_navigation_runtime import act, running_navigation  # noqa: F401
from test_rooms import headers, lobby, ok  # noqa: F401

from app.agents.action_policy import ActionFacts, ActionPolicyValidator, explicit_movement
from app.agents.adjudication_schemas import (
    BehaviorState,
    KeeperPlan,
    PlayerIntent,
    TeammateDecision,
)
from app.agents.behavior import TeammateBehaviorPolicy, bigram_jaccard
from app.agents.model import FakeModelAdapter
from app.agents.schemas import PlannedTool


def plan_for(text, kind="observe", target=None):
    return KeeperPlan(
        plan_id="plan",
        cycle_id="cycle",
        current_scene_id="scene",
        parsed_intent=PlayerIntent(
            type=kind,
            actor_member_id="actor",
            actor_character_slot_id="slot",
            evidence_quote=text,
            target_id=target,
            confidence=1,
        ),
    )


def facts_for(text):
    return ActionFacts(
        room_id="room",
        cycle_id="cycle",
        raw_text=text,
        actor_member_id="actor",
        actor_slot_id="slot",
        actor_authorized=True,
        scene_id="scene",
    )


@pytest.mark.parametrize(
    "kind",
    [
        "observe",
        "investigate",
        "converse",
        "move",
        "interact",
        "use_item",
        "assist",
        "wait",
        "out_of_character",
        "unknown",
    ],
)
def test_intent_types(kind):
    assert plan_for("玩家原始输入", kind).parsed_intent.type == kind


@pytest.mark.parametrize(
    "text",
    [
        "我检查一下前面的门",
        "我听听门后的声音",
        "我问同伴要不要过去",
        "先观察那条通道",
        "如果安全我就进入",
        "我并未走进房间",
        "同伴建议我前往那里",
        "我观察进入车厢的门",
    ],
)
def test_observation_is_not_explicit_movement(text):
    assert not explicit_movement(text)


@pytest.mark.parametrize(
    "text", ["我进入前面的车厢", "我们回到刚才的车厢", "我迈进相邻房间", "我返回之前的大厅"]
)
def test_varied_explicit_movement(text):
    assert explicit_movement(text)


def test_forged_quote_and_actor_rejected():
    plan = plan_for("捏造的输入")
    facts = facts_for("我环顾房间")
    assert (
        ActionPolicyValidator().validate_intent(plan.parsed_intent, plan, facts)[0]
        == "clarification_required"
    )
    facts.raw_text = "捏造的输入"
    facts.actor_authorized = False
    assert ActionPolicyValidator().validate_intent(plan.parsed_intent, plan, facts)[0] == "rejected"


def test_unknown_cannot_execute_side_effect():
    plan = plan_for("那个", "unknown")
    result = ActionPolicyValidator().validate(
        plan.parsed_intent,
        plan,
        facts_for("那个"),
        [PlannedTool(name="reveal_entity", arguments={"entity_id": "hidden"})],
    )
    assert result.status == "clarification_required" and not result.approved_actions


def test_move_destination_metadata_is_allowed_but_future_body_is_not():
    plan, facts = plan_for("我前往下一个房间", "move", "next"), facts_for("我前往下一个房间")
    facts.structure = True
    facts.allowed_node_ids = {"scene"}
    facts.transitions = {
        "exit": {
            "approved": True,
            "transition_id": "exit",
            "source_scene_node_id": "scene",
            "target_scene_node_id": "next",
            "target_entity_id": "next_entity",
        }
    }
    facts.available_transition_ids = {"exit"}
    plan.proposed_transition_id = "exit"
    plan.target_node_ids, plan.target_entity_ids = ["next"], ["next_entity"]
    action = PlannedTool(
        name="transition_scene",
        arguments={"target_scene_node_id": "next", "expected_revision": 0, "request_id": "move"},
    )
    validator = ActionPolicyValidator()
    assert validator.validate(plan.parsed_intent, plan, facts, [action]).status == "approved"
    plan.source_node_ids = ["next"]
    assert validator.validate(plan.parsed_intent, plan, facts, [action]).status == "rejected"


def test_current_scene_node_is_a_visible_observation_target():
    plan, facts = plan_for("我观察当前环境", target="scene"), facts_for("我观察当前环境")
    assert ActionPolicyValidator().validate_intent(plan.parsed_intent, plan, facts) is None


def test_reworded_long_observation_is_rejected_at_calibrated_threshold():
    earlier = (
        "我环顾四周，发现我们似乎被困在末班电车里，周围没有任何其他乘客。"
        "列车仍在行驶，但不知道会驶向何方。"
    )
    candidate = (
        "我注意到我们似乎被困在这辆电车里，周围没有任何其他乘客。列车仍在行驶，但不知道会驶向何方。"
    )
    decision = TeammateDecision(
        mode="speak",
        speech_text=candidate,
        reason_summary="复核公开状态",
        related_player_action_seq=10,
        confidence=1,
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[earlier],
        other_outputs=[],
        player_text="我复核观察",
        player_intent=None,
        public_ids=set(),
        action_seq=10,
        fingerprint="unchanged",
    )
    assert result.reason == "repeated_output" and not result.accepted
    assert 0.65 <= result.repetition_score < 0.72
    assert bigram_jaccard(earlier, "我留意车窗外的灯光变化，帮助同伴判断方向。") < 0.65


def test_teammate_repetition_and_cooldown():
    policy = TeammateBehaviorPolicy()
    decision = TeammateDecision(
        mode="act",
        action_type="investigate",
        target_id="door",
        action_text="我检查门框上的痕迹。",
        related_player_action_seq=10,
        confidence=1,
    )
    kwargs = dict(
        state=BehaviorState(),
        recent_outputs=["我检查门框上的痕迹！"],
        other_outputs=[],
        player_text="我看着地面",
        player_intent=None,
        public_ids={"door"},
        action_seq=10,
        fingerprint="state",
    )
    assert not policy.validate(decision, **kwargs).accepted
    kwargs["recent_outputs"] = []
    assert policy.validate(decision, **kwargs).accepted
    kwargs["state"] = policy.advance(
        BehaviorState(), decision, cycle_id="1", fingerprint="state", safe_goal=""
    )
    assert policy.validate(decision, **kwargs).reason == "target_action_cooldown"
    kwargs["fingerprint"] = "new_state"
    assert policy.validate(decision, **kwargs).accepted
    assert bigram_jaccard("我仔细检查门框上的旧痕迹", "我仔细检查门框上的痕迹") > 0.65


def modern_response(messages, kwargs):
    c = json.loads(messages[-1]["content"])
    schema = kwargs["response_schema"].__name__
    if schema == "KeeperPlan":
        ids = c["action_identifiers"]
        text = c["triggering_action"]["payload"]["text"]
        kind = (
            "unknown"
            if "那个" in text
            else "move"
            if "进入" in text
            else "investigate"
            if "检定" in text
            else "observe"
        )
        intent = {
            "type": kind,
            "actor_member_id": ids["actor_member_id"],
            "actor_character_slot_id": ids["actor_character_slot_id"],
            "evidence_quote": text,
            "confidence": 1,
        }
        plan = {
            "plan_id": ids["plan_id"],
            "cycle_id": ids["cycle_id"],
            "current_scene_id": ids["current_scene_id"],
            "parsed_intent": intent,
            "expected_navigation_revision": ids["expected_navigation_revision"],
        }
        if kind == "move":
            t = c["approved_exits"][0]
            intent["target_id"] = t["target_scene_node_id"]
            plan["proposed_transition_id"] = t["transition_id"]
        if "检定" in text:
            plan["proposed_check"] = {
                "target_member_id": ids["actor_member_id"],
                "name": "spot_hidden",
                "reason": "观察当前现场",
            }
        if plan.get("proposed_check"):
            plan["proposed_check"] = proposal_for(plan["proposed_check"], c)
        return plan
    if schema == "KeeperNarration":
        claim = c["PUBLIC_CLAIM_OPTIONS"][-1]
        return {"public_narration": claim["statement"], "grounded_claims": [claim]}
    if schema == "TeammateDecision":
        return {
            "mode": "pass",
            "related_player_action_seq": c["triggering_action"]["seq"],
            "confidence": 1,
        }
    return {"content": "记录已公开的场景和行动，尚未核实的推测不作为事实。"}


def test_two_stages_and_clarification_permissions(client, game):  # noqa: F811
    adapter = FakeModelAdapter(responder=modern_response)
    client.app.state.agent_service.model.adapter = adapter
    ok(submit(client, game, "我查看四周"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    plan = ok(client.get(game["prefix"] + f"/cycles/{cycle['id']}/plan"))
    assert plan["parsed_intent"]["type"] == "observe"
    assert (
        client.get(
            game["prefix"] + f"/cycles/{cycle['id']}/plan",
            headers=headers(game["remote"]["member_token"]),
        ).status_code
        == 403
    )
    ok(submit(client, game, "那个"))
    clarified = wait_cycle(client, game)
    assert clarified["status"] == "completed" and clarified["requires_clarification"]
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "action.clarification_requested" for e in events) == 1
    assert not any(e["type"] == "agent.action_proposed" for e in events)
    ok(
        client.post(
            game["prefix"] + "/clarifications",
            json={
                "text": "我查看当前环境",
                "client_request_id": str(uuid4()),
                "clarification_event_seq": clarified["clarification_event_seq"],
            },
            headers=headers(game["remote"]["member_token"]),
        )
    )
    assert wait_cycle(client, game)["status"] == "completed"


def test_check_wait_precedes_narration_and_duplicate_roll(client, game):  # noqa: F811
    adapter = FakeModelAdapter(responder=modern_response)
    client.app.state.agent_service.model.adapter = adapter
    ok(submit(client, game, "我冒着失去平衡的风险检查现场，请进行侦查检定"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "waiting_for_roll", cycle
    assert len(adapter.prompts) == 1
    check = ok(client.get(game["prefix"] + "/checks"))[0]
    path = game["prefix"] + f"/checks/{check['id']}/roll"
    first = ok(client.post(path, json={}))
    first["check"] = accept_original(client, game, check["id"])
    assert wait_cycle(client, game)["status"] == "completed"
    second = ok(client.post(path, json={}))
    assert all(
        first["check"]["result"][key] == second["check"]["result"][key]
        for key in ("total", "threshold", "level", "passed", "outcome")
    )
    assert first["check"]["dice"] == second["check"]["dice"]
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    invitation = next(e for e in events if e["payload"].get("check_invitation"))
    requested = next(e for e in events if e["type"] == "check.requested")
    assert invitation["seq"] < requested["seq"]
    assert any(e["type"] == "keeper.narration" and e["seq"] > requested["seq"] for e in events)


def test_navigation_non_move_then_explicit_move(client, running_navigation):  # noqa: F811
    d = running_navigation

    def response(messages, kwargs):
        output = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            context = json.loads(messages[-1]["content"])
            if "阅读" in context["triggering_action"]["payload"]["text"]:
                clue = next(
                    e for e in context["module"]["approved_entities"] if e["type"] == "clue"
                )
                output["proposed_reveal_entity_ids"] = [clue["id"]]
        return output

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    assert act(client, d, "我检查前面的门")["status"] == "completed"
    assert ok(client.get(d["room_prefix"] + "/module-navigation"))["navigation_revision"] == 0
    assert act(client, d, "我阅读公告")["status"] == "completed"
    assert act(client, d, "我进入前面的房间")["status"] == "completed"
    nav = ok(client.get(d["room_prefix"] + "/module-navigation"))
    assert nav["navigation_revision"] == 1 and nav["current_scene_node_id"] == d["nodes"]["Future"]


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_explicit_move_condition_waits_for_review(client, running_navigation, decision):  # noqa: F811
    from test_host_review import wait

    d = running_navigation
    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=modern_response)
    cycle = act(client, d, "我进入前面的房间")
    assert cycle["status"] == "waiting_for_review", cycle
    assert ok(client.get(d["room_prefix"] + "/checks")) == []
    review = ok(client.get(d["room_prefix"] + "/review-requests"))[0]
    ok(client.post(d["room_prefix"] + f"/review-requests/{review['id']}/{decision}", json={}))
    assert wait(client, d["room_prefix"], ("completed", "failed"))["status"] == "completed"
    nav = ok(client.get(d["room_prefix"] + "/module-navigation"))
    assert nav["navigation_revision"] == (1 if decision == "approve" else 0)
