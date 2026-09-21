"""Receipts, scoped cancellation and current treatment availability regressions."""

from types import SimpleNamespace

import pytest
from test_action_adjudication import plan_for

from app.agents.adjudication_schemas import BehaviorState, TeammateDecision, TurnFocus
from app.agents.behavior import TeammateBehaviorPolicy
from app.agents.results import answer_result_error, result_error, result_facts
from app.memory.facts import answer_facts, fact_records, select_facts
from app.preparation.turn_focus import reconcile_requests, repair_attribution
from app.rooms.combat_schemas import Combatant
from app.rooms.combat_service import treatment_restriction
from app.rooms.schemas import SessionStateV1


@pytest.mark.parametrize("stop", ["先别再包扎了", "不要继续治疗了", "先不用处理伤口了"])
def test_stop_treatment_and_position_patient_are_separate_requests(stop):
    raw = f"周岚，{stop}，让他靠稳。"
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(question=raw, addressee_id="nurse")
    repair_attribution(plan, raw, {"nurse": "周岚"}, "player")
    assert [r.kind for r in plan.focus.requests] == ["cancel", "delegate"]
    assert "first_aid" not in plan.focus.requests[1].operations
    assert all(raw[r.source_start : r.source_end] == r.text for r in plan.focus.requests)
    state = BehaviorState(
        pending_requests=[
            {"key": "aid", "kind": "delegate", "text": "帮伤者止血。", "operations": ["first_aid"]},
            {"key": "door", "kind": "delegate", "text": "检查门。", "operations": ["search"]},
        ]
    )
    reconcile_requests(state, plan.focus.requests, seq=9, scene_id="s", reachable_ids=set())
    assert state.request_history[0]["key"] == "aid"
    assert any(r["key"] == "door" for r in state.pending_requests)
    assert state.pending_requests[-2]["cancelled_keys"] == ["aid"]


def event(seq, kind, payload):
    return {
        "seq": seq,
        "type": kind,
        "payload": payload,
        "visibility": "public",
        "actor_member_id": "player",
    }


def test_result_recall_prefers_actual_light_receipt_and_retains_history():
    events = [
        event(1, "game.started", {}),
        event(
            2,
            "module.interaction",
            {
                "cycle_id": "old",
                "text": "拿到了钥匙。",
                "operations": ["take"],
                "passed": True,
            },
        ),
    ]
    for seq, status in ((3, "not_executed"), (5, "success")):
        events.append(
            event(
                seq,
                "action.result",
                {
                    "cycle_id": str(seq),
                    "facts": [
                        {
                            "actor_id": "player",
                            "target_id": "lamp",
                            "target_name": "壁灯",
                            "operation": "light",
                            "status": status,
                            "cycle_id": str(seq),
                            "effect": "关闭壁灯。",
                            "source_event_seq": seq - 1,
                        }
                    ],
                },
            )
        )
    selected = select_facts(events, [], "陈拓，刚才壁灯到底关掉了吗？", members={"chen": "陈拓"})
    assert selected and all(r.get("result_fact", {}).get("operation") == "light" for r in selected)
    assert "成功" in answer_facts(selected, "刚才壁灯关掉了吗？")
    history = select_facts(events, [], "第一次关壁灯是什么结果？")
    assert "没有执行成功" in answer_facts(history, "第一次关壁灯是什么结果？")
    assert "钥匙" not in answer_facts(selected, "刚才壁灯关掉了吗？")


def test_saved_receipt_keeps_cycle_and_actual_instance_in_memory():
    item = {"id": "shoe", "instance_id": "shoe:left", "names": ["鞋"]}
    raw = event(
        4,
        "module.interaction",
        {
            "cycle_id": "throw-cycle",
            "actor_id": "player",
            "target_id": "hall",
            "target_name": "走廊",
            "operations": ["throw"],
            "passed": True,
            "text": "鞋落地发出声响。",
            "operated_items": [item],
        },
    )
    facts = result_facts([raw])
    public = event(5, "action.result", {"cycle_id": "throw-cycle", "facts": facts})
    records = fact_records([event(1, "game.started", {}), raw, public])
    recalled = next(r["result_fact"] for r in records if r.get("result_fact"))
    assert recalled == facts[0]
    assert recalled["operated_items"] == [item]
    keys = {"id": "keys", "names": ["钥匙"]}
    assert result_error("钥匙已经扔出去了。", [recalled], items=[keys])
    assert not result_error("鞋已经扔出去了。", [recalled], items=[keys])


