"""Actual attempts, per-operation receipts and cancellation across a saved queue."""

import pytest
from test_action_adjudication import plan_for
from test_batch16 import module_battle  # noqa: F401
from test_batch17 import interactions  # noqa: F401
from test_batch18 import test_permissive_model_cannot_turn_observation_into_throw as reject_throw
from test_batch36_collaboration import team_game  # noqa: F401
from test_module_navigation import structure_data  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import lobby  # noqa: F401

from app.agents.adjudication_schemas import BehaviorState, TeammateDecision, TurnFocus, TurnRequest
from app.agents.behavior import TeammateBehaviorPolicy
from app.agents.results import result_error, result_facts
from app.preparation.action_authority import (
    action_kinds,
    freeze_action,
    selected_action_matches,
)
from app.preparation.runtime_schemas import ModuleInteraction, ModuleRuntimeState
from app.preparation.turn_focus import reconcile_requests, repair_attribution


@pytest.mark.parametrize("text", [
    "我摸摸口袋，找找有什么能拿来扔远一点的东西。",
    "我翻找衣袋里的杂物，看看哪些可以用来抛向远处。",
    "我摸摸自己的口袋，看看还带着什么小东西，能拿来扔远一点引开它。",
])
def test_search_purpose_never_authorizes_throw_even_as_selected_substring(text):
    plan = plan_for(text, "investigate", "scene")
    plan.focus = TurnFocus(action=text, action_target_id="scene")
    authority = freeze_action(plan, text, "actor", 1, "scene", {}, ModuleRuntimeState(), {})
    rule = ModuleInteraction(id="throw", instruction="throw", action_kinds=["throw"],
                             source_block_ids=["b"], public_result="投掷结算。")
    assert "search" in authority["kinds"] and "throw" not in authority["kinds"]
    assert not selected_action_matches(authority, text, rule)
    assert not selected_action_matches(authority, "扔远一点", rule)
    positive = "我把找到的钥匙扔向远处。"
    plan.focus.action = positive
    authority = freeze_action(plan, positive, "actor", 2, "scene", {}, ModuleRuntimeState(), {})
    assert selected_action_matches(authority, positive, rule)


@pytest.mark.parametrize("text", ["我从包里拿出钥匙。", "我准备取出钥匙。", "我试着掏出钥匙。"])
def test_natural_take_keeps_its_own_authority_with_a_separate_delegate(text):
    raw = text + "陈拓，你检查一下通道。"
    plan = plan_for(raw, "observe", "keys")
    plan.focus = TurnFocus(question=raw, addressee_id="chen")
    repair_attribution(plan, raw, {"chen": "陈拓"}, "actor")
    assert "take" in action_kinds(plan.focus.action)
    assert plan.focus.requests[0].kind == "delegate"
    authority = freeze_action(plan, raw, "actor", 1, "scene", {}, ModuleRuntimeState(), {})
    assert "take" in authority["kinds"] and "search" not in authority["kinds"]


def test_spatial_search_and_polite_delegate_keep_the_attempt_before_result_question():
    from app.preparation.action_authority import requested_action_kinds, speaker_action

    assert speaker_action("我沿门边摸索有没有照明开关。")
    assert "observe" in requested_action_kinds("帮我看看脚下的行李有没有挡路。")
    assert not action_kinds("如果有响声，我把手机扔出去。")
    assert not requested_action_kinds("帮我看看这个办法行不行。")


def test_medical_tool_claim_requires_actual_inventory_but_hands_are_allowed():
    from app.preparation.inventory import bind_item_prose
    from app.rooms.service import RoomError

    view = {"holders": [], "known_items": []}
    with pytest.raises(RoomError):
        bind_item_prose("我用随身携带的急救包为乘务员处理伤口。", view, "zhou")
    assert not bind_item_prose("我用手压住伤口。", view, "zhou")


