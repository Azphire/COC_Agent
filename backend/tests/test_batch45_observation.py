"""An unrelated hidden check cannot widen two public observations into a search."""

import pytest
from pydantic import ValidationError
from test_batch16 import module_battle  # noqa: F401
from test_batch17 import interactions  # noqa: F401
from test_module_navigation import structure_data  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import lobby  # noqa: F401

from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
from app.agents.check_policy import CheckProposal
from app.agents.generation_contracts import restore_output


def context_for(action):
    return {
        "action_identifiers": {
            "plan_id": "plan", "cycle_id": "cycle",
            "actor_member_id": "00000000-0000-0000-0000-000000000001",
            "actor_character_slot_id": "slot", "current_scene_id": "study-node",
            "expected_navigation_revision": 0,
        },
        "triggering_action": {"seq": 166, "payload": {"text": action}},
        "current_targets": [{"id": "study", "type": "scene", "title": "道格拉斯的书房"},
                            {"id": "diary", "type": "clue", "title": "道格拉斯的日记"}],
        "check_requirements": [{"entity_id": "diary", "title": "道格拉斯的日记",
            "aliases": ["日记"], "access_policy": "requires_check",
            "successful_check": {"kind": "skill", "name": "spot_hidden", "difficulty": "regular"}}],
    }


def proposal_for(action, *, obstacle="", target="study", necessity="unnecessary"):
    return KeeperPlan(
        plan_id="plan", cycle_id="cycle", current_scene_id="study-node",
        parsed_intent={"type": "investigate", "evidence_quote": action, "confidence": 1,
                       "actor_member_id": "00000000-0000-0000-0000-000000000001",
                       "actor_character_slot_id": "slot"},
        focus=TurnFocus(action=action, action_target_id=target, obstacle=obstacle),
        proposed_check=CheckProposal(
            target_member_id="00000000-0000-0000-0000-000000000001", name="spot_hidden",
            reason=action,
            target_entity_id="diary", necessity=necessity, basis_entity_id="diary",
            success_effect="侦查检定（普通）：骰点 89，目标 25，失败，未通过。",
            failure_consequence="目前尚未确认额外的线索内容。",
        ),
    )


def test_two_public_observations_do_not_become_an_unrequested_diary_search():
    action = "我环顾道格拉斯的书房，查看书架上的空档和通向屋外的窗户。"
    restored = restore_output(proposal_for(action), KeeperPlan, context_for(action))
    assert restored.focus.action_target_id == "study"
    assert restored.focus.action == action
    assert restored.proposed_check is None
    assert restored.proposed_reveal_entity_ids == []
    assert not restored.needs_clarification


def test_actual_search_keeps_approved_diary_check_even_if_model_says_unnecessary():
    action = "我搜索道格拉斯的书房，翻找书桌和书架背后的物品。"
    restored = restore_output(proposal_for(action), KeeperPlan, context_for(action))
    assert restored.proposed_check.necessity == "required"
    assert restored.proposed_check.clue_id == "diary"
    assert restored.proposed_check.name == "spot_hidden"


def test_explicit_gated_detail_keeps_its_real_required_check():
    action = "我查看道格拉斯的日记。"
    restored = restore_output(proposal_for(action, target="diary"), KeeperPlan, context_for(action))
    assert restored.proposed_check.necessity == "required"
    assert restored.proposed_check.clue_id == "diary"


def test_observation_with_concrete_obstacle_and_necessary_proposal_keeps_check():
    action = "我查看书架后面的物品。"
    restored = restore_output(proposal_for(action, obstacle="背光处的物品难以辨认。",
                                           necessity="required"), KeeperPlan, context_for(action))
    assert restored.proposed_check.necessity == "required"
    assert restored.proposed_check.clue_id == "diary"