def test_treatment_restrictions_follow_current_injury_not_old_failure_only():
    state = SessionStateV1()
    patient = Combatant(
        id="p",
        label="伤者",
        scene_id="s",
        hp=3,
        hp_max=10,
        attributes={"con": 50},
        skills={},
        weapons=[],
        source="fixture",
        injury={"first_aid_attempted": True, "last_damage_minute": 0},
    )
    assert treatment_restriction(state, patient, "first_aid")
    patient.injury.dying = True  # the existing emergency exception remains valid
    assert not treatment_restriction(state, patient, "first_aid")
    patient.injury.dying = False
    patient.injury.first_aid_attempted = False  # a genuinely new injury
    assert not treatment_restriction(state, patient, "first_aid")


def test_teammate_cannot_call_same_forbidden_aid_an_adjustment():
    args = dict(
        state=BehaviorState(last_result={"kind": "blocked"}),
        recent_outputs=[],
        other_outputs=[],
        player_text="我们接下来怎么办？",
        player_intent=SimpleNamespace(),
        public_ids={"p"},
        action_seq=1,
        fingerprint="new",
        actor_id="nurse",
        treatment_options=[
            {
                "target_id": "p",
                "target_name": "伤者",
                "operation": "first_aid",
                "restriction": "此伤势已尝试急救",
            }
        ],
    )
    choice = TeammateDecision(
        mode="act",
        action_text="我先帮伤者包扎。",
        target_id="p",
        goal_status="adjust",
        related_player_action_seq=1,
        confidence=1,
    )
    rejected = TeammateBehaviorPolicy().validate(choice, **args)
    assert not rejected.accepted and rejected.reason == "treatment_not_available"
    choice.action_text = "我扶稳伤者，让他靠在墙边。"
    assert TeammateBehaviorPolicy().validate(choice, **args).accepted


def test_switching_light_off_cannot_improve_unaided_observation():
    fact = {
        "operation": "light",
        "status": "success",
        "source_event_seq": 8,
        "effect": "你关闭了手电筒。",
    }
    assert result_error("现在能更清楚地观察伤口了。", [fact])
    assert not result_error("关灯后看不清伤口，我先扶稳他。", [fact])


def test_legacy_wrong_target_and_other_success_cannot_answer_current_question():
    action = event(1, "action.submitted", {"text": "我拿起工具箱。", "cycle_id": "c"})
    saved = event(
        2,
        "action.result",
        {
            "cycle_id": "c",
            "facts": [
                {
                    "actor_id": "player",
                    "operation": "take",
                    "status": "not_executed",
                    "target_id": "keys",
                    "target_name": "两把钥匙",
                    "source_event_seq": 1,
                }
            ],
        },
    )
    facts = result_facts([action, saved])
    assert facts[0]["target_name"] is None
    assert facts[0]["reported_target_name"] == "两把钥匙"
    facts.append(
        {
            "actor_id": "player",
            "operation": "take",
            "status": "success",
            "target_id": "keys",
            "target_name": "两把钥匙",
            "source_event_seq": 3,
        }
    )
    assert answer_result_error("你已经成功取走了两把钥匙。", "刚才工具箱拿到手了吗？", facts)
    assert not answer_result_error("工具箱没有拿到手。", "刚才工具箱拿到手了吗？", facts)


