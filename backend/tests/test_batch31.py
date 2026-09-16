"""Runtime autonomy: isolated HTTP service chains, no external model calls."""
# ruff: noqa: F811

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from test_agent_runtime import game, submit  # noqa: F401
from test_batch19 import (  # noqa: F401
    interactions,
    module_battle,
    preparation,
    rule,
    structure_data,
)
from test_batch30 import document, finalize, special_card, submit_accept
from test_combat import battle, npc, state  # noqa: F401
from test_rooms import create_room, headers, join, lobby, ok  # noqa: F401


def runtime_fixture(client, g, member=None, san=50, kind="none", phase="none"):
    """Only seed isolated runtime/save states; never replace production methods."""
    member = member or g["human"]

    async def update(session, room):
        slot = next(
            s
            for s in await client.app.state.room_service.slots(session, room)
            if s.member_id == member
        )
        data = deepcopy(room.session_state)
        c = data["characters"][slot.id]
        c["san"] = san
        c["sanity"].update(kind=kind, phase=phase)
        room.session_state = data
        return slot.id

    return client.portal.call(client.app.state.agent_service.mutate, g["room"]["id"], update)


def action_body(client, g, operation="attack", actor=None, target="guard", **extra):
    return dict(
        actor_id=actor or g["human"],
        target_id=target,
        operation=operation,
        weapon_id="unarmed",
        reason="隔离自主资格验收",
        client_request_id=str(uuid4()),
        turn_key=state(client, g)["turn_key"],
        **extra,
    )


@pytest.mark.parametrize("path", ["publish", "accept"])
def test_initial_zero_normal_http_chain(client, monkeypatch, path):
    card = special_card(client, "occultist", value=99, approve=True)
    assert card["validation"]["valid"]
    assert (card["derived_values"]["san"], card["derived_values"]["san_max"]) == (0, 0)
    frozen = ok(finalize(client, card))
    exported = document(client, card)
    assert document(client, card)["character"] == exported["character"]
    if path == "accept":
        g = submit_accept(client, card)
    else:
        created = create_room(client)
        player = join(client, created["invite_code"])
        p = f"/api/rooms/{created['room']['id']}"
        room = ok(client.post(p + "/character-slots", json={"character_id": card["id"]}))["room"]
        slot = room["character_slots"][0]["id"]
        room = ok(
            client.post(
                p + "/character-assignments",
                json={"slot_id": slot, "member_id": player["room"]["self_member_id"]},
            )
        )["room"]
        g = dict(prefix=p, remote=player, room=room, slots=[slot])
    p = g["prefix"]
    human = g["remote"]["room"]["self_member_id"]
    g["human"] = human
    ok(client.post(p + "/module", json={"module_id": "stopped-clock"}))
    profile = ok(client.post("/api/agent-profiles", json={"role": "keeper", "name": "隔离KP"}), 201)
    ok(
        client.post(
            p + "/agent-bindings",
            json={"member_id": g["room"]["host_member_id"], "profile_id": profile["id"]},
        )
    )
    ok(
        client.post(
            p + "/ready", headers=headers(g["remote"]["member_token"]), json={"ready": True}
        )
    )
    room = ok(client.post(p + "/start"))["room"]
    monkeypatch.setattr(client.app.state.agent_service.runtime, "schedule", lambda *_: None)
    c = room["session_state"]["characters"][g["slots"][0]]
    ok(
        client.post(
            p + "/combat/setup",
            json={
                "member_id": human,
                "expected_revision": room["revision"],
                "reason": "隔离战斗入口",
            },
        )
    )
    room = ok(client.get(p))
    ok(
        client.post(
            p + "/combat/setup",
            json={
                "npc": npc().model_dump(mode="json"),
                "expected_revision": room["revision"],
                "reason": "隔离守卫",
            },
        )
    )
    ordinary = submit(client, g)
    combat = client.post(
        p + "/combat/action",
        json=action_body(client, g),
        headers=headers(g["remote"]["member_token"]),
    )
    print(
        {
            "path": path,
            "san": c["san"],
            "kind": c["sanity"]["kind"],
            "phase": c["sanity"]["phase"],
            "ordinary_http": ordinary.status_code,
            "combat_http": combat.status_code,
            "combat": combat.json().get("detail"),
        }
    )
    assert (c["sanity"]["kind"], c["sanity"]["phase"]) == ("permanent", "bout")
    assert ordinary.status_code == combat.status_code == 409
    assert c["sanity"]["mythos_gain"] == c["sanity"]["day_loss"] == 0
    assert (
        ok(client.get(f"/api/characters/{card['id']}"))["derived_values"]
        == frozen["derived_values"]
    )


