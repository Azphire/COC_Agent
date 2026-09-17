"""Behavioral regressions from batch 34 and independent equivalent expressions."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_action_adjudication import facts_for, plan_for
from test_batch16 import module_battle  # noqa: F401
from test_batch17 import interactions, rule  # noqa: F401
from test_module_navigation import structure_data  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import lobby  # noqa: F401

from app.agents.action_policy import explicit_movement, local_scene_movement, named_move_exits
from app.agents.adjudication_schemas import (
    BehaviorState,
    KeeperNarration,
    TeammateDecision,
    TurnFocus,
)
from app.agents.behavior import TeammateBehaviorPolicy, public_fingerprint
from app.agents.check_policy import CheckPolicyEvaluator, CheckProposal
from app.agents.generation_contracts import restore_output
from app.agents.teammate_eligibility import TeammateEligibilityPolicy
from app.preparation.dialogue import repair_dialogue_focus


@pytest.mark.parametrize(
    "raw,readonly",
    [
        ("这边也没人……我沿着座位仔细看一圈，找找落下的东西，想知道刚才发生过什么。", False),
        ("我在门旁摸索墙面，想知道之前这里发生了什么。", False),
        ("我之前检查了座位，刚才到底找到了什么？", True),
        ("陈拓刚才说‘我检查座位’，他发现了什么？", True),
        ("您能听见吗？刚才出了什么事？我扶住他的肩，让他靠着座位。", False),
        ("我想回顾刚才的原话。", True),
    ],
)
def test_current_attempt_is_not_erased_by_historical_purpose(raw, readonly):
    from app.memory.facts import readonly_recall

    assert readonly_recall(raw) is readonly


def test_question_then_self_inspection_keeps_actor_and_target():
    from app.preparation.action_authority import action_kinds, speaker_action
    from app.preparation.search import repair_belongings_action

    raw = "陈拓，你觉得纸条指什么？我摸摸自己的口袋，看看手机还在不在。"
    assert "search" in action_kinds(raw)
    assert "我摸摸" in speaker_action(raw)
    value = {
        "focus": {"action": raw, "action_target_id": "note"},
        "parsed_intent": {"type": "interact"},
    }
    repair_belongings_action(
        value,
        {
            "triggering_action": {"actor_member_id": "me", "payload": {"text": raw}},
            "current_participants": {"members": {"me": "林", "other": "陈拓"}},
            "action_identifiers": {"current_scene_id": "scene"},
        },
    )
    assert value["focus"]["action"] == "我摸摸自己的口袋，"
    assert value["focus"]["action_target_id"] == "scene"


@pytest.mark.parametrize(
    "raw",
    [
        "我轻手轻脚地走到通往2号车厢的门边，贴着门框往里看，想看清那个走动的人。",
        "我靠近入口边，探头打量里面。",
    ],
)
def test_boundary_viewpoint_does_not_cross_scene(raw):
    assert local_scene_movement(
        raw,
        [{"type": "scene", "title": "3号车厢"}],
        [{"transition_id": "next", "target_public_title": "2号车厢"}],
    )


@pytest.mark.parametrize(
    "raw",
    [
        "现在就跑过去！我不停下来查看了，直接冲进1号车厢。",
        "我朝前面还完整的地板用力跳过去。",
        "我先过去！我朝陈拓指的那块完整地板用力跳，尽量不往下面看。",
        "您先坐稳，别勉强说话。我去前面找人帮忙，朝3号车厢走。",
    ],
)
def test_current_directional_attempt(raw):
    assert explicit_movement(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "如果他冲进去，我就在门边等。",
        "我不想冲进那间屋子。",
        "陈拓说：‘我跳过去’，我留在原地。",
        "他说“走进1号车厢”，我还没动。",
    ],
)
def test_quoted_conditional_and_negated_movement(raw):
    assert not explicit_movement(raw)


def test_away_from_named_place_is_not_that_destination():
    exits = [{"transition_id": "back", "target_public_title": "6号车厢"}]
    assert not named_move_exits("我从远离6号车厢的那一头走进下一节。", exits)


@pytest.mark.parametrize(
    "raw",
    [
        "我往列车前面的下一节车厢走，想找个工作人员问问。",
        "远离刚才那节有血腥味的车厢，我往另一头走。",
    ],
)
def test_relative_direction_uses_exit_evidence_not_candidate_order(raw):
    exits = [
        {
            "transition_id": "back",
            "target_public_title": "车尾",
            "target_description": "后方通道。",
            "is_previous_scene": True,
        },
        {
            "transition_id": "onward",
            "target_public_title": "过道",
            "target_description": "灯还亮着。",
        },
    ]
    assert named_move_exits(raw, exits) == [exits[1]]
    assert named_move_exits(raw, list(reversed(exits))) == [exits[1]]


def test_npc_binding_preserves_mixed_action():
    raw = "乘务员，您先坐稳。我朝3号车厢走，去找人帮忙。"
    plan = plan_for(raw, "move", "next")
    plan.focus = TurnFocus(action="我朝3号车厢走，去找人帮忙。", action_target_id="next")
    plan.proposed_transition_id = "forward"
    repair_dialogue_focus(plan, raw, [{"id": "npc", "title": "乘务员"}])
    assert plan.focus.action and plan.focus.action_target_id == "next"
    assert plan.proposed_transition_id == "forward"


def test_movement_check_binds_local_obstacle_to_route():
    facts = facts_for("我谨慎地穿过这段通道，进入前面的房间。")
    actor = str(uuid4())
    facts.actor_member_id = actor
    facts.characters = {
        actor: {"ruleset_id": "coc7-character-creation", "skill_values": {"stealth": 50}}
    }
    facts.local_entity_ids = facts.visible_entity_ids = {"obstacle"}
    facts.transitions = {
        "forward": {"source_scene_node_id": "scene", "target_scene_node_id": "next"}
    }
    p = CheckProposal(
        target_member_id=actor,
        kind="skill",
        name="stealth",
        reason="通过",
        transition_id="forward",
        target_entity_id="obstacle",
        basis_entity_id="obstacle",
        necessity="required",
        uncertainty="不惊动附近的人",
        success_effect="安静通过",
        failure_consequence="没有取得通行机会",
        rule_topic_id="coc7.skill_check",
    )
    intent = plan_for(facts.raw_text, "move", "next").parsed_intent
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed
    p.transition_id = "unrelated"
    assert not CheckPolicyEvaluator().evaluate(p, intent, facts).allowed


@pytest.mark.parametrize(
    "event",
    [
        {
            "seq": 2,
            "type": "scene.updated",
            "payload": {"scene_title": "走廊", "visit_kind": "revisit"},
        },
        {
            "seq": 2,
            "type": "check.resolved",
            "payload": {"id": "check", "result": {"passed": True}},
        },
        {"seq": 2, "type": "module.interaction", "payload": {"text": "门打开了。"}},
    ],
)
def test_valid_result_prose_is_preserved(event):
    output = KeeperNarration(public_narration="门后的走廊露出来，你看见了墙上的指示牌。")
    assert (
        restore_output(
            output, KeeperNarration, {"public_tool_results": {"events": [event]}}
        ).public_narration
        == output.public_narration
    )


def test_npc_new_answer_is_not_replaced_by_previous_testimony():
    output = KeeperNarration(
        npc_speech={"entity_id": "npc", "text": "驾驶室在前头，可我现在走不动。"}
    )
    restored = restore_output(
        output, KeeperNarration, {"response_brief": {"dialogue_answers": ["我摔伤了腿。"]}}
    )
    assert restored.npc_speech.text == output.npc_speech.text


def test_serial_slot_is_fair_and_direct_question_takes_priority():
    policy = TeammateEligibilityPolicy()
    trigger = SimpleNamespace(seq=20, payload={"text": "我看看车门。"})
    events = [SimpleNamespace(seq=15, type="agent.teammate_decision", payload={"member_id": "a"})]
    common = dict(events=events, trigger=trigger, profile={"name": "周岚"})
    assert policy.priority(**common, member_id="b") < policy.priority(**common, member_id="a")
    assert policy.priority(**common, member_id="a", addressed="a") < policy.priority(
        **common, member_id="b"
    )


@pytest.mark.parametrize(
    "speech,reason",
    [
        ("周岚，你看着这里。", "addressing_self"),
        ("周岚和陈拓看起来没事。", "addressing_self"),
        ("我已经包扎好了，他的疼痛缓解了。", "unsettled_result"),
    ],
)
def test_teammate_publication_identity_and_result_boundary(speech, reason):
    decision = TeammateDecision(
        mode="act",
        speech_text=speech,
        action_text="我试着检查伤口。",
        related_player_action_seq=1,
        confidence=1,
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text="帮帮他。",
        player_intent=None,
        public_ids=set(),
        action_seq=1,
        fingerprint="a",
        actor_name="周岚",
    )
    assert not result.accepted and result.reason == reason


def test_goals_are_intentions_and_can_be_completed():
    policy = TeammateBehaviorPolicy()
    d = TeammateDecision(mode="pass", related_player_action_seq=1, confidence=1)
    state = policy.advance(
        BehaviorState(), d, cycle_id="a", fingerprint="f", safe_goal="检查伤口再决定是否急救"
    )
    assert state.current_short_term_goal == "检查伤口再决定是否急救"
    d.goal_status = "complete"
    assert (
        policy.advance(
            state, d, cycle_id="b", fingerprint="f", safe_goal=None
        ).current_short_term_goal
        == ""
    )


def test_unrelated_public_changes_do_not_reset_task_fingerprint():
    context = {
        "public_state": {"scene": "room", "last_change": 1},
        "public_entities": [{"id": "door", "public_summary": "关着"}],
    }
    original = public_fingerprint(context, "door", "actor")
    context["public_state"]["last_change"] = 9
    context["public_entities"].append({"id": "newspaper", "public_summary": "报纸公开了"})
    assert public_fingerprint(context, "door", "actor") == original


def test_generic_salutation_does_not_override_a_resolved_person():
    from app.agents.conversation import addressed_targets

    targets = [
        {"id": "npc", "title": "乘务员", "type": "npc"},
        {"id": "host", "title": "主机", "type": "member"},
        {"id": "player", "title": "林知秋", "type": "member"},
    ]
    assert addressed_targets("先生，您还听得见吗？我扶住他的肩。", targets) == {}
    assert set(addressed_targets("林知秋先生，你还好吗？", targets)) == {"player"}


def test_npc_repair_keeps_question_separate_from_simultaneous_action():
    from app.preparation.dialogue import repair_dialogue_focus

    raw = "先生，您还听得见吗？这里发生什么事了？我蹲下扶住他的肩。"
    plan = plan_for(raw, "interact", "npc")
    plan.focus = TurnFocus(
        question="您还听得见吗？这里发生什么事了？",
        action="我蹲下扶住他的肩。",
        addressee_id="npc",
        action_target_id="npc",
    )
    repair_dialogue_focus(plan, raw, [{"id": "npc", "title": "乘务员"}])
    assert plan.focus.question == "您还听得见吗？这里发生什么事了？"
    assert plan.focus.action == "我蹲下扶住他的肩。"
    assert not plan.parsed_intent.requires_clarification


def test_npc_question_and_separate_teammate_request_keep_both_recipients():
    from app.preparation.dialogue import repair_teammate_focus

    raw = "先生，您伤得怎么样？我扶住您的肩膀。周岚，帮我看看他的腿。"
    plan = plan_for(raw, "interact", "npc")
    plan.focus = TurnFocus(
        question="您伤得怎么样？",
        action="我扶住您的肩膀。",
        addressee_id="npc",
        action_target_id="npc",
    )
    npc = repair_dialogue_focus(
        plan, raw, [{"id": "npc", "title": "乘务员"}], member_names=["周岚"]
    )
    repair_teammate_focus(plan, raw, {"nurse": "周岚"}, "player")
    assert npc and plan.focus.addressee_id == "npc"
    assert plan.focus.question == "您伤得怎么样？"
    assert plan.addressed_member_id == "nurse"
    assert plan.focus.action == "我扶住您的肩膀。"


def test_request_to_nurse_is_not_human_treatment_but_nurse_attempt_is():
    from app.rooms.combat_service import CombatService

    room = SimpleNamespace(session_state={})
    assert not CombatService.route(
        room, "周岚，帮他包扎。我扶着他的肩膀。", members={"nurse": "周岚"}, actor="player"
    )
    assert CombatService.route(room, "我检查伤口，并尝试包扎。")
    assert CombatService.route(
        room, "周岚，帮他包扎。我挥拳打向怪物。", members={"nurse": "周岚"}, actor="player"
    )


def test_teammate_request_scope_excludes_human_search_and_party_route():
    from app.preparation.action_authority import addressed_request_text, requested_action_kinds

    raw = "那我先去3号车厢找黑包。周岚，你帮着照看乘务员；陈拓，留神周围。我走进3号车厢。"
    request = addressed_request_text(raw, "周岚")
    assert request == "你帮着照看乘务员"
    assert "search" not in requested_action_kinds(request)


def test_held_item_acknowledgement_does_not_replace_model_selected_movement():
    from app.preparation.search import repair_local_interaction_target

    raw = "钥匙收好了。我轻轻推开前面的门，小心地走进2号车厢，想看看通向驾驶室的路。"
    facts = facts_for(raw)
    facts.approved_entities["keys"] = {"type": "item", "title": "两把钥匙", "aliases": ["钥匙"]}
    facts.local_entity_ids.add("keys")
    facts.revealed_entity_ids.add("keys")
    plan = plan_for(raw, "move", "next")
    plan.focus = TurnFocus(action=raw, action_target_id="next")
    plan.proposed_transition_id = "forward"
    repair_local_interaction_target(plan, facts, {})
    assert plan.parsed_intent.type == "move"
    assert plan.proposed_transition_id == "forward"
    assert plan.focus.action_target_id == "next"


@pytest.mark.parametrize("body", ["", "乘务员微微点头，似乎能听见你的话。"])
def test_new_attempt_requires_its_own_reply_instead_of_old_claim_text(body):
    from app.agents.generation_contracts import generation_contract
    from app.models.base import ModelFormatError

    contract = generation_contract(
        KeeperNarration,
        {
            "current_scene_reference": "scene",
            "response_brief": {
                "attempt": "我扶着他的肩膀，让他坐稳。",
                "incidental_memories": [{"text": "乘务员微微点头，似乎能听见你的话。"}],
            },
        },
    )
    with pytest.raises(ModelFormatError):
        contract.model_validate({"public_narration": body})
    reply = contract.model_validate(
        {"public_narration": "你托住他的肩，让他靠在座位边，免得身子滑下去。"}
    )
    assert "托住" in reply.public_narration


@pytest.mark.parametrize(
    "text,moves",
    [
        ("我朝喘息声的方向照过去。", False),
        ("我把钥匙递过去。", False),
        ("我看准空当冲进去。", True),
    ],
)
def test_directional_complement_is_not_always_body_movement(text, moves):
    assert explicit_movement(text) is moves


def test_settled_narration_cannot_recreate_held_bag_on_floor():
    from app.preparation.inventory import validate_item_locations
    from app.rooms.service import RoomError

    inventory = {
        "holders": [{"item_id": "bag"}],
        "known_items": [{"id": "bag", "names": ["黑色包"]}],
    }
    with pytest.raises(RoomError):
        validate_item_locations("你看到一个黑色的包正躺在地上。", inventory)
    validate_item_locations("刚才黑色包躺在地上，现在由你保管。", inventory)
    validate_item_locations(
        "你看到黑色的包躺在地上。",
        {**inventory, "holders": [], "dropped_items": [{"item_id": "bag"}]},
    )


@pytest.mark.parametrize(
    "initial,repair",
    [
        ("plan_keeper_action", "repair_keeper_plan"),
        ("decide_teammates", "repair_teammate_decision"),
    ],
)
def test_repair_uses_same_transmitted_context_budget(initial, repair):
    from app.memory.service import prompt_context_size

    context = {
        "phase": initial,
        "omit_bound_prompt_metadata": True,
        "module_context_audit": {"source": "audit only" * 1000},
        "triggering_action": {"payload": {"text": "我靠近光圈边缘，观察前面的轮廓。"}},
    }
    assert prompt_context_size(context) == prompt_context_size({**context, "phase": repair})


def test_completed_transfer_does_not_erase_current_observation_and_question():
    from app.preparation.search import repair_local_interaction_target

    raw = "钥匙收好了。我抬起灯，观察前面的身影。那是谁？"
    facts = facts_for(raw)
    facts.approved_entities["keys"] = {"type": "item", "title": "两把钥匙", "aliases": ["钥匙"]}
    facts.local_entity_ids.add("keys")
    facts.revealed_entity_ids.add("keys")
    plan = plan_for(raw, "observe", "scene")
    plan.focus = TurnFocus(
        action="我抬起灯，观察前面的身影", action_target_id="scene", question="那是谁？"
    )
    repair_local_interaction_target(plan, facts, {})
    assert plan.parsed_intent.type == "observe"
    assert plan.focus.action_target_id == "scene"
    assert plan.focus.question == "那是谁？"


def test_local_model_target_survives_conflicting_exit_proposal():
    from app.agents.adjudication_schemas import KeeperPlan

    raw = "我过去看看那个背包。"
    plan = plan_for(raw, "move", "bag")
    plan.focus = TurnFocus(action=raw, action_target_id="bag")
    plan.proposed_transition_id = "forward"
    context = {
        "triggering_action": {"payload": {"text": raw}},
        "action_identifiers": {"current_scene_id": "scene"},
        "current_targets": [{"id": "bag", "title": "背包", "type": "item"}],
        "approved_exits": [
            {
                "transition_id": "forward",
                "target_scene_node_id": "next",
                "target_public_title": "大厅",
            }
        ],
    }
    restored = restore_output(plan, KeeperPlan, context)
    assert restored.focus.action_target_id == "bag"
    assert restored.proposed_transition_id is None


def test_being_addressed_does_not_release_an_unchanged_old_attempt():
    old = "我过去看看那个黑包，你注意脚下。"
    decision = TeammateDecision(
        mode="assist",
        action_type="investigate",
        action_text=old,
        related_player_action_seq=12,
        confidence=0.8,
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[old],
        other_outputs=[],
        player_text="陈拓，留意另一边的动静。",
        player_intent=None,
        public_ids=set(),
        action_seq=12,
        fingerprint="unchanged",
        explicit_action_request=True,
    )
    assert not result.accepted and result.reason == "repeated_output"


def test_crossing_a_named_exit_outranks_the_intermediate_local_passage():
    raw = "我穿过通往先头车厢的门再关上。"
    exit_ = {"transition_id": "ahead", "target_public_title": "先头车厢"}
    assert named_move_exits(raw, [exit_]) == [exit_]


def test_teammate_address_does_not_truncate_operations_around_embedded_speech():
    from app.preparation.dialogue import repair_teammate_focus

    own = "我轻轻敲门：师傅，您在里面吗？我拿出钥匙，试着打开门锁。"
    raw = "陈拓，你帮我听听里面。" + own
    plan = plan_for(raw, "interact", "door")
    plan.focus = TurnFocus(action=own, action_target_id="door", question="师傅，您在里面吗？")
    repair_teammate_focus(plan, raw, {"actor": "林知秋", "chen": "陈拓"}, "actor")
    assert plan.focus.action == own
    assert plan.focus.question == "师傅，您在里面吗？"


def test_named_peer_question_with_background_does_not_operate_controls():
    from app.preparation.dialogue import repair_teammate_focus

    raw = "陈拓，我没开过电车，有点不敢乱碰。右边往上是减速，你觉得应该先动哪个？"
    plan = plan_for(raw, "interact", "panel")
    plan.focus = TurnFocus(question="你觉得应该先动哪个？", addressee_id="chen")
    plan.proposed_transition_id = "wrong"
    repair_teammate_focus(plan, raw, {"actor": "林知秋", "chen": "陈拓"}, "actor")
    assert not plan.focus.action and plan.parsed_intent.type == "converse"
    assert plan.addressed_member_id == "chen" and plan.proposed_transition_id is None


def test_teammate_memory_gate_retains_abilities_without_full_skill_catalogue():
    from app.agents.action_runtime import generation_prompt

    context = {
        "characters": [
            {
                "member_id": "peer",
                "name": "陈拓",
                "occupation": "工匠",
                "skill_values": {**{str(i): 1 for i in range(150)}, "mechanical_repair": 70},
            }
        ]
    }
    transmitted = generation_prompt(context, TeammateDecision)
    assert transmitted["characters"][0]["abilities"]["mechanical_repair"] == 70
    assert len(transmitted["characters"][0]["abilities"]) == 8
    assert len(context["characters"][0]["skill_values"]) == 151


def test_own_control_attempt_keeps_authority_beside_a_peer_request():
    from app.preparation.action_authority import speaker_action, teammate_request

    own = "我试着把右边拉杆往上推一点，看看车速有没有变化。"
    raw = own + "陈拓，帮我留意车的反应。"
    assert speaker_action(own)
    assert teammate_request(raw, {"chen": "陈拓"}, "actor", action=own) is None
    assert not speaker_action("我不想推这个拉杆。")


def test_public_object_name_binds_its_unique_compatible_method():
    from app.preparation.search import repair_local_interaction_target

    raw = "我握住右側拉杆，往上推。"
    facts = facts_for(raw)
    facts.approved_entities["lever"] = {
        "type": "clue",
        "title": "控制拉杆说明",
        "aliases": [],
        "interactions": [
            {
                "id": "operate",
                "instruction": "操作拉杆",
                "public_result": "实际结果",
                "source_block_ids": ["source"],
                "kp_enabled": True,
                "action_kinds": ["control"],
            }
        ],
    }
    facts.local_entity_ids.add("lever")
    facts.revealed_entity_ids.add("lever")
    plan = plan_for(raw, "interact", "panel")
    plan.focus = TurnFocus(action=raw, action_target_id="panel")
    repair_local_interaction_target(plan, facts, {})
    assert plan.focus.action_target_id == "lever"
    facts.approved_entities["other"] = {**facts.approved_entities["lever"], "title": "另一拉杆"}
    facts.local_entity_ids.add("other")
    facts.revealed_entity_ids.add("other")
    plan.focus.action_target_id = "panel"
    repair_local_interaction_target(plan, facts, {})
    assert plan.focus.action_target_id == "panel"  # Ambiguity cannot choose for the player.


def test_obstacle_focus_does_not_erase_model_resolved_destination_synonym():
    raw = "现在就跑过去！我不停下来查看了，直接冲进1号车厢。"
    plan = plan_for(raw, "move", "obstacle")
    plan.focus = TurnFocus(action=raw, action_target_id="obstacle")
    plan.proposed_transition_id = "ahead"
    restored = restore_output(
        plan,
        type(plan),
        {
            "triggering_action": {"payload": {"text": raw}},
            "current_targets": [{"id": "obstacle", "title": "逐渐消失的立足点", "type": "clue"}],
            "approved_exits": [
                {
                    "transition_id": "ahead",
                    "target_scene_node_id": "head",
                    "target_public_title": "先头车厢",
                }
            ],
        },
    )
    assert restored.proposed_transition_id == "ahead"
    assert restored.focus.action_target_id == "head" and restored.parsed_intent.type == "move"


def test_pending_ending_continues_original_action_only_after_every_actual_consequence(
    client,
    interactions,  # noqa: F811
):
    from copy import deepcopy

    from app.persistence.adjudication_models import ActionPlanRecord
    from app.persistence.agent_models import CheckRecord
    from app.persistence.room_models import RoomEvent
    from app.preparation.runtime import complete_pending_outcome
    from app.rooms.combat_service import load_state

    d, action = interactions
    svc = client.app.state.agent_service

    async def configure():
        async with svc.rooms.transaction() as session:
            e = await svc.entities.entity(session, d["room"]["id"], d["item"])
            e.snapshot = {
                **e.snapshot,
                "interactions": [
                    rule(kp_enabled=True, prepare_outcome="B", action_kinds=["control"])
                    .model_copy(update={"id": "begin"})
                    .model_dump(),
                    rule(
                        kp_enabled=True,
                        outcome="B",
                        action_kinds=["converse"],
                        mythos_reward=3,
                        required_sanity=[{"entity_id": d["item"], "effect_id": "ending"}],
                    )
                    .model_copy(update={"id": "finish"})
                    .model_dump(),
                ],
            }

    client.portal.call(configure)
    begin = client.portal.call(action, "begin", d["player"], "我推动控制杆，让车停下来。")

    async def check_continuation():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            e = await session.get(RoomEvent, (room.id, begin["source_event_seq"]))
            e.payload = {**e.payload, "cycle_id": begin["cycle_id"]}
            original = deepcopy((await session.get(ActionPlanRecord, begin["cycle_id"])).document)
            result = await complete_pending_outcome(svc, session, room)
            assert (await session.get(ActionPlanRecord, begin["cycle_id"])).document == original
            return result, load_state(room).model_dump(mode="json")

    async def settle(member):
        async with svc.rooms.transaction() as session:
            session.add(
                CheckRecord(
                    id=str(uuid4()),
                    room_id=d["room"]["id"],
                    cycle_id=begin["cycle_id"],
                    target_member_id=member,
                    agent_run_id="fixture",
                    status="resolved",
                    document={
                        "sanity": {
                            "stage": "done",
                            "entity_id": d["item"],
                            "effect": {"id": "ending"},
                        }
                    },
                )
            )

    for member in (d["player"], d["agent"]):
        receipt, state = client.portal.call(check_continuation)
        assert receipt is None and state["module_runtime"]["pending_outcome"] == "B"
        client.portal.call(settle, member)
    receipt, state = client.portal.call(check_continuation)
    assert receipt and not receipt["host_confirmed"]
    assert receipt["source_event_seq"] == begin["source_event_seq"]
    assert state["module_runtime"]["outcome"] == "B"
    assert all(c["sanity"]["mythos_gain"] == 3 for c in state["characters"].values())
    repeated, again = client.portal.call(check_continuation)
    assert repeated is None and state == again