def test_result_question_to_keeper_cannot_be_routed_to_a_silent_host_member():
    from app.agents.adjudication_schemas import KeeperPlan
    from app.agents.generation_contracts import generation_contract, restore_output

    text = "灯真的暗下来了吗？"
    context = {
        "readonly_recall": True,
        "triggering_action": {"seq": 2, "actor_member_id": "player", "payload": {"text": text}},
        "current_participants": {"members": {"host": "KP", "other": "同伴"}},
        "action_identifiers": {
            "plan_id": "p",
            "cycle_id": "c",
            "actor_member_id": "player",
            "actor_character_slot_id": "slot",
            "current_scene_id": "scene",
            "expected_navigation_revision": 0,
        },
    }
    schema = generation_contract(KeeperPlan, context)
    result = restore_output(
        schema(
            parsed_intent={"type": "converse"},
            focus={"question_clause_ids": ["u1"], "addressee_id": "host"},
        ),
        KeeperPlan,
        context,
    )
    assert result.parsed_intent.type == "recall"
    assert not result.focus.addressee_id and not result.addressed_member_id


def test_device_component_does_not_request_a_second_item_but_two_items_remain_distinct():
    from app.preparation.action_authority import freeze_action
    from app.preparation.runtime_schemas import ModuleRuntimeState

    entities = {
        "camera": {"type": "item", "title": "相机"},
        "light": {"type": "item", "title": "照明灯"},
    }
    runtime = ModuleRuntimeState(inventory={"camera": "player", "light": "player"})
    for text, expected in [
        ("我打开相机的照明灯。", {"camera"}),
        ("我打开相机的照明灯，再打开照明灯。", {"camera", "light"}),
    ]:
        plan = plan_for(text, "use_item", "camera")
        plan.focus = TurnFocus(action=text, action_target_id="camera")
        authority = freeze_action(plan, text, "player", 1, "s", entities, runtime, {})
        assert set(authority["operation_item_ids"]["light"]) == expected


def test_observation_fallback_names_the_requested_subject_not_the_carried_device():
    from app.agents.narration import fallback_narration

    answer = fallback_narration(
        "investigate",
        {"events": []},
        "",
        brief={
            "attempt": "我拿着相机，沿墙找找屋里的电闸。",
            "observation_subject": {"title": "相机", "public_summary": "相机挂在胸前。"},
        },
    )
    assert "电闸" in answer and "相机" not in answer
    assert "还没查清" in answer


def test_support_is_a_concrete_small_action_separate_from_medical_treatment():
    from app.preparation.action_authority import action_kinds

    for text in ("我调整他的姿势，让他靠在墙边。", "我先协助他稳定身体。"):
        assert "support" in action_kinds(text)
        assert not {"first_aid", "medicine", "carry"} & set(action_kinds(text))


def test_unnamed_followup_uses_latest_attempt_not_another_devices_success():
    from app.agents.results import matching_results

    facts = [
        {
            "operation": "light",
            "cycle_id": "before",
            "source_event_seq": 1,
            "target_id": "phone",
            "status": "success",
        },
        {
            "operation": "light",
            "cycle_id": "now",
            "source_event_seq": 2,
            "target_id": None,
            "status": "not_executed",
        },
    ]
    assert matching_results(facts, "灯真的暗下来了吗？") == facts[1:]
    assert answer_result_error("灯光昏暗，但并未完全熄灭。", "灯真的暗下来了吗？", facts)


def test_narration_projection_keeps_one_complete_receipt_and_unchanged_ledger():
    from copy import deepcopy

    from app.agents.action_runtime import generation_prompt
    from app.agents.adjudication_schemas import KeeperNarration

    fact = {
        "actor_id": "p",
        "cycle_id": "c",
        "source_event_seq": 3,
        "target_id": "camera",
        "operation": "light",
        "status": "success",
        "effect": "照明灯亮了。",
        "operated_items": [{"id": "camera", "instance_id": "one"}],
    }
    receipts = {
        "events": [event(3, "module.interaction", {"text": fact["effect"]})],
        "result_facts": [fact],
        "current_result_facts": [fact],
    }
    context = {
        "response_brief": {"completed_results": receipts, "result_facts": [fact]},
        "public_tool_results": receipts,
    }
    original = deepcopy(context)
    projected = generation_prompt(context, KeeperNarration)
    assert context == original
    assert projected["public_tool_results"]["result_facts"] == [{**fact, "current": True}]
    assert "current_result_facts" not in projected["public_tool_results"]
    assert "completed_results" not in projected["response_brief"]


