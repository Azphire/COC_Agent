"""Conversation acceptance with Fake; dice, permission and recovery are real services."""

import json
from uuid import uuid4

import pytest
from test_agent_runtime import accept_original, game, submit, wait_cycle  # noqa: F401
from test_check_narration_policy import policy_case
from test_rooms import headers, lobby, ok  # noqa: F401

from app.agents.check_policy import CheckPolicyEvaluator
from app.agents.model import FakeModelAdapter


def conversational(messages, kwargs):
    c = json.loads(messages[-1]["content"])
    schema = kwargs["response_schema"].__name__
    if schema == "PushReview":
        return {
            "approve": True,
            "reason": "把光源换到侧面并花时间逐段核对是额外投入",
            "consequence": {
                "kind": "time",
                "minutes": 12,
                "description": "若仍失败，会额外耗去十二分钟",
            },
        }
    if schema == "KeeperNarration":
        return {"public_narration": "我明白你的意思。先留在这里，继续说吧。"}
    if schema == "TeammateDecision":
        return {
            "mode": "act",
            "action_type": "observe",
            "action_text": "我负责辨认脚步的方向，大家先留在这里。",
            "related_player_action_seq": c["triggering_action"]["seq"],
            "confidence": 1,
        }
    if schema != "KeeperPlan":
        return {"content": "已记录公开对话；推测仍是推测。"}
    ids = c["action_identifiers"]
    text = c["triggering_action"]["payload"]["text"]
    plan = {
        "plan_id": ids["plan_id"],
        "cycle_id": ids["cycle_id"],
        "current_scene_id": ids["current_scene_id"],
        "expected_navigation_revision": ids["expected_navigation_revision"],
        "parsed_intent": {
            "type": "converse",
            "evidence_quote": text,
            "confidence": 1,
            "actor_member_id": ids["actor_member_id"],
            "actor_character_slot_id": ids["actor_character_slot_id"],
        },
    }
    if text.startswith("等一下"):
        plan["pending_action"] = "replace"
    elif text.startswith("算了"):
        plan["pending_action"] = "withdraw"
    elif text.startswith("弄清后"):
        plan["pending_action"] = "defer"
    if "辨认" in text or text.startswith("等一下"):
        plan["parsed_intent"]["type"] = "investigate"
        plan["proposed_check"] = {
            "target_member_id": ids["actor_member_id"],
            "name": "listen",
            "reason": "听一听脚步来自哪里",
            "target_entity_id": ids["current_scene_id"],
            "necessity": "required",
            "rule_topic_id": "coc7.skill_check",
            "purpose": "辨认脚步方向",
            "method": "靠近听" if text.startswith("等一下") else "原地听",
            "uncertainty": "周围有回声",
            "success_effect": "分辨方向",
            "failure_consequence": "这次无法辨别方向",
        }
    if text.startswith("队友"):
        member = next(
            mid for mid, name in c["current_participants"]["members"].items() if "队友" in name
        )
        plan["addressed_member_id"] = member
        plan["parsed_intent"].update(target_id=member, target_kind="member")
    return plan


def install(client):
    adapter = FakeModelAdapter(responder=conversational)
    client.app.state.agent_service.model.adapter = adapter
    return adapter


def test_public_salutations_resolve_without_full_titles_and_keep_ambiguity():
    from app.agents.conversation import addressed_targets

    people = [
        {"id": "a", "title": "许小禾", "type": "npc"},
        {"id": "b", "title": "沈砚", "type": "member"},
    ]
    assert list(addressed_targets("小禾，冷不冷？", people)) == ["a"]
    assert list(addressed_targets("沈砚，帮我按住纸。", people)) == ["b"]
    assert addressed_targets("陌生人，过来。", people) == {}
    people.append({"id": "c", "title": "张小禾", "type": "npc"})
    assert len(addressed_targets("小禾，过来。", people)) == 2


def pending(client, game):  # noqa: F811
    return next(c for c in ok(client.get(game["prefix"] + "/checks")) if c["status"] == "pending")