@pytest.mark.parametrize(
    "san,kind,phase",
    [
        (0, "none", "none"),
        (50, "permanent", "bout"),
        (50, "temporary", "awaiting_symptom"),
        (50, "temporary", "bout"),
        (50, "indefinite", "awaiting_symptom"),
        (50, "indefinite", "bout"),
    ],
)
def test_restricted_ordinary_and_direct_api_no_effects(client, battle, san, kind, phase):
    g = battle
    runtime_fixture(client, g, san=san, kind=kind, phase=phase)
    before = ok(client.get(g["prefix"]))
    ordinary = submit(client, g)
    response = client.post(
        g["prefix"] + "/combat/action",
        json=action_body(client, g),
        headers=headers(g["remote"]["member_token"]),
    )
    print(
        dict(
            san=san,
            kind=kind,
            phase=phase,
            ordinary=ordinary.status_code,
            combat=response.status_code,
            detail=response.json().get("detail"),
        )
    )
    assert ordinary.status_code == response.status_code == 409
    assert "自主" in response.json()["detail"]["message"]
    after = ok(client.get(g["prefix"]))
    assert after["revision"] == before["revision"]
    assert after["session_state"] == before["session_state"]
    assert not ok(client.get(g["prefix"] + "/agent-runs"))
    assert not client.app.state.agent_service.model.calls


@pytest.mark.parametrize("operation", ["pass", "reload", "first_aid", "medicine", "end"])
def test_all_new_combat_operations_require_autonomy(client, battle, operation):
    from test_combat import configure_guard

    from app.rooms.combat_schemas import Weapon

    g = battle
    configure_guard(client, g, hp=10, injury={"last_damage_minute": 0})
    room = ok(client.get(g["prefix"]))
    pistol = Weapon(
        id="pistol",
        name="隔离手枪",
        kind="firearm",
        skill="handgun",
        damage="1d6",
        ammo=1,
        capacity=6,
        reserve=4,
        source="合法装填夹具",
    )
    ok(
        client.post(
            g["prefix"] + "/combat/setup",
            json=dict(
                member_id=g["human"],
                expected_revision=room["revision"],
                weapons=[pistol.model_dump(mode="json")],
                reason="隔离配装",
            ),
        )
    )
    runtime_fixture(client, g, san=0)
    before = ok(client.get(g["prefix"]))
    body = action_body(client, g, operation)
    body["weapon_id"] = "pistol"
    response = client.post(
        g["prefix"] + "/combat/action",
        json=body,
        headers=headers(g["remote"]["member_token"]),
    )
    assert response.status_code == 409 and "自主" in response.json()["detail"]["message"]
    after = ok(client.get(g["prefix"]))
    assert (after["revision"], after["session_state"]) == (
        before["revision"],
        before["session_state"],
    )


@pytest.mark.parametrize(
    "san,kind,phase",
    [
        (None, "none", "none"),
        (50, "none", "none"),
        (50, "temporary", "underlying"),
        (50, "indefinite", "underlying"),
    ],
)
def test_healthy_and_underlying_keep_ordinary_and_combat(client, battle, san, kind, phase):
    g = battle
    runtime_fixture(client, g, san=san, kind=kind, phase=phase)
    ok(
        client.post(
            g["prefix"] + "/combat/action",
            json=action_body(client, g, "pass"),
            headers=headers(g["remote"]["member_token"]),
        )
    )
    ok(client.post(g["prefix"] + "/agent-cycle/cancel"))
    ok(submit(client, g))


