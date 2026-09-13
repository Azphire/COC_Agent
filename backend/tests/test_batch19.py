"""Risk-focused fixtures. These are not natural play or a long-running acceptance."""
# ruff: noqa: F811

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_batch16 import module_battle  # noqa: F401
from test_batch17 import interactions, rule  # noqa: F401
from test_module_navigation import structure_data  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.preparation.action_authority import authority_error, freeze_action, selected_action_matches
from app.preparation.current_state import bind_terminal_confirmation, current_results
from app.preparation.inventory import held_instance
from app.preparation.runtime_schemas import ItemUseEffect
from app.rooms.combat_service import load_state
from app.rooms.resource_service import advance_time, complete_resources
from app.rooms.schemas import CharacterRuntimeV1, SessionStateV1
from app.rooms.service import RoomError


def resource_state(mp=3, maximum=10):
    sid = uuid4()
    state = SessionStateV1(
        characters={
            sid: CharacterRuntimeV1(hp=12, hp_max=12, mp=mp, mp_max=maximum, mp_recovery_per_hour=1)
        }
    )
    return state, state.characters[sid], sid


def tick(state, minute, key):
    return advance_time(state, minute, key=key, source={"event": key})


def test_mp_boundary_progress_idempotency_and_snapshot():
    state, c, sid = resource_state()
    tick(state, 59, "a")
    assert (c.mp, c.mp_recovery_progress) == (3, 59)
    first = tick(state, 60, "b")
    assert (c.mp, c.mp_recovery_progress) == (4, 0)
    assert tick(state, 60, "b") == first
    tick(state, 89, "c")
    restored = SessionStateV1.model_validate_json(state.model_dump_json())
    tick(restored, 120, "d")
    assert (restored.characters[sid].mp, restored.characters[sid].mp_recovery_progress) == (5, 0)
    assert restored.characters[sid].hp == 12
    with pytest.raises(RoomError):
        tick(restored, 121, "b")


def test_old_save_derives_maximum_without_refill_or_retroactive_time():
    state, c, sid = resource_state()
    c.mp_max, c.mp_recovery_per_hour = None, 0
    state.game_minute = 700
    slots = [
        SimpleNamespace(
            id=str(sid),
            character_snapshot={
                "ruleset_id": "coc7-character-creation",
                "derived_values": {"mp": 10, "hp": 12},
                "effective_attributes": {"pow": 50},
            },
        )
    ]
    complete_resources(state, slots)
    assert (c.mp, c.mp_max, c.mp_recovery_progress) == (3, 10, 0)
    tick(state, 759, "elapsed")
    complete_resources(state, slots)
    assert (c.mp, c.mp_recovery_progress) == (3, 59)
    tick(state, 760, "boundary")
    assert c.mp == 4


def test_no_idle_credit_at_cap_or_recovery_for_dead_or_undefined_mp():
    state, c, _ = resource_state(mp=10)
    tick(state, 10000, "full")
    c.mp = 9
    tick(state, 10001, "spent")
    assert (c.mp, c.mp_recovery_progress) == (9, 1)
    tick(state, 10100, "cap")
    assert (c.mp, c.mp_recovery_progress) == (10, 0)
    c.mp, c.injury.dead = 0, True
    tick(state, 10200, "dead")
    assert c.mp == 0
    c.mp = None
    tick(state, 10400, "undefined")
    assert c.mp is None


@pytest.mark.parametrize("kind", ["dying", "con", "hourly", "bout", "combat"])
def test_time_effect_cannot_skip_obligations(kind):
    state, c, _ = resource_state()
    if kind == "dying":
        c.injury.dying = True
    elif kind == "con":
        c.injury.con_pending = "unconscious"
    elif kind == "hourly":
        c.injury.stabilized, c.injury.check_due_minute = True, 30
    elif kind == "bout":
        c.sanity.phase = "bout"
    else:
        state.combat.active = True
    before = state.model_dump()
    with pytest.raises(RoomError):
        tick(state, 60, "blocked")
    assert state.model_dump() == before