def test_npc_profile_is_not_published_as_a_spoken_reply(client, game):  # noqa: F811
    used = []

    def response(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            c = json.loads(messages[-1]["content"])
            claim = next(x for x in c["PUBLIC_CLAIM_OPTIONS"] if "caretaker" in x["entity_ids"])
            used.append(claim)
            return {
                "public_narration": "管理员等你继续说。",
                "grounded_claims": [claim],
                "npc_speech": None,
            }
        return conversational(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "林先生，我想和你聊聊。"))
    assert wait_cycle(client, game)["status"] == "completed"
    assert used
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert not any(e["type"] == "npc.spoke" for e in events)


def finish_roll(client, game, check):  # noqa: F811
    token = headers(game["remote"]["member_token"])
    ok(client.post(game["prefix"] + f"/checks/{check['id']}/roll", json={}, headers=token))
    accept_original(client, game, check["id"])
    return wait_cycle(client, game, timeout=45)


@pytest.mark.parametrize("kind", ["observe", "converse", "move", "use_item"])
def test_kp_can_call_contextual_check_without_risk_words(kind):
    facts, intent, p = policy_case()
    facts.raw_text = "我试着让他相信我"
    intent.type = kind
    p.clue_id = None
    p.rule_topic_id = "coc7.skill_check"
    p.purpose, p.method = "取得信任", "解释来意"
    facts.revealed_entity_ids.add("door")
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed
    p.target_member_id = uuid4()
    assert not CheckPolicyEvaluator().evaluate(p, intent, facts).allowed


def test_chat_while_waiting_replacement_and_stale_roll(client, game):  # noqa: F811
    adapter = install(client)
    ok(submit(client, game, "我想辨认那阵脚步"))
    first_cycle = wait_cycle(client, game, timeout=45)
    check = pending(client, game)
    ok(submit(client, game, "这个数值怎么算？"))
    resumed = wait_cycle(client, game, timeout=45)
    assert resumed["id"] == first_cycle["id"] and pending(client, game)["id"] == check["id"]
    ok(submit(client, game, "等一下，我靠近些再听"))
    changed = wait_cycle(client, game, timeout=45)
    replacement = pending(client, game)
    assert replacement["id"] != check["id"] and changed["id"] != first_cycle["id"]
    assert (
        client.post(
            game["prefix"] + f"/checks/{check['id']}/roll",
            json={},
            headers=headers(game["remote"]["member_token"]),
        ).status_code
        == 409
    )
    result = finish_roll(client, game, replacement)
    assert result["status"] == "completed", result
    assert adapter.max_active == 1


def test_dependent_action_resumes_and_direct_teammate_is_adjudicated(client, game):  # noqa: F811
    install(client)
    ok(submit(client, game, "我想辨认脚步"))
    wait_cycle(client, game, timeout=45)
    check = pending(client, game)
    ok(submit(client, game, "弄清后我再告诉大家"))
    wait_cycle(client, game, timeout=45)
    assert pending(client, game)["id"] == check["id"]
    assert finish_roll(client, game, check)["status"] == "completed"
    ok(submit(client, game, "队友，你能帮我听听吗？"))
    cycle = wait_cycle(client, game, timeout=45)
    assert cycle["status"] == "completed", cycle
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    proposals = [e for e in events if e["type"] == "agent.action_proposed"]
    assert len(proposals) == 1
    checks = ok(client.get(game["prefix"] + "/checks"))
    assert any(c["target_member_id"] == game["agent"] and c["status"] == "resolved" for c in checks)


def test_same_item_new_purpose_but_same_task_cannot_refresh_push():
    facts, intent, p = policy_case()
    p.purpose, p.method = "辨认痕迹", "仔细查看"
    old = {
        "id": "old",
        "target_member_id": facts.actor_member_id,
        "policy_target_id": "door",
        "attempt_purpose": p.purpose,
        "settlement": {"push_requested": True},
        "cycle_id": "earlier",
    }
    facts.completed_checks = [old]
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).code == "push_exhausted"
    p.purpose = "用门板挡风"
    p.clue_id, p.rule_topic_id = None, "coc7.skill_check"
    p.method = "把板子立起来"
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed
    p.continues_check_id = "old"
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).code == "push_exhausted"