def test_ai_queue_skips_zero_runtime(client, battle):
    g = battle
    runtime_fixture(client, g, member=g["agent"], san=0)

    async def queue(session, room):
        from app.rooms.combat_service import load_state, store_state

        data = load_state(room)
        data.combat.active = True
        data.combat.order = [g["agent"], g["human"], "guard"]
        data.combat.index = 0
        store_state(room, data)
        await client.app.state.agent_service.combat.queue_automatic(session, room)
        cycle = await client.app.state.agent_service.cycle(session, room.id, active=True)
        return cycle.state if cycle else None

    cycle = client.portal.call(client.app.state.agent_service.mutate, g["room"]["id"], queue)
    assert cycle is None
    assert state(client, g)["current_actor_id"] == g["human"]


def test_runtime_zero_load_is_idempotent_and_positive_does_not_cure():
    from app.rooms.schemas import SessionStateV1

    slot = uuid4()
    raw = dict(characters={str(slot): dict(san=0, sanity=dict(day_loss=7, mythos_gain=2))})
    parsed = SessionStateV1.model_validate(raw)
    current = parsed.characters[slot]
    assert current.sanity.kind == "permanent"
    dumped = parsed.model_dump(mode="json")
    assert SessionStateV1.model_validate(dumped).model_dump(mode="json") == dumped
    current.san = 25
    restored = SessionStateV1.model_validate(parsed.model_dump(mode="json"))
    assert restored.characters[UUID(str(slot))].sanity.kind == "permanent"
    assert (current.sanity.day_loss, current.sanity.mythos_gain) == (7, 2)


def isolate_human_turn(client, g):
    async def update(session, room):
        from app.rooms.combat_service import load_state, store_state

        data = load_state(room)
        data.combat.participants[g["agent"]].scene_id = "elsewhere"
        data.luck_spending = True
        store_state(room, data)

    client.portal.call(client.app.state.agent_service.mutate, g["room"]["id"], update)


@pytest.mark.parametrize("waiting", ["attack_roll", "attack_choice", "defense", "health"])
def test_restricted_wait_has_bounded_host_resolution(client, battle, waiting):
    from test_batch10 import FixedRandom
    from test_combat import attack, configure_guard, step

    from app.dice.service import DiceService

    g = battle
    isolate_human_turn(client, g)
    rng = FixedRandom(10, 8, 10, 8, 2, 10, 8)
    client.app.state.room_service.dice = DiceService(rng)
    if waiting == "health":
        ok(
            client.post(
                g["prefix"] + "/combat/damage",
                json=dict(
                    target_id=g["human"],
                    amount=6,
                    client_request_id=str(uuid4()),
                    reason="隔离强制伤势检查",
                ),
            )
        )
    elif waiting == "defense":
        configure_guard(client, g, attributes={"dex": 90, "con": 50})
        attack(client, g, actor="guard", target=g["human"])
    else:
        attack(client, g)
        if waiting == "attack_choice":
            ok(step(client, g)[0])
    pending = state(client, g)["pending"]
    assert pending and (pending["stage"] == waiting or waiting == "health")
    runtime_fixture(client, g, san=0)
    used = list(rng.used)
    body = dict(
        action_id=pending["id"],
        stage=pending["stage"],
        operation="resolve_restricted",
        reason="当前SAN为零，按受限阶段固定策略处理",
    )
    assert (
        client.post(
            g["prefix"] + "/combat/step", json=body, headers=headers(g["remote"]["member_token"])
        ).status_code
        == 403
    )
    if waiting != "health":
        normal = {
            **body,
            "operation": "dodge"
            if waiting == "defense"
            else "luck"
            if waiting == "attack_choice"
            else "roll",
        }
        normal.pop("reason")
        if waiting == "attack_choice":
            normal["spend"] = 1
        assert (
            client.post(
                g["prefix"] + "/combat/step",
                json=normal,
                headers=headers(g["remote"]["member_token"]),
            ).status_code
            == 409
        )
    checkpoint = ok(client.post(g["prefix"] + "/snapshots", json={"name": "受限待处理"}))[
        "snapshot"
    ]
    ok(client.post(g["prefix"] + "/combat/step", json=body))
    settled = state(client, g)
    assert settled["pending"] is None
    if waiting == "attack_roll":
        assert rng.used == used
    if waiting == "attack_choice":
        # Existing attack settlement still rolls the healthy NPC's defense.
        assert rng.used == used + [10, 8]
    after_dice = list(rng.used)
    ok(client.post(g["prefix"] + "/combat/step", json=body))
    assert state(client, g) == settled and rng.used == after_dice
    ok(client.post(g["prefix"] + "/pause"))
    ok(client.post(g["prefix"] + f"/snapshots/{checkpoint['id']}/load"))
    ok(client.post(g["prefix"] + "/resume"))
    ok(client.post(g["prefix"] + "/combat/step", json=body))
    restored = state(client, g)
    assert restored["pending"] is None and rng.used == after_dice
    assert restored["participants"] == settled["participants"]