@pytest.mark.parametrize("raw,item", [
    ("钥匙后面还用得上。我脱下一只鞋，把鞋朝远处扔过去。", None),
    ("手机留着联系别人。我把帽子抛向另一头。", None),
    ("我拿出钥匙，把它扔向远处。", "keys"),
    ("我把手机收好，把钥匙扔到远处。", "keys"),
])
def test_operated_item_cannot_come_from_an_unrelated_fragment(raw, item):
    from app.preparation.action_authority import authority_error
    from app.preparation.adjudication import decision_contract

    runtime = ModuleRuntimeState(inventory={"keys": "actor", "phone": "actor"})
    entities = {"keys": {"title": "钥匙", "type": "item"},
                "phone": {"title": "手机", "type": "item"}}
    plan = plan_for(raw, "interact", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene")
    authority = freeze_action(plan, raw, "actor", 1, "scene", entities, runtime, {})
    rule = ModuleInteraction(id="throw", instruction="投掷", encounter_operation="sound_once",
                             allow_worn_sound_item=True, source_block_ids=["b"],
                             public_result="产生声响。")
    for chosen in ("keys", "phone", None):
        error = authority_error(authority, rule, runtime, actor="actor", seq=1,
                                scene="scene", item_id=chosen)
        assert (error is None) == (chosen == item)
    schema = decision_contract({"actor": "actor", "action_authority": authority,
        "items": [{"holder_id": "actor", "item_id": i, "instance_id": i} for i in entities],
        "members": {}, "npc_instances": [], "clauses": [{"id": "u1", "text": raw}]},
        [{"option": "1", "rule": rule.model_dump()}])
    from pydantic import ValidationError

    assert schema(used_item_id=item).used_item_id == item
    with pytest.raises(ValidationError):
        schema(used_item_id="phone")


def test_item_result_cannot_back_another_item_of_the_same_operation():
    items = [{"id": "keys", "names": ["钥匙"]}, {"id": "phone", "names": ["手机"]}]
    facts = result_facts([{"seq": 7, "type": "module.interaction", "payload": {
        "actor_id": "actor", "target_id": "scene", "operations": ["throw"], "passed": True,
        "operated_items": [items[0]], "text": "实际投出钥匙，产生声响。",
    }}])
    assert not result_error("我把钥匙扔出去了。", facts, actor_id="actor", items=items)
    assert result_error("我把手机扔出去了。", facts, actor_id="actor", items=items)
    assert not result_error("我准备把手机扔出去。", facts, actor_id="actor", items=items)


@pytest.mark.parametrize("scope,allowed", [("current_scene", True), ("historical", False)])
def test_named_visible_patient_is_enough_for_an_actual_aid_attempt(scope, allowed):
    decision = TeammateDecision(mode="assist", action_type="observe", target_id="patient",
        action_text="我立刻上前，用手压住乘务员的伤口。", speech_text="周岚在这里，我先处理伤口。",
        related_player_action_seq=1, confidence=1)
    result = TeammateBehaviorPolicy().validate(decision, state=BehaviorState(),
        recent_outputs=[], other_outputs=[], player_text="周岚，帮他止一下血。",
        player_intent=plan_for("帮他止血", "converse").parsed_intent,
        public_ids={"scene"}, action_seq=1, fingerprint="same", actor_id="zhou", actor_name="周岚",
        explicit_action_request=True, requested_operations=["first_aid"],
        requested_targets=["patient"], inventory_state={"holders": [], "known_items": [],
            "other_actors": [{"id": "patient", "names": ["乘务员"], "fact_scope": scope}]})
    assert result.accepted is allowed
    assert decision.action_text.startswith("我立刻上前")
    assert "first_aid" in action_kinds(decision.action_text)


def test_natural_medical_request_is_not_completed_by_speech():
    raw = "周岚，麻烦帮他止一下血。"
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(question=raw, addressee_id="zhou")
    repair_attribution(plan, raw, {"zhou": "周岚"}, "actor")
    assert plan.focus.requests[0].kind == "delegate"
    assert plan.focus.requests[0].operations == ["first_aid"]
    decision = TeammateDecision(mode="speak", speech_text="我来帮他止一下血。", confidence=1,
                                related_player_action_seq=1)
    state = BehaviorState(task_status="pending")
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=state, recent_outputs=[], other_outputs=[], player_text=raw,
        player_intent=plan.parsed_intent, public_ids=set(), action_seq=1, fingerprint="same",
        explicit_action_request=True, requested_operations=["first_aid"],
    )
    assert result.accepted and decision.mode == "act"
    assert decision.action_text == "我来帮他止一下血。"
    assert state.task_status == "pending" and not state.last_attempt_result
    future = decision.model_copy(update={"mode": "speak", "action_text": None,
                                         "speech_text": "我会帮他止血。"})
    result = TeammateBehaviorPolicy().validate(future, state=state, recent_outputs=[],
        other_outputs=[], player_text=raw, player_intent=plan.parsed_intent,
        public_ids=set(), action_seq=1, fingerprint="same", explicit_action_request=True,
        requested_operations=["first_aid"])
    assert not result.accepted and future.mode == "speak" and state.task_status == "pending"