def test_same_task_cannot_refresh_dice_by_changing_skill():
    from app.agents.check_policy import state_fingerprint

    facts, intent, p = policy_case()
    p.purpose, p.method = "辨清门外的人数", "隔门听动静"
    facts.completed_checks = [
        {
            "id": "previous",
            "target_member_id": facts.actor_member_id,
            "kind": "skill",
            "name": "different_skill",
            "policy_target_id": "door",
            "policy_fingerprint": state_fingerprint(facts),
            "cycle_id": "previous-cycle",
            "attempt_purpose": p.purpose,
            "attempt_method": p.method,
        }
    ]
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).code == "repeat_unchanged"


def test_compound_action_recipient_is_distinct_from_check_target():
    facts, intent, p = policy_case()
    facts.member_ids = {"teammate"}
    intent.type, intent.target_id, intent.target_kind = "converse", "teammate", "member"
    p.target_entity_id, p.clue_id = "door", None
    p.rule_topic_id = "coc7.skill_check"
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed
    intent.target_id = "unknown-member"
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).code == "intent_target_mismatch"


def test_teammate_cannot_hide_party_movement_as_an_observation():
    from test_action_adjudication import facts_for, plan_for

    from app.agents.action_policy import ActionPolicyValidator
    from app.agents.schemas import PlannedTool

    facts = facts_for("我看看门口，带大家过去")
    facts.can_move_party = False
    plan = plan_for(facts.raw_text, "observe")
    result = ActionPolicyValidator().validate(
        plan.parsed_intent,
        plan,
        facts,
        [PlannedTool(name="update_scene", arguments={"scene_id": "next"})],
    )
    assert result.status == "rejected"
    assert result.rejected_actions[0].code == "permission_denied"


def test_explicit_rules_discussion_cannot_become_world_action(client, game):  # noqa: F811
    # Deliberately keep the erroneous model check; server honors the OOC instruction.
    install(client)
    ok(submit(client, game, "KP，我问的是规则：辨认脚步失败能换句话再掷骰吗？"))
    cycle = wait_cycle(client, game, timeout=45)
    assert cycle["status"] == "completed", cycle
    assert ok(client.get(game["prefix"] + "/checks")) == []
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert not any(
        e["type"] in {"clue.revealed", "scene.updated", "sanity.check_requested"}
        for e in events
        if e["payload"].get("cycle_id") == cycle["id"]
    )


def test_pre_roll_restore_does_not_make_fixed_dice_withdrawable(
    client, game, monkeypatch  # noqa: F811
):
    import asyncio
    import threading

    from test_batch10 import FixedRandom

    from app.dice.service import DiceService

    install(client)
    # A successful roll resumes the graph immediately. Hold it at the next
    # node so pause/load deterministically races with a live continuation.
    client.app.state.room_service.dice = DiceService(FixedRandom(7, 10))
    ok(submit(client, game, "我想辨认脚步"))
    wait_cycle(client, game)
    check = pending(client, game)
    saved = ok(client.post(game["prefix"] + "/snapshots", json={"name": "骰点固定前"}))["snapshot"]
    runtime = client.app.state.agent_service.runtime
    original_node = runtime.node
    reached = threading.Event()

    async def hold_resume(state, name):
        if name == "wait_for_human_roll" and not reached.is_set():
            reached.set()
            await asyncio.Event().wait()
        return await original_node(state, name)

    monkeypatch.setattr(runtime, "node", hold_resume)
    path = game["prefix"] + f"/checks/{check['id']}/roll"
    original = ok(client.post(path, json={}, headers=headers(game["remote"]["member_token"])))[
        "check"
    ]
    assert reached.wait(5), "successful roll did not resume the waiting node"
    ok(client.post(game["prefix"] + "/pause"))
    assert not runtime.tasks
    ok(client.post(game["prefix"] + f"/snapshots/{saved['id']}/load"))
    ok(client.post(game["prefix"] + "/resume"))
    ok(submit(client, game, "算了，撤回这次尝试"))
    wait_cycle(client, game)
    assert pending(client, game)["id"] == check["id"]
    repeated = ok(client.post(path, json={}, headers=headers(game["remote"]["member_token"])))[
        "check"
    ]
    assert repeated["dice"] == original["dice"]
    accept_original(client, game, check["id"])
    wait_cycle(client, game)
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "check.dice_fixed" for e in events) == 1
    assert not any(e["type"] == "agent.run_failed" for e in events)


