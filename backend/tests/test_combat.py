"""Batch 15 combat: actual API transactions, permissions, saves and fixed dice."""

from uuid import uuid4

import pytest
from test_agent_runtime import game  # noqa: F401
from test_batch10 import (
    FixedRandom,
    san_game,  # noqa: F401
)
from test_module_preparation import preparation  # noqa: F401
from test_rooms import headers, lobby, ok  # noqa: F401

from app.dice.service import DiceService
from app.rooms.combat_schemas import Combatant, Weapon
from app.rules.checks import judge
from app.rules.combat import apply_injury, damage_plan, melee_result


def npc(**changes):
    return Combatant.model_validate(
        {
            "id": "guard",
            "npc_id": "guard",
            "label": "守卫",
            "team": "enemy",
            "scene_id": "square",
            "attributes": {"dex": 20, "con": 50},
            "skills": {"brawl": 40, "dodge": 30},
            "hp": 12,
            "hp_max": 12,
            "source": "主机原创隔离场景",
            **changes,
        }
    )


@pytest.mark.parametrize("mode,expected", [("dodge", None), ("fight_back", "attack")])
def test_melee_equal_levels_ignore_full_value(mode, expected):
    assert melee_result(judge(40, "regular", 30), judge(90, "regular", 70), mode) == expected
    assert melee_result(judge(40, "regular", 80), judge(90, "regular", 95), mode) is None


@pytest.mark.parametrize(
    "amount,major,dead,hp",
    [(5, False, False, 7), (6, True, False, 6), (12, True, False, 0), (13, False, True, 0)],
)
def test_injury_edges_and_idempotence(amount, major, dead, hp):
    p = npc()
    receipt = apply_injury(
        p, amount, armor_applies=True, key="hit", reason="测试", minute=0, round_number=1
    )
    assert (p.injury.major_wound, p.injury.dead, p.hp) == (major, dead, hp)
    assert (
        apply_injury(
            p, amount, armor_applies=True, key="hit", reason="测试", minute=0, round_number=1
        )
        == receipt
    )
    assert p.hp == hp


def test_armor_minor_zero_and_damage_extreme():
    p = npc(armor=2)
    assert (
        apply_injury(p, 7, armor_applies=True, key="1", reason="测试", minute=0, round_number=1)[
            "damage"
        ]
        == 5
    )
    assert not p.injury.major_wound
    for i in range(3):
        apply_injury(
            p, 5, armor_applies=False, key=str(i + 2), reason="测试", minute=0, round_number=1
        )
    assert p.hp == 0 and p.injury.unconscious and not p.injury.dying
    club = Weapon(id="club", name="棍棒", skill="brawl", damage="1d6", source="PDF372")
    assert damage_plan(club, "1d4", judge(50, "regular", 5)) == (10, [])
    club.impale = True
    assert damage_plan(club, "1d4", judge(50, "regular", 5)) == (10, ["1d6"])
    assert damage_plan(club, "1d4", judge(50, "regular", 5), counter=True) == (0, ["1d6", "1d4"])


@pytest.fixture
def battle(client, game, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client.app.state.agent_service.runtime, "schedule", lambda *_: None)
    g = game
    room = ok(client.get(g["prefix"]))
    scene = room["game"]["module"]["scene"]["id"] if "scene" in room["game"]["module"] else "square"

    # Real module state is authority; fixture does not rewrite original module files.
    async def get_scene():
        async with client.app.state.database.sessions() as session:
            return (await client.app.state.agent_service.module(session, room["id"])).state[
                "scene_id"
            ]

    scene = client.portal.call(get_scene)
    for member in (g["remote"]["room"]["self_member_id"], g["agent"]):
        room = ok(client.get(g["prefix"]))
        ok(
            client.post(
                g["prefix"] + "/combat/setup",
                json={
                    "expected_revision": room["revision"],
                    "member_id": member,
                    "reason": "隔离验收",
                    "stats_public": True,
                },
            )
        )
    room = ok(client.get(g["prefix"]))
    ok(
        client.post(
            g["prefix"] + "/combat/setup",
            json={
                "expected_revision": room["revision"],
                "npc": npc(scene_id=scene).model_dump(mode="json"),
                "reason": "测试守卫",
            },
        )
    )
    return {**g, "human": g["remote"]["room"]["self_member_id"], "scene": scene}


