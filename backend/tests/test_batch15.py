"""Compound rules and real room/dice/decision/recovery paths; model plans are Fake."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_batch10 import FixedRandom, restore, save
from test_module_preparation import approve_opening, preparation  # noqa: F401
from test_rooms import character, headers, join, lobby, ok, prepare  # noqa: F401

from app.agents.model import FakeModelAdapter
from app.agents.schemas import CheckRequest
from app.dice.service import DiceService
from app.rules.check_options import can_push, luck_options
from app.rules.checks import judge
from app.rules.compound import check_result, opposed_result, result_signature


@pytest.mark.parametrize(
    "values,totals,winner,comparison",
    [
        ((60, 80), (11, 35), 0, "level"),
        ((60, 80), (35, 70), 1, "value"),
        ((60, 60), (35, 59), None, "stalemate"),
        ((60, 80), (90, 95), 1, "value"),
        ((60, 60), (90, 91), None, "stalemate"),
        ((60, 80), (100, 95), 1, "level"),
        ((60, 80), (1, 2), 0, "level"),
        ((60, 60), (100, 100), None, "stalemate"),
    ],
)
def test_opposed_comparison(values, totals, winner, comparison):
    sides = [
        {"participant_id": str(i), "value": value, "result": judge(value, "regular", total)}
        for i, (value, total) in enumerate(zip(values, totals))
    ]
    result = opposed_result(sides)
    assert result["winner"] == winner
    assert result["comparison"] == comparison
    assert result["passed"] == (winner == 0)
    assert result["both_failed"] == all(not s["result"]["passed"] for s in sides)


def combined_document(requirement="all", total=40):
    d = {
        "kind": "skill",
        "name": "spot_hidden",
        "value": 60,
        "difficulty": "regular",
        "combined": {"name": "listen", "difficulty": "regular", "requirement": requirement},
        "compound": {
            "components": [
                {"name": "spot_hidden", "value": 60, "difficulty": "regular"},
                {"name": "listen", "value": 30, "difficulty": "regular"},
            ]
        },
    }
    d["result"] = check_result(d, total)
    return d


@pytest.mark.parametrize("requirement,passed,push", [("any", True, False), ("all", False, True)])
def test_combined_partial_and_single_luck_cost(requirement, passed, push):
    d = combined_document(requirement)
    assert d["result"]["passed"] is passed
    assert d["result"]["partial_success"]
    assert can_push(d) is push
    choice = next(o for o in luck_options(d, 15, True) if o["spend"] == 10)
    assert choice["result"]["passed"]
    assert [c["total"] for c in choice["result"]["components"]] == [30, 30]


@pytest.mark.parametrize("total", [1, 96, 100])
def test_combined_protects_every_component_extreme(total):
    d = combined_document("any", total)
    assert not can_push(d)
    assert not luck_options(d, 99, True)


def test_combined_luck_keeps_changes_to_the_secondary_skill():
    d = combined_document("any")
    d["value"] = d["compound"]["components"][0]["value"] = 300
    d["result"] = check_result(d, 40)
    selected = next(o for o in luck_options(d, 20, True) if o["spend"] == 10)["result"]
    assert d["result"]["level"] == selected["level"] == "extreme"
    assert d["result"]["passed"] == selected["passed"] is True
    assert result_signature(d["result"]) != result_signature(selected)


@pytest.mark.parametrize("variant", ["missing", "hidden", "elsewhere", "foreign_member"])
def test_unavailable_opponent_is_rejected_by_policy(variant):
    from test_check_narration_policy import policy_case

    from app.agents.check_policy import CheckPolicyEvaluator
    from app.rules.compound import OpposedCheck

    facts, intent, proposal = policy_case()
    if variant == "foreign_member":
        proposal.opposed = OpposedCheck(opponent_member_id=str(uuid4()), name="listen")
    else:
        proposal.opposed = OpposedCheck(opponent_npc_id="npc", name="listen")
        facts.approved_entities["npc"] = {"type": "npc", "check_stats": {"skills": {"listen": 40}}}
        if variant != "hidden":
            facts.visible_entity_ids.add("npc")
        if variant != "elsewhere":
            facts.local_entity_ids.add("npc")
        if variant == "missing":
            facts.approved_entities["npc"]["check_stats"] = None
    assert not CheckPolicyEvaluator().evaluate(proposal, intent, facts).allowed


@pytest.mark.parametrize("mode", ["any", "all", "opposed"])
def test_compound_cannot_silently_relax_an_ordinary_clue_gate(mode):
    from test_check_narration_policy import policy_case

    from app.agents.check_policy import CheckPolicyEvaluator
    from app.rules.compound import CombinedCheck, OpposedCheck

    facts, intent, proposal = policy_case()
    actor = facts.characters[facts.actor_member_id]
    actor["skill_values"]["listen"] = 80
    if mode == "opposed":
        other = str(uuid4())
        facts.characters[other] = actor
        proposal.opposed = OpposedCheck(opponent_member_id=other, name="listen")
    else:
        proposal.combined = CombinedCheck(name="listen", requirement=mode)
    decision = CheckPolicyEvaluator().evaluate(proposal, intent, facts)
    if mode == "all":
        assert decision.allowed  # This still requires the configured first skill to pass.
    else:
        assert not decision.allowed and decision.code == "check_mismatch"
        proposal.alternative_basis = "采用另一条证据路径辨识痕迹，替代原先只观察痕迹的条件"
        assert CheckPolicyEvaluator().evaluate(proposal, intent, facts).allowed


def test_compound_exclusion_and_repeated_options():
    d = combined_document()
    for settlement in ({"luck_spent": 1}, {"push_requested": True}):
        d["settlement"] = settlement
        assert not can_push(d) and not luck_options(d, 99, True)
    d = {
        "kind": "skill",
        "name": "listen",
        "value": 30,
        "difficulty": "regular",
        "opposed": {"opponent_member_id": str(uuid4()), "name": "spot_hidden"},
        "result": judge(30, "regular", 40),
    }
    assert luck_options(d, 20, True) and not can_push(d)


@pytest.mark.parametrize(
    "extra",
    [
        {"opposed": {"opponent_member_id": "x", "opponent_npc_id": "y", "name": "listen"}},
        {"opposed": {"name": "listen"}},
        {"combined": {"name": "listen"}},
        {"combined": {"name": "spot_hidden", "requirement": "all"}},
        {"combined": {"name": "listen", "requirement": "all"}, "kind": "attribute"},
        {"opposed": {"opponent_member_id": "x", "name": "dodge"}},
        {"opposed": {"opponent_member_id": "x", "name": "handgun"}},
        {"opposed": {"opponent_member_id": "x", "name": "listen"}, "difficulty": "hard"},
        {"opposed": {"opponent_member_id": "x", "name": "listen", "value": 99}},
        {"combined": {"name": "listen", "requirement": "any"}, "combat": True},
    ],
)
def test_invalid_compound_contract(extra):
    with pytest.raises(ValidationError):
        CheckRequest(target_member_id=uuid4(), name="spot_hidden", reason="测试", **extra)


def test_generation_contract_constrains_compound_modes_and_keeps_ordinary_checks():
    from pydantic import TypeAdapter

    from app.agents.check_policy import CheckProposal
    from app.agents.compound_generation import proposal_contract

    mid, other = str(uuid4()), str(uuid4())
    context = {
        "action_identifiers": {"actor_member_id": mid},
        "characters": [
            {
                "member_id": mid,
                "effective_attributes": {"str": 60},
                "skill_values": {"spot_hidden": 60, "listen": 30, "brawl": 25, "handgun": 20},
            }
        ],
        "compound_candidates": {"members": [other], "npcs": []},
    }
    adapter = TypeAdapter(proposal_contract(CheckProposal, context))
    ordinary = {"target_member_id": mid, "name": "brawl", "reason": "原普通检定"}
    assert adapter.validate_python(ordinary).name == "brawl"
    valid = {
        "target_member_id": mid,
        "name": "str",
        "kind": "attribute",
        "reason": "比力气",
        "opposed": {"opponent_member_id": other, "kind": "attribute", "name": "str"},
    }
    assert adapter.validate_python(valid).opposed.name == "str"
    for changes in (
        {"difficulty": "hard"},
        {"name": "brawl", "kind": "skill"},
        {"opposed": {"opponent_member_id": str(uuid4()), "name": "str", "kind": "attribute"}},
        {"combined": {"name": "listen", "requirement": "all"}},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python({**valid, **changes})
    assert adapter.validate_python(
        {
            "target_member_id": mid,
            "name": "spot_hidden",
            "kind": "skill",
            "reason": "定位声音",
            "combined": {"name": "listen", "requirement": "all"},
        }
    ).combined


def install(client, g, mode="opposed", opponent=None):
    def respond(messages, kwargs):
        c = json.loads(messages[-1]["content"])
        schema = kwargs["response_schema"].__name__
        if schema == "KeeperNarration":
            return {"public_narration": "这次尝试的结果已经确定，你们可以决定下一步。"}
        if schema == "TeammateDecision":
            return {"mode": "pass", "reason": "等待伙伴决定", "confidence": 1}
        if schema == "PushReview":
            return {
                "approve": True,
                "reason": "增加了核对时间",
                "consequence": {"kind": "time", "minutes": 8, "description": "失败额外耗时八分钟"},
            }
        if schema != "KeeperPlan":
            return {"content": "已经记录此次尝试。"}
        ids = c["action_identifiers"]
        proposal = {
            "target_member_id": ids["actor_member_id"],
            "kind": "skill",
            "name": "spot_hidden",
            "reason": "本次行动有实际阻力",
            "target_entity_id": ids["current_scene_id"],
            "necessity": "required",
            "rule_topic_id": "coc7.skill_check",
            "purpose": "找到线索",
            "method": "边观察边分辨脚步",
            "uncertainty": "视线受阻且有杂音",
            "success_effect": "完成这次尝试",
            "failure_consequence": "这次无法辨认",
        }
        if mode == "opposed":
            proposal["opposed"] = {
                "opponent_member_id": opponent or g["agent"],
                "kind": "attribute",
                "name": "dex",
            }
        elif mode == "npc":
            proposal["opposed"] = {"opponent_npc_id": opponent, "kind": "skill", "name": "listen"}
        else:
            proposal["combined"] = {"name": "listen", "requirement": mode}
        return {
            "plan_id": ids["plan_id"],
            "cycle_id": ids["cycle_id"],
            "current_scene_id": ids["current_scene_id"],
            "parsed_intent": {
                "type": "investigate",
                "evidence_quote": c["triggering_action"]["payload"]["text"],
                "confidence": 1,
                "actor_member_id": ids["actor_member_id"],
                "actor_character_slot_id": ids["actor_character_slot_id"],
            },
            "proposed_check": proposal,
        }

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=respond)


def player(client, g, path, body):
    return client.post(g["prefix"] + path, json=body, headers=headers(g["remote"]["member_token"]))


def last(client, g):
    return ok(client.get(g["prefix"] + "/checks"))[-1]


def begin(client, g, mode, *dice, luck=True, opponent=None):
    install(client, g, mode, opponent)
    room = ok(client.get(g["prefix"]))
    ok(
        client.patch(
            g["prefix"] + "/check-rules",
            json={"luck_spending": luck, "expected_revision": room["revision"]},
        )
    )
    rng = FixedRandom(*dice)
    client.app.state.room_service.dice = DiceService(rng)
    ok(submit(client, g, "我靠近工作台，一边找痕迹一边听脚步。"))
    cycle = wait_cycle(client, g, timeout=30)
    assert cycle["status"] == "waiting_for_roll", cycle
    return last(client, g), rng


def test_opposed_ai_permissions_fixed_dice_choice_and_restore(client, game):  # noqa: F811
    g = game
    check, rng = begin(client, g, "opposed", 10, 4, 10, 8)
    path = f"/checks/{check['id']}"
    sides = check["compound"]["participants"]
    assert not sides[0]["dice"] and sides[1]["dice"]["selected"] == 40
    assert check["result"] is None and sides[1]["result"] is None
    one_rolled = save(client, g)
    assert (
        client.post(g["prefix"] + path + "/roll", json={"participant_id": g["player"]}).status_code
        == 403
    )
    assert player(client, g, path + "/roll", {"participant_id": g["agent"]}).status_code == 403
    ok(player(client, g, path + "/roll", {}))
    check = last(client, g)
    assert check["compound"]["stage"] == "choice" and check["result"] is None
    assert len(rng.used) == 4
    ok(player(client, g, path + "/roll", {}))
    assert len(rng.used) == 4
    assert (
        player(client, g, path + "/choice", {"operation": "push", "effort": "再试"}).status_code
        == 422
    )
    choices = save(client, g)
    option = last(client, g)["compound"]["participants"][0]["options"]["luck"][0]
    body = {"operation": "luck", "spend": option["spend"]}
    ok(player(client, g, path + "/choice", body))
    assert wait_cycle(client, g, timeout=30)["status"] == "completed"
    final = last(client, g)
    assert final["status"] == "resolved"
    before = ok(client.get(g["prefix"]))["session_state"]
    ok(player(client, g, path + "/choice", body))
    assert ok(client.get(g["prefix"]))["session_state"] == before
    assert player(client, g, path + "/choice", {"operation": "accept"}).status_code == 409
    completed = save(client, g)
    for checkpoint in (one_rolled, choices):
        restore(client, g, checkpoint)
        if checkpoint == one_rolled:
            ok(player(client, g, path + "/roll", {}))
        ok(player(client, g, path + "/choice", body))
        assert wait_cycle(client, g, timeout=30)["status"] == "completed"
        assert last(client, g)["result"] == final["result"]
        assert ok(client.get(g["prefix"]))["session_state"] == before
        assert len(rng.used) == 4
    restore(client, g, completed)
    assert last(client, g)["status"] == "resolved"
    assert len(rng.used) == 4


@pytest.mark.parametrize("mode", ["any", "all"])
def test_combined_api_one_dice_and_single_deduction(client, game, mode):  # noqa: F811
    g = game
    check, rng = begin(client, g, mode, 10, 8)
    path = f"/checks/{check['id']}"
    ok(player(client, g, path + "/roll", {}))
    check = last(client, g)
    raw = check["settlement"]["original_result"]
    assert [r["total"] for r in raw["components"]] == [80, 80]
    options = check["options"]["luck"]
    selected = options[-1]
    saved = save(client, g)
    body = {"operation": "luck", "spend": selected["spend"]}
    ok(player(client, g, path + "/choice", body))
    assert wait_cycle(client, g, timeout=30)["status"] == "completed"
    final = last(client, g)
    assert final["settlement"]["luck_before"] - final["settlement"]["luck_after"] == body["spend"]
    assert final["result"]["total"] == 80 - body["spend"]
    assert [r["total"] for r in final["result"]["components"]] == [80 - body["spend"]] * 2
    ok(player(client, g, path + "/choice", body))
    restore(client, g, saved)
    ok(player(client, g, path + "/choice", body))
    assert wait_cycle(client, g, timeout=30)["status"] == "completed"
    assert last(client, g)["result"] == final["result"]
    assert len(rng.used) == 2


def test_combined_push_shares_second_roll_and_consequence(client, game):  # noqa: F811
    g = game
    check, rng = begin(client, g, "all", 10, 8, 10, 9)
    path = f"/checks/{check['id']}"
    ok(player(client, g, path + "/roll", {}))
    ok(player(client, g, path + "/choice", {"operation": "push", "effort": "多花时间分段复查"}))
    wait_cycle(client, g, timeout=30)
    assert last(client, g)["settlement"]["stage"] == "push_roll"
    saved = save(client, g)
    ok(player(client, g, path + "/push-roll", {}))
    assert wait_cycle(client, g, timeout=30)["status"] == "completed"
    final = last(client, g)
    assert [c["total"] for c in final["result"]["components"]] == [90, 90]
    assert final["settlement"]["consequence_status"] == "applied"
    assert player(client, g, path + "/choice", {"operation": "luck", "spend": 1}).status_code == 409
    restore(client, g, saved)
    ok(player(client, g, path + "/push-roll", {}))
    assert wait_cycle(client, g, timeout=30)["status"] == "completed"
    assert len(rng.used) == 4


def test_two_humans_sequential_choices_and_second_player_restore(client, lobby):  # noqa: F811
    g = lobby
    remote = join(client, g["created"]["invite_code"], "另一真人")
    mid = remote["room"]["self_member_id"]
    card = character(client, "另一真人角色")
    room = ok(client.post(g["prefix"] + "/character-slots", json={"character_id": card["id"]}))[
        "room"
    ]
    slot = next(s for s in room["character_slots"] if s["source_character_id"] == card["id"])
    ok(
        client.post(
            g["prefix"] + "/character-assignments", json={"slot_id": slot["id"], "member_id": mid}
        )
    )
    ok(
        client.post(
            g["prefix"] + "/ready", headers=headers(remote["member_token"]), json={"ready": True}
        )
    )
    g = game.__wrapped__(client, lobby)
    check, rng = begin(client, g, "opposed", 10, 8, 10, 9, opponent=mid)
    path = g["prefix"] + f"/checks/{check['id']}"

    def second(endpoint, body):
        return client.post(path + endpoint, headers=headers(remote["member_token"]), json=body)

    assert second("/choice", {"operation": "accept"}).status_code == 409
    ok(second("/roll", {}))
    assert last(client, g)["result"] is None
    assert last(client, g)["compound"]["stage"] == "rolling"
    assert len(rng.used) == 2
    ok(player(client, g, f"/checks/{check['id']}/roll", {}))
    assert second("/choice", {"operation": "luck", "spend": 1}).status_code == 409
    assert (
        client.post(
            path + "/choice", json={"participant_id": mid, "operation": "accept"}
        ).status_code
        == 403
    )
    assert (
        second("/choice", {"participant_id": g["player"], "operation": "accept"}).status_code == 403
    )
    ok(player(client, g, f"/checks/{check['id']}/choice", {"operation": "luck", "spend": 1}))
    c = last(client, g)
    assert c["result"] is None and c["compound"]["choice_index"] == 1
    assert c["compound"]["participants"][0]["settlement"]["luck_spent"] == 1
    saved = save(client, g)
    ok(second("/choice", {"operation": "luck", "spend": 1}))
    assert wait_cycle(client, g, timeout=30)["status"] == "completed"
    final = last(client, g)
    restore(client, g, saved)
    assert last(client, g)["compound"]["choice_index"] == 1
    ok(second("/choice", {"operation": "luck", "spend": 1}))
    assert wait_cycle(client, g, timeout=30)["status"] == "completed"
    assert last(client, g)["result"] == final["result"]
    assert len(rng.used) == 4


@pytest.mark.parametrize("stage", ["rolling", "choice", "final"])
def test_opposed_restart_saved_stage(client, game, character_settings, stage):  # noqa: F811
    from fastapi.testclient import TestClient

    from app.main import create_app

    g = game
    check, rng = begin(client, g, "opposed", 10, 4, 10, 8)
    path = f"/checks/{check['id']}"
    if stage == "rolling":
        saved = save(client, g)
    ok(player(client, g, path + "/roll", {}))
    if stage == "choice":
        saved = save(client, g)
    ok(player(client, g, path + "/choice", {"operation": "accept"}))
    assert wait_cycle(client, g, timeout=30)["status"] == "completed"
    if stage == "final":
        saved = save(client, g)
    final = last(client, g)
    client.__exit__(None, None, None)
    app = create_app(character_settings)
    with TestClient(
        app, headers=headers(character_settings.host_admin_token.get_secret_value())
    ) as second:
        install(second, g)
        no_new_dice = FixedRandom()
        second.app.state.room_service.dice = DiceService(no_new_dice)
        restore(second, g, saved)
        c = last(second, g)
        assert c["compound"]["stage"] == stage
        if stage == "rolling":
            ok(player(second, g, path + "/roll", {}))
        if stage != "final":
            ok(player(second, g, path + "/choice", {"operation": "accept"}))
        assert wait_cycle(second, g, timeout=30)["status"] == "completed"
        assert last(second, g)["result"] == final["result"]
        assert no_new_dice.used == [] and len(rng.used) == 4


def test_prepared_npc_frozen_values_and_private_provenance(client, lobby, preparation):  # noqa: F811
    npc = ok(
        client.post(
            "/api/module-entities",
            json={
                "preparation_id": preparation["prep"]["id"],
                "type": "npc",
                "title": "守卫",
                "public_summary": "守卫在当前大厅门边看守。",
                "initial_visibility": "revealed",
                "check_stats": {
                    "skills": {"listen": 45},
                    "source": "不可公开的准备数值来源",
                    "page": 3,
                },
            },
        )
    )
    preparation["entities"].append(npc)
    approved = approve_opening(client, preparation)
    p = lobby["prefix"]
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
    # A later preparation edit cannot affect the already-bound room.
    ok(client.post(f"/api/module-entities/{npc['id']}/draft"))
    ok(
        client.patch(
            f"/api/module-entities/{npc['id']}",
            json={"check_stats": {"skills": {"listen": 99}, "source": "后来准备的新值"}},
        )
    )
    check, rng = begin(client, lobby, "npc", 10, 4, 10, 6, luck=False, opponent=npc["id"])
    side = check["compound"]["participants"][1]
    assert side["controller"] == "npc" and side["value"] == 45
    public = client.get(p + "/checks", headers=headers(lobby["remote"]["member_token"]))
    assert "不可公开的准备数值来源" not in public.text
    assert "source_page" not in public.text
    path = f"/checks/{check['id']}"
    ok(player(client, lobby, path + "/roll", {}))
    assert wait_cycle(client, lobby, timeout=30)["status"] == "completed"
    assert last(client, lobby)["result"]["winner"] == 1
    assert len(rng.used) == 4


def test_missing_npc_values_are_rejected_before_roll(client, game):  # noqa: F811
    g = game
    install(client, g, "npc", "caretaker")
    rng = FixedRandom()
    client.app.state.room_service.dice = DiceService(rng)
    ok(submit(client, g, "我想绕过守卫的注意。"))
    # The generation contract now rejects this unavailable opponent before policy execution.
    assert wait_cycle(client, g, timeout=30)["status"] == "failed"
    assert ok(client.get(g["prefix"] + "/checks")) == []
    assert not rng.used