def test_medical_failure_uses_roll_not_treated_flag_or_hp_and_speech_is_checked():
    facts = result_facts([{"seq": 296, "type": "combat.resolved", "payload": {
        "actor_id": "zhou", "target_id": "patient", "operation": "first_aid",
        "treatment": {"treated": True}, "summary": "急救失败。",
        "rolls": {"treatment": {"result": {"passed": False}}},
    }}])
    assert facts[0]["status"] == "failure"
    facts.append({"actor_id": "chen", "operation": "take", "status": "success",
                  "source_event_seq": 300, "effect": "钥匙已取到。"})
    assert result_error("伤口已经止血。", facts)
    assert not result_error("刚才急救失败了，我建议先让他靠稳，请人协助。", facts)
    assert not result_error("我拿到了钥匙。", facts, actor_id="chen")
    assert result_error("我拿到了钥匙。", facts, actor_id="zhou")
    assert result_error("灯光已经变暗了。", facts)
    assert result_error("你将钥匙扔向另一头。", facts, narration=True)
    assert not result_error("我试着将钥匙扔向另一头。", facts)
    assert result_error("伤口还在流血，但已经比之前慢了些。", facts)
    assert result_error("乘务员的伤口已经暂时止住了血。", facts)
    assert not result_error("我过去看看车门有没有异常。", facts)
    assert result_error("前面的通道看起来没问题。", facts)
    assert result_error("通道暂时没被行李挡住。", facts)
    assert result_error("伤者的情况暂时稳定，但需要持续观察。", facts)
    assert result_error("患者已经好转了一些。", facts)
    assert not result_error("伤者还没有确认好转，我建议先安置他。", facts)


@pytest.mark.parametrize("account", [
    "现在脚下亮了些，但钥匙还没找到。",
    "我暂时没拿到钥匙。",
    "车厢门尚未打开。",
])
def test_negative_attempt_account_cannot_become_a_new_search(account):
    raw = account + "陈拓，你觉得接下来怎么办？"
    plan = plan_for(raw, "investigate", "keys")
    plan.focus = TurnFocus(action=account, question="陈拓，你觉得接下来怎么办？",
                           addressee_id="chen", action_target_id="keys", obstacle="尚未找到")
    plan.proposed_reveal_entity_ids = ["keys"]
    repair_attribution(plan, raw, {"chen": "陈拓"}, "actor")
    assert not plan.focus.action
    assert plan.parsed_intent.type == "converse"
    assert not plan.proposed_check and not plan.proposed_reveal_entity_ids
    assert not action_kinds(account)
    assert "search" in action_kinds("我还没找到钥匙，我再检查一下包底。")
    assert "light" in action_kinds("我站在原地，把手机的手电筒功能打开。")