def test_unsupported_resource_patches_and_weapon_shortcuts_rejected():
    for key in ("hp", "san", "luck", "injury", "ammo"):
        with pytest.raises(ValidationError):
            ItemUseEffect.model_validate({"id": "bad", "basis": "fixture", key: 10})
    with pytest.raises(ValidationError):
        rule(item_id="item", use_effect={"id": "bad", "basis": "fixture"}, san_zero=True)


def test_same_type_instances_require_owned_explicit_identity():
    from test_action_adjudication import plan_for

    state = SessionStateV1()
    state.module_runtime.inventory = {"one": "actor", "two": "actor", "peer": "other"}
    state.module_runtime.item_instances = {i: "tonic" for i in ("one", "two", "peer")}
    assert held_instance(state.module_runtime, "tonic", "actor") is None
    assert held_instance(state.module_runtime, "tonic", "actor", "two") == "two"
    assert held_instance(state.module_runtime, "tonic", "actor", "peer") is None
    raw = "我使用补剂 two。"
    plan = plan_for(raw, "use_item", "tonic")
    from app.agents.adjudication_schemas import TurnFocus

    plan.focus = TurnFocus(action=raw, action_target_id="tonic")
    frozen = freeze_action(
        plan,
        raw,
        "actor",
        4,
        "scene",
        {"tonic": {"type": "item", "title": "补剂"}},
        state.module_runtime,
        {},
    )
    use = rule(item_id="tonic", use_effect={"id": "heal", "basis": "fixture"})
    assert not authority_error(
        frozen, use, state.module_runtime, actor="actor", seq=4, scene="scene", instance_id="two"
    )
    assert authority_error(
        frozen, use, state.module_runtime, actor="actor", seq=4, scene="scene", instance_id="one"
    )


def test_normal_interaction_cost_restore_transfer_atomic_rejection_and_save(client, interactions):  # noqa: F811
    d, action = interactions
    svc = client.app.state.agent_service
    client.portal.call(action, "take", d["player"], "我拿起钥匙。")

    async def configure():
        async with svc.rooms.transaction() as session:
            entity = await svc.entities.entity(session, d["room"]["id"], d["item"])
            entity.snapshot = {
                **entity.snapshot,
                "item_uses": 3,
                "interactions": [
                    *entity.snapshot["interactions"],
                    rule(
                        item_id=d["item"],
                        kp_enabled=True,
                        use_effect={"id": "pulse", "basis": "独立示例：消耗4MP激活", "mp_cost": 4},
                        set_flags={"pulsed": True},
                    )
                    .model_copy(update={"id": "pulse"})
                    .model_dump(),
                    rule(
                        item_id=d["item"],
                        kp_enabled=True,
                        use_effect={"id": "recover", "basis": "独立示例：恢复5MP", "mp_restore": 5},
                    )
                    .model_copy(update={"id": "recover"})
                    .model_dump(),
                ],
            }

    def state():
        return ok(client.get(d["prefix"]))["session_state"]

    client.portal.call(configure)
    first = client.portal.call(action, "pulse", d["player"], "我使用钥匙。")
    assert first["use_result"]["actor_before"] == 10
    assert first["use_result"]["actor_after"] == 6
    assert first["use_result"]["uses_after"] == 2
    assert not first["host_confirmed"] and first["check_ids"] == []
    before = state()
    client.portal.call(lambda: action("pulse", d["player"], "我使用钥匙。", reuse=first))
    assert state() == before
    restored = client.portal.call(action, "recover", d["player"], "我使用钥匙。")
    assert restored["use_result"]["actor_after"] == 10
    client.portal.call(action, "give", d["player"], "我将钥匙交给同伴。", d["agent"])
    before = state()
    with pytest.raises(RoomError):
        client.portal.call(action, "pulse", d["player"], "我使用钥匙。")
    assert state() == before
    peer = client.portal.call(action, "pulse", d["agent"], "我使用钥匙。")
    assert peer["use_result"]["actor_after"] == 6
    assert peer["use_result"]["uses_after"] == 0 and d["item"] not in peer["inventory"]
    before = state()
    with pytest.raises(RoomError):
        client.portal.call(action, "pulse", d["agent"], "我使用钥匙。")
    assert state() == before
    save = ok(client.post(d["prefix"] + "/snapshots", json={"name": "effects"}))["snapshot"]
    ok(client.post(d["prefix"] + "/pause"))
    ok(client.post(d["prefix"] + f"/snapshots/{save['id']}/load"))
    assert state() == before


