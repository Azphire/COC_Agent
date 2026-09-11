"""Batch 11: final decisions, durable dice, deterministic encounters and ownership."""

import json
from uuid import uuid4

import pytest
from test_agent_runtime import game, submit  # noqa: F401
from test_agent_runtime import wait_cycle as wait_agent_cycle
from test_batch10 import FixedRandom, current, effect, restore, roll, save
from test_module_preparation import approve_opening, preparation  # noqa: F401
from test_rooms import headers, lobby, ok, prepare  # noqa: F401

from app.agents.model import FakeModelAdapter
from app.dice.service import DiceService
from app.rules.check_options import can_push, luck_options
from app.rules.checks import judge


@pytest.fixture(autouse=True)
def explicit_host_override_windows(monkeypatch):
    """These legacy tests exercise host overrides and exact save windows.

    Normal automatic KP approval is covered by batch 12; keep the generation
    pending here so the host override and each pre-approval save remain testable.
    """
    from app.agents.settlement import CheckSettlementService

    async def wait_for_test_override(self, runtime, state, check_id):
        return None

    monkeypatch.setattr(CheckSettlementService, "keeper_review", wait_for_test_override)


def wait_cycle(client, game, expected=("completed", "failed", "waiting_for_roll")):  # noqa: F811
    # Extra serial settlement stages need headroom under the full SQLite suite.
    # Wait for the same terminal/interrupt states; no result assertions are relaxed.
    return wait_agent_cycle(client, game, expected, timeout=30)


@pytest.mark.parametrize("stage", ["choice", "push_review", "push_roll", "consequence"])
def test_process_restart_each_ordinary_wait(client, game, character_settings, stage):  # noqa: F811
    from adjudication_helpers import ScenarioAdapter
    from fastapi.testclient import TestClient
    from test_agent_runtime import scenario

    from app.main import create_app

    g = game
    check, _ = begin_check(client, g, 10, 8, 10, 9)
    path = f"/checks/{check['id']}"
    review = {
        "approve": True,
        "reason": "还有额外尝试的空间",
        "consequence": {"kind": "host_manual", "description": "跌落，等待主机处理伤害"},
    }
    if stage != "choice":
        ok(player(client, g, path + "/choice", {"operation": "push", "effort": "冒险俯身再试"}))
    if stage in {"push_roll", "consequence"}:
        ok(client.post(g["prefix"] + path + "/push-review", json=review))
    if stage == "consequence":
        ok(player(client, g, path + "/push-roll", {}))
    saved = save(client, g)
    client.__exit__(None, None, None)
    app = create_app(character_settings)
    app.state.agent_model_adapter = ScenarioAdapter(responder=scenario)
    with TestClient(
        app, headers=headers(character_settings.host_admin_token.get_secret_value())
    ) as second:
        second.app.state.room_service.dice = DiceService(FixedRandom(10, 9))
        restore(second, g, saved)
        c = ok(second.get(g["prefix"] + "/checks"))[-1]
        assert c["settlement"]["stage"] == stage
        assert c["settlement"]["original_result"]["total"] == 80
        if stage == "choice":
            ok(player(second, g, path + "/choice", {"operation": "accept"}))
        else:
            if stage == "push_review":
                ok(second.post(g["prefix"] + path + "/push-review", json=review))
            if stage != "consequence":
                ok(player(second, g, path + "/push-roll", {}))
            ok(second.post(g["prefix"] + path + "/consequence", json={"reason": "主机已处理跌落"}))
        assert wait_cycle(second, g)["status"] == "completed"


def document(total=80, **extra):
    return {
        "name": "spot_hidden",
        "kind": "skill",
        "value": 60,
        "difficulty": "regular",
        "result": judge(60, "regular", total),
        **extra,
    }