def test_unknown_resource_claim_is_checked_even_in_followup_speech():
    from app.preparation.inventory import inventory_question, validate_resource_claims
    from app.rooms.service import RoomError

    view = {"holders": [], "known_items": []}
    assert inventory_question("工具箱现在在我手里了吗？", view)
    assert inventory_question("你有没有急救包？", view)
    assert not inventory_question("你觉得这个工具箱的办法怎么样？", view)
    for text in ("工具箱还在你手里。", "工具箱的卡扣完好无损。", "我检查工具包的搭扣。"):
        with pytest.raises(RoomError):
            validate_resource_claims(text, view)
    for text in ("工具箱在你手里吗？", "我没有工具箱。", "我们需要找到绷带。", "我靠墙歇一会。"):
        validate_resource_claims(text, view)
    validate_resource_claims("工具箱的搭扣完好。", {
        "known_items": [{"id": "kit", "names": ["工具箱"]}],
    })
    raw = "陈拓，工具箱现在在我手里了吗？"
    result = TeammateBehaviorPolicy().validate(
        TeammateDecision(mode="speak", speech_text="工具箱还在你手里。", confidence=1,
                         related_player_action_seq=1), state=BehaviorState(), recent_outputs=[],
        other_outputs=[], player_text=raw, player_intent=plan_for(raw, "converse").parsed_intent,
        public_ids=set(), action_seq=1, fingerprint="same", inventory_state=view,
        actor_id="chen",
    )
    assert not result.accepted


def test_unknown_requested_resource_cannot_be_substituted_with_available_keys():
    from app.preparation.action_authority import authority_error

    entities = {"keys": {"title": "黑色包里的两把钥匙", "type": "item", "aliases": ["钥匙"]}}
    runtime = ModuleRuntimeState()
    rule = ModuleInteraction(id="take_keys", instruction="取钥匙", action_kinds=["take"],
                             acquire_item_ids=["keys"], source_block_ids=["b"],
                             public_result="拿到钥匙。")
    for raw, allowed in (("我拿起陈拓脚边的工具箱。", False),
                         ("我准备拿起工具箱。", False), ("我从包里拿出钥匙。", True)):
        plan = plan_for(raw, "interact", "keys")
        plan.focus = TurnFocus(action=raw, action_target_id="keys")
        authority = freeze_action(plan, raw, "actor", 1, "scene", entities, runtime, {})
        assert selected_action_matches(authority, raw, rule) == allowed
        error = authority_error(authority, rule, runtime, actor="actor", seq=1, scene="scene")
        assert bool(error) != allowed


def test_failed_generation_can_reply_only_with_the_matching_confirmed_result():
    from app.agents.results import result_reply

    facts = [{"actor_id": "zhou", "target_id": "patient", "operation": "first_aid",
              "status": "failure", "effect": "急救失败。", "source_event_seq": 296},
             {"actor_id": "chen", "operation": "take", "status": "success",
              "effect": "取得钥匙。", "source_event_seq": 301}]
    text, sources = result_reply(facts, "伤者到底止住血了吗？", "zhou")
    assert "没有成功" in text and sources == [296]
    assert result_reply(facts, "这两根杆怎么用？", "zhou") == ("", [])


@pytest.mark.parametrize("target", [None, "wrong-new-target"])
def test_cancel_resolves_old_task_not_empty_or_new_target(target):
    old = [
        {"key": "door", "text": "检查车门。", "kind": "delegate", "target_id": "door",
         "operations": ["search"], "scene_id": "s"},
        {"key": "aid", "text": "帮乘务员包扎伤口。", "kind": "delegate", "target_id": "npc",
         "operations": ["first_aid"], "scene_id": "s", "continuity": "ongoing"},
    ]
    state = BehaviorState(pending_requests=old)
    reconcile_requests(state, [TurnRequest(kind="cancel", addressee_id="chen",
        text="先不用查门了。", target_id=target)], seq=2, scene_id="s", reachable_ids={"npc"})
    assert {r["key"] for r in state.pending_requests} == {"aid", "2:0"}
    assert state.request_history[0]["key"] == "door"
    assert state.request_history[0]["status"] == "cancelled"
    restored = BehaviorState.model_validate_json(state.model_dump_json())
    reconcile_requests(restored, [], seq=3, scene_id="s", reachable_ids={"npc"})
    assert "door" not in {r["key"] for r in restored.pending_requests}