@pytest.mark.parametrize(
    "raw,authorized",
    [
        ("确认结束，继续结算。", True),
        ("继续结算。", True),
        ("门现在什么状态？", False),
        ("如果确认结束会怎么样？", False),
        ("不要结束调查。", False),
    ],
)
def test_terminal_confirmation_binds_only_executed_pending_task(raw, authorized):
    from test_action_adjudication import facts_for, plan_for

    facts, plan = facts_for(raw), plan_for(raw, "converse", "ending")
    terminal = rule(kp_enabled=True, outcome="B", action_kinds=["converse"])
    origin = rule(prepare_outcome="B")
    facts.approved_entities = {
        "ending": {"type": "location", "interactions": [terminal.model_dump()]},
        "lever": {"type": "location", "interactions": [origin.model_dump()]},
    }
    facts.local_entity_ids = facts.revealed_entity_ids = {"ending", "lever"}
    state = SessionStateV1().module_runtime
    state.pending_outcome = "B"
    assert bind_terminal_confirmation(plan, facts, state) is None
    state.receipts["original"] = {
        "entity_id": "lever",
        "interaction_id": "test",
        "scene_node_id": facts.scene_id,
        "source_event_seq": 7,
    }
    binding = bind_terminal_confirmation(plan, facts, state)
    assert bool(binding) == authorized
    if binding:
        frozen = freeze_action(
            plan, raw, "actor", 9, facts.scene_id, facts.approved_entities, state, {}
        )
        frozen["terminal_confirmation"] = binding
        assert selected_action_matches(
            frozen, "继续结算。" if "继续结算。" in raw else raw, terminal
        )
        assert not authority_error(
            frozen, terminal, state, actor="actor", seq=9, scene=facts.scene_id
        )


def test_current_door_receipt_survives_chatter_and_restore():
    definition = SimpleNamespace(
        source_entity_id="door",
        snapshot={
            "interactions": [
                rule(
                    set_flags={"unlocked": True}, encounter_operation="open_door", door_id="door"
                ).model_dump()
            ]
        },
    )
    state = SessionStateV1()
    state.module_runtime.doors["door"] = True
    state.module_runtime.flags["unlocked"] = True
    state.module_runtime.receipts["open"] = {
        "entity_id": "door",
        "interaction_id": "test",
        "source_event_seq": 12,
        "text": "门已经打开。",
        "actor_member_id": "peer",
    }
    state.module_runtime.receipts["later"] = {
        "entity_id": "door",
        "interaction_id": "observe",
        "source_event_seq": 45,
        "text": "看了门。",
        "actor_member_id": "actor",
    }
    before = current_results(state.module_runtime.model_dump(), [definition])
    restored = SessionStateV1.model_validate_json(state.model_dump_json())
    assert before == current_results(restored.module_runtime.model_dump(), [definition])
    assert before == [
        {
            "entity_id": "door",
            "source_event_seq": 12,
            "text": "门已经打开。",
            "actor_member_id": "peer",
        }
    ]