def test_legacy_pending_save_restores_old_resolved_dice_without_new_roll(client, game):  # noqa: F811
    from app.persistence.agent_models import AgentSaveState, CheckRecord
    from app.rules.checks import roll_check

    g = game
    ok(submit(client, g, "我冒着失去平衡的风险调查并请求侦查检定"))
    assert wait_cycle(client, g)["status"] == "waiting_for_roll"
    check = ok(client.get(g["prefix"] + "/checks"))[-1]
    saved = save(client, g)

    async def legacy_fixture():
        agents = client.app.state.agent_service

        async def operation(session, room):
            archive = await session.get(AgentSaveState, saved["id"])
            data = json.loads(json.dumps(archive.document))
            for row in data["checks"]:
                row["document"].pop("settlement", None)
                row["document"].pop("settlement_rewound", None)
            for key in (
                "settlement_phase",
                "ordinary_check_id",
                "encounter_queue",
                "encounters_scanned",
            ):
                data["cycle"]["state"].pop(key, None)
            archive.document = data
            record = await session.get(CheckRecord, check["id"])
            document = {k: v for k, v in record.document.items() if not k.startswith("settlement")}
            dice, result = roll_check(DiceService(FixedRandom(10, 4)), 25, "regular", 0, 0)
            record.status = "resolved"
            record.document = {**document, "status": "resolved", "dice": dice, "result": result}
            # An old installation wrote only check.resolved, with no dice_fixed ledger.
            agents.rooms.append(
                session,
                room,
                "check.resolved",
                g["player"],
                {**record.document, "cycle_id": record.cycle_id},
            )

        await agents.mutate(g["room"]["id"], operation)

    client.portal.call(legacy_fixture)
    rng = FixedRandom()
    client.app.state.room_service.dice = DiceService(rng)
    restore(client, g, saved)
    path = f"/checks/{check['id']}"
    raw = ok(player(client, g, path + "/roll", {}))["check"]
    assert raw["settlement"]["original_result"]["total"] == 40
    assert raw["status"] == "pending" and raw["result"] is None
    ok(player(client, g, path + "/choice", {"operation": "accept"}))
    assert wait_cycle(client, g)["status"] == "completed"
    assert rng.used == []


@pytest.mark.parametrize("luck", [None, 0, 1, 20, 60, 99])
def test_luck_bounds_levels_and_no_purchased_critical(luck):
    choices = luck_options(document(), luck, True)
    assert all(
        0 < o["spend"] <= luck
        and o["result"]["total"] == 80 - o["spend"]
        and o["result"]["level"] != "critical"
        for o in choices
    )
    if luck and luck >= 20:
        assert next(o for o in choices if o["spend"] == 20)["result"]["level"] == "regular"
    if luck and luck >= 60:
        assert next(o for o in choices if o["spend"] == 50)["result"]["level"] == "hard"
    assert not luck_options(document(), luck, False)


@pytest.mark.parametrize(
    "extra",
    [
        {"name": "luck"},
        {"name": "SAN"},
        {"name": "damage"},
        {"sanity": {"stage": "int"}},
        {"opposed": True},
        {"combined": True},
        {"combat": True},
        {"malfunction": True},
        {"settlement": {"push_requested": True}},
        {"settlement": {"luck_spent": 1}},
    ],
)
def test_excluded_options(extra):
    assert not luck_options(document(**extra), 99, True)
    assert not can_push(document(**extra))


@pytest.mark.parametrize("total", [1, 100])
def test_extreme_dice_cannot_be_changed(total):
    assert not luck_options(document(total), 99, True)
    assert not can_push(document(total))


def player(client, g, suffix, body):
    return client.post(
        g["prefix"] + suffix, json=body, headers=headers(g["remote"]["member_token"])
    )


def begin_check(client, g, *values):
    room = ok(client.get(g["prefix"]))
    ok(
        client.patch(
            g["prefix"] + "/check-rules",
            json={"luck_spending": True, "expected_revision": room["revision"]},
        )
    )
    rng = FixedRandom(*values)
    client.app.state.room_service.dice = DiceService(rng)
    ok(submit(client, g, "对工作台进行检定，我冒着失去平衡的风险尝试。"))
    assert wait_cycle(client, g)["status"] == "waiting_for_roll"
    check = ok(client.get(g["prefix"] + "/checks"))[-1]
    ok(player(client, g, f"/checks/{check['id']}/roll", {}))
    check = ok(client.get(g["prefix"] + "/checks"))[-1]
    assert check["status"] == "pending" and check["result"] is None
    assert check["settlement"]["stage"] == "choice"
    return check, rng