@pytest.mark.parametrize("raw", [
    "我摸摸口袋，找找有什么能拿来扔远一点的东西。",
    "我翻找自己的衣袋，看看有什么可以用来抛向远处。",
])
def test_real_executor_preserves_inventory_sounds_route_and_dice(client, interactions, raw):  # noqa: F811
    reject_throw(client, interactions, raw, False)


def test_real_executor_cannot_throw_background_keys_instead_of_shoe(client, interactions):  # noqa: F811
    from copy import deepcopy

    from sqlalchemy import func, select
    from test_batch17 import rule

    from app.persistence.agent_models import CheckRecord
    from app.persistence.room_models import RoomEvent
    from app.rooms.service import RoomError

    data, action = interactions
    service = client.app.state.agent_service
    client.portal.call(action, "take", data["player"], "我拿起钥匙。")

    async def inspect(configure=False):
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, data["room"]["id"])
            if configure:
                entity = await service.entities.entity(session, room.id, data["item"])
                entity.snapshot = {**entity.snapshot, "interactions": [rule(
                    kp_enabled=True, encounter_operation="sound_once", sound_item_id=data["item"],
                    allow_worn_sound_item=True, set_flags={"safe_passage": True},
                ).model_dump()]}
            return (deepcopy(room.session_state),
                    await session.scalar(select(func.count()).select_from(CheckRecord)),
                    await session.scalar(select(func.count()).select_from(RoomEvent)))

    before = client.portal.call(inspect, True)
    with pytest.raises(RoomError, match="未获本次操作授权"):
        client.portal.call(action, "test", data["player"],
                          "钥匙以后还要用。我脱下一只鞋，把鞋朝远处扔过去。")
    assert client.portal.call(inspect) == before


def test_cancelled_queue_is_rechecked_and_other_task_still_activates(client, team_game):  # noqa: F811
    from uuid import uuid4

    from app.agents.conversation import activate_next, initial_state
    from app.persistence.adjudication_models import AgentBehaviorRecord
    from app.persistence.agent_models import AgentCycle

    service = client.app.state.agent_service
    room_id, actor = team_game["created"]["room"]["id"], team_game["chen"]

    async def exercise():
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, room_id)
            state = BehaviorState(pending_requests=[{
                "key": "aid", "kind": "delegate", "text": "照顾乘务员。",
            }], request_history=[{"key": "door", "status": "cancelled"}])
            await session.merge(AgentBehaviorRecord(room_id=room_id, member_id=actor,
                                                   document=state.model_dump(mode="json")))
            children = []
            for key in ("door", "aid"):
                cid = str(uuid4())
                child = AgentCycle(id=cid, room_id=room_id, status="queued", state={
                    **initial_state(room_id, cid, actor, 1, [], origin="teammate"),
                    "status": "queued", "request_keys": [key],
                })
                session.add(child)
                children.append(child)
                await session.flush()
            selected = await activate_next(service, session, room)
            assert children[0].status == "cancelled"
            assert selected.id == children[1].id and selected.status == "running"
            assert (await activate_next(service, session, room)).id == selected.id
            assert children[0].status == "cancelled"

    client.portal.call(exercise)


def test_public_device_description_resolves_only_an_authorized_local_operation():
    from types import SimpleNamespace

    from app.preparation.search import named_local_interaction_ids

    entity = {"title": "控制拉杆说明", "type": "clue",
              "public_summary": "左侧拉杆控制刹车和起步。", "interactions": [{
                  "id": "stop", "instruction": "操作左侧制动杆", "action_kinds": ["control"],
                  "kp_enabled": True, "source_block_ids": ["source"], "public_result": "制动。",
              }]}
    facts = SimpleNamespace(raw_text="我握住左侧刹车杆，慢慢拉动。",
                            approved_entities={"controls": entity},
                            local_entity_ids={"controls"}, revealed_entity_ids={"controls"},
                            reveal_errors={})
    assert named_local_interaction_ids(facts) == {"controls"}
    assert not named_local_interaction_ids(facts, "我找找有什么能拿来拉动刹车杆的东西。")