def test_bounded_exit_projection_keeps_every_destination_and_requirement():
    from app.agents.action_runtime import planning_prompt

    routes = [{"transition_id": str(i), "target_scene_node_id": "node" + str(i),
               "target_entity_id": "scene" + str(i), "target_public_title": "目的地" + str(i),
               "target_description": "重复的目的地介绍" * 20,
               "required_flags": {"open": True}, "required_item_ids": ["pass"]}
              for i in range(8)]
    prompt = planning_prompt({"omit_bound_prompt_metadata": True, "approved_exits": routes})
    assert len(prompt["approved_exits"]) == 8
    for original, projected in zip(routes, prompt["approved_exits"], strict=True):
        assert projected == {k: v[:60] if k == "target_description" else v
                             for k, v in original.items() if k != "target_scene_node_id"}


def test_names_and_nouns_do_not_grant_a_push_pull_operation():
    from app.preparation.action_authority import action_kinds

    for text in ("我想打听尼古拉的事。", "我去拉萨找朋友。", "我看看拉杆。", "我推测有人来过。"):
        assert "control" not in action_kinds(text)
    for text in ("我拉右侧拉杆。", "我推动把手。", "我把门往外拉。", "轻轻推一下门。"):
        assert "control" in action_kinds(text)


@pytest.mark.parametrize("kinds", [["converse"], ["converse", "rest"]])
def test_new_npc_question_can_attempt_only_social_gate_and_recall_cannot(kinds):
    from app.preparation.action_authority import freeze_action, selected_action_matches
    from app.preparation.runtime_schemas import ModuleInteraction, ModuleRuntimeState

    entity = {"witness": {"type": "npc", "title": "证人"}}
    defaults = {"instruction": "尝试取得证人信任", "source_block_ids": ["source"],
                "public_result": "证人回答问题。"}
    rule = ModuleInteraction(id="interview", action_kinds=kinds, check_name="charm",
                             **defaults)
    for text, allowed in (("您认识那位失踪者吗？", True), ("刚才的询问成功了吗？", False)):
        plan = plan_for(text, "converse", "witness")
        plan.focus = TurnFocus(action=text, question=text, addressee_id="witness",
                               action_target_id="witness")
        authority = freeze_action(plan, text, "p", 1, "s", entity, ModuleRuntimeState(), {})
        assert selected_action_matches(authority, text, rule) is allowed
        assert not selected_action_matches(authority, text, ModuleInteraction(
            id="search", action_kinds=["search"], check_name="spot_hidden", **defaults,
        ))


def test_budget_projection_retains_instances_aliases_and_target_scope():
    from copy import deepcopy

    from app.agents.action_runtime import generation_prompt
    from app.agents.adjudication_schemas import KeeperPlan

    target = {"id": "w", "type": "npc", "title": "证人", "fact_scope": "historical"}
    context = {
        "omit_bound_prompt_metadata": True,
        "current_targets": [target], "response_fact_candidates": [target],
        "inventory_state": {
            "holders": [{"item_id": "pen", "instance_id": "one", "title": "钢笔",
                         "holder_id": "p", "remaining_uses": None},
                        {"item_id": "lamp", "instance_id": "two", "title": "灯",
                         "holder_id": "q", "remaining_uses": 0}],
            "known_items": [{"id": "pen", "names": ["钢笔", "钢笔"]},
                            {"id": "lamp", "names": ["灯", "煤油灯", "灯"]}],
        },
    }
    original = deepcopy(context)
    view = generation_prompt(context, KeeperPlan)
    assert context == original
    assert view["current_targets"] == []
    assert view["response_fact_candidates"] == [target]
    assert view["inventory_state"]["known_items"] == [{"id": "lamp", "names": ["灯", "煤油灯"]}]
    assert view["inventory_state"]["holders"] == [
        {k: v for k, v in h.items() if v is not None}
        for h in original["inventory_state"]["holders"]
    ]


