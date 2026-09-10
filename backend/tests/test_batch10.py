"""Deterministic SAN rules, authorization, transactions and rewind regressions."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_agent_runtime import wait_cycle
from test_module_preparation import approve_opening, preparation  # noqa: F401
from test_rooms import character, headers, lobby, ok, prepare  # noqa: F401

from app.agents.model import FakeModelAdapter
from app.dice.service import DiceService
from app.rooms.sanity_schemas import SanityEffect
from app.rules.sanity import RULE_SOURCE, insanity_trigger, judge_sanity, loss_bounds


class FixedRandom:
    def __init__(self, *values):
        self.values = list(values)
        self.used = []

    def randint(self, low, high):
        value = self.values.pop(0)
        assert low <= value <= high
        self.used.append(value)
        return value


@pytest.mark.parametrize(
    "san,roll,passed,fumble",
    [
        (50, 1, True, False),
        (50, 50, True, False),
        (50, 51, False, False),
        (49, 95, False, False),
        (49, 96, False, True),
        (50, 96, False, False),
        (99, 99, True, False),
        (99, 100, False, True),
        (0, 1, False, False),
    ],
)
def test_sanity_binary_boundaries(san, roll, passed, fumble):
    result = judge_sanity(san, roll)
    assert result["passed"] == passed
    assert (result["level"] == "fumble") == fumble
    assert result["level"] not in {"critical", "hard", "extreme"}


def test_accumulation_fraction_and_formula_bounds():
    assert insanity_trigger(40, 1, 49, 9, "none") == "none"
    assert insanity_trigger(39, 1, 49, 10, "none") == "indefinite"
    assert loss_bounds("1d6-1") == (0, 5)
    assert loss_bounds("0") == (0, 0)
    assert loss_bounds("1d100") == (1, 100)


@pytest.mark.parametrize(
    "formula",
    [
        "-1",
        "1d6-2",
        "101",
        "100d100",
        "1d101",
        "2D6",
        "1d6;exit",
        "__import__('os')",
        "1d6+100",
        "1d1",
        "",
    ],
)
def test_sanity_invalid_formula(formula):
    with pytest.raises(ValueError):
        loss_bounds(formula)
    with pytest.raises(ValidationError):
        effect("bad", "0", formula)


def effect(name, success, failure):
    return SanityEffect(
        id=name,
        encounter="隔离 Fake 验收遭遇",
        success_loss=success,
        failure_loss=failure,
        source=RULE_SOURCE,
        page=131,
        basis="规则损失格式与单次创伤测试；非原模组剧情",
        visibility="actor_and_host",
        repeat="host_confirmed",
    )


@pytest.fixture
def san_game(client, lobby, preparation, request):  # noqa: F811
    p = lobby["prefix"]
    entity = next(e for e in preparation["entities"] if e["title"] == "公告")
    effects = [
        effect("zero", "0", "0"),
        effect("small", "1", "1d4+1"),
        effect("five", "5", "5"),
        effect("ten", "10", "10"),
        effect("fatal", "100", "100"),
        effect("dice_success", "1d3", "1d6"),
    ]
    if "pipeline" in request.node.name:
        effects = effects[:2]
        effects[0].automation = "automatic"
        effects[1].action_types = []
    if "mythos" in request.node.name:
        effects[2].mythos = True
    if "permissions" in request.node.name:
        stranger = ok(
            client.post(
                "/api/rooms/join",
                json={"invite_code": lobby["created"]["invite_code"], "display_name": "其他调查员"},
            ),
            201,
        )
        card = character(client, "其他调查员")
        room = ok(client.post(p + "/character-slots", json={"character_id": card["id"]}))["room"]
        slot = next(
            s for s in room["character_slots"] if s["character_snapshot"]["id"] == card["id"]
        )
        ok(
            client.post(
                p + "/character-assignments",
                json={"slot_id": slot["id"], "member_id": stranger["room"]["self_member_id"]},
            )
        )
        ok(
            client.post(
                p + "/ready", headers=headers(stranger["member_token"]), json={"ready": True}
            )
        )
        lobby["stranger"] = stranger
    ok(
        client.patch(
            f"/api/module-entities/{entity['id']}",
            json={
                "sanity_effects": [e.model_dump() for e in effects],
                "initial_visibility": "revealed",
            },
        )
    )
    approved = approve_opening(client, preparation)
    ok(client.patch(p + "/module-preparation", json={"preparation_id": approved["id"]}))
    prepare(client, lobby)
    ok(client.post(p + "/pause"))
    for role, member in [
        ("keeper", lobby["room"]["host_member_id"]),
        ("investigator", lobby["agent"]),
    ]:
        profile = ok(client.post("/api/agent-profiles", json={"role": role, "name": role}), 201)
        ok(
            client.post(
                p + "/agent-bindings", json={"member_id": member, "profile_id": profile["id"]}
            )
        )
    ok(client.post(p + "/resume"))
    return {**lobby, "entity": entity["id"]}


def source_event(client, game, actor=None):
    async def append():
        agents = client.app.state.agent_service

        async def operation(session, room):
            event = agents.rooms.append(
                session,
                room,
                "action.submitted",
                actor or game["player"],
                {"target_entity_id": game["entity"], "text": "隔离测试：目睹批准的遭遇"},
            )
            return event.seq

        return await agents.mutate(game["room"]["id"], operation)

    return client.portal.call(append)


def request(client, game, name, seq=None, actor=None):
    seq = seq or source_event(client, game, actor)
    response = client.post(
        game["prefix"] + "/sanity/encounters",
        json={
            "target_member_id": actor or game["player"],
            "entity_id": game["entity"],
            "effect_id": name,
            "source_event_seq": seq,
            "encounter_confirmed": True,
            "repeat_confirmed": True,
            "reason": "主机确认独立的隔离测试遭遇",
        },
    )
    return ok(response)["check"]


def roll(client, game, check, stage, token=None):
    return ok(
        client.post(
            game["prefix"] + f"/sanity/checks/{check['id']}/roll",
            headers=headers(token or game["remote"]["member_token"]),
            json={"expected_stage": stage},
        )
    )["check"]


def current(client, game, slot=None):
    return ok(client.get(game["prefix"]))["session_state"]["characters"][slot or game["slots"][0]]


def manage(client, game, operation, **kwargs):
    return client.post(
        game["prefix"] + "/sanity/manage",
        json={
            "operation": operation,
            "expected_revision": ok(client.get(game["prefix"]))["revision"],
            "slot_id": game["slots"][0],
            "reason": "隔离测试的明确主机裁定",
            **kwargs,
        },
    )


def save(client, game):
    ok(client.post(game["prefix"] + "/pause"))
    result = ok(client.post(game["prefix"] + "/snapshots", json={"name": "SAN checkpoint"}))[
        "snapshot"
    ]
    ok(client.post(game["prefix"] + "/resume"))
    return result


def restore(client, game, snapshot):
    ok(client.post(game["prefix"] + "/pause"))
    ok(client.post(game["prefix"] + f"/snapshots/{snapshot['id']}/load"))
    ok(client.post(game["prefix"] + "/resume"))


def test_sanity_loss_permissions_idempotency_and_three_rewinds(client, san_game):
    g = san_game
    rng = FixedRandom(70, 3)
    client.app.state.room_service.dice = DiceService(rng)
    before = save(client, g)
    seq = source_event(client, g)
    check = request(client, g, "small", seq)
    assert (
        client.post(
            g["prefix"] + "/sanity/encounters",
            json={
                "target_member_id": g["player"],
                "entity_id": g["entity"],
                "effect_id": "fatal",
                "source_event_seq": seq,
                "encounter_confirmed": True,
                "repeat_confirmed": True,
                "reason": "主机确认测试遭遇",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            g["prefix"] + "/sanity/encounters",
            json={
                "target_member_id": g["player"],
                "entity_id": g["entity"],
                "effect_id": "small",
                "source_event_seq": seq,
                "encounter_confirmed": True,
                "repeat_confirmed": True,
                "reason": "主机确认测试遭遇",
                "loss": 99,
            },
        ).status_code
        == 422
    )
    pending = save(client, g)
    args = {"expected_stage": "san"}
    assert client.post(g["prefix"] + f"/checks/{check['id']}/roll", json={}).status_code == 422
    stranger = g["stranger"]
    stranger_headers = headers(stranger["member_token"])
    assert (
        client.post(
            g["prefix"] + f"/sanity/checks/{check['id']}/roll", headers=stranger_headers, json=args
        ).status_code
        == 403
    )
    assert (
        client.post(
            g["prefix"] + "/sanity/encounters",
            headers=headers(g["remote"]["member_token"]),
            json={
                "target_member_id": g["player"],
                "entity_id": g["entity"],
                "effect_id": "small",
                "source_event_seq": seq,
                "encounter_confirmed": True,
                "repeat_confirmed": True,
                "reason": "主机确认测试遭遇",
            },
        ).status_code
        == 403
    )
    first = roll(client, g, check, "san")
    assert first["sanity"]["stage"] == "loss"
    assert current(client, g)["san"] == 50
    assert roll(client, g, check, "san") == first
    done = roll(client, g, check, "loss")
    assert done["status"] == "resolved" and current(client, g)["san"] == 46
    assert request(client, g, "small", seq)["id"] == check["id"]
    assert current(client, g)["san"] == 46
    settled = save(client, g)
    restore(client, g, pending)
    assert current(client, g)["san"] == 50
    roll(client, g, check, "san")
    roll(client, g, check, "loss")
    assert current(client, g)["san"] == 46 and rng.used == [70, 3]
    restore(client, g, settled)
    roll(client, g, check, "loss")
    assert current(client, g)["san"] == 46
    restore(client, g, before)
    assert current(client, g)["san"] == 50
    request(client, g, "small", seq)
    roll(client, g, check, "san")
    roll(client, g, check, "loss")
    assert current(client, g)["san"] == 46 and rng.used == [70, 3]
    other = ok(client.get(g["prefix"], headers=stranger_headers))
    assert g["slots"][0] not in other["session_state"]["characters"] and not other["game"]["checks"]
    events = ok(client.get(g["prefix"] + "/events", headers=stranger_headers))["events"]
    assert not any(e["type"] in {"sanity.progressed", "sanity.dice_fixed"} for e in events)


def test_temporary_bout_immunity_relapse_time_and_indefinite(client, san_game):
    g = san_game
    rng = FixedRandom(20, 30, 2, 3, 20, 2, 20, 4)
    client.app.state.room_service.dice = DiceService(rng)
    check = request(client, g, "five")
    roll(client, g, check, "san")
    assert current(client, g)["san"] == 45
    assert client.post(g["prefix"] + "/agent-cycle/cancel").status_code == 409
    partial = save(client, g)
    roll(client, g, check, "int")
    roll(client, g, check, "duration")
    assert current(client, g)["sanity"]["kind"] == "temporary"
    ok(manage(client, g, "symptom", symptom="偏执：担心被监视"))
    immune = request(client, g, "fatal")
    assert immune["status"] == "resolved" and current(client, g)["san"] == 45
    assert manage(client, g, "end_bout").status_code == 409
    ok(manage(client, g, "advance", round=3, minute=119))
    ok(manage(client, g, "end_bout"))
    assert manage(client, g, "recover").status_code == 409
    relapse = request(client, g, "small")
    roll(client, g, relapse, "san")
    assert current(client, g)["sanity"]["phase"] == "awaiting_symptom"
    ok(manage(client, g, "symptom", symptom="畏缩"))
    ok(manage(client, g, "advance", minute=120, round=5))
    ok(manage(client, g, "recover"))
    assert current(client, g)["sanity"]["kind"] == "none"
    # Rewind to deduction before INT: no second five-point deduction.
    restore(client, g, partial)
    roll(client, g, check, "int")
    roll(client, g, check, "duration")
    ok(manage(client, g, "symptom", symptom="偏执"))
    assert current(client, g)["san"] == 45
    ok(manage(client, g, "advance", minute=120, round=3))
    ok(manage(client, g, "recover"))
    # Another five in the same host-defined day reaches exactly one fifth.
    check2 = request(client, g, "five")
    roll(client, g, check2, "san")
    assert current(client, g)["sanity"]["kind"] == "indefinite"
    ok(manage(client, g, "symptom", symptom="失忆"))
    ok(manage(client, g, "new_day", minute=5000, round=100))
    assert current(client, g)["sanity"]["day_loss"] == 0
    assert current(client, g)["sanity"]["kind"] == "indefinite"
    assert manage(client, g, "recover", recovery_basis="safe_sleep").status_code == 409
    ok(manage(client, g, "recover", recovery_basis="chapter_end"))


def test_zero_success_loss_fumble_int_failure_and_zero_san(client, san_game):
    g = san_game
    rng = FixedRandom(1, 1, 2, 100, 80, 70, 1)
    client.app.state.room_service.dice = DiceService(rng)
    zero = request(client, g, "zero")
    roll(client, g, zero, "san")
    assert current(client, g)["san"] == 50
    variable = request(client, g, "dice_success")
    roll(client, g, variable, "san")
    roll(client, g, variable, "loss")
    assert current(client, g)["san"] == 48
    fumble = request(client, g, "small")
    roll(client, g, fumble, "san")
    assert current(client, g)["san"] == 43
    assert fumble["sanity"]["before"] == 48
    roll(client, g, fumble, "int")
    assert current(client, g)["sanity"]["kind"] == "none"
    ok(manage(client, g, "new_day"))
    fatal = request(client, g, "fatal")
    roll(client, g, fatal, "san")
    assert current(client, g)["san"] == 0
    assert current(client, g)["sanity"]["kind"] == "permanent"
    assert manage(client, g, "recover", recovery_basis="chapter_end").status_code == 409
    assert (
        client.post(
            g["prefix"] + "/actions",
            headers=headers(g["remote"]["member_token"]),
            json={"text": "继续调查", "client_request_id": str(uuid4())},
        ).status_code
        == 409
    )


def test_resource_correction_and_rule_question_no_sanity_side_effect(client, san_game):
    g = san_game
    room = ok(client.get(g["prefix"]))
    args = dict(
        expected_revision=room["revision"],
        slot_id=g["slots"][0],
        resource="san",
        value=51,
        reason="录入纠错",
    )
    assert (
        client.post(
            g["prefix"] + "/resources/correct",
            headers=headers(g["remote"]["member_token"]),
            json=args,
        ).status_code
        == 403
    )
    assert (
        client.post(g["prefix"] + "/resources/correct", json={**args, "value": 100}).status_code
        == 422
    )
    ok(client.post(g["prefix"] + "/resources/correct", json=args))
    events = ok(client.get(g["prefix"] + "/events"))["events"]
    assert not any(e["type"] == "scene.updated" and e["seq"] > room["revision"] for e in events)
    saved = current(client, g)
    ok(
        client.post(
            g["prefix"] + "/actions",
            headers=headers(g["remote"]["member_token"]),
            json={
                "text": "SAN 疯狂如何判定？",
                "category": "rule_question",
                "client_request_id": str(uuid4()),
            },
        )
    )
    wait_cycle(client, g)
    assert current(client, g) == saved


def test_keeper_approved_sanity_pipeline_and_ai_roll(client, san_game):
    g = san_game

    def responder(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            ids = context["action_identifiers"]
            e = context["sanity_effects"][1 if ids["actor_member_id"] == g["agent"] else 0]
            return {
                "plan_id": ids["plan_id"],
                "cycle_id": ids["cycle_id"],
                "current_scene_id": ids["current_scene_id"],
                "expected_navigation_revision": ids["expected_navigation_revision"],
                "parsed_intent": {
                    "type": "investigate",
                    "actor_member_id": ids["actor_member_id"],
                    "actor_character_slot_id": ids["actor_character_slot_id"],
                    "target_id": g["entity"],
                    "evidence_quote": context["triggering_action"]["payload"]["text"],
                    "confidence": 1,
                },
                "proposed_tool_calls": [
                    {
                        "name": "request_sanity_check",
                        "arguments": {
                            k: e[k]
                            for k in (
                                "entity_id",
                                "effect_id",
                                "target_member_id",
                                "source_event_seq",
                            )
                        },
                    }
                ],
            }
        return {"public_narration": "", "grounded_claims": [], "needs_host_ruling": False}

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=responder)
    rng = FixedRandom(20, 20, 96, 30, 2, 3)
    client.app.state.room_service.dice = DiceService(rng)
    ok(
        client.post(
            g["prefix"] + "/actions",
            headers=headers(g["remote"]["member_token"]),
            json={
                "text": "我查看公告",
                "target_entity_id": g["entity"],
                "client_request_id": str(uuid4()),
            },
        )
    )
    cycle = wait_cycle(client, g)
    assert cycle["status"] == "waiting_for_roll", cycle
    check = ok(client.get(g["prefix"] + "/checks"))[-1]
    pending_snapshot = save(client, g)
    roll(client, g, check, "san")
    assert wait_cycle(client, g)["status"] == "completed"
    restore(client, g, pending_snapshot)
    roll(client, g, check, "san")
    assert wait_cycle(client, g, expected=("completed", "failed"))["status"] == "completed"
    request(client, g, "small", actor=g["agent"])
    assert wait_cycle(client, g, expected=("completed", "failed"))["status"] == "completed"
    assert current(client, g, g["slots"][1])["san"] == 49
    # AI encounter stages run serially and stop at host symptom selection.
    request(client, g, "small", actor=g["agent"])
    assert wait_cycle(client, g)["status"] == "waiting_for_roll"
    assert current(client, g, g["slots"][1])["sanity"]["phase"] == "awaiting_symptom"
    symptom_snapshot = save(client, g)
    ok(manage(client, g, "symptom", slot_id=g["slots"][1], symptom="偏执"))
    assert wait_cycle(client, g, expected=("completed", "failed"))["status"] == "completed"
    restore(client, g, symptom_snapshot)
    ok(manage(client, g, "symptom", slot_id=g["slots"][1], symptom="偏执"))
    assert wait_cycle(client, g, expected=("completed", "failed"))["status"] == "completed"
    assert current(client, g, g["slots"][1])["san"] == 44
    assert rng.used == [20, 20, 96, 30, 2, 3]


def test_mythos_growth_cap_and_snapshot_preserved(client, san_game):
    g = san_game
    room = ok(client.get(g["prefix"]))
    snapshot = room["character_slots"][0]["character_snapshot"]
    ok(
        client.post(
            g["prefix"] + "/resources/correct",
            json={
                "expected_revision": room["revision"],
                "slot_id": g["slots"][0],
                "resource": "san",
                "value": 99,
                "reason": "隔离测试初值",
            },
        )
    )
    client.app.state.room_service.dice = DiceService(FixedRandom(20, 30, 1, 2, 20, 30, 1, 2))
    for gain, cap in [(5, 94), (6, 93)]:
        check = request(client, g, "five")
        for stage in ("san", "int", "duration"):
            roll(client, g, check, stage)
        ok(manage(client, g, "symptom", symptom="偏执"))
        state = current(client, g)
        assert state["sanity"]["mythos_gain"] == gain and state["san_max"] == cap
        ok(manage(client, g, "recover", recovery_basis="safe_sleep"))
        ok(manage(client, g, "new_day"))
    room = ok(client.get(g["prefix"]))
    assert room["character_slots"][0]["character_snapshot"] == snapshot
    assert (
        client.post(
            g["prefix"] + "/resources/correct",
            json={
                "expected_revision": room["revision"],
                "slot_id": g["slots"][0],
                "resource": "san",
                "value": 94,
                "reason": "越过新的上限",
            },
        ).status_code
        == 422
    )