def prepared_search_case(action):
    from app.agents.generation_contracts import utterance_clauses
    from app.preparation.action_authority import action_kinds
    from app.preparation.runtime_schemas import ModuleInteraction

    # The approved real-04 search_diary method used both kinds. Its 1440-minute
    # search was therefore offered for the public observation clause u2.
    rule = ModuleInteraction(
        id="search_diary", action_kinds=["search", "observe"],
        instruction="至少花一天全面搜索书房，侦查；失败后可换方法孤注。",
        source_block_ids=["source"], check_name="spot_hidden", kp_enabled=True,
        elapsed_minutes=1440, set_flags={"diary_found": True},
        reveal_entity_ids=["diary"], public_result="经过一天的搜索，找到日记。",
    )
    authority = {
        "actor_member_id": "player", "source_event_seq": 1,
        "scene_node_id": "study-node", "action": action, "utterance": action,
        "kinds": action_kinds(action), "conversation_target_id": None,
    }
    context = {
        "actor": "player", "action_authority": authority,
        "clauses": utterance_clauses(action), "members": {"player": "调查员"},
        "items": [], "npc_instances": [],
    }
    return rule, authority, context


def test_public_observation_cannot_enter_timed_prepared_search_candidates():
    from app.preparation.action_authority import authority_error, selected_action_matches
    from app.preparation.runtime_schemas import ModuleRuntimeState

    action = "我环顾道格拉斯的书房，查看书架上的空档和通向屋外的窗户。"
    rule, authority, context = prepared_search_case(action)
    assert authority_error(
        authority, rule, ModuleRuntimeState(), actor="player", seq=1,
        scene="study-node", check_facts=False,
    ) == "实际动作不授权此操作"
    assert not selected_action_matches(authority, context["clauses"][1]["text"], rule)


def test_prepared_decision_cannot_validate_real_observation_as_daylong_search():
    from app.preparation.adjudication import decision_contract

    action = "我环顾道格拉斯的书房，查看书架上的空档和通向屋外的窗户。"
    rule, _, context = prepared_search_case(action)
    schema = decision_contract(context, [{"option": "1", "rule": rule.model_dump()}])
    with pytest.raises(ValidationError, match="action_clause_ids"):
        schema.model_validate({
            "option": "1", "applicable": True, "action_clause_ids": ["u2"],
            "evidence_quotes": [action], "reason": "查看书架空档和窗户可应用全面搜索。",
        })


@pytest.mark.parametrize("observing", [False, True])
def test_real_search_and_explicit_observation_methods_keep_their_check(observing):
    from app.preparation.action_authority import authority_error, selected_action_matches
    from app.preparation.adjudication import decision_contract
    from app.preparation.runtime_schemas import ModuleRuntimeState

    action = "我查看道格拉斯的日记。" if observing else "我全面搜索道格拉斯的书房。"
    rule, authority, context = prepared_search_case(action)
    if observing:
        rule.action_kinds = ["observe"]
        rule.elapsed_minutes = 0
    assert authority_error(
        authority, rule, ModuleRuntimeState(), actor="player", seq=1,
        scene="study-node", check_facts=False,
    ) is None
    assert selected_action_matches(authority, action, rule)
    schema = decision_contract(context, [{"option": "1", "rule": rule.model_dump()}])
    assert schema.model_validate({
        "option": "1", "applicable": True, "action_clause_ids": ["u1"],
        "evidence_quotes": [action], "reason": "本轮已声明对应操作。",
    }).applicable
    assert rule.check_name == "spot_hidden"


def test_prepared_executor_rejects_observation_without_search_time_or_effects(
    client, interactions,  # noqa: F811
):
    from copy import deepcopy

    from sqlalchemy import func, select

    from app.persistence.agent_models import CheckRecord
    from app.persistence.room_models import RoomEvent
    from app.rooms.service import RoomError

    game, execute = interactions
    service = client.app.state.agent_service
    action = "我环顾道格拉斯的书房，查看书架上的空档和通向屋外的窗户。"
    rule, _, _ = prepared_search_case(action)
    rule.reveal_entity_ids = []

    async def configure():
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, game["room"]["id"])
            entity = await service.entities.entity(session, room.id, game["item"])
            entity.snapshot = {**entity.snapshot, "interactions": [rule.model_dump()]}

    async def inspect():
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, game["room"]["id"])
            return (
                deepcopy(room.session_state),
                await session.scalar(select(func.count()).select_from(CheckRecord)),
                await session.scalar(select(func.count()).select_from(RoomEvent)),
            )

    client.portal.call(configure)
    before = client.portal.call(inspect)
    with pytest.raises(RoomError, match="实际动作不授权"):
        client.portal.call(lambda: execute(rule.id, game["player"], action, verified=True))
    assert client.portal.call(inspect) == before