def test_legacy_zero_correction_nonactions_and_restore(client, battle):
    g = battle
    slot = runtime_fixture(client, g, san=0)
    room = ok(client.get(g["prefix"]))
    frozen = room["character_slots"]
    c = room["session_state"]["characters"][slot]
    assert c["sanity"]["kind"] == "permanent"
    history = c["sanity"]["history"]
    for _ in range(3):
        assert (
            ok(client.get(g["prefix"]))["session_state"]["characters"][slot]["sanity"]["history"]
            == history
        )
    corrected = ok(
        client.post(
            g["prefix"] + "/resources/correct",
            json=dict(
                expected_revision=room["revision"],
                slot_id=slot,
                resource="san",
                value=25,
                reason="仅更正数值，不解除永久疯狂",
            ),
        )
    )["room"]
    assert corrected["session_state"]["characters"][slot]["sanity"]["kind"] == "permanent"
    assert submit(client, g).status_code == 409
    assert client.get(g["prefix"] + "/logs").status_code == 200
    ok(
        client.post(
            g["prefix"] + "/messages",
            json={"text": "仍可聊天", "client_request_id": str(uuid4())},
            headers=headers(g["remote"]["member_token"]),
        )
    )
    saved = ok(client.post(g["prefix"] + "/snapshots", json={"name": "永久疯狂仍可保存"}))[
        "snapshot"
    ]
    for _ in range(2):
        ok(client.post(g["prefix"] + "/pause"))
        ok(client.post(g["prefix"] + f"/snapshots/{saved['id']}/load"))
        ok(client.post(g["prefix"] + "/resume"))
    room = ok(client.get(g["prefix"]))
    assert room["character_slots"] == frozen
    assert len(room["session_state"]["characters"][slot]["sanity"]["history"]) == len(history) + 1
    before = room["session_state"]["characters"]
    ok(
        client.post(
            g["prefix"] + "/actions",
            json={
                "category": "rule_question",
                "text": "SAN归零是什么状态？",
                "client_request_id": str(uuid4()),
            },
            headers=headers(g["remote"]["member_token"]),
        )
    )
    assert ok(client.get(g["prefix"]))["session_state"]["characters"] == before


def test_restricted_patient_is_still_treatable(client, battle):
    from test_batch10 import FixedRandom

    from app.dice.service import DiceService

    g = battle
    runtime_fixture(client, g, san=0)
    ok(
        client.post(
            g["prefix"] + "/combat/damage",
            json=dict(
                target_id=g["human"],
                amount=2,
                client_request_id=str(uuid4()),
                reason="受限角色仍可受伤",
            ),
        )
    )
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 2))
    body = action_body(client, g, "first_aid", actor=g["agent"], target=g["human"])
    ok(client.post(g["prefix"] + "/combat/action", json=body))
    assert state(client, g)["participants"][g["human"]]["hp"] == 11
    ok(client.post(g["prefix"] + "/combat/action", json=body))
    assert state(client, g)["participants"][g["human"]]["hp"] == 11