def test_luck_atomic_ownership_and_rewind(client, game):  # noqa: F811
    g = game
    check, rng = begin_check(client, g, 10, 4)  # 40; character skill 25
    path = f"/checks/{check['id']}/choice"
    before = current(client, g)["luck"]
    snapshot = save(client, g)
    assert (
        client.post(g["prefix"] + path, json={"operation": "luck", "spend": 15}).status_code == 403
    )
    assert player(client, g, path, {"operation": "luck", "spend": 99}).status_code == 422
    assert player(client, g, path, {"operation": "luck", "spend": True}).status_code == 422
    result = ok(player(client, g, path, {"operation": "luck", "spend": 15}))["check"]
    assert result["result"]["passed"] and result["dice"]["selected"] == 40
    assert current(client, g)["luck"] == before - 15
    ok(player(client, g, path, {"operation": "luck", "spend": 15}))
    assert current(client, g)["luck"] == before - 15
    assert player(client, g, path, {"operation": "accept"}).status_code == 409
    assert wait_cycle(client, g)["status"] == "completed"
    paid = save(client, g)
    restore(client, g, snapshot)
    assert current(client, g)["luck"] == before
    again = ok(client.get(g["prefix"] + "/checks"))[-1]
    assert again["settlement"]["original_result"]["total"] == 40
    ok(player(client, g, path, {"operation": "luck", "spend": 15}))
    assert wait_cycle(client, g)["status"] == "completed"
    restore(client, g, paid)
    assert current(client, g)["luck"] == before - 15
    assert rng.used == [10, 4]


def test_paid_luck_save_before_graph_wakeup_resumes(client, game, monkeypatch):  # noqa: F811
    g = game
    check, rng = begin_check(client, g, 10, 4)
    path = f"/checks/{check['id']}/choice"
    before = current(client, g)["luck"]
    # Freeze only scheduling, after the real choice interrupt was checkpointed.
    # This represents pausing immediately after the deduction response arrives.
    with monkeypatch.context() as patch:
        patch.setattr(client.app.state.agent_service.runtime, "schedule", lambda *a, **k: None)
        ok(player(client, g, path, {"operation": "luck", "spend": 15}))
        paid = save(client, g)
    restore(client, g, paid)
    assert wait_cycle(client, g)["status"] == "completed"
    assert current(client, g)["luck"] == before - 15
    ok(player(client, g, path, {"operation": "luck", "spend": 15}))
    assert current(client, g)["luck"] == before - 15
    assert rng.used == [10, 4]