def state(client, g):
    return ok(client.get(g["prefix"]))["combat"]


def attack(client, g, actor=None, target="guard", weapon="unarmed"):
    body = {
        "actor_id": actor or g["human"],
        "target_id": target,
        "weapon_id": weapon,
        "operation": "attack",
        "reason": "挥拳攻击",
        "client_request_id": str(uuid4()),
        "turn_key": state(client, g)["turn_key"],
    }
    # Stable equal-DEX order may put the AI first; start with the actual first actor.
    result = ok(
        client.post(
            g["prefix"] + "/combat/action",
            json=body,
            headers=headers(g["remote"]["member_token"])
            if body["actor_id"] == g["human"]
            else None,
        )
    )
    return body, result


def step(client, g, operation="roll", **extra):
    p = state(client, g)["pending"]
    body = {"action_id": p["id"], "stage": p["stage"], "operation": operation, **extra}
    return client.post(
        g["prefix"] + "/combat/step", json=body, headers=headers(g["remote"]["member_token"])
    ), body


def test_permissions_and_wait_persist(client, battle):
    g = battle

    # Start on the human only by excluding an uninvolved AI from this encounter.
    async def position():
        from app.rooms.combat_service import load_state, store_state

        async def mutate(session, room):
            data = load_state(room)
            data.combat.participants[g["agent"]].scene_id = "elsewhere"
            store_state(room, data)

        await client.app.state.agent_service.mutate(g["room"]["id"], mutate)

    client.portal.call(position)
    rng = FixedRandom(10, 2, 10, 8, 2)
    client.app.state.room_service.dice = DiceService(rng)
    body, result = attack(client, g)
    pending = result["room"]["combat"]["pending"]
    assert pending["stage"] == "attack_roll"
    # Remote human cannot be rolled by host.
    assert (
        client.post(
            g["prefix"] + "/combat/step",
            json={"action_id": pending["id"], "stage": pending["stage"], "operation": "roll"},
        ).status_code
        == 403
    )
    save = ok(client.post(g["prefix"] + "/snapshots", json={"name": "待攻击骰"}))["snapshot"]
    response, roll_body = step(client, g)
    ok(response)
    after = state(client, g)
    assert after["participants"]["guard"]["hp"] == 10
    used = list(rng.used)
    ok(
        client.post(
            g["prefix"] + "/combat/step",
            json=roll_body,
            headers=headers(g["remote"]["member_token"]),
        )
    )
    assert state(client, g)["participants"]["guard"]["hp"] == 10 and rng.used == used
    ok(client.post(g["prefix"] + "/pause"))
    ok(client.post(g["prefix"] + f"/snapshots/{save['id']}/load"))
    ok(client.post(g["prefix"] + "/resume"))
    assert state(client, g)["participants"]["guard"]["hp"] == 12
    ok(step(client, g)[0])
    assert state(client, g)["participants"]["guard"]["hp"] == 10 and rng.used == used
    public = ok(client.get(g["prefix"], headers=headers(g["remote"]["member_token"])))
    assert "hp" not in public["combat"]["participants"]["guard"]
    assert "source" not in public["combat"]["participants"]["guard"]


def configure_guard(client, g, **changes):
    room = ok(client.get(g["prefix"]))
    ok(
        client.post(
            g["prefix"] + "/combat/setup",
            json={
                "expected_revision": room["revision"],
                "reason": "隔离案例声明",
                "npc": npc(scene_id=g["scene"], **changes).model_dump(mode="json"),
            },
        )
    )