def test_healthy_wait_cannot_use_host_restricted_shortcut(client, battle):
    from test_combat import attack

    g = battle
    isolate_human_turn(client, g)
    attack(client, g)
    pending = state(client, g)["pending"]
    response = client.post(
        g["prefix"] + "/combat/step",
        json=dict(
            action_id=pending["id"],
            stage=pending["stage"],
            operation="resolve_restricted",
            reason="不能代打健康真人",
        ),
    )
    assert response.status_code == 409
    assert not pending["rolls"]


@pytest.mark.parametrize("checkpoint_stage", ["attack_roll", "attack_choice"])
def test_fixed_shot_and_ammo_survive_restriction_and_restore(client, battle, checkpoint_stage):
    from test_batch10 import FixedRandom
    from test_combat import attack, step

    from app.dice.service import DiceService
    from app.rooms.combat_schemas import Weapon

    g = battle
    isolate_human_turn(client, g)
    room = ok(client.get(g["prefix"]))
    pistol = Weapon(
        id="pistol",
        name="隔离手枪",
        kind="firearm",
        skill="handgun",
        damage="1d6",
        ammo=2,
        capacity=6,
        reserve=4,
        ready=True,
        source="隔离测试",
    )
    ok(
        client.post(
            g["prefix"] + "/combat/setup",
            json=dict(
                member_id=g["human"],
                expected_revision=room["revision"],
                weapons=[pistol.model_dump(mode="json")],
                reason="隔离枪械",
            ),
        )
    )
    rng = FixedRandom(10, 4)
    client.app.state.room_service.dice = DiceService(rng)
    attack_body, _ = attack(client, g, weapon="pistol")
    early = ok(client.post(g["prefix"] + "/snapshots", json={"name": "击发前"}))["snapshot"]
    ok(step(client, g)[0])
    assert state(client, g)["pending"]["stage"] == "attack_choice"
    assert state(client, g)["participants"][g["human"]]["weapons"][0]["ammo"] == 1
    runtime_fixture(client, g, san=0)
    late = ok(client.post(g["prefix"] + "/snapshots", json={"name": "已有原骰弹耗"}))["snapshot"]
    saved = early if checkpoint_stage == "attack_roll" else late
    for _ in range(2):
        ok(client.post(g["prefix"] + "/pause"))
        ok(client.post(g["prefix"] + f"/snapshots/{saved['id']}/load"))
        ok(client.post(g["prefix"] + "/resume"))
        runtime_fixture(client, g, san=0)
        pending = state(client, g)["pending"]
        body = dict(
            action_id=pending["id"],
            stage=pending["stage"],
            operation="resolve_restricted",
            reason="保留原骰及实际击发消耗",
        )
        ok(client.post(g["prefix"] + "/combat/step", json=body))
        ok(client.post(g["prefix"] + "/combat/step", json=body))
        ok(
            client.post(
                g["prefix"] + "/combat/action",
                json=attack_body,
                headers=headers(g["remote"]["member_token"]),
            )
        )
        combat = state(client, g)
        assert combat["pending"] is None
        assert combat["participants"][g["human"]]["weapons"][0]["ammo"] == 1
        assert combat["participants"]["guard"]["hp"] == 12
        assert rng.used == [10, 4]