def test_kp_push_approval_player_consent_and_fixed_result(client, game):  # noqa: F811
    from test_batch10 import FixedRandom

    from app.dice.service import DiceService

    install(client)
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 8, 10, 9))
    ok(submit(client, game, "我想辨认脚步方向"))
    wait_cycle(client, game, timeout=45)
    check = pending(client, game)
    token = headers(game["remote"]["member_token"])
    path = game["prefix"] + f"/checks/{check['id']}"
    raw = ok(client.post(path + "/roll", json={}, headers=token))["check"]
    assert raw["settlement"]["original_result"]["total"] == 80
    body = {"operation": "push", "effort": "换到侧面，逐段听，愿意多花时间"}
    ok(client.post(path + "/choice", json=body, headers=token))
    wait_cycle(client, game, timeout=45)
    reviewed = pending(client, game)
    assert reviewed["settlement"]["stage"] == "push_roll"
    assert not reviewed["settlement"].get("push_dice")
    ok(client.post(path + "/choice", json=body, headers=token))
    assert client.post(path + "/push-roll", json={}).status_code == 403
    first = ok(client.post(path + "/push-roll", json={}, headers=token))["check"]
    second = ok(client.post(path + "/push-roll", json={}, headers=token))["check"]
    assert first["settlement"]["push_result"] == second["settlement"]["push_result"]
    assert first["settlement"]["push_result"]["total"] == 90
    assert first["settlement"]["consequence_status"] == "applied"
    assert wait_cycle(client, game, timeout=45)["status"] == "completed"
    assert ok(client.get(game["prefix"]))["session_state"]["game_minute"] == 12


def test_queued_message_and_wait_restore_without_duplicate_dice(
    client,
    game,  # noqa: F811
    character_settings,
    monkeypatch,
):  # noqa: F811
    from fastapi.testclient import TestClient
    from test_batch10 import restore, save

    from app.main import create_app

    install(client)
    ok(submit(client, game, "我想辨认脚步"))
    wait_cycle(client, game, timeout=45)
    check = pending(client, game)
    runtime = client.app.state.agent_service.runtime
    monkeypatch.setattr(runtime, "schedule", lambda room_id: None)
    request_id = str(uuid4())
    first = ok(submit(client, game, "这个数值怎么算？", request_id))
    second = ok(submit(client, game, "这个数值怎么算？", request_id))
    assert first["event"]["seq"] == second["event"]["seq"]
    assert (
        client.post(
            game["prefix"] + f"/checks/{check['id']}/roll",
            json={},
            headers=headers(game["remote"]["member_token"]),
        ).status_code
        == 409
    )
    snapshot = save(client, game)
    client.__exit__(None, None, None)
    app = create_app(character_settings)
    app.state.agent_model_adapter = FakeModelAdapter(responder=conversational)
    with TestClient(
        app, headers=headers(character_settings.host_admin_token.get_secret_value())
    ) as second_client:
        restore(second_client, game, snapshot)
        wait_cycle(second_client, game, timeout=45)
        assert pending(second_client, game)["id"] == check["id"]
        assert finish_roll(second_client, game, check)["status"] == "completed"
        events = ok(second_client.get(game["prefix"] + "/events"))["events"]
        assert sum(e["type"] == "check.dice_fixed" for e in events) == 1