@pytest.mark.parametrize("defense,damage", [("dodge", 0), ("fight_back", 2)])
def test_defense_wait_restart_and_fixed_rolls(
    client, battle, character_settings, monkeypatch, defense, damage
):
    from fastapi.testclient import TestClient

    from app.main import create_app

    g = battle
    configure_guard(client, g, attributes={"dex": 70, "con": 50})
    rng = FixedRandom(10, 3, 10, 2, *([2] if damage else []))
    client.app.state.room_service.dice = DiceService(rng)
    _, result = attack(client, g, actor="guard", target=g["human"])
    assert result["room"]["combat"]["pending"]["stage"] == "defense"
    saved = ok(client.post(g["prefix"] + "/snapshots", json={"name": "待防御"}))["snapshot"]
    rejected = client.post(
        g["prefix"] + "/combat/step",
        json={
            "action_id": state(client, g)["pending"]["id"],
            "stage": "defense",
            "operation": defense,
        },
    )
    assert rejected.status_code == 403
    ok(step(client, g, defense)[0])
    assert state(client, g)["pending"]["stage"] == "defense_roll"
    ok(step(client, g)[0])
    assert state(client, g)["participants"][g["human"]]["hp"] == 12 - damage
    used = list(rng.used)
    ok(client.post(g["prefix"] + "/pause"))
    client.__exit__(None, None, None)
    app = create_app(character_settings)
    with TestClient(
        app, headers=headers(character_settings.host_admin_token.get_secret_value())
    ) as second:
        monkeypatch.setattr(app.state.agent_service.runtime, "schedule", lambda *_: None)
        app.state.room_service.dice = DiceService(FixedRandom())
        ok(second.post(g["prefix"] + f"/snapshots/{saved['id']}/load"))
        ok(second.post(g["prefix"] + "/resume"))
        assert state(second, g)["pending"]["stage"] == "defense"
        ok(step(second, g, defense)[0])
        response, request = step(second, g)
        ok(response)
        assert state(second, g)["participants"][g["human"]]["hp"] == 12 - damage
        ok(
            second.post(
                g["prefix"] + "/combat/step",
                json=request,
                headers=headers(g["remote"]["member_token"]),
            )
        )
        assert state(second, g)["participants"][g["human"]]["hp"] == 12 - damage
        assert used == rng.used


def test_defense_luck_and_push_forbidden(client, battle):
    g = battle
    configure_guard(client, g, attributes={"dex": 70, "con": 50})
    room = ok(client.get(g["prefix"]))
    ok(
        client.patch(
            g["prefix"] + "/check-rules",
            json={"expected_revision": room["revision"], "luck_spending": True},
        )
    )
    rng = FixedRandom(10, 3, 10, 4)
    client.app.state.room_service.dice = DiceService(rng)
    attack(client, g, actor="guard", target=g["human"])
    ok(step(client, g, "dodge")[0])
    ok(step(client, g)[0])
    p = state(client, g)["pending"]
    assert p["stage"] == "defense_choice"
    before = ok(client.get(g["prefix"], headers=headers(g["remote"]["member_token"])))[
        "session_state"
    ]["characters"]
    assert step(client, g, "push")[0].status_code == 422
    response, body = step(client, g, "luck", spend=10)
    ok(response)
    after = ok(client.get(g["prefix"], headers=headers(g["remote"]["member_token"])))[
        "session_state"
    ]["characters"]
    sid = next(iter(before))
    assert after[sid]["luck"] == before[sid]["luck"] - 10 and after[sid]["hp"] == 12
    ok(
        client.post(
            g["prefix"] + "/combat/step", json=body, headers=headers(g["remote"]["member_token"])
        )
    )
    assert (
        client.post(
            g["prefix"] + "/combat/step",
            json={**body, "spend": 11},
            headers=headers(g["remote"]["member_token"]),
        ).status_code
        == 409
    )