@pytest.mark.parametrize("consume", [True, False])
def test_attempt_failure_consumes_only_configured_cost_and_never_restores(
    client, interactions, consume
):  # noqa: F811
    from app.preparation.item_effects import apply_use
    from app.preparation.runtime_schemas import ModuleActionArgs

    d, action = interactions
    svc = client.app.state.agent_service
    client.portal.call(action, "take", d["player"], "我拿起钥匙。")

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            entity = await svc.entities.entity(session, room.id, d["item"])
            entity.snapshot = {**entity.snapshot, "item_uses": 2}
            state = load_state(room)
            before = {sid: c.model_dump() for sid, c in state.characters.items()}
            r = rule(
                item_id=d["item"],
                use_effect={
                    "id": "attempt",
                    "basis": "failure fixture",
                    "mp_cost": 3,
                    "mp_restore": 8,
                    "consume_on_failure": consume,
                },
            )
            result = await apply_use(
                svc,
                session,
                room,
                state,
                r,
                ModuleActionArgs(
                    entity_id=d["item"], interaction_id=r.id, evidence_quote="我使用钥匙。"
                ),
                d["player"],
                False,
            )
            assert (result["actor_after"], result["uses_after"]) == ((7, 1) if consume else (10, 2))
            assert result["restore"] == 0
            for sid, c in state.characters.items():
                assert all(
                    c.model_dump()[k] == before[sid][k]
                    for k in ("hp", "san", "luck", "injury", "weapons")
                )

    client.portal.call(verify)


def test_insufficient_mp_rolls_back_item_and_resource_transaction(client, interactions):  # noqa: F811
    d, action = interactions
    svc = client.app.state.agent_service
    client.portal.call(action, "take", d["player"], "我拿起钥匙。")

    async def configure():
        async with svc.rooms.transaction() as session:
            entity = await svc.entities.entity(session, d["room"]["id"], d["item"])
            entity.snapshot = {
                **entity.snapshot,
                "item_uses": 2,
                "interactions": [
                    rule(
                        item_id=d["item"],
                        kp_enabled=True,
                        use_effect={"id": "cost", "basis": "insufficient fixture", "mp_cost": 11},
                    ).model_dump()
                ],
            }

    client.portal.call(configure)
    before = ok(client.get(d["prefix"]))["session_state"]
    with pytest.raises(RoomError, match="MP不足"):
        client.portal.call(action, "test", d["player"], "我使用钥匙。")
    assert ok(client.get(d["prefix"]))["session_state"] == before


def test_recall_prompt_carries_each_historical_fact_once_without_losing_scope():
    from app.agents.action_runtime import planning_prompt

    context = {
        "prepared_module": True,
        "triggering_action": {"seq": 900},
        "known_targets": [
            {"id": str(i), "title": "已公开线索", "fact_scope": "historical"} for i in range(20)
        ],
        "response_fact_candidates": [{"id": str(i), "kind": "fact"} for i in range(20)],
        "module": {"interaction_state": {"doors": {"cab": True}}},
    }
    result = planning_prompt(context)
    assert "known_targets" not in result
    assert len(result["response_fact_candidates"]) == 20
    assert result["response_fact_candidates"][0] == {
        "id": "0",
        "title": "已公开线索",
        "fact_scope": "historical",
        "kind": "fact",
    }
    assert result["module"]["interaction_state"]["doors"]["cab"] is True
    assert len(context["known_targets"]) == 20  # Full audit/validation data is unchanged.


def test_local_time_operation_accepts_current_scene_focus_without_overriding_scope():
    from types import SimpleNamespace

    from app.preparation.adjudication import matches_action_focus

    plan = SimpleNamespace(
        focus=SimpleNamespace(action="我坐下休息半小时。", action_target_id="current-node"),
        parsed_intent=SimpleNamespace(type="wait"),
        proposed_transition_id=None,
    )
    rest = rule(elapsed_minutes=30, action_kinds=["rest"])
    assert matches_action_focus(plan, "current-node", "local-scene-entity", rest)
    assert not matches_action_focus(
        plan, "current-node", "local-scene-entity", rest.model_copy(update={"elapsed_minutes": 0})
    )
    assert not matches_action_focus(
        plan,
        "current-node",
        "local-scene-entity",
        rest.model_copy(update={"scene_node_ids": ["elsewhere"]}),
    )