@pytest.mark.parametrize("outcome", ["reject", "success", "condition", "time", "manual"])
def test_push_single_followup_approval_outcome_and_rewinds(client, game, outcome):  # noqa: F811
    g = game
    check, rng = begin_check(client, g, 10, 8, 10, 1 if outcome == "success" else 9)
    path = f"/checks/{check['id']}"
    body = {"operation": "push", "effort": "多花时间俯身检查，冒着跌落的风险"}
    assert player(client, g, path + "/choice", {"operation": "push"}).status_code == 422
    ok(player(client, g, path + "/choice", body))
    pending_review = save(client, g)
    assert player(client, g, path + "/push-roll", {}).status_code == 409
    review = {
        "approve": outcome != "reject",
        "reason": "仍可达成目标，额外努力合理",
        "consequence": {
            "kind": "host_manual" if outcome in {"manual", "success", "reject"} else outcome,
            "description": "跌落或延误使处境更糟",
            "condition": "失去平衡",
            "minutes": 5,
        },
    }
    assert player(client, g, path + "/push-review", review).status_code == 403
    ok(client.post(g["prefix"] + path + "/push-review", json=review))
    if outcome == "reject":
        assert wait_cycle(client, g)["status"] == "completed"
        assert len(rng.used) == 2
        return
    pending_roll = save(client, g)
    assert player(client, g, path + "/choice", {"operation": "luck", "spend": 1}).status_code == 409
    ok(player(client, g, path + "/push-roll", {}))
    ok(player(client, g, path + "/push-roll", {}))
    if outcome == "manual":
        c = ok(client.get(g["prefix"] + "/checks"))[-1]
        assert c["status"] == "pending" and c["settlement"]["stage"] == "consequence"
        pending_consequence = save(client, g)
        ok(client.post(g["prefix"] + path + "/consequence", json={"reason": "已按场景处理摔倒"}))
        assert wait_cycle(client, g)["status"] == "completed"
        restore(client, g, pending_consequence)
        ok(client.post(g["prefix"] + path + "/consequence", json={"reason": "已按场景处理摔倒"}))
    assert wait_cycle(client, g)["status"] == "completed"
    final = ok(client.get(g["prefix"] + "/checks"))[-1]
    assert final["settlement"]["original_result"]["total"] == 80
    assert final["result"]["passed"] == (outcome == "success")
    if outcome == "condition":
        assert current(client, g)["conditions"].count("失去平衡") == 1
    restore(client, g, pending_roll)
    ok(player(client, g, path + "/push-roll", {}))
    if outcome == "manual":
        ok(client.post(g["prefix"] + path + "/consequence", json={"reason": "已处理"}))
    assert wait_cycle(client, g)["status"] == "completed"
    restore(client, g, pending_review)
    ok(client.post(g["prefix"] + path + "/push-review", json=review))
    ok(player(client, g, path + "/push-roll", {}))
    if outcome == "manual":
        ok(client.post(g["prefix"] + path + "/consequence", json={"reason": "已处理"}))
    assert wait_cycle(client, g)["status"] == "completed"
    assert len(rng.used) == 4


@pytest.mark.parametrize("kind", ["condition", "time"])
def test_push_consequence_capacity_checked_before_dice(client, game, kind):  # noqa: F811
    g = game
    check, rng = begin_check(client, g, 10, 8, 10, 9)
    path = f"/checks/{check['id']}"
    ok(player(client, g, path + "/choice", {"operation": "push", "effort": "花额外时间检查"}))
    ok(
        client.post(
            g["prefix"] + path + "/push-review",
            json={
                "approve": True,
                "reason": "额外尝试合理",
                "consequence": {
                    "kind": kind,
                    "description": "延误或状态恶化",
                    "condition": "失衡",
                    "minutes": 5,
                },
            },
        )
    )

    async def capacity(full):
        async def operation(session, room):
            state = json.loads(json.dumps(room.session_state))
            if kind == "condition":
                state["characters"][check["slot_id"]]["conditions"] = (
                    [str(i) for i in range(30)] if full else []
                )
            else:
                state["game_minute"] = 1_000_000 if full else 0
            room.session_state = state

        await client.app.state.agent_service.mutate(g["room"]["id"], operation)

    client.portal.call(capacity, True)
    for _ in range(2):
        assert player(client, g, path + "/push-roll", {}).status_code == 409
    assert rng.used == [10, 8]
    client.portal.call(capacity, False)
    pushed = ok(player(client, g, path + "/push-roll", {}))["check"]
    assert pushed["settlement"]["push_result"]["total"] == 90
    assert wait_cycle(client, g)["status"] == "completed"
    assert rng.used == [10, 8, 10, 9]