@pytest.mark.parametrize("total,jammed,expected_hp", [(20, False, 8), (100, True, 12)])
def test_single_shot_ammo_malfunction_and_repeated_request(
    client, battle, total, jammed, expected_hp
):
    g = battle
    pistol = Weapon(
        id="pistol",
        name="测试单发枪",
        kind="firearm",
        skill="handgun",
        damage="1d6",
        impale=True,
        ammo=2,
        capacity=6,
        reserve=4,
        ready=True,
        source="主机测试数据",
    )
    room = ok(client.get(g["prefix"]))
    ok(
        client.post(
            g["prefix"] + "/combat/setup",
            json={
                "expected_revision": room["revision"],
                "member_id": g["human"],
                "weapons": [pistol.model_dump(mode="json")],
                "reason": "明确测试枪械",
            },
        )
    )
    client.app.state.room_service.dice = DiceService(
        FixedRandom(10, total // 10 if total < 100 else 10, *([] if jammed else [4]))
    )
    body, _ = attack(client, g, weapon="pistol")
    response, roll_body = step(client, g)
    ok(response)
    data = state(client, g)
    own = data["participants"][g["human"]]["weapons"][0]
    assert own["ammo"] == 1 and own["jammed"] is jammed
    assert data["participants"]["guard"]["hp"] == expected_hp
    ok(
        client.post(
            g["prefix"] + "/combat/action", json=body, headers=headers(g["remote"]["member_token"])
        )
    )
    ok(
        client.post(
            g["prefix"] + "/combat/step",
            json=roll_body,
            headers=headers(g["remote"]["member_token"]),
        )
    )
    assert state(client, g)["participants"][g["human"]]["weapons"][0]["ammo"] == 1


def test_cover_roll_precedes_shot_and_costs_next_action(client, battle):
    g = battle
    pistol = Weapon(
        id="pistol",
        name="测试枪",
        kind="firearm",
        skill="handgun",
        damage="1d6",
        ammo=2,
        capacity=2,
        ready=True,
        source="主机测试数据",
    )
    configure_guard(
        client,
        g,
        attributes={"dex": 70, "con": 50},
        skills={"handgun": 45, "dodge": 30},
        weapons=[pistol.model_dump(mode="json")],
    )
    rng = FixedRandom(10, 2, 10, 2, 9)
    client.app.state.room_service.dice = DiceService(rng)
    attack(client, g, actor="guard", target=g["human"], weapon="pistol")
    assert step(client, g, "dodge")[0].status_code == 422
    ok(step(client, g, "cover")[0])
    assert state(client, g)["pending"]["stage"] == "defense_roll"
    ok(step(client, g)[0])
    assert state(client, g)["participants"][g["human"]]["hp"] == 12
    assert rng.used == [10, 2, 10, 2, 9]


def test_major_wound_waits_for_own_con_and_damage_replay(client, battle):
    g = battle
    body = {
        "target_id": g["human"],
        "client_request_id": str(uuid4()),
        "amount": 6,
        "reason": "已确认疯狂撞伤",
    }
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 8))
    assert (
        client.post(
            g["prefix"] + "/combat/damage", json=body, headers=headers(g["remote"]["member_token"])
        ).status_code
        == 403
    )
    ok(client.post(g["prefix"] + "/combat/damage", json=body))
    assert state(client, g)["pending"]["stage"].endswith("wound_roll")
    ok(step(client, g)[0])
    p = state(client, g)["participants"][g["human"]]
    assert p["hp"] == 6 and p["injury"]["major_wound"] and p["injury"]["unconscious"]


def test_dying_first_aid_hourly_relapse_and_death(client, battle):
    g = battle
    body = {
        "target_id": g["human"],
        "client_request_id": str(uuid4()),
        "amount": 12,
        "reason": "单次恰好最大HP",
    }
    ok(client.post(g["prefix"] + "/combat/damage", json=body))
    p = state(client, g)["participants"][g["human"]]
    assert p["hp"] == 0 and p["injury"]["dying"] and not p["injury"]["dead"]
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 2))
    ok(
        client.post(
            g["prefix"] + "/combat/action",
            json={
                "actor_id": g["agent"],
                "target_id": g["human"],
                "operation": "first_aid",
                "reason": "立即急救",
                "turn_key": 0,
                "client_request_id": str(uuid4()),
            },
        )
    )
    p = state(client, g)["participants"][g["human"]]
    assert p["hp"] == 1 and p["injury"]["stabilized"]
    ok(client.post(g["prefix"] + "/agent-cycle/cancel"))
    room = ok(client.get(g["prefix"]))
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 8))
    ok(
        client.post(
            g["prefix"] + "/combat/control",
            json={
                "operation": "advance",
                "minutes": 60,
                "expected_revision": room["revision"],
                "reason": "稳定后一小时",
            },
        )
    )
    assert state(client, g)["pending"]["stage"].endswith("hourly_roll")
    ok(step(client, g)[0])
    p = state(client, g)["participants"][g["human"]]
    assert p["hp"] == 0 and p["injury"]["dying"] and not p["injury"]["stabilized"]
    ok(client.post(g["prefix"] + "/agent-cycle/cancel"))
    room = ok(client.get(g["prefix"]))
    assert (
        client.post(
            g["prefix"] + "/combat/control",
            json={
                "operation": "advance",
                "minutes": 60,
                "expected_revision": room["revision"],
                "reason": "不能跳过濒死检查",
            },
        ).status_code
        == 409
    )
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 8))
    ok(
        client.post(
            g["prefix"] + "/combat/control",
            json={
                "operation": "advance",
                "expected_revision": room["revision"],
                "reason": "下一轮",
            },
        )
    )
    ok(step(client, g)[0])
    assert state(client, g)["participants"][g["human"]]["injury"]["dead"]