@pytest.mark.parametrize("san,kind", [(0, "none"), (50, "permanent")])
def test_item_use_same_runtime_restriction_without_cost(client, interactions, san, kind):
    from app.preparation.item_effects import apply_use
    from app.preparation.runtime_schemas import ModuleActionArgs
    from app.rooms.combat_service import load_state
    from app.rooms.service import RoomError

    g, action = interactions
    svc = client.app.state.agent_service
    client.portal.call(action, "take", g["player"], "我拿起钥匙。")
    runtime_fixture(client, g, member=g["player"], san=san, kind=kind)

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, g["room"]["id"])
            entity = await svc.entities.entity(session, room.id, g["item"])
            entity.snapshot = {**entity.snapshot, "item_uses": 2}
            data = load_state(room)
            before = data.model_dump()
            interaction = rule(
                item_id=g["item"],
                use_effect={
                    "id": "attempt",
                    "basis": "隔离物品资格",
                    "mp_cost": 3,
                    "mp_restore": 5,
                },
            )
            with pytest.raises(RoomError, match="自主"):
                await apply_use(
                    svc,
                    session,
                    room,
                    data,
                    interaction,
                    ModuleActionArgs(
                        entity_id=g["item"],
                        interaction_id=interaction.id,
                        evidence_quote="我使用钥匙。",
                    ),
                    g["player"],
                    True,
                )
            assert data.model_dump() == before

    client.portal.call(verify)


def test_zero_ai_skipped_in_normal_teammate_graph(client, game):
    from test_agent_runtime import wait_cycle

    g = game
    runtime_fixture(client, g, member=g["agent"], san=0)
    ok(submit(client, g, "检查公告"))
    assert wait_cycle(client, g)["status"] == "completed"
    runs = ok(client.get(g["prefix"] + "/agent-runs"))
    assert runs and all(r["actor_member_id"] != g["agent"] for r in runs)


def test_resource_correction_moves_restricted_current_turn(client, battle):
    from app.rooms.combat_service import load_state, store_state

    g = battle

    async def prepare(session, room):
        data = load_state(room)
        data.combat.active = True
        data.combat.order = [g["human"], g["agent"], "guard"]
        data.combat.index = 0
        store_state(room, data)

    client.portal.call(client.app.state.agent_service.mutate, g["room"]["id"], prepare)
    room = ok(client.get(g["prefix"]))
    slot = next(s["id"] for s in room["character_slots"] if s["member_id"] == g["human"])
    room = ok(
        client.post(
            g["prefix"] + "/resources/correct",
            json=dict(
                expected_revision=room["revision"],
                slot_id=slot,
                resource="san",
                value=0,
                reason="确认当前SAN归零",
            ),
        )
    )["room"]
    assert room["combat"]["current_actor_id"] == g["agent"]
    assert room["combat"]["participants"][g["human"]]["autonomy_blocked"]
    assert not room["combat"]["participants"][g["human"]]["incapacitated"]


def test_permanent_character_can_receive_rule_answer(client, game):
    from test_agent_runtime import wait_cycle

    g = game
    human = g["remote"]["room"]["self_member_id"]
    runtime_fixture(client, g, member=human, san=0)
    before = ok(client.get(g["prefix"]))["session_state"]["characters"]
    ok(
        client.post(
            g["prefix"] + "/actions",
            json={
                "category": "rule_question",
                "text": "SAN归零是什么状态？",
                "client_request_id": str(uuid4()),
            },
            headers=headers(g["remote"]["member_token"]),
        )
    )
    assert wait_cycle(client, g)["status"] == "completed"
    assert ok(client.get(g["prefix"]))["session_state"]["characters"] == before
    assert not client.app.state.agent_service.model.calls
    assert any(
        e["type"] == "rules.answered" for e in ok(client.get(g["prefix"] + "/events"))["events"]
    )


def test_already_queued_ai_rechecks_before_model(client, battle):
    from app.rooms.combat_service import load_state, store_state

    g = battle
    svc = client.app.state.agent_service

    async def queue(session, room):
        data = load_state(room)
        data.combat.active = True
        data.combat.order = [g["agent"], g["human"], "guard"]
        data.combat.index = 0
        store_state(room, data)
        await svc.combat.queue_automatic(session, room)

    client.portal.call(svc.mutate, g["room"]["id"], queue)
    runtime_fixture(client, g, member=g["agent"], san=0)
    client.portal.call(svc.runtime.drive, g["room"]["id"])
    cycle = ok(client.get(g["prefix"] + "/agent-cycle"))
    assert cycle["status"] == "completed"
    assert not svc.model.calls
    assert state(client, g)["pending"] is None