@pytest.fixture
def encounter_game(client, lobby, preparation, request):  # noqa: F811
    g = lobby
    e = next(e for e in preparation["entities"] if e["title"] == "公告")
    reveal = "reveal" in request.node.name
    checked = "check_reveal" in request.node.name
    san = effect("automatic", "1", "1").model_dump()
    san.update(
        automation="automatic",
        repeat="first_only",
        trigger="entity_revealed" if reveal else "action_target",
    )
    if "review" in request.node.name:
        san["condition"] = "确认确实直视而非仅听闻"
    ok(
        client.patch(
            f"/api/module-entities/{e['id']}",
            json={
                "sanity_effects": [san],
                "initial_visibility": "hidden" if reveal else "revealed",
            },
        )
    )
    if checked:
        scene = next(x for x in preparation["entities"] if x["type"] == "scene")
        ok(
            client.patch(
                f"/api/module-entities/{scene['id']}",
                json={"public_summary": "候车厅内可以看见公告，细节尚未辨认。"},
            )
        )
        ok(
            client.patch(
                f"/api/module-entities/{e['id']}",
                json={
                    "reveal_conditions": {
                        "access_policy": "requires_check",
                        "successful_check": {
                            "kind": "skill",
                            "name": "spot_hidden",
                            "difficulty": "regular",
                        },
                    }
                },
            )
        )
    approved = approve_opening(client, preparation)
    ok(client.patch(g["prefix"] + "/module-preparation", json={"preparation_id": approved["id"]}))
    prepare(client, g)
    ok(client.post(g["prefix"] + "/pause"))
    kp = ok(client.post("/api/agent-profiles", json={"role": "keeper", "name": "KP"}), 201)
    ok(
        client.post(
            g["prefix"] + "/agent-bindings",
            json={"member_id": g["room"]["host_member_id"], "profile_id": kp["id"]},
        )
    )
    ai = ok(client.post("/api/agent-profiles", json={"role": "investigator", "name": "队友"}), 201)
    ok(
        client.post(
            g["prefix"] + "/agent-bindings", json={"member_id": g["agent"], "profile_id": ai["id"]}
        )
    )
    ok(client.post(g["prefix"] + "/resume"))
    if checked:
        room = ok(client.get(g["prefix"]))
        state = {**room["session_state"], "scene_summary": "候车厅内可以看见公告，细节尚未辨认。"}
        ok(
            client.patch(
                g["prefix"] + "/session-state",
                json={"expected_revision": room["revision"], "state": state},
            )
        )

    def respond(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "TeammateDecision":
            return {
                "mode": "pass",
                "related_player_action_seq": context["triggering_action"]["seq"],
                "confidence": 1,
            }
        if kwargs["response_schema"].__name__ != "KeeperPlan":
            return {"public_narration": "", "grounded_claims": []}
        ids = context["action_identifiers"]
        text = context["triggering_action"]["payload"]["text"]
        tools = [{"name": "reveal_entity", "arguments": {"entity_id": e["id"]}}] if reveal else []
        output = {
            "plan_id": ids["plan_id"],
            "cycle_id": ids["cycle_id"],
            "current_scene_id": ids["current_scene_id"],
            "expected_navigation_revision": ids["expected_navigation_revision"],
            "parsed_intent": {
                "type": "wait" if "等待" in text else "recall" if "回顾" in text else "investigate",
                "actor_member_id": ids["actor_member_id"],
                "actor_character_slot_id": ids["actor_character_slot_id"],
                "target_id": ids["current_scene_id"] if reveal and not checked else e["id"],
                "evidence_quote": text,
                "confidence": 1,
            },
            "proposed_tool_calls": tools,
        }
        if checked:
            output["proposed_check"] = {
                "target_member_id": ids["actor_member_id"],
                "name": "spot_hidden",
                "kind": "skill",
                "difficulty": "regular",
                "reason": "检查公告",
                "clue_id": e["id"],
                "target_entity_id": e["id"],
                "basis_entity_id": e["id"],
                "necessity": "required",
                "uncertainty": "细节隐蔽",
                "success_effect": "辨认细节",
                "failure_consequence": "无法辨认更多细节",
            }
        if "redundant" in request.node.name:
            output["proposed_check"] = {
                "target_member_id": ids["actor_member_id"],
                "name": "spot_hidden",
                "reason": "模型误提普通侦查",
                "target_entity_id": e["id"],
                "necessity": "required",
                "uncertainty": "是否看见公告",
                "success_effect": "看见公告",
                "failure_consequence": "没有看见公告",
            }
        return output

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=respond)
    client.app.state.room_service.dice = DiceService(FixedRandom(20))
    return {**g, "entity": e["id"]}


def test_redundant_model_roll_does_not_suppress_actual_encounter(client, encounter_game):
    g = encounter_game
    ok(
        player(
            client,
            g,
            "/actions",
            {
                "text": "我查看公告",
                "target_entity_id": g["entity"],
                "client_request_id": str(uuid4()),
            },
        )
    )
    cycle = wait_cycle(client, g)
    assert cycle["status"] == "waiting_for_roll", cycle
    checks = ok(client.get(g["prefix"] + "/checks"))
    assert len(checks) == 1 and checks[0]["sanity"]["origin"] == "automatic"