def test_natural_combat_uses_model_and_returns_to_human(client, battle, monkeypatch):
    from test_agent_runtime import submit, wait_cycle

    from app.agents.model import FakeModelAdapter

    g = battle
    configure_guard(client, g, attributes={"dex": 70, "con": 50})

    async def away():
        from app.rooms.combat_service import load_state, store_state

        async def mutate(session, room):
            data = load_state(room)
            data.combat.participants[g["agent"]].scene_id = "away"
            store_state(room, data)

        await client.app.state.agent_service.mutate(g["room"]["id"], mutate)

    client.portal.call(away)

    def responder(messages, kwargs):
        import json

        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "CombatNarration":
            return {"text": "守卫挥拳逼近，交锋的结果已经确定。"}
        return {
            "operation": "attack",
            "target_id": g["human"] if context["automatic"] else "guard",
            "weapon_id": "unarmed",
            "reason": "挥拳攻击对方",
        }

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=responder)
    monkeypatch.delattr(client.app.state.agent_service.runtime, "schedule")
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 2, 10, 6, 2))
    ok(submit(client, g, "我挥拳攻击守卫。"))
    assert wait_cycle(client, g, timeout=30)["status"] == "waiting_for_roll"
    assert state(client, g)["pending"]["stage"] == "defense"
    started_events = ok(client.get(g["prefix"] + "/events"))["events"]
    assert any(
        e["type"] == "keeper.narration" and "尚未掷骰" in e["payload"].get("text", "")
        for e in started_events
    )
    ok(step(client, g, "dodge")[0])
    ok(step(client, g)[0])
    final = wait_cycle(client, g, expected=("completed", "failed"), timeout=30)
    assert final["status"] == "completed", final
    data = state(client, g)
    assert data["current_actor_id"] == g["human"] and data["participants"][g["human"]]["hp"] == 10
    events = ok(client.get(g["prefix"] + "/events"))["events"]
    assert any(e["type"] == "combat.resolved" for e in events)
    receipt = next(e["payload"] for e in events if e["type"] == "combat.resolved")
    assert any(
        e["type"] == "keeper.narration" and receipt["summary"] in e["payload"].get("text", "")
        for e in events
    )
    assert any(e["type"] == "keeper.narration" and e["payload"].get("combat") for e in events)


def test_confirmed_push_damage_waits_for_con_and_returns_to_narration(client, game, monkeypatch):  # noqa: F811
    from test_agent_runtime import wait_cycle
    from test_batch11 import begin_check, player

    async def hold_review(*args):
        return None

    monkeypatch.setattr(client.app.state.agent_service.settlement, "keeper_review", hold_review)
    g = {**game, "human": game["remote"]["room"]["self_member_id"]}
    check, rng = begin_check(client, g, 10, 8, 10, 9, 10, 2)
    path = f"/checks/{check['id']}"
    ok(player(client, g, path + "/choice", {"operation": "push", "effort": "冒险探身再试"}))
    wait_cycle(client, g, timeout=30)
    ok(
        client.post(
            g["prefix"] + path + "/push-review",
            json={
                "approve": True,
                "reason": "再次探身可能跌落",
                "consequence": {
                    "kind": "damage",
                    "description": "跌落造成6点伤害",
                    "damage_formula": "6",
                    "armor_applies": False,
                },
            },
        )
    )
    ok(player(client, g, path + "/push-roll", {}))
    assert wait_cycle(client, g, timeout=30)["status"] == "waiting_for_roll"
    assert state(client, g)["pending"]["stage"].endswith("wound_roll")
    assert state(client, g)["participants"][g["human"]]["hp"] == 6
    ok(step(client, g)[0])
    if state(client, g)["pending"]:
        ok(step(client, g, "accept")[0])
    assert (
        wait_cycle(client, g, expected=("completed", "failed"), timeout=30)["status"] == "completed"
    )
    ok(player(client, g, path + "/push-roll", {}))
    assert state(client, g)["participants"][g["human"]]["hp"] == 6
    assert len(rng.used) == 6