def test_unexecuted_operation_cannot_be_an_incidental_world_change():
    from app.agents.adjudication_schemas import KeeperNarration
    from app.agents.narration import NarrationValidator
    from app.rooms.service import RoomError

    facts = [{"operation": "control", "actor_id": "actor", "target_id": "controls",
              "status": "not_executed", "effect": "本次制动没有实际结算。", "source_event_seq": 9}]
    assert result_error("你握住刹车杆，慢慢拉动。", facts, narration=True)
    validator = NarrationValidator()
    args = dict(documents=[], public_ids=set(), scene_id="night", brief={},
                results={"events": [], "result_facts": facts, "current_result_facts": facts})
    with pytest.raises(RoomError):
        validator.validate(KeeperNarration(public_narration="列车速度逐渐减缓。"), **args)
    validator.validate(KeeperNarration(public_narration="这次没有完成制动，车速尚未确认改变。"),
                       **args)


@pytest.mark.parametrize("raw,allowed", [
    ("我试着关掉这节车厢的灯。", False),
    ("我把天花板上的照明关掉。", False),
    ("手机留着照路。我试着关掉这节车厢的灯。", False),
    ("我把手机的手电筒功能关闭。", True),
    ("我准备关掉手里的手机灯。", True),
])
def test_environmental_light_cannot_borrow_a_held_device(raw, allowed):
    from app.agents.adjudication_schemas import KeeperPlan
    from app.agents.generation_contracts import (
        generation_contract,
        restore_output,
        utterance_clauses,
    )
    from app.preparation.action_authority import authority_error

    runtime = ModuleRuntimeState(inventory={"instance": "actor"},
                                 item_instances={"instance": "phone"},
                                 flags={"phone_light": True})
    plan = plan_for(raw, "use_item", "phone")
    plan.focus = TurnFocus(action=raw, action_target_id="phone")
    authority = freeze_action(plan, raw, "actor", 1, "scene", {
        "phone": {"title": "随身手机", "type": "item", "aliases": ["手机"]},
    }, runtime, {})
    rule = ModuleInteraction(id="off", instruction="关闭持有手机的灯", action_kinds=["light"],
                             required_item_ids=["phone"], set_flags={"phone_light": False},
                             source_block_ids=["b"], public_result="手机灯已关。")
    assert selected_action_matches(authority, raw, rule) is allowed
    assert (authority_error(authority, rule, runtime, actor="actor", seq=1, scene="scene")
            is None) is allowed
    assert "close" not in authority["kinds"]
    assert set(action_kinds("我关闭手机灯，再关上车门。")) == {"light", "close"}
    context = {"action_identifiers": {
        "plan_id": "plan", "cycle_id": "cycle", "actor_member_id": "actor",
        "actor_character_slot_id": "slot", "current_scene_id": "scene",
        "expected_navigation_revision": 0,
    }, "triggering_action": {"seq": 1, "payload": {"text": raw}},
        "current_targets": [{"id": "phone", "title": "随身手机", "type": "item",
                             "aliases": ["手机"]}]}
    schema = generation_contract(KeeperPlan, context)
    restored = restore_output(schema(parsed_intent={"type": "use_item"}, focus={
        "action_clause_ids": [c["id"] for c in utterance_clauses(raw)], "action_target_id": "phone",
    }), KeeperPlan, context)
    assert restored.focus.action_target_id == ("phone" if allowed else "scene")