@pytest.mark.parametrize(
    "text,explicit",
    [
        ("我等待片刻", True),
        ("我回顾公告", True),
        ("我查看公告及候车厅", False),
        ("规则问题：SAN 如何扣减？", False),
    ],
)
def test_readonly_inputs_and_compound_first_target_encounter(
    client, encounter_game, text, explicit
):
    g = encounter_game
    body = {"text": text, "client_request_id": str(uuid4())}
    if explicit:
        body["target_entity_id"] = g["entity"]
    if "规则问题" in text:
        body["category"] = "rule_question"
    ok(player(client, g, "/actions", body))
    cycle = wait_cycle(client, g)
    if "及候车厅" in text:
        # The KP selected the first executable object; two named objects do not
        # by themselves make that selection ambiguous or require host approval.
        assert cycle["status"] == "waiting_for_roll"
        checks = ok(client.get(g["prefix"] + "/checks"))
        assert len(checks) == 1 and checks[0]["sanity"]["origin"] == "automatic"
    else:
        assert cycle["status"] == "completed"
        assert not ok(client.get(g["prefix"] + "/checks"))


def test_push_cannot_be_replaced_by_new_fingerprint():
    from test_check_narration_policy import policy_case

    from app.agents.check_policy import CheckPolicyEvaluator

    facts, intent, proposal = policy_case()
    facts.completed_checks = [
        {
            "target_member_id": str(proposal.target_member_id),
            "policy_target_id": "door",
            "policy_fingerprint": "old",
            "cycle_id": "old",
            "settlement": {"push_requested": True},
        }
    ]
    assert CheckPolicyEvaluator().evaluate(proposal, intent, facts).code == "push_exhausted"


@pytest.mark.parametrize("natural", [False, True])
def test_automatic_san_model_omission_and_old_information(client, encounter_game, natural):
    g = encounter_game
    body = {"text": "我查看公告", "client_request_id": str(uuid4())}
    if not natural:
        body["target_entity_id"] = g["entity"]
    ok(player(client, g, "/actions", body))
    cycle = wait_cycle(client, g)
    assert cycle["status"] == "waiting_for_roll", cycle
    checks = ok(client.get(g["prefix"] + "/checks"))
    assert len(checks) == 1 and checks[0]["target_member_id"] == g["player"]
    snapshot = save(client, g)
    roll(client, g, checks[0], "san")
    assert wait_cycle(client, g)["status"] == "completed"
    assert current(client, g)["san"] == 49
    restore(client, g, snapshot)
    roll(client, g, checks[0], "san")
    assert wait_cycle(client, g)["status"] == "completed"
    for text in ("我回顾公告", "我查看公告"):
        body.update(text=text, client_request_id=str(uuid4()))
        ok(player(client, g, "/actions", body))
        assert wait_cycle(client, g)["status"] == "completed"
    assert len(ok(client.get(g["prefix"] + "/checks"))) == 1
    if not natural:
        assert client.post(
            g["prefix"] + "/sanity/encounters",
            json={
                "target_member_id": g["player"],
                "entity_id": g["entity"],
                "effect_id": "automatic",
                "source_event_seq": cycle["state"]["triggering_event_seq"],
                "encounter_confirmed": True,
                "repeat_confirmed": True,
                "reason": "重复点击同一遭遇",
            },
        ).is_success  # Same source shares the automatic receipt.
        latest = ok(client.get(g["prefix"] + "/agent-cycle"))
        assert (
            client.post(
                g["prefix"] + "/sanity/encounters",
                json={
                    "target_member_id": g["player"],
                    "entity_id": g["entity"],
                    "effect_id": "automatic",
                    "source_event_seq": latest["state"]["triggering_event_seq"],
                    "encounter_confirmed": True,
                    "repeat_confirmed": True,
                    "reason": "新行动不能冒充新遭遇",
                },
            ).status_code
            == 409
        )