def test_confirmed_insanity_damage_uses_same_health_wait(client, san_game):  # noqa: F811
    from test_agent_runtime import wait_cycle
    from test_batch10 import manage, request, roll

    g = {**san_game, "human": san_game["player"]}
    client.app.state.room_service.dice = DiceService(FixedRandom(20, 30, 2, 3, 10, 8))
    check = request(client, g, "five")
    for stage in ("san", "int", "duration"):
        roll(client, g, check, stage)
    ok(manage(client, g, "symptom", symptom="惊恐撞向硬物", damage=6))
    assert state(client, g)["pending"]["stage"].endswith("wound_roll")
    assert state(client, g)["participants"][g["human"]]["hp"] == 6
    ok(step(client, g)[0])
    assert (
        wait_cycle(client, g, expected=("completed", "failed"), timeout=30)["status"] == "completed"
    )
    assert state(client, g)["participants"][g["human"]]["injury"]["unconscious"]


def test_medicine_cannot_repeat_and_hourly_time_cannot_be_skipped(client, battle):
    g = battle
    ok(
        client.post(
            g["prefix"] + "/combat/damage",
            json={
                "target_id": g["human"],
                "client_request_id": str(uuid4()),
                "amount": 4,
                "reason": "轻伤治疗案例",
            },
        )
    )
    client.app.state.room_service.dice = DiceService(FixedRandom(1, 10, 2))
    body = {
        "actor_id": g["agent"],
        "target_id": g["human"],
        "operation": "medicine",
        "reason": "花一小时治疗",
        "turn_key": 0,
        "client_request_id": str(uuid4()),
    }
    ok(client.post(g["prefix"] + "/combat/action", json=body))
    assert state(client, g)["participants"][g["human"]]["hp"] == 10
    assert ok(client.get(g["prefix"]))["session_state"]["game_minute"] == 60
    ok(client.post(g["prefix"] + "/agent-cycle/cancel"))
    assert (
        client.post(
            g["prefix"] + "/combat/action",
            json={
                **body,
                "client_request_id": str(uuid4()),
            },
        ).status_code
        == 422
    )


def test_start_without_free_attack_is_idempotent(client, battle):
    g = battle
    configure_guard(client, g, attributes={"dex": 70, "con": 50})
    body, first = attack(client, g)
    assert first["room"]["combat"]["current_actor_id"] == "guard"
    ok(client.post(g["prefix"] + "/agent-cycle/cancel"))
    before = state(client, g)
    ok(
        client.post(
            g["prefix"] + "/combat/action", json=body, headers=headers(g["remote"]["member_token"])
        )
    )
    assert state(client, g) == before


def test_medicine_replaces_dying_temporary_hp(client, battle):
    g = battle
    ok(
        client.post(
            g["prefix"] + "/combat/damage",
            json={
                "target_id": g["human"],
                "client_request_id": str(uuid4()),
                "amount": 12,
                "reason": "濒死治疗不叠加临时HP",
            },
        )
    )
    rng = FixedRandom(10, 2, 1, 10, 2)
    client.app.state.room_service.dice = DiceService(rng)
    for operation in ("first_aid", "medicine"):
        body = {
            "actor_id": g["agent"],
            "target_id": g["human"],
            "operation": operation,
            "reason": "先稳定再医学治疗",
            "turn_key": state(client, g)["turn_key"],
            "client_request_id": str(uuid4()),
        }
        ok(client.post(g["prefix"] + "/combat/action", json=body))
        ok(client.post(g["prefix"] + "/agent-cycle/cancel"))
    patient = state(client, g)["participants"][g["human"]]
    assert patient["hp"] == 2
    assert not patient["injury"]["dying"] and not patient["injury"]["stabilized"]
    assert patient["injury"]["check_due_minute"] is None
    ok(client.post(g["prefix"] + "/combat/action", json=body))
    assert state(client, g)["participants"][g["human"]] == patient
    assert len(rng.used) == 5


