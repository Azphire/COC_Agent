"""Batch 17 failure semantics with deliberately permissive model receipts."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_batch16 import module_battle  # noqa: F401
from test_batch17 import interactions, rule  # noqa: F401
from test_module_navigation import structure_data  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import lobby  # noqa: F401

from app.agents.adjudication_schemas import KeeperPlan, PlayerIntent, TurnFocus
from app.agents.generation_contracts import restore_output
from app.preparation.action_authority import authority_error, freeze_action, teammate_request
from app.preparation.encounters import apply_encounter, end_transient_sound
from app.preparation.runtime_schemas import ModuleActionArgs
from app.rooms.combat_service import load_state, store_state
from app.rooms.schemas import SessionStateV1
from app.rooms.service import RoomError


@pytest.mark.parametrize("terminal_field", ["outcome", "pending_outcome"])
def test_completed_module_does_not_schedule_another_teammate_action(
    client,
    interactions,  # noqa: F811
    terminal_field,
):
    from app.agents.action_runtime import ActionRuntimeMixin

    d, _ = interactions
    rooms = client.app.state.agent_service.rooms

    async def verify():
        async with rooms.transaction() as session:
            room = await rooms.room(session, d["room"]["id"])
            data = load_state(room)
            setattr(data.module_runtime, terminal_field, "A")
            store_state(room, data)

        async def node(state, name):
            assert name == "decide_teammates"
            return state

        state = {"room_id": d["room"]["id"], "teammate_queue": ["eager-peer"]}
        runtime = SimpleNamespace(rooms=rooms, node=node)
        # No planning record or model is needed after the terminal settlement.
        assert await ActionRuntimeMixin.decide_teammates(runtime, state) == state

    client.portal.call(verify)


def test_terminal_public_result_is_not_overwritten_by_model_narration(client, interactions):  # noqa: F811
    from app.agents.action_runtime import ActionRuntimeMixin

    d, _ = interactions
    rooms = client.app.state.agent_service.rooms

    async def verify():
        async with rooms.transaction() as session:
            room = await rooms.room(session, d["room"]["id"])
            data = load_state(room)
            data.module_runtime.receipts["actual-stop"] = {
                "source_event_seq": 12,
                "consequence_entity_ids": ["nightmare"],
            }
            store_state(room, data)

        async def node(state, name):
            assert name == "generate_keeper_narration"
            return state

        state = {"room_id": d["room"]["id"], "triggering_event_seq": 12}
        runtime = SimpleNamespace(rooms=rooms, node=node)
        assert await ActionRuntimeMixin.generate_keeper_narration(runtime, state) == state
        state["triggering_event_seq"] = 13
        with pytest.raises(KeyError, match="cycle_id"):
            # An earlier ending receipt cannot suppress a different action.
            await ActionRuntimeMixin.generate_keeper_narration(runtime, state)

    client.portal.call(verify)


@pytest.mark.parametrize("actual_source", [12, 11, None])
async def test_actual_consequence_enters_san_queue_despite_entity_review_gate(actual_source):
    from unittest.mock import AsyncMock, Mock

    from test_action_adjudication import facts_for, plan_for

    from app.agents.action_policy import ActionPolicyValidator
    from app.agents.adjudication_schemas import AdjudicationRecord
    from app.persistence.adjudication_models import ActionPlanRecord
    from app.rooms.encounters import EncounterService
    from app.rooms.sanity_schemas import SanityEffect

    raw = "我持续上推油门杆让电车停车。"
    plan = plan_for(raw, "interact", "lever")
    facts = facts_for(raw)
    facts.visible_entity_ids = {"lever"}
    facts.local_entity_ids = {"lever", "nightmare"}
    facts.approved_entities = {
        "lever": {"interactions": [{"id": "stop", "reveal_entity_ids": ["nightmare"]}]},
        "nightmare": {
            "sanity_effects": [
                SanityEffect(
                    id="ending-horror",
                    encounter="Horror",
                    source="Isolated fixture",
                    page=1,
                    basis="The actual terminal consequence",
                    success_loss="1d4",
                    failure_loss="1d10",
                    trigger="entity_revealed",
                    kp_enabled=True,
                    audience="party",
                ).model_dump()
            ]
        },
    }
    facts.reveal_errors = {"nightmare": "实体访问需要主机审阅"}
    validator = ActionPolicyValidator()
    doc = AdjudicationRecord(
        plan=plan, validation=validator.validate(plan.parsed_intent, plan, facts, [])
    )
    record = SimpleNamespace(document=doc.model_dump(mode="json"), run_id="run")

    async def get(model, identifier):
        return record if model is ActionPlanRecord else None

    session = SimpleNamespace(
        get=get,
        scalars=AsyncMock(
            side_effect=[
                [SimpleNamespace(seq=14, payload={"cycle_id": "cycle", "entity_id": "nightmare"})],
                [],
            ]
        ),
    )
    receipt = {"entity_id": "lever", "interaction_id": "stop", "source_event_seq": actual_source}
    room = SimpleNamespace(
        id="room",
        host_member_id="host",
        session_state={"module_runtime": {"receipts": {"stop": receipt} if actual_source else {}}},
    )
    cycle = SimpleNamespace(
        id="cycle", state={"request_category": "investigation", "triggering_event_seq": 12}
    )
    agents = SimpleNamespace(
        adjudication=SimpleNamespace(facts=AsyncMock(return_value=facts), validator=validator)
    )
    service = EncounterService(SimpleNamespace(agents=agents, rooms=SimpleNamespace(append=Mock())))
    await service.discover(session, room, cycle)
    queue = cycle.state["encounter_queue"]
    assert len(queue) == (1 if actual_source == 12 else 0)
    if queue:
        assert (queue[0]["entity_id"], queue[0]["source_event_seq"], queue[0]["status"]) == (
            "nightmare",
            14,
            "kp_review",
        )


@pytest.mark.parametrize("actual_receipt", [True, False])
def test_terminal_san_uses_actual_public_consequence_even_when_model_denies_it(
    client,
    interactions,  # noqa: F811
    monkeypatch,
    actual_receipt,
):
    from app.persistence.agent_models import AgentCycle
    from app.preparation import sanity_adjudication

    d, _ = interactions
    svc = client.app.state.agent_service
    cid = str(uuid4())

    async def prepare():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            entity = await svc.entities.entity(session, room.id, d["item"])
            effect = dict(entity.snapshot["sanity_effects"][0])
            effect.update(
                kp_enabled=True, audience="party", perception="other", trigger="entity_revealed"
            )
            entity.snapshot = {**entity.snapshot, "sanity_effects": [effect]}
            event = svc.rooms.append(
                session, room, "action.submitted", d["player"], {"text": "我上推油门杆停车。"}
            )
            nav = await svc.navigation.state(session, room.id)
            slot = next(
                s for s in await svc.rooms.slots(session, room) if s.member_id == d["player"]
            )
            data = load_state(room)
            data.module_runtime.pending_outcome = "B"
            data.module_runtime.receipts["stop"] = {
                "source_event_seq": event.seq if actual_receipt else event.seq - 1,
                "scene_node_id": nav.current_scene_node_id,
                "consequence_entity_ids": [d["item"]],
                "text": "列车停下，你们实际经历了噩梦。",
            }
            store_state(room, data)
            state = {
                "room_id": room.id,
                "cycle_id": cid,
                "triggering_event_seq": event.seq,
                "triggering_member_id": d["player"],
                "encounter_queue": [
                    {
                        "entity_id": d["item"],
                        "effect_id": effect["id"],
                        "trigger": "entity_revealed",
                        "status": "kp_review",
                        "source_event_seq": event.seq,
                        "target_member_ids": [d["player"]],
                        "slot_ids": [slot.id],
                    }
                ],
            }
            session.add(AgentCycle(id=cid, room_id=room.id, status="running", state=state))
            return state

    calls = []

    async def deny(*args, **kwargs):
        calls.append(True)
        return sanity_adjudication.SanitySituation(
            applies="no", evidence_quotes=["我上推油门杆停车。"], reason="Injected erroneous denial"
        )

    monkeypatch.setattr(sanity_adjudication, "call_model", deny)
    state = client.portal.call(prepare)
    result = client.portal.call(sanity_adjudication.resolve_sanity_conditions, svc.runtime, state)
    entry = result["encounter_queue"][0]
    assert entry["status"] == ("approved" if actual_receipt else "rejected")
    assert bool(calls) is not actual_receipt
    if actual_receipt:
        assert set(entry["target_member_ids"]) == {d["player"], d["agent"]}
        assert entry["evidence_quotes"] == ["列车停下，你们实际经历了噩梦。"]


def test_throw_method_candidates_keep_real_branch_without_inventing_distance():
    from app.preparation.adjudication import prefer_established_sound_methods

    def candidate(method):
        return {"entity_id": "sound", "rule": method.model_dump()}

    generic = candidate(rule(encounter_operation="sound_once"))
    far = candidate(
        rule(encounter_operation="sound_once", required_facts=["sound_distance_over_half_car"])
    )
    observe = candidate(
        rule(action_kinds=["observe"], required_facts=["visual_target_in_phone_light"])
    )
    runtime = SessionStateV1().module_runtime
    authority = {
        "action": "我把手机扔到旁边的空座椅下。",
        "actor_member_id": "actor",
        "scene_node_id": "scene",
        "source_event_seq": 7,
        "kinds": ["throw"],
    }
    select = prefer_established_sound_methods
    assert select([far, generic, observe], runtime, authority) == [generic, observe]
    assert select([far], runtime, authority) == [far]  # Still needs clarification.
    assert not runtime.scene_facts
    authority["action"] = "我站在后门，声源与它相距超过半节车厢，我把手机扔出。"
    assert select([far, generic], runtime, authority) == [far, generic]
    assert not runtime.scene_facts  # Candidate preparation cannot establish effects.


def test_final_plan_budget_preserves_action_rules_and_rolls_after_scene_allocation():
    import json

    from app.agents.action_runtime import compact_planning_prose, planning_prompt

    context = {
        "prepared_module": True,
        "triggering_action": {"seq": 285, "payload": {"text": "我查看前门附近的行李。"}},
        "module": {
            "current_scene": {"node_id": "car3", "summary": "Source scene. " * 150},
            "approved_entities": [{"id": "keys", "keeper_summary": "Approved detail. " * 80}],
            "interaction_state": {"held_items": [{"instance_id": "phone:actor"}]},
        },
        "check_requirements": [{"entity_id": "keys", "successful_check": {"name": "spot_hidden"}}],
        "module_interactions": [{"entity_id": "keys", "interactions": [{"id": "take"}]}],
        "previous_attempts": [{"id": "first-roll", "result": {"total": 100, "passed": False}}],
        "approved_exits": [{"transition_id": "forward"}],
    }
    original = deepcopy(context)
    compact = compact_planning_prose(context, 950)
    assert (
        len(json.dumps(planning_prompt(compact), ensure_ascii=False, separators=(",", ":"))) <= 950
    )
    for key in (
        "triggering_action",
        "check_requirements",
        "module_interactions",
        "previous_attempts",
        "approved_exits",
    ):
        assert compact[key] == original[key]
    assert compact["module"]["interaction_state"] == original["module"]["interaction_state"]
    assert compact["module"]["current_scene"]["node_id"] == "car3"
    assert context == original


def test_redundant_reveal_of_known_item_does_not_reject_the_actual_observation():
    from test_action_adjudication import facts_for, plan_for

    from app.agents.action_policy import ActionPolicyValidator
    from app.agents.schemas import PlannedTool

    raw = "我借手机灯观察喘息来源。"
    plan = plan_for(raw)
    plan.proposed_tool_calls = [PlannedTool(name="reveal_entity", arguments={"entity_id": "phone"})]
    facts = facts_for(raw)
    facts.approved_entities = {"phone": {"id": "phone", "type": "item", "title": "手机"}}
    facts.local_entity_ids = facts.observed_entity_ids = {"phone"}
    facts.reveal_errors = {"phone": "必须通过关联的真实检定"}
    facts.revealed_entity_ids = {"phone"}
    validator = ActionPolicyValidator()
    assert (
        validator.validate(plan.parsed_intent, plan, facts, plan.proposed_tool_calls).status
        == "approved"
    )
    facts.revealed_entity_ids = set()
    assert (
        validator.validate(plan.parsed_intent, plan, facts, plan.proposed_tool_calls).status
        == "rejected"
    )


def test_actual_ending_consequence_is_current_and_survives_runtime_roundtrip():
    from app.preparation.runtime import current_entity_ids

    snapshot = SimpleNamespace(entity_bindings=[SimpleNamespace(entity_id="lever", node_id="cab")])
    state = SessionStateV1()
    state.module_runtime.pending_outcome = "B"
    assert current_entity_ids(snapshot, ["cab"], state.module_runtime.model_dump()) == {"lever"}
    state.module_runtime.receipts["12:lever:stop"] = {
        "scene_node_id": "cab",
        "consequence_entity_ids": ["nightmare"],
    }
    restored = SessionStateV1.model_validate_json(state.model_dump_json())
    assert current_entity_ids(snapshot, ["cab"], restored.module_runtime.model_dump()) == {
        "lever",
        "nightmare",
    }
    assert not current_entity_ids(snapshot, ["other"], restored.module_runtime.model_dump())


def test_cross_section_encounter_requires_this_actions_actual_configured_consequence():
    from app.rooms.encounters import interaction_revealed_consequence

    facts = SimpleNamespace(
        local_entity_ids={"lever"},
        approved_entities={
            "lever": {
                "interactions": [
                    {"id": "stop", "reveal_entity_ids": ["nightmare"]},
                ]
            }
        },
    )
    rt = {
        "receipts": {
            "done": {
                "entity_id": "lever",
                "interaction_id": "stop",
                "source_event_seq": 12,
            }
        }
    }
    assert interaction_revealed_consequence(rt, facts, "nightmare", 12)
    assert not interaction_revealed_consequence(rt, facts, "unplayed_ending", 12)
    assert not interaction_revealed_consequence(rt, facts, "nightmare", 13)
    assert not interaction_revealed_consequence({}, facts, "nightmare", 12)
    facts.local_entity_ids = set()
    assert not interaction_revealed_consequence(rt, facts, "nightmare", 12)


def test_wrong_parent_control_focus_repairs_only_one_matching_public_device():
    from app.preparation.search import repair_control_target

    raw = "我上推右侧油门杆，持续减速直到列车停下。"
    value = {"focus": {"action": raw, "action_target_id": "panel"}}
    context = {
        "current_targets": [{"id": "panel", "title": "控制面板", "type": "location"}],
        "module_interactions": [
            {
                "entity_id": "lever_instructions",
                "interactions": [
                    {
                        "action_kinds": ["control"],
                        "instruction": "实际向上推动右侧油门减速停车。",
                    }
                ],
            }
        ],
    }
    repair_control_target(value, context)
    assert value["focus"] == {"action": raw, "action_target_id": "lever_instructions"}
    assert "proposed_tool_calls" not in value
    for text in ["我观察右侧油门杆。", "如果我上推右侧油门杆呢？", "我推动控制面板。"]:
        value = {"focus": {"action": text, "action_target_id": "panel"}}
        repair_control_target(value, context)
        assert value["focus"]["action_target_id"] == "panel"
    context["module_interactions"].append(
        {
            **context["module_interactions"][0],
            "entity_id": "other_lever",
        }
    )
    value = {"focus": {"action": raw, "action_target_id": "panel"}}
    repair_control_target(value, context)
    assert value["focus"]["action_target_id"] == "panel"


@pytest.mark.parametrize(
    "blocked", [None, "remote", "gated", "condition", "question", "ambiguous", "request"]
)
def test_named_local_operation_repair_preserves_discovery_and_action_gates(blocked):
    from test_action_adjudication import facts_for, plan_for

    from app.preparation.search import named_local_interaction_ids, repair_local_interaction_target

    raw = {
        "question": "如果我用钥匙打开侧门呢？",
        "request": "沈砚，请用钥匙打开侧门。",
    }.get(blocked, "我用钥匙打开侧门。")
    facts = facts_for(raw)
    facts.approved_entities = {
        "door": {
            "title": "上锁侧门",
            "aliases": ["侧门"],
            "type": "location",
            "reveal_conditions": {"access_policy": "automatic"},
            "interactions": [rule(kp_enabled=True, encounter_operation="open_door").model_dump()],
        }
    }
    facts.local_entity_ids = {"door"} if blocked != "remote" else set()
    if blocked == "gated":
        facts.approved_entities["door"]["reveal_conditions"]["access_policy"] = "requires_check"
    if blocked == "condition":
        facts.reveal_errors["door"] = "Prerequisite not met"
    if blocked == "ambiguous":
        facts.approved_entities["other"] = deepcopy(facts.approved_entities["door"])
        facts.local_entity_ids.add("other")
    plan = plan_for(raw, "use_item", "parent")
    plan.focus = TurnFocus(action=raw, action_target_id="parent")
    repair_local_interaction_target(plan, facts, {"peer": "沈砚"})
    assert plan.focus.action_target_id == ("door" if blocked is None else "parent")
    assert plan.proposed_reveal_entity_ids == (["door"] if blocked is None else [])
    assert not plan.proposed_tool_calls and not plan.action_authority
    if blocked is None:
        assert named_local_interaction_ids(facts) == {"door"}


def test_wrong_parent_hidden_automatic_door_reaches_real_reveal_and_owned_key_operation(
    client,
    interactions,  # noqa: F811
    monkeypatch,
):
    from sqlalchemy import select

    from app.agents.adjudication_schemas import AdjudicationRecord
    from app.persistence.adjudication_models import ActionPlanRecord
    from app.persistence.agent_models import AgentCycle, AgentRun, ProfileRecord
    from app.preparation import adjudication
    from app.preparation.runtime import apply_interaction

    d, take = interactions
    svc = client.app.state.agent_service
    client.portal.call(take, "take", d["agent"], "我拿起钥匙。")
    raw = "我用钥匙打开侧门。"

    async def prepare():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            door = await svc.entities.entity(session, room.id, d["guard"])
            door.entity_type = "location"
            door.state = "hidden"
            door.snapshot = {
                **door.snapshot,
                "type": "location",
                "title": "侧门",
                "aliases": ["侧门"],
                "combat_template": None,
                "sanity_effects": [],
                "reveal_conditions": {
                    "access_policy": "automatic",
                    "scene_id": None,
                    "required_entity_ids": [],
                    "successful_check": None,
                },
                "interactions": [
                    rule(
                        kp_enabled=True,
                        encounter_operation="open_door",
                        door_id=d["guard"],
                        required_item_ids=[d["item"]],
                        set_flags={"opened": True},
                    ).model_dump()
                ],
            }
            event = svc.rooms.append(
                session, room, "agent.action_proposed", d["agent"], {"text": raw}
            )
            nav = await svc.navigation.state(session, room.id)
            slot = next(
                s for s in await svc.rooms.slots(session, room) if s.member_id == d["agent"]
            )
            profile = await session.scalar(select(ProfileRecord))
            cid, rid = str(uuid4()), str(uuid4())
            state = {
                "room_id": room.id,
                "cycle_id": cid,
                "origin": "teammate",
                "triggering_member_id": d["agent"],
                "triggering_event_seq": event.seq,
            }
            session.add(AgentCycle(id=cid, room_id=room.id, status="completed", state=state))
            plan = KeeperPlan(
                plan_id=cid,
                cycle_id=cid,
                current_scene_id=nav.current_scene_node_id,
                parsed_intent=PlayerIntent(
                    type="use_item",
                    actor_member_id=d["agent"],
                    actor_character_slot_id=slot.id,
                    evidence_quote=raw,
                    confidence=1,
                ),
                focus=TurnFocus(action=raw, action_target_id=nav.current_scene_node_id),
            )
            session.add(
                AgentRun(
                    id=rid,
                    room_id=room.id,
                    cycle_id=cid,
                    profile_id=profile.id,
                    actor_member_id=room.host_member_id,
                    graph_node="plan_keeper_action",
                    status="decided",
                    input_seq_start=event.seq,
                    input_seq_end=event.seq,
                    provider="fixture",
                    model="fixture",
                    context={"module": {"approved_entities": [{"id": d["guard"]}]}},
                    structured_output=plan.model_dump(mode="json"),
                )
            )
            return state, rid

    calls = []

    async def permissive(runtime, state, schema, instruction, context, node):
        calls.append(context)
        assert context["action_authority"]["target_id"] == d["guard"]
        assert context["action_authority"]["held_instances"][d["item"]] == d["item"]
        return schema(applicable=True, option="1", action_clause_ids=["u1"], reason="matching")

    monkeypatch.setattr(adjudication, "call_model", permissive)
    state, rid = client.portal.call(prepare)
    client.portal.call(adjudication.adjudicate_prepared, svc.runtime, state, rid)

    async def execute():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            run = await session.get(AgentRun, rid)
            cycle = await session.get(AgentCycle, state["cycle_id"])
            plan = KeeperPlan.model_validate(run.structured_output)
            facts = await svc.adjudication.facts(session, room, cycle, run)
            actions = svc.adjudication.actions(plan, facts)
            assert [a.name for a in actions] == ["reveal_entity", "apply_module_action"]
            assert not load_state(room).module_runtime.flags.get("opened")
            session.add(
                ActionPlanRecord(
                    cycle_id=cycle.id,
                    room_id=room.id,
                    run_id=rid,
                    document=AdjudicationRecord(plan=plan).model_dump(mode="json"),
                )
            )
            await session.flush()
            await svc.entities.reveal(session, room, d["guard"], d["agent"], cycle.id)
            receipt = await apply_interaction(
                svc, session, room, ModuleActionArgs(**actions[-1].arguments), run=run
            )
            assert receipt["actor_member_id"] == d["agent"]
            assert receipt["check_ids"] == []
            assert load_state(room).module_runtime.flags["opened"]
            assert load_state(room).module_runtime.inventory[d["item"]] == d["agent"]

    client.portal.call(execute)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "raw,target_type,expected",
    [
        ("我上推油门杆，让列车减速。", "clue", "interact"),
        ("我走进前方车厢，推开门。", "location", "move"),
        ("我上推油门杆。", "scene", "move"),
        ("我观察油门杆。", "clue", "move"),
    ],
)
def test_moving_a_control_is_not_a_scene_transition(raw, target_type, expected):
    plan = KeeperPlan(
        plan_id="p",
        cycle_id="c",
        current_scene_id="here",
        parsed_intent=PlayerIntent(
            type="move",
            actor_member_id="human",
            actor_character_slot_id="slot",
            evidence_quote=raw,
            confidence=1,
        ),
        focus=TurnFocus(action=raw, action_target_id="control"),
    )
    context = {
        "triggering_action": {"payload": {"text": raw}},
        "current_targets": [{"id": "control", "title": "拉杆", "type": target_type}],
    }
    fixed = restore_output(plan, KeeperPlan, context)
    assert fixed.parsed_intent.type == expected
    assert fixed.focus.action_target_id == "control"
    assert fixed.focus.action == raw
    assert not fixed.proposed_tool_calls
    assert fixed.proposed_transition_id is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("我在原地操作已经打开的控制面板，上推油门杆。", {"control"}),
        ("我把已经打开的手机扔出去。", {"throw"}),
        ("我打开已经解锁的面板。", {"open"}),
    ],
)
def test_completed_state_modifier_cannot_authorize_a_new_operation(raw, expected):
    from app.preparation.action_authority import action_kinds

    assert set(action_kinds(raw)) == expected


def test_teammate_keeps_actual_public_results_without_private_receipts():
    from app.module_ir.context import public_interaction_results

    runtime = {
        "receipts": {
            "old": {"entity_id": "door", "source_event_seq": 10, "text": "门已打开"},
            "hidden": {"entity_id": "secret", "source_event_seq": 40, "text": "未公开目标"},
            "panel": {
                "entity_id": "panel",
                "source_event_seq": 30,
                "actor_member_id": "peer",
                "text": "面板已解锁",
                "kp_ruling": {"private": "秘密"},
                "inventory": {"hidden-item": "peer"},
            },
            "new": {"entity_id": "door", "source_event_seq": 20, "text": "门已关闭"},
        }
    }
    results = public_interaction_results(runtime, {"door", "panel"})
    assert [r["text"] for r in results] == ["门已关闭", "面板已解锁"]
    assert results[-1] == {
        "entity_id": "panel",
        "source_event_seq": 30,
        "actor_member_id": "peer",
        "text": "面板已解锁",
    }
    assert public_interaction_results({}, {"door"}) == []


@pytest.mark.parametrize("verb", ["交还给", "还给", "归还给"])
def test_return_item_variants_require_current_actor_instance_and_are_not_questions(verb):
    runtime = SessionStateV1().module_runtime
    runtime.inventory["keys:peer"] = "peer"
    runtime.item_instances["keys:peer"] = "keys"
    raw = f"我把钥匙{verb}周岚。"
    plan = KeeperPlan(
        plan_id="p",
        cycle_id="c",
        current_scene_id="here",
        parsed_intent=PlayerIntent(
            type="use_item",
            actor_member_id="peer",
            actor_character_slot_id="slot",
            evidence_quote=raw,
            confidence=1,
        ),
        focus=TurnFocus(action=raw, action_target_id="keys"),
    )

    def freeze(text):
        plan.focus.action = text
        return freeze_action(
            plan,
            text,
            "peer",
            448,
            "here",
            {"keys": {"title": "钥匙", "type": "item"}},
            runtime,
            {"human": "周岚", "peer": "沈砚"},
        )

    method = rule(inventory_operation="give", item_id="keys")
    args = dict(actor="peer", seq=448, scene="here", recipient="human")
    assert not authority_error(freeze(raw), method, runtime, **args)
    assert authority_error(freeze("能否" + raw + "？"), method, runtime, **args)
    frozen = freeze(raw)
    runtime.inventory["keys:peer"] = "human"
    assert authority_error(frozen, method, runtime, **args)


@pytest.mark.parametrize(
    "raw,repair",
    [
        ("我拿出钥匙，尝试插入驾驶室门的锁孔。", True),
        ("我转动锁孔里的钥匙。", True),
        ("如果能插入驾驶室门的锁孔就好了。", False),
        ("我观察驾驶室门的锁孔。", False),
    ],
)
def test_method_in_transition_slot_still_needs_actual_open_action(raw, repair):
    from app.preparation.adjudication import repair_interaction_transition

    plan = KeeperPlan(
        plan_id="p",
        cycle_id="c",
        current_scene_id="here",
        parsed_intent=PlayerIntent(
            type="use_item",
            evidence_quote=raw,
            actor_member_id="peer",
            actor_character_slot_id="slot",
            confidence=1,
        ),
        focus=TurnFocus(action=raw, action_target_id="door"),
        proposed_transition_id="unlock_door",
    )
    entities = {
        "door": {
            "title": "驾驶室门",
            "interactions": [
                {
                    "id": "unlock_door",
                    "instruction": "用钥匙开门。",
                    "source_block_ids": ["source"],
                    "public_result": "门已打开。",
                    "kp_enabled": True,
                    "encounter_operation": "open_door",
                }
            ],
        }
    }
    plan.action_authority = freeze_action(
        plan, raw, "peer", 542, "here", entities, SessionStateV1().module_runtime, {}
    )
    facts = SimpleNamespace(
        transitions={}, approved_entities=entities, local_entity_ids={"door"}, scene_id="here"
    )
    repair_interaction_transition(plan, facts)
    assert plan.proposed_transition_id == (None if repair else "unlock_door")
    assert not plan.proposed_tool_calls  # Repair itself cannot unlock or move.
    plan.proposed_transition_id = "real_exit"
    facts.transitions["real_exit"] = {}
    repair_interaction_transition(plan, facts)
    assert plan.proposed_transition_id == "real_exit"


def test_using_key_does_not_retarget_to_searching_for_owned_key():
    from app.preparation.search import repair_search_target

    value = {
        "focus": {
            "action": "我转动钥匙，但门锁似乎异常，需要更多时间检查。",
            "action_target_id": "door",
        },
        "parsed_intent": {"type": "use_item"},
    }
    original = deepcopy(value)
    repair_search_target(value, {"search_targets": [{"entity_id": "keys", "title": "钥匙"}]})
    assert value == original


@pytest.mark.parametrize("raw,allowed", [("我用钥匙打开门。", True), ("我观察钥匙。", False)])
def test_instrument_focus_needs_matching_operation_and_actual_holder(raw, allowed):
    from app.preparation.adjudication import matches_action_focus

    runtime = SessionStateV1().module_runtime
    runtime.inventory["keys:peer"] = "peer"
    runtime.item_instances["keys:peer"] = "keys"
    plan = KeeperPlan(
        plan_id="p",
        cycle_id="c",
        current_scene_id="here",
        parsed_intent=PlayerIntent(
            type="use_item",
            actor_member_id="peer",
            actor_character_slot_id="slot",
            evidence_quote=raw,
            confidence=1,
        ),
        focus=TurnFocus(action=raw, action_target_id="keys"),
    )
    plan.action_authority = freeze_action(
        plan,
        raw,
        "peer",
        542,
        "here",
        {"keys": {"title": "钥匙"}, "door": {"title": "门"}},
        runtime,
        {},
    )
    method = rule(encounter_operation="open_door", required_item_ids=["keys"])
    assert matches_action_focus(plan, "here", "door", method) is allowed
    assert not matches_action_focus(plan, "here", "other_door", method)
    runtime.inventory["keys:peer"] = "human"
    assert authority_error(
        plan.action_authority, method, runtime, actor="peer", seq=542, scene="here"
    )


def test_key_method_ignores_only_extra_quote_and_keeps_actual_clause_and_holder_gates():
    from app.preparation.adjudication import (
        PreparedDecision,
        action_evidence,
        state_verified_method,
    )

    raw = "我用钥匙插入锁孔，看看是否能打开。"
    method = rule(encounter_operation="open_door", required_item_ids=["keys"])
    decision = PreparedDecision(
        option="1",
        applicable=True,
        action_clause_ids=["u1"],
        evidence_quotes=["我用钥匙插入锁孔。"],
        reason="匹配",
    )
    evidence = action_evidence(
        decision,
        [{"id": "u1", "text": raw}],
        raw,
        [raw],
        state_verified=state_verified_method(method.model_dump()),
    )
    assert evidence == (raw, [raw])
    assert (
        action_evidence(
            decision, [{"id": "u1", "text": "我观察门锁。"}], raw, [raw], state_verified=True
        )
        is None
    )
    assert not state_verified_method(rule(encounter_operation="continuous_lure").model_dump())


def test_auxiliary_clause_selection_preserves_newline_but_cannot_borrow_unselected_words():
    from app.agents.generation_contracts import utterance_clauses
    from app.preparation.adjudication import PreparedDecision, action_evidence

    raw = "我用钥匙尝试打开驾驶室门。\n我手里有钥匙，试试看能不能打开这扇门。"
    clauses = utterance_clauses(raw)
    decision = PreparedDecision(
        option="1",
        applicable=True,
        reason="匹配",
        action_clause_ids=[c["id"] for c in clauses if c["text"].strip()],
    )
    assert action_evidence(decision, clauses, raw, [raw], state_verified=True) == (raw, [raw])
    raw = "我观察手机。把手机扔出去。然后等待。"
    clauses = utterance_clauses(raw)
    decision.action_clause_ids = [clauses[0]["id"], clauses[2]["id"]]
    assert action_evidence(decision, clauses, raw, [raw], state_verified=True) is None


def test_actual_public_change_allows_same_teammate_attempt_but_not_chat_or_stale_spam():
    from app.agents.adjudication_schemas import BehaviorState, TeammateDecision
    from app.agents.behavior import TeammateBehaviorPolicy

    decision = TeammateDecision(
        mode="assist",
        action_text="我用钥匙尝试打开驾驶室门。",
        related_player_action_seq=156,
        confidence=1,
    )
    kwargs = dict(
        state=BehaviorState(),
        recent_outputs=[decision.action_text],
        other_outputs=[],
        player_text="沈砚，继续吧。",
        player_intent="converse",
        public_ids=set(),
        action_seq=156,
        fingerprint="after_door_reveal",
    )
    policy = TeammateBehaviorPolicy()
    assert policy.validate(decision, **kwargs).reason == "repeated_output"
    assert policy.validate(decision, **kwargs, explicit_action_request=True).accepted
    assert (
        policy.validate(
            decision, **kwargs, explicit_action_request=True, requested_operations=["give"]
        ).reason
        == "assistance_does_not_attempt_requested_operation"
    )
    assert policy.validate(
        decision, **kwargs, explicit_action_request=True, requested_operations=["open"]
    ).accepted
    assert policy.validate(decision, **kwargs, public_change_after_last_output=True).accepted
    decision.mode = "speak"
    assert (
        policy.validate(decision, **kwargs, public_change_after_last_output=True).reason
        == "repeated_output"
    )
    decision.mode = "assist"
    decision.related_public_entity_ids = ["secret"]
    assert (
        policy.validate(decision, **kwargs, public_change_after_last_output=True).reason
        == "target_not_public"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "我用手机灯看清前方的身影。",
        "借手机的光观察声音来源。",
        "我把手机灯照向那边，仔细观察。",
        "I observe the shapes using my phone light.",
        "我们可以把手机扔到远处。",
        "不如把手机抛过去。",
        "如果有响声，我把手机扔出去。",
    ],
)
@pytest.mark.parametrize("distant", [False, True])
def test_permissive_model_cannot_turn_observation_into_throw(
    client,
    interactions,  # noqa: F811
    raw,
    distant,
):
    from sqlalchemy import func, select

    from app.persistence.agent_models import CheckRecord
    from app.persistence.room_models import RoomEvent

    d, action = interactions
    service = client.app.state.agent_service
    client.portal.call(action, "take", d["player"], "我拿起钥匙。")

    async def configure():
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, d["room"]["id"])
            entity = await service.entities.entity(session, room.id, d["item"])
            # Both model layers have claimed applicable/matches=true; the real
            # execution service must still reject the operation before any write.
            entity.snapshot = {
                **entity.snapshot,
                "interactions": [
                    rule(
                        kp_enabled=True,
                        encounter_operation="sound_once",
                        sound_item_id=d["item"],
                        required_facts=["sound_distance_over_half_car"] if distant else [],
                        set_flags={"safe_passage": True, "sound_throw_used": True},
                    ).model_dump()
                ],
            }
            state = load_state(room)
            state.module_runtime.sounds[d["item"]] = {
                "active": True,
                "continuous": True,
                "actor_id": d["player"],
                "scene_node_id": "here",
                "kind": "item",
            }
            store_state(room, state)
            return (
                deepcopy(room.session_state),
                await session.scalar(select(func.count()).select_from(CheckRecord)),
                await session.scalar(select(func.count()).select_from(RoomEvent)),
            )

    before = client.portal.call(configure)
    with pytest.raises(RoomError, match="实际动作不授权"):
        client.portal.call(lambda: action("test", d["player"], raw, verified=True))

    async def inspect():
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, d["room"]["id"])
            return (
                room.session_state,
                await session.scalar(select(func.count()).select_from(CheckRecord)),
                await session.scalar(select(func.count()).select_from(RoomEvent)),
            )

    assert client.portal.call(inspect) == before


def test_lighting_prose_cannot_invent_prepared_equipment_or_remote_light():
    from app.preparation.observation import validate_lighting_prose

    entity = {
        "id": "lamp",
        "type": "item",
        "title": "随身手电筒",
        "aliases": ["手电筒"],
        "interactions": [{"action_kinds": ["light"], "set_flags": {"light_on": True}}],
    }
    runtime = SessionStateV1().module_runtime
    text = "四周一片漆黑，只有手电筒的光束在前方晃动。"
    with pytest.raises(RoomError, match="照明物品"):
        validate_lighting_prose(text, [entity], runtime, "here")
    runtime.flags["light_on"] = True
    runtime.item_instances["instance"] = "lamp"
    runtime.dropped_items["instance"] = "elsewhere"
    with pytest.raises(RoomError, match="照明物品"):
        validate_lighting_prose(text, [entity], runtime, "here")
    runtime.dropped_items["instance"] = "here"
    validate_lighting_prose(text, [entity], runtime, "here")
    runtime.flags["light_on"] = False
    validate_lighting_prose("你关闭了手电筒的灯光。", [entity], runtime, "here")
    validate_lighting_prose("如果有手电筒的光束就可以观察。", [entity], runtime, "here")


def test_real_throw_keeps_ringing_instance_and_distinct_impact():
    state = SessionStateV1()
    r = state.module_runtime
    r.inventory = {"phone:actor": "actor"}
    r.item_instances = {"phone:actor": "phone"}
    r.sounds = {"phone:actor": {"active": True, "continuous": True, "kind": "item"}}
    args = ModuleActionArgs(
        entity_id="passage",
        interaction_id="throw",
        evidence_quote="我把响着的手机抛向后方。",
        used_item_id="phone",
    )
    apply_encounter(state, rule(encounter_operation="sound_once"), args, "actor", "scene", 8)
    assert r.dropped_items == {"phone:actor": "scene"} and not r.inventory
    assert r.sounds["phone:actor"]["continuous"] and r.sounds["phone:actor"]["actor_id"] is None
    assert r.sounds["impact:phone:actor:8"]["kind"] == "impact"
    end_transient_sound(state, "scene")
    assert r.sounds["phone:actor"]["active"]
    assert not r.sounds["impact:phone:actor:8"]["active"]


@pytest.mark.parametrize(
    "raw", ["我将手机抛向后方墙壁。", "我把手机扔向另一头。", "I toss my phone away."]
)
def test_throw_capability_and_distance_are_separate(raw):
    runtime = SessionStateV1().module_runtime
    runtime.inventory = {"phone:actor": "actor"}
    runtime.item_instances = {"phone:actor": "phone"}
    plan = SimpleNamespace(
        focus=TurnFocus(action=raw, action_target_id="passage"),
        parsed_intent=SimpleNamespace(type="interact"),
    )
    frozen = freeze_action(
        plan,
        raw,
        "actor",
        3,
        "scene",
        {"phone": {"title": "随身手机", "aliases": ["手机", "phone"]}},
        runtime,
        {},
    )
    args = dict(actor="actor", seq=3, scene="scene", item_id="phone")
    assert not authority_error(frozen, rule(encounter_operation="sound_once"), runtime, **args)
    distant = rule(encounter_operation="sound_once", required_facts=["distance"])
    assert "场景事实" in authority_error(frozen, distant, runtime, **args)
    runtime.scene_facts["distance"] = {
        "established": True,
        "scene_node_id": "scene",
        "actor_member_id": "actor",
        "source_event_seq": 2,
        "origin": "rule_description",
    }
    assert authority_error(frozen, distant, runtime, **args)
    runtime.scene_facts["distance"]["origin"] = "scene_adjudication"
    assert not authority_error(frozen, distant, runtime, **args)
    runtime.inventory["phone:actor"] = "teammate"
    assert "物品实例" in authority_error(frozen, distant, runtime, **args)


@pytest.mark.parametrize(
    "raw", ["我找乘务员说的钥匙。", "我翻找那个包。", "我在前门附近寻找黑包，找到后拿起来。"]
)
@pytest.mark.parametrize("partial_check", [False, True, "wrong_skill"])
def test_hidden_search_repairs_wrong_parent_focus_and_malformed_tool(raw, partial_check):
    actor, target = str(uuid4()), str(uuid4())
    plan = KeeperPlan(
        plan_id="plan",
        cycle_id="cycle",
        current_scene_id="scene",
        parsed_intent=PlayerIntent(
            type="investigate",
            actor_member_id=actor,
            actor_character_slot_id="slot",
            confidence=1,
            evidence_quote=raw,
            target_id="scene",
        ),
        focus=TurnFocus(action=raw, action_target_id="scene"),
        proposed_tool_calls=[
            {
                "name": "apply_module_action",
                "arguments": {
                    "entity_id": target,
                    "interaction_id": "spot_hidden",
                    "evidence_quote": raw,
                },
            }
        ],
    )
    if partial_check:
        from app.agents.adjudication_schemas import CheckProposal

        plan.proposed_check = CheckProposal(
            target_member_id=actor,
            kind="skill",
            name="search" if partial_check == "wrong_skill" else "spot_hidden",
            target_entity_id=target,
            reason=raw,
            clue_id=None,
        )
    context = {
        "triggering_action": {"payload": {"text": raw}},
        "action_identifiers": {"actor_member_id": actor},
        "check_requirements": [
            {
                "entity_id": target,
                "title": "黑色包里的两把钥匙",
                "aliases": ["钥匙", "黑包"],
                "search_aliases": ["那个包"],
                "access_policy": "requires_check",
                "successful_check": {
                    "kind": "skill",
                    "name": "spot_hidden",
                    "difficulty": "regular",
                },
            }
        ],
    }
    fixed = restore_output(plan, KeeperPlan, context)
    assert fixed.focus.action_target_id == target
    assert str(fixed.proposed_check.target_member_id) == actor
    assert fixed.proposed_check.clue_id == target and fixed.proposed_check.name == "spot_hidden"
    assert not fixed.proposed_tool_calls
    assert fixed.focus.action == raw


def test_settled_starting_choice_blocks_model_reveal_without_changing_original_result():
    from app.preparation.search import guard_initial_reselection

    actor = str(uuid4())
    raw = "我检查自己的口袋和夹层，试图找到手电筒。"
    plan = KeeperPlan(
        plan_id="plan",
        cycle_id="cycle",
        current_scene_id="scene",
        parsed_intent=PlayerIntent(
            type="investigate",
            actor_member_id=actor,
            actor_character_slot_id="slot",
            confidence=1,
            evidence_quote=raw,
        ),
        focus=TurnFocus(action=raw, action_target_id="torch"),
        proposed_tool_calls=[
            {
                "name": "apply_module_action",
                "arguments": {
                    "entity_id": "scene",
                    "interaction_id": "initial_torch",
                    "evidence_quote": raw,
                },
            }
        ],
        proposed_reveal_entity_ids=["torch"],
        action_authority={"target_id": "torch", "kinds": ["search"]},
    )
    facts = SimpleNamespace(
        actor_member_id=actor,
        scene_id="scene",
        local_entity_ids={"scene", "torch"},
        approved_entities={
            "scene": {
                "interactions": [
                    {"inventory_operation": "initial", "item_id": "torch", "kp_enabled": True}
                ]
            }
        },
    )
    runtime = SessionStateV1().module_runtime
    runtime.initial_belongings[actor] = {
        "chosen_item_id": "phone",
        "item_ids": [],
        "check_ids": ["original-85"],
    }
    before = runtime.model_dump()
    assert guard_initial_reselection(plan, facts, runtime)
    assert plan.needs_clarification and not plan.proposed_reveal_entity_ids
    assert not plan.proposed_tool_calls and plan.proposed_check is None
    assert runtime.model_dump() == before
    from app.agents.action_policy import ActionFacts, ActionPolicyValidator

    validation = ActionPolicyValidator().validate(
        plan.parsed_intent,
        plan,
        ActionFacts(
            room_id="room",
            cycle_id="cycle",
            raw_text=raw,
            actor_member_id=actor,
            actor_slot_id="slot",
            actor_authorized=True,
            scene_id="scene",
        ),
        [],
    )
    assert "不能重新选择或重骰" in validation.clarification_question
    # Another investigator has their own first choice; a later recovery scene
    # and an actually held gift are separate legitimate tasks.
    facts.actor_member_id = str(uuid4())
    assert not guard_initial_reselection(plan, facts, runtime)
    facts.actor_member_id = actor
    facts.local_entity_ids = {"torch"}
    assert not guard_initial_reselection(plan, facts, runtime)
    facts.local_entity_ids.add("scene")
    runtime.item_instances["gift"] = "torch"
    runtime.inventory["gift"] = actor
    assert not guard_initial_reselection(plan, facts, runtime)


def test_named_automatic_item_repairs_wrong_gated_item_before_freezing():
    from app.preparation.search import repair_search_target

    actor, phone, torch = str(uuid4()), str(uuid4()), str(uuid4())
    raw = "我低头检查自己的口袋和夹层，试图找到手电筒。"
    value = {
        "focus": {"action": raw, "action_target_id": phone},
        "parsed_intent": {"type": "investigate", "target_id": phone},
    }
    repair_search_target(
        value,
        {
            "search_targets": [{"entity_id": torch, "title": "随身手电筒", "aliases": ["手电筒"]}],
            "check_requirements": [{"entity_id": phone, "title": "随身手机", "aliases": ["手机"]}],
        },
    )
    assert value["focus"]["action_target_id"] == torch
    plan = KeeperPlan(
        plan_id="plan",
        cycle_id="cycle",
        current_scene_id="scene",
        focus=TurnFocus(**value["focus"]),
        parsed_intent=PlayerIntent(
            **value["parsed_intent"],
            actor_member_id=actor,
            actor_character_slot_id="slot",
            confidence=1,
            evidence_quote=raw,
        ),
    )
    runtime = SessionStateV1().module_runtime
    frozen = freeze_action(
        plan,
        raw,
        actor,
        1,
        "scene",
        {
            phone: {"type": "item", "title": "随身手机", "aliases": ["手机"]},
            torch: {"type": "item", "title": "随身手电筒", "aliases": ["手电筒"]},
        },
        runtime,
        {actor: "调查员"},
    )
    args = dict(actor=actor, seq=1, scene="scene")
    assert authority_error(
        frozen, rule(inventory_operation="initial", item_id=phone), runtime, **args
    )
    assert not authority_error(
        frozen, rule(inventory_operation="initial", item_id=torch), runtime, **args
    )


def test_initial_possession_method_makes_only_its_local_item_searchable():
    from app.preparation.search import searchable_entity_ids

    entities = {
        "scene": {
            "type": "scene",
            "interactions": [
                {
                    "inventory_operation": "initial",
                    "check_name": "luck",
                    "kp_enabled": True,
                    "item_id": "torch",
                    "scene_node_ids": ["scene"],
                }
            ],
        },
        "torch": {"type": "item", "reveal_conditions": {"access_policy": "automatic"}},
        "npc": {"type": "npc", "reveal_conditions": {"access_policy": "automatic"}},
        "unrelated": {"type": "item", "reveal_conditions": {"access_policy": "automatic"}},
    }
    assert searchable_entity_ids(entities, set(entities), "scene") == {"torch"}
    assert searchable_entity_ids(entities, set(entities), "elsewhere") == set()
    assert searchable_entity_ids(entities, {"torch", "npc", "unrelated"}, "scene") == set()
    assert searchable_entity_ids(entities, {"scene", "npc", "unrelated"}, "scene") == set()


def test_local_fallback_prompt_reserves_space_for_current_transfer():
    import json

    from app.agents.action_runtime import planning_prompt

    context = {
        "structure_navigation": False,
        "prepared_module": None,
        "module_context_audit": {"context_mode": "local_fallback", "navigation_revision": 1},
        "triggering_action": {
            "seq": 146,
            "actor_member_id": "human",
            "payload": {"text": "我把两把钥匙交给沈砚。"},
        },
        "search_targets": [{"entity_id": "keys", "aliases": ["黑包"] * 500}],
        "public_entities": [{"id": "keys", "source_references": ["source"] * 500}],
        "response_fact_candidates": [
            {"entity_id": "keys", "holder_id": "human", "instance_id": "keys"}
        ],
        "current_targets": [{"id": "keys", "title": "两把钥匙"}],
        "previous_attempts": [
            {
                "id": "fixed-die",
                "result": {"total": 49, "passed": True, "display_text": "检定成功" * 500},
            }
        ],
        "module": {"approved_entities": [{"id": "keys", "interactions": ["give"]}]},
    }
    before = deepcopy(context)
    prompt = planning_prompt(context)
    assert len(json.dumps(context, ensure_ascii=False)) > 6092
    assert len(json.dumps(prompt, ensure_ascii=False)) < 6092
    assert prompt["triggering_action"]["seq"] == 146
    assert prompt["response_fact_candidates"] == context["response_fact_candidates"]
    assert prompt["previous_attempts"][0]["result"] == {"total": 49, "passed": True}
    assert prompt["module"] == context["module"]
    assert context == before  # Full aliases and source audit remain available to server validation.


def test_fallback_prompt_keeps_rule_conditions_and_original_failures_without_duplicate_exits():
    from app.agents.action_runtime import planning_prompt

    check = {"kind": "attribute", "name": "luck", "difficulty": "regular"}
    context = {
        "module_context_audit": {"context_mode": "local_fallback"},
        "structure_navigation": False,
        "triggering_action": {"seq": 290, "payload": {"text": "我把钥匙交给队友。"}},
        "approved_exits": [{"transition_id": "exit", "target_scene_node_id": "next"}],
        "module": {
            "outgoing_transitions": [{"transition_id": "exit"}],
            "interaction_state": {"held_items": [{"instance_id": "keys", "holder_id": "actor"}]},
        },
        "check_requirements": [
            {
                "entity_id": "phone",
                "successful_check": check,
                "task_scope": "only this hidden search",
                "conditions": {
                    "access_policy": "requires_check",
                    "successful_check": check,
                    "required_entity_ids": ["hint"],
                    "scene_id": "current",
                },
            }
        ],
        "previous_attempts": [
            {
                "id": "initial-roll",
                "policy_target_id": "phone",
                "result": {"total": 85, "passed": False},
            },
            {
                "id": "search-roll",
                "policy_target_id": "keys",
                "result": {"total": 88, "passed": False},
            },
        ],
    }
    before = deepcopy(context)
    prompt = planning_prompt(context)
    assert "outgoing_transitions" not in prompt["module"]
    assert prompt["approved_exits"] == context["approved_exits"]
    assert prompt["module"]["interaction_state"] == context["module"]["interaction_state"]
    requirement = prompt["check_requirements"][0]
    assert requirement["successful_check"] == check
    assert requirement["conditions"] == {"required_entity_ids": ["hint"], "scene_id": "current"}
    assert prompt["previous_attempts"] == context["previous_attempts"]
    assert context == before


def test_automatic_blind_actor_cannot_propose_unperceived_attack_or_untrained_treatment():
    from app.agents.combat_runtime import automatic_decision_contract

    context = {
        "attack_targets": [],
        "treatment_targets": [{"id": "wounded", "label": "伤者"}],
        "own": {"skills": {"brawl": 60}, "weapons": [{"id": "bite", "capacity": 0}]},
    }
    schema = automatic_decision_contract(context)
    # Wounded bystanders are not valid attack targets merely because the
    # general treatment view lists them; this actor cannot provide treatment.
    assert "wounded" not in str(schema.model_json_schema()["properties"]["target_id"])
    with pytest.raises(ValueError):
        schema(operation="attack", target_id="unperceived_human", weapon_id="bite", reason="攻击")
    with pytest.raises(ValueError):
        schema(operation="first_aid", target_id="wounded", reason="治疗")
    assert (
        schema(operation="pass", target_id=None, reason="循着现有铃声继续侦听").operation == "pass"
    )

    context["attack_targets"] = ["heard_actor"]
    schema = automatic_decision_contract(context)
    assert (
        schema(
            operation="attack", target_id="heard_actor", weapon_id="bite", reason="攻击声源"
        ).target_id
        == "heard_actor"
    )
    with pytest.raises(ValueError):
        schema(operation="attack", target_id="wounded", weapon_id="bite", reason="攻击")


@pytest.mark.parametrize(
    "raw,allowed", [("我攻击面前的循声者。", {"one", "two"}), ("我攻击循声者 2。", {"two"})]
)
def test_named_human_combat_target_cannot_be_replaced_with_teammate(raw, allowed):
    from app.agents.combat_runtime import human_decision_contract

    context = {
        "input": raw,
        "combat": {
            "participants": {
                "peer": {"label": "沈砚"},
                "one": {"label": "循声者 1"},
                "two": {"label": "循声者 2"},
            }
        },
    }
    schema = human_decision_contract(context)
    with pytest.raises(ValueError):
        schema(operation="attack", target_id="peer", reason="Wrong positive model decision")
    for target in allowed:
        assert (
            schema(operation="attack", target_id=target, reason="实际点名目标").target_id == target
        )
    for target in {"one", "two"} - allowed:
        with pytest.raises(ValueError):
            schema(operation="attack", target_id=target, reason="Wrong numbered instance")


@pytest.mark.parametrize("raw", ["钥匙在哪？", "我看看四周。", "如果找到那个包就好了。"])
def test_questions_and_general_observation_do_not_search_hidden_items(raw):
    from app.preparation.search import repair_search_target

    value = {
        "focus": {"action": raw, "action_target_id": "scene"},
        "parsed_intent": {"type": "observe"},
    }
    repair_search_target(
        value,
        {
            "check_requirements": [
                {
                    "entity_id": "keys",
                    "title": "钥匙",
                    "aliases": ["那个包"],
                }
            ]
        },
    )
    assert value["focus"]["action_target_id"] == "scene"


def test_teammate_requests_do_not_authorize_requesters_inventory():
    members = {"human": "周岚", "agent": "沈砚"}
    assert teammate_request("沈砚，请打开手机铃声。", members, "human") == "agent"
    assert teammate_request("我打开手机铃声。", members, "human") is None
    assert teammate_request("我把手机交给沈砚。", members, "human") is None


def test_sanity_unknown_survives_snapshot_answer_settles_once(client, interactions, monkeypatch):  # noqa: F811
    from sqlalchemy import select
    from test_rooms import ok

    from app.persistence.agent_models import AgentCycle, AgentRun, CheckRecord, ProfileRecord
    from app.preparation import sanity_adjudication as san
    from app.rooms.sanity_schemas import SanityRequest

    d, _ = interactions
    service = client.app.state.agent_service

    async def make_cycle(raw, original=False):
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, d["room"]["id"])
            entity = await service.entities.entity(session, room.id, d["item"])
            effect = {
                **entity.snapshot["sanity_effects"][0],
                "kp_enabled": True,
                "perception": "visual",
                "visibility_any_flags": ["torch_light"],
                "success_loss": "1",
                "failure_loss": "1",
            }
            entity.snapshot = {**entity.snapshot, "sanity_effects": [effect]}
            data = load_state(room)
            data.module_runtime.flags["torch_light"] = True
            store_state(room, data)
            event = service.rooms.append(
                session, room, "action.submitted", d["player"], {"text": raw}
            )
            slot = next(
                s for s in await service.rooms.slots(session, room) if s.member_id == d["player"]
            )
            profile = await session.scalar(select(ProfileRecord))
            cid, rid = str(uuid4()), str(uuid4())
            state = {
                "room_id": room.id,
                "cycle_id": cid,
                "keeper_run_id": rid,
                "triggering_member_id": d["player"],
                "triggering_event_seq": event.seq,
                "status": "running",
                "current_node": "discover_encounters",
                "call_count": 0,
                "encounter_queue": [
                    {
                        "entity_id": d["item"],
                        "effect_id": effect["id"],
                        "status": "kp_review",
                        "source_event_seq": event.seq,
                        "target_member_ids": [d["player"]],
                        "slot_ids": [slot.id],
                    }
                ]
                if original
                else [],
            }
            session.add(AgentCycle(id=cid, room_id=room.id, status="running", state=state))
            plan = KeeperPlan(
                plan_id=cid,
                cycle_id=cid,
                current_scene_id="scene",
                parsed_intent=PlayerIntent(
                    type="interact",
                    actor_member_id=d["player"],
                    actor_character_slot_id=slot.id,
                    target_id=d["item"],
                    evidence_quote=raw,
                    confidence=1,
                ),
                focus=TurnFocus(action=raw, action_target_id=d["item"]),
                proposed_tool_calls=[
                    {
                        "name": "apply_module_action",
                        "arguments": {
                            "entity_id": d["item"],
                            "interaction_id": "pickup",
                            "evidence_quote": raw,
                        },
                    }
                ],
            )
            session.add(
                AgentRun(
                    id=rid,
                    room_id=room.id,
                    cycle_id=cid,
                    profile_id=profile.id,
                    actor_member_id=room.host_member_id,
                    graph_node="plan_keeper_action",
                    status="decided",
                    input_seq_start=event.seq,
                    input_seq_end=event.seq,
                    provider="fixture",
                    model="fixture",
                    context={},
                    structured_output=plan.model_dump(mode="json"),
                )
            )
            return state, rid

    async def unknown(*args, **kwargs):
        return san.SanitySituation(
            applies="unknown", reason="缺少实际观察角度", clarification="你是否转向目标观察？"
        )

    monkeypatch.setattr(san, "call_model", unknown)
    state, rid = client.portal.call(make_cycle, "我向亮处观察。", True)
    ruled = client.portal.call(san.resolve_sanity_conditions, service.runtime, state)
    assert ruled["encounter_queue"][0]["status"] == "awaiting_clarification"

    async def finish_cycle():
        async with service.rooms.transaction() as session:
            cycle = await session.get(AgentCycle, state["cycle_id"])
            cycle.status = "completed"
            cycle.state = {**cycle.state, "status": "completed", "current_node": "completed"}

    client.portal.call(finish_cycle)
    ok(client.post(d["prefix"] + "/pause"))
    saved = ok(client.post(d["prefix"] + "/snapshots", json={"name": "awaiting SAN answer"}))[
        "snapshot"
    ]
    ok(client.post(d["prefix"] + f"/snapshots/{saved['id']}/load"))
    ok(client.post(d["prefix"] + "/resume"))
    pending = ok(client.get(d["prefix"]))["session_state"]["module_runtime"][
        "sanity_clarifications"
    ]
    assert next(iter(pending.values()))["status"] == "awaiting_answer"

    answer = "是的，我转向灯光照着的目标，看清了它。"

    async def yes(*args, **kwargs):
        return san.SanitySituation(
            applies="yes", evidence_quotes=[answer], reason="照明及角度已满足"
        )

    monkeypatch.setattr(san, "call_model", yes)
    child, child_run = client.portal.call(make_cycle, answer)
    assert client.portal.call(san.resume_sanity_clarification, service.runtime, child, child_run)

    async def settle():
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, d["room"]["id"])
            cycle = await session.get(AgentCycle, child["cycle_id"])
            run = await session.get(AgentRun, child_run)
            assert not run.structured_output["proposed_tool_calls"]
            entry = cycle.state["sanity_resumed"][0]
            before = load_state(room).characters
            before_san = {str(k): v.san for k, v in before.items()}
            args = SanityRequest(
                target_member_id=d["player"],
                entity_id=d["item"],
                effect_id=entry["effect_id"],
                source_event_seq=entry["source_event_seq"],
            )
            check = await service.sanity.request(session, room, args, run=run, encounter=entry)
            await service.sanity.roll(session, room, check, "san")
            if check.document["sanity"]["stage"] == "loss":
                await service.sanity.roll(session, room, check, "loss")
            after = {str(k): v.san for k, v in load_state(room).characters.items()}
            assert before_san[entry["slot_ids"][0]] - after[entry["slot_ids"][0]] == 1
            again = await service.sanity.request(session, room, args, run=run, encounter=entry)
            assert again.id == check.id
            await service.sanity.roll(session, room, again, "san")
            assert after == {str(k): v.san for k, v in load_state(room).characters.items()}
            assert (
                len(
                    list(
                        await session.scalars(
                            select(CheckRecord).where(CheckRecord.room_id == room.id)
                        )
                    )
                )
                == 1
            )

    client.portal.call(settle)
    assert not client.portal.call(
        san.resume_sanity_clarification, service.runtime, child, child_run
    )


def test_visual_gate_rejects_hearsay_and_requires_phone_range():
    from app.preparation.sanity_adjudication import visual_gate

    effect = SimpleNamespace(
        perception="visual", visibility_any_flags=["phone_light", "torch_light"]
    )
    assert visual_gate(effect, {}, "我看见了。") == "no"
    assert visual_gate(effect, {"phone_light": True}, "我确实看见了。") == "unknown"
    assert visual_gate(effect, {"phone_light": True}, "我靠近观察。") is None


def test_completed_observation_receipt_overrides_wrong_san_model(
    client,
    interactions,  # noqa: F811
    monkeypatch,
):
    from app.persistence.agent_models import AgentCycle
    from app.preparation import sanity_adjudication as san

    d, _ = interactions
    service = client.app.state.agent_service

    async def prepare():
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, d["room"]["id"])
            entity = await service.entities.entity(session, room.id, d["item"])
            effect = entity.snapshot["sanity_effects"][0]
            event = service.rooms.append(
                session, room, "action.submitted", d["player"], {"text": "借光观察眼前形态。"}
            )
            cid = str(uuid4())
            data = load_state(room)
            data.module_runtime.observations["receipt"] = {
                "established": True,
                "actor_member_id": d["player"],
                "cycle_id": cid,
                "entity_id": d["item"],
                "effect_id": effect["id"],
                "source_event_seq": event.seq,
                "scene_node_id": "here",
                "text": "已看清批准的恐怖形态。",
                "origin": "interaction_receipt",
            }
            store_state(room, data)
            slot = next(
                s for s in await service.rooms.slots(session, room) if s.member_id == d["player"]
            )
            state = {
                "room_id": room.id,
                "cycle_id": cid,
                "triggering_event_seq": event.seq,
                "triggering_member_id": d["player"],
                "encounter_queue": [
                    {
                        "entity_id": d["item"],
                        "effect_id": effect["id"],
                        "status": "kp_review",
                        "source_event_seq": event.seq,
                        "target_member_ids": [d["player"]],
                        "slot_ids": [slot.id],
                    }
                ],
            }
            session.add(AgentCycle(id=cid, room_id=room.id, status="running", state=state))
            return state

    async def wrong_model(*args, **kwargs):
        pytest.fail("An established observation must not be independently denied by another model")

    monkeypatch.setattr(san, "call_model", wrong_model)
    state = client.portal.call(prepare)
    result = client.portal.call(san.resolve_sanity_conditions, service.runtime, state)
    assert result["encounter_queue"][0]["status"] == "approved"


def test_descriptive_negative_clause_does_not_cancel_actual_search():
    from app.preparation.action_authority import action_kinds

    assert "search" in action_kinds("我检查随身手机，看看有没有带在身上。")
    assert "throw" not in action_kinds("不要扔手机，我打开手机灯。")
    assert not action_kinds("如果有响声，我把手机扔出去。")


def test_possession_prerequisite_freezes_holder_without_substituting_thrown_items():
    runtime = SessionStateV1().module_runtime
    runtime.inventory = {"keys:actor": "actor", "phone:actor": "actor"}
    runtime.item_instances = {"keys:actor": "keys", "phone:actor": "phone"}
    entities = {"keys": {"title": "钥匙"}, "phone": {"title": "手机"}}
    raw = "我下推右侧拉杆加速。"
    plan = SimpleNamespace(
        focus=TurnFocus(action=raw, action_target_id="controls"),
        parsed_intent=SimpleNamespace(type="interact"),
    )
    frozen = freeze_action(plan, raw, "actor", 7, "scene", entities, runtime, {})
    args = dict(actor="actor", seq=7, scene="scene")
    assert not authority_error(frozen, rule(required_item_ids=["keys"]), runtime, **args)
    raw = "我把手机扔到后方。"
    plan.focus = TurnFocus(action=raw, action_target_id="keys")  # Wrong primary target.
    frozen = freeze_action(plan, raw, "actor", 7, "scene", entities, runtime, {})
    assert authority_error(
        frozen, rule(encounter_operation="sound_once"), runtime, item_id="keys", **args
    )


@pytest.mark.parametrize("raw", ["我借手机灯观察喘息来源。", "我用手机的光看清怪物。"])
def test_visual_action_repairs_equipment_focus_before_freezing(raw):
    from app.preparation.search import repair_observation_target

    value = {
        "focus": {"action": raw, "action_target_id": "phone"},
        "parsed_intent": {"type": "use_item"},
    }
    repair_observation_target(
        value,
        {
            "observation_targets": [{"aliases": ["喘息来源", "怪物"]}],
            "action_identifiers": {"current_scene_id": "scene"},
        },
    )
    assert value["focus"]["action_target_id"] == "scene"
    assert value["parsed_intent"]["type"] == "observe"
    assert value["focus"]["action"] == raw


def test_configured_observation_completion_requires_actual_light_and_position(
    client,
    interactions,  # noqa: F811
):
    from app.preparation.observation import approved_visual_result
    from app.rooms.sanity_schemas import SanityEffect

    d, _ = interactions
    service = client.app.state.agent_service

    async def check():
        async with service.rooms.transaction() as session:
            room = await service.rooms.room(session, d["room"]["id"])
            entity = await service.entities.entity(session, room.id, d["item"])
            effect = SanityEffect.model_validate(
                {**entity.snapshot["sanity_effects"][0], "perception": "visual"}
            )
            method = rule(
                kp_enabled=True,
                observation_entity_id=d["item"],
                observation_effect_id=effect.id,
                required_flags={"phone_light": True},
                visibility_any_flags=["phone_light"],
                required_facts=["visual_target_in_phone_light"],
            )
            entity.snapshot = {**entity.snapshot, "interactions": [method.model_dump()]}

            async def result(raw):
                return await approved_visual_result(service, session, room, d["item"], effect, raw)

            assert not await result("我靠近观察。")
            data = load_state(room)
            data.module_runtime.flags["phone_light"] = True
            store_state(room, data)
            assert not await result("我看清了。")
            assert not await result("我建议靠近观察。")
            assert (await result("我选择靠近，借手机的光仔细观察。"))[
                "text"
            ] == method.public_result

    client.portal.call(check)


def test_inventory_drops_misquoted_supplement_without_authorizing_a_different_action():
    from app.preparation.adjudication import PreparedDecision, action_evidence

    raw = "我把手机交给沈砚。"
    decision = PreparedDecision(
        applicable=True,
        option="1",
        action_clause_ids=["u1"],
        evidence_quotes=[raw, "我的手机仍在身上。"],
        reason="交接",
    )
    clauses = [{"id": "u1", "text": raw}]
    quotes = [raw, "你的手机仍在身上。"]
    assert action_evidence(decision, clauses, raw, quotes) is None
    assert action_evidence(decision, clauses, raw, quotes, state_verified=True) == (raw, [raw])
    assert action_evidence(decision, clauses, "我观察手机。", quotes, state_verified=True) is None


def test_frozen_named_recipient_cannot_be_substituted():
    r = SessionStateV1().module_runtime
    r.inventory = {"phone": "actor"}
    raw = "我把手机交给沈砚。"
    p = SimpleNamespace(
        focus=TurnFocus(action=raw, action_target_id="phone"),
        parsed_intent=SimpleNamespace(type="interact"),
    )
    a = freeze_action(
        p,
        raw,
        "actor",
        1,
        "scene",
        {"phone": {"title": "手机"}},
        r,
        {"actor": "周岚", "peer": "沈砚", "other": "林远"},
    )
    kwargs = dict(actor="actor", seq=1, scene="scene")
    method = rule(inventory_operation="give", item_id="phone")
    assert not authority_error(a, method, r, recipient="peer", **kwargs)
    assert "接收者" in authority_error(a, method, r, recipient="other", **kwargs)


def test_context_supplement_includes_moved_item_instance_but_not_remote_item(
    client,
    interactions,  # noqa: F811
    monkeypatch,
):
    from app.agents.adjudication import ContextSupplementService
    from app.agents.adjudication_schemas import ContextGap
    from app.persistence.adjudication_models import ActionPlanRecord

    d, action = interactions
    receipt = client.portal.call(action, "take", d["player"], "我拿起钥匙。")
    svc = client.app.state.agent_service
    original = svc.module_context.allowed

    async def moved_context(session, room):
        state, snapshot, ir, local, linked, ancestors = await original(session, room)
        # The carried item originated elsewhere and no longer has a local binding.
        snapshot.entity_bindings = [b for b in snapshot.entity_bindings if b.entity_id != d["item"]]
        return state, snapshot, ir, local, linked, ancestors

    monkeypatch.setattr(svc.module_context, "allowed", moved_context)

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            data = load_state(room)
            data.module_runtime.inventory = {"keys:owner": d["player"]}
            data.module_runtime.item_instances = {"keys:owner": d["item"]}
            store_state(room, data)
            record = await session.get(ActionPlanRecord, receipt["cycle_id"])
            nav = await svc.navigation.state(session, room.id)
            gap = ContextGap(
                missing_kind="entity",
                requested_target=d["item"],
                current_scene=nav.current_scene_node_id,
                attempted_tool="apply_module_action",
                reason="omitted from prompt",
            )
            run = SimpleNamespace(context={}, cycle_id=receipt["cycle_id"])
            service = ContextSupplementService(svc)
            result = await service.supplement(session, room, run, record, [gap])
            assert [e["id"] for e in result["entities"]] == [d["item"]]
            record.document = {**record.document, "supplement_attempted": False}
            data.module_runtime.inventory = {}
            data.module_runtime.dropped_items = {"keys:owner": "remote-scene"}
            store_state(room, data)
            assert not (await service.supplement(session, room, run, record, [gap]))["entities"]

    client.portal.call(verify)


def test_throw_clause_and_held_focus_can_match_scene_effect_without_rewriting_action():
    from app.preparation.action_authority import selected_action_matches
    from app.preparation.adjudication import matches_action_focus

    raw = "我把仍在响铃的手机扔向后方车厢壁，让它撞出声响。"
    r = SessionStateV1().module_runtime
    r.inventory = {"phone:actor": "actor"}
    r.item_instances = {"phone:actor": "phone"}
    p = SimpleNamespace(
        focus=TurnFocus(action=raw, action_target_id="phone"),
        parsed_intent=SimpleNamespace(type="use_item"),
        proposed_transition_id=None,
    )
    p.action_authority = freeze_action(
        p, raw, "actor", 1, "scene", {"phone": {"title": "手机"}}, r, {}
    )
    method = rule(encounter_operation="sound_once", scene_node_ids=["scene"])
    assert matches_action_focus(p, "scene", "scene", method)
    assert selected_action_matches(p.action_authority, raw.split("，")[0], method)
    assert not selected_action_matches(p.action_authority, "让它撞出声响。", method)
    assert not selected_action_matches(p.action_authority, raw, rule(inventory_operation="drop"))
    assert p.focus.action == raw and p.focus.action_target_id == "phone"
    p.action_authority["kinds"] = ["observe"]
    assert not matches_action_focus(p, "scene", "scene", method)
    assert not selected_action_matches(p.action_authority, raw, method)


def test_selected_observation_cannot_borrow_throw_capability_from_another_clause():
    from app.preparation.action_authority import selected_action_matches

    a = {"action": "我观察手机，然后扔出手机。", "kinds": ["observe", "throw"]}
    method = rule(encounter_operation="sound_once")
    assert not selected_action_matches(a, "我观察手机", method)
    assert selected_action_matches(a, "然后扔出手机。", method)
