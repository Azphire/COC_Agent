"""Isolated mechanics tests; injected KP output is not a user numeric approval."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_batch16 import module_battle, move  # noqa: F401
from test_module_navigation import structure_data  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import headers, lobby, ok  # noqa: F401

from app.agents.adjudication_schemas import AdjudicationRecord, KeeperPlan, PlayerIntent, TurnFocus
from app.persistence.adjudication_models import ActionPlanRecord
from app.persistence.agent_models import AgentCycle
from app.preparation.encounters import apply_encounter, can_locate, freeze_combat_basis
from app.preparation.inventory import apply_inventory, held_instance, public_inventory
from app.preparation.runtime import apply_interaction
from app.preparation.runtime_schemas import ModuleActionArgs, ModuleInteraction
from app.rooms.combat_schemas import Combatant, CombatTemplate
from app.rooms.combat_service import load_state, store_state
from app.rooms.schemas import SessionStateV1
from app.rooms.service import RoomError


def rule(**overrides):
    return ModuleInteraction(
        id="test",
        instruction="isolated fixture",
        public_result="applied",
        source_block_ids=["fixture"],
        **overrides,
    )


def test_operation_specific_npc_values_and_no_implicit_weapon():
    original = CombatTemplate(source="manuscript", attributes={"str": 55})
    assert original.missing("treatment") == ["hp", "hp_max", "con"]
    supplement = CombatTemplate(
        source="UNAPPROVED TEST", attributes={"str": 55, "con": 50}, hp=3, hp_max=11
    )
    assert not supplement.missing("treatment")
    assert "weapons" in supplement.missing("attack")
    p = Combatant(
        id="staff",
        label="staff",
        scene_id="here",
        **supplement.model_dump(exclude={"count", "limitations"}),
    )
    assert p.weapons == [] and p.armor is None and p.damage_bonus is None
    assert p.missing("damage") == ["armor"]


def test_treatment_context_reveals_only_addressed_source_visible_ungated_npc(client, module_battle):  # noqa: F811
    from app.agents.combat_runtime import reveal_addressed_npc

    svc, d = client.app.state.agent_service, module_battle

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            npc = await svc.entities.entity(session, room.id, d["guard"])
            scene = SimpleNamespace(
                state={"scene_id": "s"},
                document={
                    "scenes": [
                        {
                            "id": "s",
                            "public_description": "一位受伤的" + npc.snapshot["title"] + "在这里。",
                        }
                    ]
                },
            )
            npc.state = "hidden"
            await reveal_addressed_npc(svc, session, room, "我治疗另一人。", scene)
            assert npc.state == "hidden"
            raw = "我为" + npc.snapshot["title"] + "包扎急救。"
            pre = npc.snapshot["reveal_conditions"]
            npc.snapshot = {
                **npc.snapshot,
                "reveal_conditions": {**pre, "access_policy": "host_review"},
            }
            await reveal_addressed_npc(svc, session, room, raw, scene)
            assert npc.state == "hidden"
            npc.snapshot = {
                **npc.snapshot,
                "reveal_conditions": {**pre, "access_policy": "automatic"},
            }
            await reveal_addressed_npc(svc, session, room, raw, scene)
            assert npc.state == "revealed"
            assert load_state(room).combat.participants[f"module:{d['guard']}:1"].public

    client.portal.call(verify)


def test_method_selection_cannot_turn_reading_or_moving_into_initial_belongings():
    from app.preparation.adjudication import matches_action_focus

    intent = SimpleNamespace(type="investigate", target_id="note")
    plan = SimpleNamespace(
        focus=TurnFocus(action="借着手机灯读便签", action_target_id="note"),
        parsed_intent=intent,
        proposed_transition_id=None,
        proposed_check=None,
    )
    initial = rule(inventory_operation="initial", item_id="phone")
    assert not matches_action_focus(plan, "scene", "scene", initial)
    assert not matches_action_focus(plan, "scene", "phone", rule(set_flags={"phone_light": True}))
    assert matches_action_focus(plan, "scene", "note", rule(reveal_entity_ids=["back"]))
    plan.focus.action_target_id = "phone"
    assert matches_action_focus(plan, "scene", "scene", initial)
    plan.proposed_transition_id = "forward"
    # A stale optional exit is not a movement intent and cannot suppress the
    # actual, explicitly focused item operation.
    assert matches_action_focus(plan, "scene", "phone", rule(set_flags={"phone_light": True}))
    plan.parsed_intent.type = "move"
    assert not matches_action_focus(plan, "scene", "phone", rule(set_flags={"phone_light": True}))


def test_current_clause_ids_authorize_gift_without_requoting_but_not_history_alone():
    from app.preparation.adjudication import PreparedDecision, action_evidence

    current, history = "我将两把钥匙交到沈砚手里。", "你收起黑色包和两把钥匙。"
    clauses = [{"id": "u1", "text": current}]
    decision = PreparedDecision(action_clause_ids=["u1"], evidence_quotes=[history])
    assert action_evidence(decision, clauses, current, [current, history]) == (
        current,
        [current, history],
    )
    decision.action_clause_ids = []
    assert action_evidence(decision, clauses, current, [current, history]) is None
    decision.action_clause_ids, decision.evidence_quotes = ["u1"], ["虚构的历史"]
    assert action_evidence(decision, clauses, current, [current, history]) is None
    question = "你要钥匙吗？"
    decision.evidence_quotes = [history]
    assert (
        action_evidence(decision, [{"id": "u1", "text": question}], question, [question, history])
        is None
    )


async def test_action_authority_pass_cannot_use_past_context_to_turn_light_into_throw(monkeypatch):
    from app.preparation import adjudication

    async def reject(runtime, state, schema, instruction, context, node):
        assert set(context) == {
            "current_action",
            "proposed_operation",
            "operation_description",
            "selected_item",
        }
        assert context["current_action"] == "我用手机灯看清身影。"
        assert context["proposed_operation"] == "sound_once"
        return adjudication.CurrentActionMatch(matches=False, reason="观察并未授权投掷")

    monkeypatch.setattr(adjudication, "call_model", reject)
    assert (
        await adjudication.verify_current_action(
            None, {}, "我用手机灯看清身影。", rule(encounter_operation="sound_once")
        )
        is None
    )


def test_grab_counts_actual_contacts_and_single_enemy_cannot_hold_two_people():
    state = SessionStateV1()
    state.module_runtime.npc_counts["clicker"] = 3
    state.module_runtime.npc_locations["clicker"] = "scene"
    grab = rule(encounter_operation="grab", npc_id="clicker")
    args = ModuleActionArgs(
        entity_id="clicker",
        interaction_id="test",
        evidence_quote="run",
        npc_instance_id="module:clicker:1",
    )
    apply_encounter(state, grab, args, "human", "scene", 1)
    assert state.module_runtime.grapples["human"] == ["module:clicker:1"]
    with pytest.raises(RoomError):
        apply_encounter(state, grab, args, "other", "scene", 2)
    apply_encounter(state, rule(encounter_operation="release"), args, "human", "scene", 3)
    assert not state.module_runtime.grapples  # Three generated enemies are not three grips.
    apply_encounter(state, grab, args, "human", "scene", 4)
    args.npc_instance_id = "module:clicker:2"
    apply_encounter(state, grab, args, "human", "scene", 5)
    with pytest.raises(RoomError):
        apply_encounter(state, rule(encounter_operation="release"), args, "human", "scene", 6)
    state.combat.participants["module:clicker:2"] = Combatant(
        id="module:clicker:2",
        label="fallen",
        scene_id="scene",
        hp=0,
        hp_max=14,
        attributes={},
        skills={},
        weapons=[],
        source="explicit incapacitation fixture",
        injury={"unconscious": True},
    )
    with pytest.raises(RoomError, match="不能抓握"):
        apply_encounter(state, grab, args, "human", "scene", 7)
    store_state(SimpleNamespace(session_state={}), state)
    assert state.module_runtime.grapples["human"] == ["module:clicker:1"]


def test_blind_sound_tracking_ignores_light_and_respects_actual_contact():
    state = SessionStateV1()
    actor = SimpleNamespace(id="c1", traits=["blind_sound_tracking"], scene_node_id="scene")
    target = SimpleNamespace(id="human")
    state.module_runtime.flags["torch_light"] = True
    assert not can_locate(state, actor, target)
    state.module_runtime.sounds["human"] = {
        "actor_id": "human",
        "active": True,
        "scene_node_id": "scene",
    }
    assert can_locate(state, actor, target)
    state.module_runtime.sounds["human"]["active"] = False
    assert not can_locate(state, actor, target)
    state.module_runtime.grapples["human"] = ["c1"]
    assert can_locate(state, actor, target)


def test_continuous_lure_requires_real_placed_local_source():
    state = SessionStateV1()
    args = ModuleActionArgs(entity_id="passage", interaction_id="test", evidence_quote="pass")
    method = rule(encounter_operation="continuous_lure")
    state.module_runtime.flags["continuous_sound"] = True
    with pytest.raises(RoomError):
        apply_encounter(state, method, args, "human", "scene", 1)
    state.module_runtime.sounds["phone"] = {
        "active": True,
        "continuous": True,
        "actor_id": "human",
        "scene_node_id": "scene",
    }
    with pytest.raises(RoomError):
        apply_encounter(state, method, args, "human", "scene", 2)
    state.module_runtime.sounds["phone"]["actor_id"] = None
    state.module_runtime.dropped_items["phone"] = "other"
    with pytest.raises(RoomError):
        apply_encounter(state, method, args, "human", "scene", 3)
    state.module_runtime.dropped_items["phone"] = "scene"
    apply_encounter(state, method, args, "human", "scene", 4)


@pytest.fixture
def interactions(client, module_battle, monkeypatch):  # noqa: F811
    d, svc = module_battle, client.app.state.agent_service
    monkeypatch.setattr(svc.runtime, "schedule", lambda *_: None)
    for member, role in [(d["room"]["host_member_id"], "keeper"), (d["agent"], "investigator")]:
        profile = ok(client.post("/api/agent-profiles", json={"role": role, "name": role}), 201)
        ok(
            client.post(
                d["prefix"] + "/agent-bindings",
                json={"member_id": member, "profile_id": profile["id"]},
            )
        )
    ok(
        client.post(
            d["prefix"] + "/ready",
            json={"ready": True},
            headers=headers(d["remote"]["member_token"]),
        )
    )
    ok(client.post(d["prefix"] + "/ready", json={"ready": True, "member_id": d["agent"]}))
    ok(client.post(d["prefix"] + "/start"))
    ok(client.post(d["prefix"] + f"/entities/{d['item']}/reveal"))

    async def configure():
        async with svc.rooms.transaction() as session:
            entity = await svc.entities.entity(session, d["room"]["id"], d["item"])
            entity.snapshot = {
                **entity.snapshot,
                "interactions": [
                    rule(inventory_operation=op, item_id=d["item"], kp_enabled=True)
                    .model_copy(update={"id": op})
                    .model_dump()
                    for op in ("give", "drop", "pickup", "consume")
                ]
                + [
                    rule(kp_enabled=True, acquire_item_ids=[d["item"]])
                    .model_copy(update={"id": "take"})
                    .model_dump(),
                    rule(kp_enabled=True, required_item_ids=[d["item"]], set_flags={"used": True})
                    .model_copy(update={"id": "use"})
                    .model_dump(),
                ],
            }

    client.portal.call(configure)

    async def action(op, actor, quote, recipient=None, *, reuse=None, actual=True, verified=True):
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            nav = await svc.navigation.state(session, room.id)
            slot = next(s for s in await svc.rooms.slots(session, room) if s.member_id == actor)
            if reuse:
                cycle = await session.get(AgentCycle, reuse["cycle_id"])
                seq = reuse["source_event_seq"]
            else:
                event = svc.rooms.append(
                    session,
                    room,
                    "agent.action_proposed" if actor == d["agent"] else "action.submitted",
                    actor,
                    {"text": quote},
                )
                seq, cid = event.seq, str(uuid4())
                cycle = AgentCycle(
                    id=cid,
                    room_id=room.id,
                    status="completed",
                    state={
                        "triggering_event_seq": seq,
                        "current_node": "completed",
                        "triggering_member_id": actor,
                        "origin": "teammate" if actor == d["agent"] else "human",
                    },
                )
                session.add(cycle)
                plan = KeeperPlan(
                    plan_id=cid,
                    cycle_id=cid,
                    current_scene_id=nav.current_scene_node_id,
                    parsed_intent=PlayerIntent(
                        type="interact",
                        actor_member_id=actor,
                        actor_character_slot_id=slot.id,
                        target_id=d["item"],
                        target_kind="item",
                        evidence_quote=quote,
                        confidence=1,
                    ),
                    focus=TurnFocus(action=quote if actual else "", action_target_id=d["item"]),
                )
                session.add(
                    ActionPlanRecord(
                        cycle_id=cid,
                        room_id=room.id,
                        run_id="fixture",
                        document=AdjudicationRecord(plan=plan).model_dump(mode="json"),
                    )
                )
                data = load_state(room)
                if actual:
                    data.module_runtime.rulings[f"{seq}:{d['item']}:{op}"] = {
                        "action": quote,
                        "actor_member_id": actor,
                        "authority": "isolated_fixture_only",
                        "current_action_verification": {"matches": verified, "action_quote": quote},
                    }
                    store_state(room, data)
                await session.flush()
            receipt = await apply_interaction(
                svc,
                session,
                room,
                ModuleActionArgs(
                    entity_id=d["item"],
                    interaction_id=op,
                    evidence_quote=quote,
                    recipient_member_id=recipient,
                ),
                run=SimpleNamespace(
                    id="fixture", profile_id="fixture", cycle_id=cycle.id, context={}
                ),
            )
            return {**receipt, "cycle_id": cycle.id}

    return d, action


def test_real_actor_transfer_use_drop_pickup_consumption_permissions(client, interactions):
    d, action = interactions
    with pytest.raises(RoomError, match="当前动作未授权"):
        client.portal.call(lambda: action("take", d["player"], "我取下钥匙。", verified=False))
    first = client.portal.call(action, "take", d["player"], "我取下钥匙。")
    with pytest.raises(RoomError):
        client.portal.call(action, "give", d["agent"], "我把钥匙递给你。", d["player"])
    with pytest.raises(RoomError):
        client.portal.call(
            lambda: action("give", d["player"], "你要钥匙吗？", d["agent"], actual=False)
        )
    passed = client.portal.call(action, "give", d["player"], "我将钥匙交到同伴手中。", d["agent"])
    assert passed["inventory"][d["item"]] == d["agent"]
    assert not passed["host_confirmed"] and passed["check_ids"] == []
    used = client.portal.call(action, "use", d["agent"], "我用刚收到的钥匙开门。")
    assert used["actor_member_id"] == d["agent"]
    with pytest.raises(RoomError):
        client.portal.call(action, "use", d["player"], "我使用队友的钥匙。")
    same = client.portal.call(
        lambda: action("give", d["player"], "我将钥匙交到同伴手中。", d["agent"], reuse=passed)
    )
    assert same == passed
    dropped = client.portal.call(action, "drop", d["agent"], "我放下钥匙。")
    assert not dropped["inventory"]
    picked = client.portal.call(action, "pickup", d["player"], "我拾起地上的钥匙。")
    assert picked["inventory"][d["item"]] == first["actor_member_id"]
    client.portal.call(action, "consume", d["player"], "我消耗已配置的一次性测试物。")
    with pytest.raises(RoomError):
        client.portal.call(action, "take", d["player"], "我重新取用耗尽的物品。")


def test_partial_npc_treatment_and_frozen_attack_defense_basis(client, module_battle):  # noqa: F811
    svc, d = client.app.state.agent_service, module_battle

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            state = load_state(room)
            guard = await svc.entities.entity(session, room.id, d["guard"])
            guard.snapshot = {
                **guard.snapshot,
                "check_adjustments": [
                    {
                        "id": "dark",
                        "kind": "skill",
                        "name": "*",
                        "combat_only": True,
                        "denominator": 2,
                        "add": 10,
                        "source_block_ids": ["fixture"],
                        "basis": "Isolated source correction",
                    }
                ],
            }
            participant = state.combat.participants[d["player"]]
            attack, defense = (
                svc.combat.roll_basis(participant, "brawl"),
                svc.combat.roll_basis(participant, "dodge"),
            )
            original = (attack["value"], defense["value"])
            for basis in (attack, defense):
                await freeze_combat_basis(svc, session, room, state, participant, basis)
            assert (attack["value"], defense["value"]) == tuple(v // 2 + 10 for v in original)
            assert attack["module_adjustment"]["rule"]["source_block_ids"] == ["fixture"]

    client.portal.call(verify)


def test_initial_item_copies_reuse_each_actor_identity(client, module_battle):  # noqa: F811
    svc, d = client.app.state.agent_service, module_battle

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            state = load_state(room)
            args = ModuleActionArgs(
                entity_id=d["item"], interaction_id="test", evidence_quote="I check my item"
            )
            for actor in (d["player"], d["agent"]):
                await apply_inventory(
                    svc,
                    session,
                    room,
                    state,
                    rule(inventory_operation="initial", item_id=d["item"]),
                    args,
                    actor,
                    "here",
                    1,
                    ["FIXTURE_DIE"],
                )
                assert held_instance(state.module_runtime, d["item"], actor)
            assert len(state.module_runtime.inventory) == 2
            with pytest.raises(RoomError):
                await apply_inventory(
                    svc,
                    session,
                    room,
                    state,
                    rule(inventory_operation="initial", item_id=d["item"]),
                    args,
                    d["player"],
                    "here",
                    2,
                    ["DIFFERENT_DIE"],
                )
            store_state(room, state)
            assert state.module_runtime.initial_belongings[d["player"]]["check_ids"] == [
                "FIXTURE_DIE"
            ]
            assert await public_inventory(svc, session, room) == []  # Still hidden: no DTO leak.

    client.portal.call(verify)


def test_treatment_only_npc_actual_hp_and_dice_replay(client, interactions):
    from test_batch10 import FixedRandom

    from app.dice.service import DiceService

    d, _ = interactions
    svc = client.app.state.agent_service
    pid = f"module:{d['guard']}:1"
    ok(client.post(d["prefix"] + f"/entities/{d['guard']}/reveal"))

    async def partial():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            data = load_state(room)
            npc = data.combat.participants[pid]
            npc.attributes = {"con": 50, "str": 55}
            npc.skills, npc.weapons, npc.damage_bonus, npc.armor = {}, [], None, None
            npc.hp, npc.hp_max = 3, 11
            npc.injury.last_damage_minute = 0
            store_state(room, data)

    client.portal.call(partial)
    rng = FixedRandom(1, 1)  # Explicit isolated success fixture; never model-game dice.
    svc.rooms.dice = DiceService(rng)
    command = {
        "actor_id": d["player"],
        "target_id": pid,
        "operation": "first_aid",
        "reason": "实际急救无攻击数值的伤员",
        "turn_key": 0,
        "client_request_id": str(uuid4()),
    }
    pending = ok(
        client.post(
            d["prefix"] + "/combat/action",
            json=command,
            headers=headers(d["remote"]["member_token"]),
        )
    )["room"]["combat"]["pending"]
    body = {"action_id": pending["id"], "stage": pending["stage"], "operation": "roll"}
    ok(
        client.post(
            d["prefix"] + "/combat/step", json=body, headers=headers(d["remote"]["member_token"])
        )
    )
    final = ok(client.get(d["prefix"]))["combat"]["participants"][pid]
    assert final["hp"] == 4 and final["weapons"] == []
    used = list(rng.used)
    ok(
        client.post(
            d["prefix"] + "/combat/step", json=body, headers=headers(d["remote"]["member_token"])
        )
    )
    assert ok(client.get(d["prefix"]))["combat"]["participants"][pid]["hp"] == 4
    assert used == rng.used


def test_transfer_and_dropped_location_survive_snapshot(client, interactions):
    d, action = interactions
    client.portal.call(action, "take", d["player"], "我拿起钥匙。")
    client.portal.call(action, "give", d["player"], "我把钥匙交给同伴。", d["agent"])
    ok(client.post(d["prefix"] + "/pause"))
    saved = ok(client.post(d["prefix"] + "/snapshots", json={"name": "transfer fixture"}))[
        "snapshot"
    ]
    client.portal.call(action, "drop", d["agent"], "我把钥匙放在地上。")
    ok(client.post(d["prefix"] + f"/snapshots/{saved['id']}/load"))
    runtime = ok(client.get(d["prefix"]))["session_state"]["module_runtime"]
    assert runtime["inventory"][d["item"]] == d["agent"] and not runtime["dropped_items"]


def test_loud_impact_returns_to_continuous_sound_without_copying_thrown_item():
    from app.preparation.encounters import end_transient_sound

    state = SessionStateV1()
    npc = Combatant(
        id="c1",
        label="clicker",
        scene_id="here",
        scene_node_id="here",
        hp=14,
        hp_max=14,
        attributes={"con": 70, "dex": 40},
        skills={"brawl": 60},
        source="isolated fixture",
        traits=["blind_sound_tracking"],
    )
    state.combat.participants[npc.id] = npc
    state.module_runtime.inventory = {"phone": "human", "torch": "other"}
    args = ModuleActionArgs(entity_id="phone", interaction_id="test", evidence_quote="ring")
    apply_encounter(
        state,
        rule(encounter_operation="sound_start", sound_item_id="phone"),
        args,
        "human",
        "here",
        1,
    )
    assert can_locate(state, npc, SimpleNamespace(id="human"))
    args.used_item_id = "torch"
    apply_encounter(state, rule(encounter_operation="sound_once"), args, "other", "here", 2)
    assert not can_locate(state, npc, SimpleNamespace(id="human"))
    assert not can_locate(state, npc, SimpleNamespace(id="other"))
    assert state.module_runtime.dropped_items == {"torch": "here"}
    with pytest.raises(RoomError):
        apply_encounter(state, rule(encounter_operation="sound_once"), args, "other", "here", 3)
    end_transient_sound(state, "here")
    assert can_locate(state, npc, SimpleNamespace(id="human"))


def test_kp_sanity_condition_selects_fixed_party_effect_without_numeric_approval(
    client, interactions, monkeypatch
):
    from app.preparation import sanity_adjudication

    d, _ = interactions
    svc = client.app.state.agent_service
    cid = str(uuid4())

    async def prepare():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            entity = await svc.entities.entity(session, room.id, d["item"])
            effect = dict(entity.snapshot["sanity_effects"][0])
            effect.update(kp_enabled=True, audience="party")
            entity.snapshot = {**entity.snapshot, "sanity_effects": [effect]}
            event = svc.rooms.append(
                session, room, "action.submitted", d["player"], {"text": "我看清了这幅景象。"}
            )
            slot = next(
                s for s in await svc.rooms.slots(session, room) if s.member_id == d["player"]
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

    async def fake(*args, **kwargs):
        return sanity_adjudication.SanitySituation(
            applies="yes",
            evidence_quotes=["我看清了这幅景象。"],
            reason="Explicit fixture, not a real model test",
        )

    monkeypatch.setattr(sanity_adjudication, "call_model", fake)
    state = client.portal.call(prepare)
    result = client.portal.call(sanity_adjudication.resolve_sanity_conditions, svc.runtime, state)
    item = result["encounter_queue"][0]
    assert item["status"] == "approved" and item["origin"] == "kp_ruling"
    assert not item["numeric_approval"] and set(item["target_member_ids"]) == {
        d["player"],
        d["agent"],
    }


def test_c_ending_requires_actual_loss_and_replays_without_extra_san(client, interactions):
    d, action = interactions
    svc = client.app.state.agent_service

    async def configure(dead=False):
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            entity = await svc.entities.entity(session, room.id, d["item"])
            entity.snapshot = {
                **entity.snapshot,
                "interactions": [
                    rule(
                        kp_enabled=True, outcome="C", requires_party_loss=True, san_zero=True
                    ).model_dump()
                ],
            }
            if dead:
                data = load_state(room)
                for character in data.characters.values():
                    character.hp = 0
                    character.injury.dead = True
                store_state(room, data)

    client.portal.call(configure)
    with pytest.raises(RoomError, match="结局需要实际"):
        client.portal.call(action, "test", d["player"], "我感到死亡的威胁。")
    client.portal.call(configure, True)  # Explicit terminal-state fixture, not a model route.
    receipt = client.portal.call(action, "test", d["player"], "我面对已经发生的终幕。")
    before = ok(client.get(d["prefix"]))["session_state"]
    assert before["module_runtime"]["outcome"] == "C"
    assert all(c["san"] == 0 for c in before["characters"].values())
    again = client.portal.call(
        lambda: action("test", d["player"], "我面对已经发生的终幕。", reuse=receipt)
    )
    assert again == receipt
    assert ok(client.get(d["prefix"]))["session_state"]["characters"] == before["characters"]


def test_recover_only_declared_lost_item_and_keep_initial_die(client, module_battle):  # noqa: F811
    svc, d = client.app.state.agent_service, module_battle

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            state = load_state(room)
            args = ModuleActionArgs(
                entity_id=d["item"], interaction_id="test", evidence_quote="search"
            )
            initial = rule(inventory_operation="initial", item_id=d["item"], check_passed=False)
            await apply_inventory(
                svc,
                session,
                room,
                state,
                initial,
                args,
                d["player"],
                "start",
                1,
                ["original-failure"],
            )
            recover = rule(inventory_operation="recover", item_id=d["item"])
            with pytest.raises(RoomError):
                await apply_inventory(
                    svc,
                    session,
                    room,
                    state,
                    recover,
                    args,
                    d["agent"],
                    "third",
                    2,
                    ["recovery-success"],
                )
            await apply_inventory(
                svc,
                session,
                room,
                state,
                recover,
                args,
                d["player"],
                "third",
                2,
                ["recovery-success"],
            )
            assert state.module_runtime.initial_belongings[d["player"]]["check_ids"] == [
                "original-failure"
            ]
            assert len(state.module_runtime.inventory) == 1
            with pytest.raises(RoomError):
                await apply_inventory(
                    svc, session, room, state, recover, args, d["player"], "third", 3, ["different"]
                )

    client.portal.call(verify)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我推开前方的门，走进5号车厢。", True),
        ("我们是否应该走进5号车厢？", False),
        ("如果安全，我走进5号车厢。", False),
        ("我走进7号车厢。", False),
    ],
)
def test_selected_exit_repairs_missing_focus_only_with_actual_matching_move(text, expected):
    from app.agents.generation_contracts import generation_contract, restore_output

    context = {
        "action_identifiers": dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="member",
            actor_character_slot_id="slot",
            current_scene_id="s",
            expected_navigation_revision=0,
        ),
        "triggering_action": {"seq": 1, "payload": {"text": text}},
        "approved_exits": [
            {
                "transition_id": "forward",
                "target_scene_node_id": "five",
                "target_public_title": "5号车厢",
            }
        ],
    }
    schema = generation_contract(KeeperPlan, context)
    plan = schema(
        parsed_intent={"type": "unknown"},
        focus={},
        proposed_transition_id="forward",
        proposed_tool_calls=[
            {"name": "apply_module_action", "arguments": {"interaction_id": "unrelated"}}
        ],
    )
    plan = plan.model_copy(update={"focus": None})  # Legacy output remains restorable.
    restored = restore_output(plan, KeeperPlan, context)
    assert (restored.parsed_intent.type == "move") == expected
    if expected:
        assert restored.focus.action == text and restored.focus.action_target_id == "five"
        assert not restored.proposed_tool_calls


def test_approaching_current_npc_never_moves_party_to_an_exit():
    from app.agents.action_policy import ActionFacts, ActionPolicyValidator
    from app.agents.generation_contracts import generation_contract, restore_output

    text = "我走到受伤的乘务员身边，蹲下查看他的伤势。"
    context = {
        "action_identifiers": dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="member",
            actor_character_slot_id="slot",
            current_scene_id="s",
            expected_navigation_revision=0,
        ),
        "triggering_action": {"seq": 1, "payload": {"text": text}},
        "current_targets": [{"id": "staff", "title": "乘务员", "type": "npc"}],
        "approved_exits": [
            {
                "transition_id": "back",
                "target_scene_node_id": "five",
                "target_public_title": "5号车厢",
            }
        ],
    }
    schema = generation_contract(KeeperPlan, context)
    plan = schema(parsed_intent={"type": "move"}, focus={}, proposed_transition_id="back")
    plan = plan.model_copy(update={"focus": None})  # Legacy nullable focus.
    restored = restore_output(plan, KeeperPlan, context)
    assert restored.parsed_intent.type == "interact" and restored.proposed_transition_id is None
    assert restored.focus.action_target_id == "staff" and restored.proposed_reveal_entity_ids == [
        "staff"
    ]
    facts = ActionFacts(
        room_id="r",
        cycle_id="p",
        raw_text=text,
        actor_member_id="member",
        actor_slot_id="slot",
        actor_authorized=True,
        scene_id="s",
        local_entity_ids={"staff"},
        approved_entities={"staff": {"id": "staff", "title": "乘务员", "type": "npc"}},
    )
    forged = PlayerIntent(
        type="move",
        actor_member_id="member",
        actor_character_slot_id="slot",
        target_id="five",
        evidence_quote=text,
        confidence=1,
    )
    assert "不构成跨场景" in ActionPolicyValidator().validate_intent(forged, restored, facts)[1]


@pytest.mark.parametrize("selected_clauses", [["u1"], []])
def test_move_restores_return_clause_after_model_focus_fragment_selection(selected_clauses):
    from app.agents.generation_contracts import generation_contract, restore_output

    text = "我打开后方通往4号车厢的门，回到4号车厢。"
    context = {
        "action_identifiers": dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="member",
            actor_character_slot_id="slot",
            current_scene_id="three",
            expected_navigation_revision=0,
        ),
        "triggering_action": {"seq": 1, "payload": {"text": text}},
        "approved_exits": [
            {
                "transition_id": "back",
                "target_scene_node_id": "four",
                "target_public_title": "4号车厢",
            }
        ],
    }
    schema = generation_contract(KeeperPlan, context)
    plan = schema(
        parsed_intent={"type": "move"},
        focus={"action_clause_ids": selected_clauses, "action_target_id": "four"},
        proposed_transition_id="back",
    )
    restored = restore_output(plan, KeeperPlan, context)
    assert restored.focus.action == "回到4号车厢。"
    assert restored.proposed_transition_id == "back"


def test_prepared_methods_do_not_intercept_question_or_suggestion_focus():
    from app.preparation.adjudication import matches_action_focus

    for focus in (
        None,
        TurnFocus(),
        TurnFocus(question="钥匙在哪里？"),
        TurnFocus(suggestion="请你去找钥匙。"),
    ):
        plan = SimpleNamespace(
            parsed_intent=SimpleNamespace(type="interact"),
            focus=focus,
            proposed_transition_id=None,
        )
        assert not matches_action_focus(plan, "scene", "scene", rule())


def test_sound_item_contract_excludes_teammate_item_and_unknown_npc():
    from pydantic import ValidationError

    from app.preparation.adjudication import decision_contract

    context = {
        "actor": "human",
        "members": {"human": "周岚", "ai": "沈砚"},
        "items": [{"item_id": "phone", "holder_id": "ai"}],
        "clauses": [{"id": "u1", "text": "我扔出鞋。"}],
        "npc_instances": ["module:clicker:1"],
    }
    schema = decision_contract(context, [{"option": "1"}])
    assert schema(used_item_id=None).used_item_id is None
    for args in (
        {"used_item_id": "phone"},
        {"recipient_member_id": "human"},
        {"npc_instance_id": "invented"},
    ):
        with pytest.raises(ValidationError):
            schema(**args)


async def test_actual_throw_does_not_authorize_replacing_shoe_with_owned_phone(monkeypatch):
    from app.preparation import adjudication

    async def verify(runtime, state, schema, instruction, context, node):
        assert context["current_action"] == "我将鞋扔向后墙。"
        assert context["selected_item"] == "随身手机"
        return adjudication.CurrentActionMatch(matches=False, reason="鞋不是手机")

    monkeypatch.setattr(adjudication, "call_model", verify)
    assert (
        await adjudication.verify_current_action(
            None,
            {},
            "我将鞋扔向后墙。",
            rule(encounter_operation="sound_once"),
            selected_item="随身手机",
        )
        is None
    )


def test_fact_citations_allow_quote_typography_but_not_changed_content():
    from app.preparation.adjudication import PreparedDecision, action_evidence

    action = "我揭下便签，翻看背面。"
    source = "便签写着：“只管前进吧，已经没有退路了。”"
    decision = PreparedDecision(
        action_clause_ids=["u1"], evidence_quotes=["便签写着：'只管前进吧，已经没有退路了。'"]
    )
    args = ([{"id": "u1", "text": action}], action, [action, source])
    assert action_evidence(decision, *args)
    for changed in ("便签写着：可以退回去。", "沈砚已经交出钥匙。", "HP15", "''"):
        decision.evidence_quotes = [changed]
        assert action_evidence(decision, *args) is None


def test_generation_requires_focus_for_dialogue_instead_of_silent_null():
    from pydantic import ValidationError

    from app.agents.generation_contracts import generation_contract, restore_output

    context = {
        "action_identifiers": dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="human",
            actor_character_slot_id="slot",
            current_scene_id="four",
            expected_navigation_revision=0,
        ),
        "triggering_action": {"seq": 1, "payload": {"text": "乘务员，你的钥匙在哪里？"}},
        "current_targets": [{"id": "staff", "title": "乘务员", "type": "npc"}],
    }
    schema = generation_contract(KeeperPlan, context)
    with pytest.raises(ValidationError):
        schema(parsed_intent={"type": "converse"}, focus=None)
    parsed = schema(
        parsed_intent={"type": "converse"},
        focus={
            "question_clause_ids": ["u1", "u2"],
            "addressee_id": "staff",
            "answer_basis": "facts",
        },
    )
    output = restore_output(parsed, KeeperPlan, context)
    assert output.focus.addressee_id == "staff"
    assert output.focus.question == "乘务员，你的钥匙在哪里？" and not output.focus.action


@pytest.mark.parametrize("is_action", [True, False])
def test_selected_gated_entity_requires_its_check_without_repeating_full_title(is_action):
    from app.agents.generation_contracts import generation_contract, restore_output

    actor = str(uuid4())
    text = "我寻找驾驶室钥匙。" if is_action else "驾驶室钥匙是什么样子？"
    context = {
        "action_identifiers": dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id=actor,
            actor_character_slot_id="slot",
            current_scene_id="three",
            expected_navigation_revision=0,
        ),
        "triggering_action": {"seq": 1, "payload": {"text": text}},
        "current_targets": [{"id": "keys", "title": "黑色包里的两把钥匙", "type": "item"}],
        "check_requirements": [
            {
                "entity_id": "keys",
                "title": "黑色包里的两把钥匙",
                "access_policy": "requires_check",
                "successful_check": {
                    "kind": "skill",
                    "name": "spot_hidden",
                    "difficulty": "regular",
                },
            }
        ],
    }
    schema = generation_contract(KeeperPlan, context)
    plan = schema(
        parsed_intent={"type": "investigate"},
        focus={
            "action_target_id": "keys",
            "action_clause_ids": ["u1"] if is_action else [],
            "question_clause_ids": [] if is_action else ["u1"],
        },
    )
    output = restore_output(plan, KeeperPlan, context)
    if is_action:
        assert output.proposed_check.name == "spot_hidden"
        assert output.proposed_check.clue_id == "keys"
        assert str(output.proposed_check.target_member_id) == actor
    else:
        assert output.proposed_check is None