def test_firearm_priority_surrender_and_two_round_reload(client, battle):
    g = battle
    pistol = Weapon(
        id="pistol",
        name="手枪",
        skill="handgun",
        kind="firearm",
        damage="1d10",
        ammo=1,
        capacity=8,
        reserve=7,
        ready=True,
        source="PDF97/373",
    )
    configure_guard(
        client,
        g,
        attributes={"dex": 20, "con": 50},
        skills={"brawl": 40, "dodge": 30, "handgun": 45},
        weapons=[pistol.model_dump(mode="json"), npc().weapons[0].model_dump(mode="json")],
    )
    # DEX20+50 starts first, but choosing melee surrenders that priority to DEX60 investigators.
    _, response = attack(client, g, actor="guard", target=g["human"])
    assert response["room"]["combat"]["current_actor_id"] in {g["human"], g["agent"]}
    assert response["room"]["combat"]["pending"] is None
    ok(client.post(g["prefix"] + "/agent-cycle/cancel"))
    room = ok(client.get(g["prefix"]))
    ok(
        client.post(
            g["prefix"] + "/combat/control",
            json={
                "operation": "end",
                "expected_revision": room["revision"],
                "reason": "双方同意停止",
            },
        )
    )
    body = {
        "actor_id": "guard",
        "operation": "reload",
        "weapon_id": "pistol",
        "reason": "装填散装弹",
        "turn_key": state(client, g)["turn_key"],
        "client_request_id": str(uuid4()),
    }
    ok(client.post(g["prefix"] + "/combat/action", json=body))
    w = state(client, g)["participants"]["guard"]["weapons"][0]
    assert (w["ammo"], w["reserve"]) == (3, 5)
    ok(client.post(g["prefix"] + "/combat/action", json=body))
    assert state(client, g)["participants"]["guard"]["weapons"][0] == w


@pytest.mark.parametrize("human_last", [False, True])
def test_withdrawn_target_cannot_be_attacked_again_in_same_combat(client, battle, human_last):
    g = battle
    configure_guard(client, g, attributes={"dex": 70, "con": 50})
    attack(client, g)
    ok(client.post(g["prefix"] + "/agent-cycle/cancel"))

    async def arrange_equal_dex_order():
        from app.rooms.combat_service import load_state, store_state

        async def mutate(session, room):
            data = load_state(room)
            # Exercise both positions in the DEX60 tie, independently of random UUIDs.
            tied = [g["agent"], g["human"]] if human_last else [g["human"], g["agent"]]
            data.combat.order = ["guard", *tied]
            store_state(room, data)

        await client.app.state.agent_service.mutate(g["room"]["id"], mutate)

    client.portal.call(arrange_equal_dex_order)
    for _ in range(3):
        before = state(client, g)
        actor = before["current_actor_id"]
        operation = "end" if actor == g["human"] else "pass"
        body = {
            "actor_id": actor,
            "operation": operation,
            "reason": "退出后的目标资格验收",
            "turn_key": before["turn_key"],
            "client_request_id": str(uuid4()),
        }
        response = client.post(
            g["prefix"] + "/combat/action",
            json=body,
            headers=headers(g["remote"]["member_token"]) if actor == g["human"] else {},
        )
        ok(response)
        ok(client.post(g["prefix"] + "/agent-cycle/cancel"))
    before = state(client, g)
    assert before["current_actor_id"] == "guard"
    response = client.post(
        g["prefix"] + "/combat/action",
        json={
            "actor_id": "guard",
            "target_id": g["human"],
            "operation": "attack",
            "weapon_id": "unarmed",
            "reason": "不能追击已退出当前战斗的角色",
            "turn_key": before["turn_key"],
            "client_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 422
    assert state(client, g) == before