@pytest.mark.parametrize("place,boundary", [("驾驶室", "驾驶室门"), ("设备间", "设备间入口")])
def test_local_room_operation_does_not_choose_the_only_backwards_exit(place, boundary):
    from test_action_adjudication import facts_for

    from app.agents.action_policy import ActionPolicyValidator
    from app.agents.adjudication_schemas import KeeperPlan
    from app.agents.generation_contracts import generation_contract, restore_output

    raw = f"我走进{place}，把左侧拉杆拉到刹车的位置。"
    exits = [{"transition_id": "back", "target_scene_node_id": "rear",
              "target_public_title": "2号车厢", "approved": True}]
    targets = [{"id": "door", "title": boundary, "type": "location"}]
    facts = facts_for(raw)
    facts.transitions = {"back": exits[0]}
    facts.approved_entities = {"door": targets[0]}
    facts.local_entity_ids = {"door"}
    wrong = plan_for(raw, "move", "rear")
    assert ActionPolicyValidator().validate_intent(wrong.parsed_intent, wrong, facts)
    context = {"action_identifiers": {
        "plan_id": "plan", "cycle_id": "cycle", "actor_member_id": "actor",
        "actor_character_slot_id": "slot", "current_scene_id": "scene",
        "expected_navigation_revision": 0,
    }, "triggering_action": {"seq": 1, "payload": {"text": raw}},
        "approved_exits": exits, "current_targets": targets}
    schema = generation_contract(KeeperPlan, context)
    plan = restore_output(schema(parsed_intent={"type": "move"}, focus={
        "action_clause_ids": ["u1", "u2"], "action_target_id": "rear",
    }, proposed_transition_id="back"), KeeperPlan, context)
    assert plan.parsed_intent.type == "interact" and plan.proposed_transition_id is None
    assert plan.focus.action_target_id == "scene"
    assert "control" in action_kinds(plan.focus.action)


@pytest.mark.parametrize("text", [
    "你沿着前门走进先头车厢，脚步在黑暗中回响。", "我进入了先头车厢。",
    "你穿过车厢，来到前方的房间。", "你们已经到达先头车厢。", "你来到了先头车厢。",
])
def test_scene_change_claim_uses_shared_result_in_any_voice(text):
    negative = [{"operation": "move", "actor_id": "actor", "target_name": "先头车厢",
                 "status": "not_executed", "source_event_seq": 1}]
    assert result_error(text, negative, narration=True)
    assert not result_error("我先走到旁边等你。", negative)
    assert not result_error("你可以试着进入先头车厢。", negative, narration=True)
    positive = result_facts([{"seq": 2, "type": "scene.updated", "payload": {
        "actor_id": "actor", "scene_title": "先头车厢",
    }}])
    assert positive[0]["operation"] == "move" and positive[0]["status"] == "success"
    assert not result_error(text, positive, narration=True)


@pytest.mark.parametrize("raw,allowed", [
    ("我搜索前面的门能不能走。", False),
    ("我检查走廊，寻找能用来止血的东西。", False),
    ("我用手压住乘务员的伤口。", True),
])
def test_search_cannot_borrow_a_treatment_gated_discovery(raw, allowed):
    from uuid import uuid4

    from test_action_adjudication import facts_for

    from app.agents.action_policy import ActionPolicyValidator
    from app.agents.adjudication_schemas import KeeperPlan
    from app.agents.check_policy import CheckProposal
    from app.agents.generation_contracts import (
        generation_contract,
        restore_output,
        utterance_clauses,
    )
    from app.agents.schemas import PlannedTool

    actor, clue = str(uuid4()), str(uuid4())
    check = {"kind": "skill", "name": "first_aid", "difficulty": "regular"}
    context = {"action_identifiers": {
        "plan_id": "plan", "cycle_id": "cycle", "actor_member_id": actor,
        "actor_character_slot_id": "slot", "current_scene_id": "scene",
        "expected_navigation_revision": 0,
    }, "triggering_action": {"seq": 1, "payload": {"text": raw}},
        "check_requirements": [{"entity_id": clue, "title": "伤情处理结果",
                                "access_policy": "requires_check", "successful_check": check}]}
    proposal = CheckProposal(**check, target_member_id=actor, reason=raw, clue_id=clue,
                             target_entity_id=clue, basis_entity_id=clue, necessity="required",
                             uncertainty="能否控制出血", success_effect="伤情有所改善",
                             failure_consequence="伤情没有改善")
    schema = generation_contract(KeeperPlan, context)
    restored = restore_output(schema(parsed_intent={"type": "interact"}, focus={
        "action_clause_ids": [c["id"] for c in utterance_clauses(raw)],
        "action_target_id": clue, "obstacle": "需要确认",
    }, proposed_check=proposal.model_dump(mode="json"),
        proposed_reveal_entity_ids=[clue]), KeeperPlan, context)
    assert (restored.proposed_check is not None) is allowed
    assert (clue in restored.proposed_reveal_entity_ids) is allowed

    # Directly supplied proposals must hit the same guard after generation.
    plan = plan_for(raw, "interact", clue)
    plan.parsed_intent.actor_member_id = actor
    plan.focus = TurnFocus(action=raw, action_target_id=clue, obstacle="需要确认")
    plan.proposed_check = proposal
    facts = facts_for(raw)
    facts.actor_member_id = actor
    facts.characters = {actor: {"ruleset_id": "coc7-character-creation",
                                "skill_values": {"first_aid": 60}}}
    facts.local_entity_ids = facts.visible_entity_ids = {clue}
    facts.approved_entities = {clue: {"type": "clue", "title": "伤情处理结果",
                                     "reveal_conditions": {"successful_check": check}}}
    result = ActionPolicyValidator().validate(plan.parsed_intent, plan, facts, [
        PlannedTool(name="request_skill_check", arguments={
            **check, "target_member_id": actor, "reason": raw, "clue_id": clue,
        }),
    ])
    assert bool(result.approved_actions) is allowed, result.validation_reasons