def test_reveal_then_automatic_san(client, encounter_game):
    g = encounter_game
    ok(
        player(
            client,
            g,
            "/actions",
            {"text": "我查看候车厅内的环境", "client_request_id": str(uuid4())},
        )
    )
    cycle = wait_cycle(client, g)
    assert cycle["status"] == "waiting_for_roll", cycle
    check = ok(client.get(g["prefix"] + "/checks"))[-1]
    assert check["sanity"]["effect"]["trigger"] == "entity_revealed"
    roll(client, g, check, "san")
    assert wait_cycle(client, g)["status"] == "completed"


def test_check_reveal_san_final_order_and_save(client, encounter_game, monkeypatch):
    g = encounter_game
    room = ok(client.get(g["prefix"]))
    ok(
        client.patch(
            g["prefix"] + "/check-rules",
            json={"expected_revision": room["revision"], "luck_spending": True},
        )
    )
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 4, 20))
    ok(
        player(
            client,
            g,
            "/actions",
            {"text": "我调查公告的隐蔽细节", "client_request_id": str(uuid4())},
        )
    )
    assert wait_cycle(client, g)["status"] == "waiting_for_roll"
    check = ok(client.get(g["prefix"] + "/checks"))[-1]
    assert not check.get("sanity")
    ok(player(client, g, f"/checks/{check['id']}/roll", {}))
    assert not any(e["id"] == g["entity"] for e in ok(client.get(g["prefix"] + "/public-entities")))
    chosen = save(client, g)
    with monkeypatch.context() as patch:
        patch.setattr(client.app.state.agent_service.runtime, "schedule", lambda *a, **k: None)
        ok(player(client, g, f"/checks/{check['id']}/choice", {"operation": "luck", "spend": 15}))
        paid = save(client, g)
    restore(client, g, paid)
    cycle = wait_cycle(client, g)
    assert cycle["status"] == "waiting_for_roll", cycle
    san = next(c for c in ok(client.get(g["prefix"] + "/checks")) if c.get("sanity"))
    assert san["sanity"]["origin"] == "automatic"
    awaiting_san = save(client, g)
    roll(client, g, san, "san")
    assert wait_cycle(client, g)["status"] == "completed"
    restore(client, g, awaiting_san)
    roll(client, g, san, "san")
    assert wait_cycle(client, g)["status"] == "completed"
    restore(client, g, paid)
    assert wait_cycle(client, g)["status"] == "waiting_for_roll"
    roll(client, g, san, "san")
    assert wait_cycle(client, g)["status"] == "completed"
    assert current(client, g)["san"] == 49
    restore(client, g, chosen)
    ok(player(client, g, f"/checks/{check['id']}/choice", {"operation": "accept"}))
    assert wait_cycle(client, g)["status"] == "completed"
    assert current(client, g)["san"] == 50


@pytest.mark.parametrize("approve", [True, False])
def test_review_uncertain_encounter_and_snapshot(client, encounter_game, approve):
    g = encounter_game
    ok(
        player(
            client,
            g,
            "/actions",
            {
                "text": "我查看公告",
                "target_entity_id": g["entity"],
                "client_request_id": str(uuid4()),
            },
        )
    )
    cycle = wait_cycle(client, g)
    assert cycle["state"]["wait_reason"] == "sanity_encounter_review", cycle
    snapshot = save(client, g)
    item = cycle["state"]["encounter_queue"][0]
    body = {k: item[k] for k in ("source_event_seq", "entity_id", "effect_id")}
    body.update(approve=approve, target_member_ids=[g["player"]], reason="主机核实实际目睹条件")
    assert player(client, g, "/sanity/encounter-review", body).status_code == 403
    ok(client.post(g["prefix"] + "/sanity/encounter-review", json=body))
    assert wait_cycle(client, g)["status"] == ("waiting_for_roll" if approve else "completed")
    restore(client, g, snapshot)
    assert wait_cycle(client, g)["state"]["wait_reason"] == "sanity_encounter_review"
    ok(client.post(g["prefix"] + "/sanity/encounter-review", json=body))
    assert wait_cycle(client, g)["status"] == ("waiting_for_roll" if approve else "completed")