def test_new_conversation_request_does_not_fall_back_to_old_same_person_receipt():
    from app.agents.results import result_reply

    facts = [{"actor_id": "p", "operation": "search", "status": "not_executed",
              "source_event_seq": 2, "action_text": "想打听旧友的事", "cycle_id": "old"}]
    assert result_reply(facts, "你来和那位先生聊聊旧友吧，", "q") == ("", [])


def test_unique_given_name_request_never_becomes_players_search():
    from app.preparation.action_authority import addressed_spans, teammate_request

    people = {"p": "丹尼斯·李", "q": "玛丽·琼斯"}
    text = "玛丽，帮我检查一下地面吧。"
    plan = plan_for(text, "investigate", "floor")
    plan.focus = TurnFocus(action=text, action_target_id="floor")
    repair_attribution(plan, text, people, "p")
    assert not plan.focus.action
    assert plan.proposed_check is None
    assert plan.focus.requests[0].addressee_id == "q"
    assert plan.focus.requests[0].kind == "delegate"
    assert teammate_request(text, people, "p") == "q"
    assert not addressed_spans(text, {**people, "r": "玛丽·王"})
    assert addressed_spans("甲，帮忙检查。", {"q": "甲"})


def test_raw_and_persisted_movement_are_one_receipt_with_actual_target():
    original = event(2, "scene.updated", {"cycle_id": "c", "scene_title": "庭院"})
    raw = result_facts([original])[0]
    saved = event(3, "action.result", {"cycle_id": "c", "facts": [{**raw, "target_id": "yard"}]})
    assert result_facts([original, saved]) == [{**raw, "target_id": "yard"}]


def test_advice_about_looking_does_not_require_an_actual_search_clause():
    from app.agents.adjudication_schemas import KeeperPlan
    from app.agents.generation_contracts import generation_contract, restore_output

    text = "玛丽，我有点不敢推开门。你觉得该进去看，还是留在外面，看看是谁来？"
    context = {"action_identifiers": {
        "plan_id": "plan", "cycle_id": "cycle", "actor_member_id": "p",
        "actor_character_slot_id": "slot", "current_scene_id": "scene",
        "expected_navigation_revision": 0,
    }, "triggering_action": {"seq": 1, "actor_member_id": "p", "payload": {"text": text}},
        "current_participants": {"members": {"p": "丹尼斯·李", "q": "玛丽·琼斯"}}}
    schema = generation_contract(KeeperPlan, context)
    plan = restore_output(schema(parsed_intent={"type": "converse"}, focus={
        "action_clause_ids": [], "question_clause_ids": ["u3", "u4", "u5"],
        "addressee_id": "q",
    }), KeeperPlan, context)
    assert not plan.focus.action
    assert not plan.proposed_check


def test_actual_method_receipt_keeps_authorized_operand_and_original_action():
    raw = event(8, "module.interaction", {
        "cycle_id": "c", "source_event_seq": 5, "actor_id": "p",
        "entity_id": "method-container", "action_target_id": "actual-object",
        "action_text": "我拿走盒子。", "operations": ["take"], "passed": True,
        "operated_items": [{"id": "box", "instance_id": "box-one", "names": ["盒子"]}],
        "text": "盒子已经拿到。",
    })
    fact = result_facts([raw])[0]
    saved = event(9, "action.result", {"cycle_id": "c", "facts": [fact]})
    assert result_facts([raw, saved]) == [fact]
    assert fact["source_action_seq"] == 5
    assert fact["source_event_seq"] == 8
    assert fact["action_target_id"] == "actual-object"
    assert fact["action_text"] == "我拿走盒子。"
    assert any(r["kind"] == "result" for r in fact_records([raw, saved]))


def test_only_unconditional_nonvisual_reveals_can_recover_automatically():
    from app.rooms.encounters import unconditional_reveal

    effect = {"trigger": "entity_revealed", "automation": "automatic", "kp_enabled": True}
    assert unconditional_reveal(effect)
    for extra in ({"trigger": "action_target"}, {"automation": "host_review"},
                  {"condition": "理解内容"}, {"perception": "visual"},
                  {"visibility_any_flags": ["light"]}):
        assert not unconditional_reveal({**effect, **extra})