@pytest.mark.parametrize("text,operation", [
    ("我刚才没关灯，是林知秋关的。", "light"),
    ("我没有拿出钥匙，是林知秋拿出的。", "take"),
    ("我没扔手机，是林知秋扔的。", "throw"),
])
def test_elliptical_result_attribution_uses_same_operation_and_actual_actor(text, operation):
    facts = [{"operation": operation, "actor_id": "player", "status": "not_executed",
              "source_event_seq": 5}]
    args = {"actor_id": "chen", "names": {"player": "林知秋", "chen": "陈拓"}}
    assert result_error(text, facts, **args) == "unconfirmed_result:" + operation
    assert not result_error(text, [{**facts[0], "status": "success"}], **args)


def test_illumination_change_claim_needs_its_own_actor_result():
    facts = [{"operation": "light", "actor_id": "player", "status": "not_executed",
              "source_event_seq": 5}]
    text = "我刚才没有关灯，是周岚在检查伤者时调整了灯光。"
    assert result_error(text, facts, actor_id="chen", names={"zhou": "周岚"})
    assert not result_error("可以请周岚调整照明。", facts)


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_medical_anaphora_can_only_reveal_a_unique_public_scene_identity(ambiguous):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agents.combat_runtime import human_decision_contract, reveal_addressed_npc

    rows = [SimpleNamespace(entity_type="npc", state="hidden", source_entity_id="staff",
                            snapshot={"title": "乘务员"})]
    summary = "乘务员腿部受伤倒在车厢里。"
    if ambiguous:
        rows.append(SimpleNamespace(entity_type="npc", state="hidden", source_entity_id="rider",
                                    snapshot={"title": "乘客"}))
        summary += "另一名乘客也躺在地上。"
    entities = SimpleNamespace(rows=AsyncMock(return_value=rows),
                               check_conditions=AsyncMock(), reveal=AsyncMock())
    scene = SimpleNamespace(document={"scenes": [{"id": "scene", "public_description": summary}]},
                            state={"scene_id": "scene"})
    raw = "我立刻过去查看伤者情况，准备进行止血处理。"
    await reveal_addressed_npc(SimpleNamespace(entities=entities), None,
                               SimpleNamespace(id="room", host_member_id="host"), raw, scene)
    assert entities.reveal.await_count == (0 if ambiguous else 1)
    if not ambiguous:
        entities.check_conditions.assert_awaited_once()
        context = {"input": raw, "treatment_targets": [{"id": "staff", "label": "乘务员"}]}
        schema = human_decision_contract(context)
        decision = schema(operation="first_aid", target_id="staff", reason="尝试止血")
        assert decision.target_id == "staff"
        with pytest.raises(ValueError):
            schema(operation="first_aid", target_id="chen", reason="尝试止血")