def test_recovered_sanity_must_be_queued_for_exact_source_effect_and_witness():
    from app.rooms.encounters import queued_automatic_reveal
    from app.rooms.sanity_schemas import SanityEffect

    effect = SanityEffect(id="truth", encounter="事实公开", source="fixture", page=1,
                          basis="已批准效果", trigger="entity_revealed", automation="automatic",
                          kp_enabled=True, failure_loss="1d4")
    source = SimpleNamespace(type="entity.revealed", seq=5, payload={"id": "clue"})
    entry = {"status": "approved", "origin": "automatic", "source_event_seq": 5,
             "entity_id": "clue", "effect_id": "truth", "target_member_ids": ["p"]}
    assert queued_automatic_reveal(effect, source, [entry], "p")
    assert not queued_automatic_reveal(effect, source, [entry], "q")
    for changes in ({"source_event_seq": 6}, {"effect_id": "other"},
                    {"entity_id": "other"}, {"status": "rejected"}):
        assert not queued_automatic_reveal(effect, source, [{**entry, **changes}], "p")


@pytest.mark.parametrize("question", ["刚才找到开关了吗？", "先前把箱子放好了没有？",
                                     "这次把窗户擦干净了吗？"])
def test_completed_action_followup_does_not_schedule_another_attempt(question):
    from app.memory.facts import readonly_recall

    assert readonly_recall(question)
    assert not readonly_recall(question + "我再仔细检查窗台。")
    decision = TeammateDecision(mode="act", action_text="我还没看到开关。",
                                related_player_action_seq=10, confidence=1)
    result = TeammateBehaviorPolicy().validate(
        decision, state=BehaviorState(), recent_outputs=[], other_outputs=[],
        player_text=question, player_intent="converse", public_ids=set(),
        action_seq=10, fingerprint="followup",
    )
    assert not result.accepted
    assert result.reason == "recall_has_no_new_action"


def test_search_result_cannot_borrow_an_operation_on_the_kind_of_object_sought():
    from app.agents.results import matching_results, question_operations

    phone = {"actor_id": "p", "operation": "light", "status": "success",
             "target_name": "手机", "action_text": "我把手机灯关掉。", "cycle_id": "c",
             "source_event_seq": 5, "effect": "手机灯关了。"}
    assert question_operations("刚才找到开关了吗？") == {"search"}
    assert question_operations("急救包找到了吗？") == {"search"}
    assert not matching_results([phone], "刚才找到开关了吗？")
    assert matching_results([phone], "手机灯关掉了吗？") == [phone]
    old_note = {**phone, "operation": "search", "target_name": "便签",
                "action_text": "我翻看便签背面。", "effect": "背面写着钥匙的去向。"}
    assert not matching_results([phone, old_note], "刚才找到开关了吗？")
    assert answer_facts([{"result_fact": old_note}], "刚才找到开关了吗？") == "还没有确认找到开关。"
    assert answer_result_error(
        "乘客坐在地上。", "刚才找到开关了吗？", [],
    ) == "unrelated_result_target"
    assert answer_result_error(
        "已经找到开关。", "刚才找到开关了吗？", [],
    ) == "unconfirmed_result:search"
    assert not answer_result_error("还没有确认找到开关。", "刚才找到开关了吗？", [])
    assert answer_result_error("照明操作没成功。可以查看开关。", "刚才找到开关了吗？", [])


def test_valid_missing_result_answer_is_not_replaced_by_unrelated_old_excerpt():
    from app.agents.generation_contracts import restore_output

    context = {"readonly_recall": True,
               "triggering_action": {"seq": 1, "payload": {"text": "刚才找到开关了吗？"}},
               "fact_evidence": [{"id": "old", "kind": "source_text",
                                  "text": "便签上写着钥匙的位置。"}]}
    answer = TeammateDecision(mode="speak", speech_text="还没有确认找到开关。",
                              related_player_action_seq=1, confidence=1)
    restored = restore_output(answer, TeammateDecision, context)
    assert restored.speech_text == answer.speech_text
