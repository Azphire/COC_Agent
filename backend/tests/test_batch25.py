"""Small adversarial tests for acceptance, not replacement live-game evidence."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_batch25 import Batch25, select_model  # noqa: E402
from check_batch25_evidence import (  # noqa: E402
    check_result,
    continued_action,
    owned_item,
    preserved_summaries,
    turn_response,
)


@pytest.mark.parametrize("mode", ["plain", "moved", "changed", "missing"])
def test_summary_navigation_wrapper_preserves_exact_saved_body(mode):
    import json

    expected = {"original-summary": "真实失败与持有记录"}
    active = dict(expected)
    if mode in {"moved", "changed"}:
        active["original-summary"] = json.dumps(
            dict(
                content=expected["original-summary"] if mode == "moved" else "改成成功",
                current_scene_node_id="next-scene",
                heading_path=["next"],
            )
        )
    if mode == "missing":
        active = {}
    assert preserved_summaries(expected, expected, active) == (mode in {"plain", "moved"})


def investigation():
    dice = {"selected": 20, "roll_record": {"id": "original", "source": "system"}}
    result = {"passed": True, "level": "hard"}
    check = dict(
        id="check", status="resolved", dice=dice, result=result, settlement={"original_dice": dice}
    )
    before = dict(
        room={"revision": 10},
        check={"clue_id": "target", "policy_target_id": "target"},
        plan={
            "validation": {
                "approved_actions": [
                    {"tool": {"name": "request_skill_check", "arguments": {"clue_id": "target"}}},
                    {"tool": {"name": "reveal_entity", "arguments": {"entity_id": "target"}}},
                ]
            }
        },
    )
    events = [
        dict(seq=10, type="check.requested", payload={"id": "check"}),
        dict(seq=11, type="check.dice_fixed", payload={"check_id": "check", "dice": dice}),
        dict(seq=12, type="check.resolved", payload={"id": "check", "result": result}),
        dict(seq=13, type="entity.revealed", payload={"id": "target"}),
    ]
    return check, before, events


@pytest.mark.parametrize(
    "failure",
    [None, "missing_result", "missing_reveal", "duplicate_roll", "unbound", "wrong_target"],
)
def test_completed_cycle_does_not_prove_investigation(failure):
    check, before, events = investigation()
    if failure == "missing_result":
        check.pop("result")
    elif failure == "missing_reveal":
        events.pop()
    elif failure == "duplicate_roll":
        events.append(copy.deepcopy(events[1]))
    elif failure == "unbound":
        before["plan"]["validation"]["approved_actions"].pop()
    elif failure == "wrong_target":
        events[-1]["payload"]["id"] = "other"
    result = check_result(check, events, before, "target")
    assert all(result[k] for k in ("binding", "dice", "reveal")) is (failure is None)


def test_real_failed_roll_keeps_target_hidden():
    check, before, events = investigation()
    check["result"]["passed"] = False
    events.pop()
    result = check_result(check, events, before, "target")
    assert result["dice"] and result["reveal"] and result["passed"] is False
    events.append(dict(seq=13, type="entity.revealed", payload={"id": "target"}))
    assert not check_result(check, events, before, "target")["reveal"]


@pytest.mark.parametrize("held", [False, True])
def test_discovered_is_not_owned(held):
    room = {
        "inventory": [dict(item_id="note", instance_id="instance", holder_id="player")]
        if held
        else []
    }
    events = [dict(seq=1, type="entity.revealed", payload={"id": "note"})]
    if held:
        events.append(
            dict(
                seq=2,
                type="module.interaction_receipt",
                payload={
                    "entity_id": "note",
                    "interaction_id": "take",
                    "inventory": {"instance": "player"},
                },
            )
        )
    assert owned_item(room, "note", "player", events)[0] is held
    assert not owned_item(room, "note", "other-player", events)[0]


@pytest.mark.parametrize("reply", [False, True])
def test_post_restore_action_requires_actual_response(reply):
    step = dict(
        request={"client_request_id": "request"},
        before={"revision": 10},
        after={"revision": 15},
        cycle={"status": "completed", "id": "cycle"},
    )
    events = [dict(seq=11, type="action.submitted", client_request_id="request", payload={})]
    if reply:
        events.append(
            dict(
                seq=14,
                type="keeper.narration",
                payload={"cycle_id": "cycle", "text": "已经看过的便签仍是同一张。"},
            )
        )
    assert turn_response(step, events) is reply
    step["before"]["revision"] = 15
    assert not turn_response(step, events)  # Old response cannot stand in for a new action.


@pytest.mark.parametrize("mode", ["recall", "proposal_only", "executed"])
def test_recall_response_cannot_pass_as_post_restore_action(mode):
    step = dict(
        before={"revision": 10},
        after={"revision": 15, "session_state": {"scene_title": "5号车厢"}},
        plan={
            "plan": {
                "focus": {"action": "前往5号车厢" if mode != "recall" else ""},
                "parsed_intent": {"type": "recall" if mode == "recall" else "move"},
            }
        },
    )
    events = [dict(seq=14, type="keeper.narration", payload={"text": "实际回应"})]
    if mode == "executed":
        events.append(dict(seq=13, type="scene.updated", payload={"scene_title": "5号车厢"}))
    assert continued_action(step, events, "5号车厢") is (mode == "executed")


def test_local_model_selection_is_explicit_and_external_denied():
    _, config, _ = select_model("ollama", base_url="http://127.0.0.1:11434/v1/")
    assert config.provider == "ollama"
    with pytest.raises(ValueError):
        select_model("ollama", base_url="http://192.0.2.1:11434/v1/")
    with pytest.raises(ValueError):
        select_model("openai")


def test_local_automatic_discovery_is_selectable_without_publishing_it():
    from app.preparation.search import searchable_entity_ids

    entities = {
        name: {"type": "clue", "reveal_conditions": {"access_policy": policy}}
        for name, policy in [
            ("map", "automatic"),
            ("detail", "requires_check"),
            ("review", "host_review"),
            ("elsewhere", "automatic"),
        ]
    }
    before = copy.deepcopy(entities)
    assert searchable_entity_ids(entities, {"map", "detail", "review"}, "scene") == {
        "map",
        "detail",
    }
    assert entities == before


def test_interrupted_journal_resumes_original_request_without_resubmission(tmp_path):
    from types import SimpleNamespace

    runner = object.__new__(Batch25)
    runner.directory, runner.prefix = tmp_path, "/rooms/room"
    runner.args = SimpleNamespace(retry_failed=False)
    step = dict(
        request={"text": "原行动", "client_request_id": "original-request"},
        before={"revision": 10},
        before_checks=[],
        status="pending",
        attempts=[],
    )
    runner.result = {"steps": {"continue": step}}
    room = {"revision": 15, "game": {"cycle": {"id": "original-cycle", "status": "completed"}}}
    events = [
        dict(seq=11, type="action.submitted", client_request_id="original-request"),
        dict(
            seq=14,
            type="keeper.narration",
            payload={"cycle_id": "original-cycle", "text": "实际回应"},
        ),
    ]
    runner.evidence = lambda: dict(events=events, plans={}, receipts=[])

    def request(method, path):
        assert method == "GET", "Completed request/check must never be submitted again"
        return [] if path.endswith("/checks") else room

    runner.request = request
    result = runner.step("continue", "调用者提供的文本不能替换已发送请求")
    assert result["status"] == "completed"
    assert result["request"]["text"] == "原行动"
    assert result["cycle"]["id"] == "original-cycle"
    events.pop()
    with pytest.raises(RuntimeError, match="no matching response"):
        runner.step("continue", "完成标签本身不能证明实际行动")


@pytest.mark.parametrize("advances", [True, False])
def test_summary_catches_up_to_actual_effects_not_only_successful_http(tmp_path, advances):
    runner = object.__new__(Batch25)
    runner.directory, runner.prefix = tmp_path, "/rooms/room"
    runner.result = {"steps": {"take": {"after": {"revision": 200}}}}
    runner.evidence = lambda: {"events": [dict(seq=200, type="module.interaction_receipt")]}
    cursor, calls = 0, []

    def request(method, path, body=None):
        nonlocal cursor
        if path.endswith("/memories"):
            return [dict(kind="summary", active=True, coverage_end=cursor, content="partial")]
        if method == "POST":
            calls.append(path)
            cursor += 100 if advances else 0
            return [{"last_successful_summary_seq": cursor, "stale": False}]
        return {"revision": 201}

    runner.request = request
    if advances:
        runner.summarize()
        assert len(calls) == 2
        assert runner.result["summary"]["memories"][0]["coverage_end"] == 200
        runner.summarize()
        assert len(calls) == 2
    else:
        with pytest.raises(RuntimeError, match="did not advance"):
            runner.summarize()
        assert len(calls) == 1


def test_completed_summary_is_frozen_when_resuming_after_movement():
    runner = object.__new__(Batch25)
    summary = dict(
        after_seq=220,
        target_seq=200,
        memories=[dict(kind="summary", active=True, coverage_end=200, content="存档前原文")],
    )
    runner.result = {"summary": summary}

    def unexpected_request(*args):
        pytest.fail("Completed evidence must not be replaced with the later room state")

    runner.request = unexpected_request
    original = copy.deepcopy(summary)
    runner.summarize()
    assert runner.result["summary"] == original


def test_summary_public_state_keeps_original_failure_and_real_holder():
    from app.memory.state_facts import summary_state_facts

    entities = [
        dict(type="clue", title="背面", public_summary="第三个箱子有钥匙", revealed_event_seq=5)
    ]
    events = [
        dict(type="check.resolved", payload={"display_text": "骰点89，目标60，失败"}),
        dict(type="action.submitted", payload={"text": "我已经拿到了隐藏物"}),
    ]
    inventory = [dict(holder_name="周衡", title="便签", instance_id="instance")]
    text = summary_state_facts({"scene_title": "6号车厢"}, entities, inventory, events)
    assert all(
        s in text
        for s in (
            "6号车厢",
            "骰点89，目标60，失败",
            "第三个箱子有钥匙",
            "周衡当前持有：便签",
            "instance",
        )
    )
    assert "隐藏物" not in text
    text = summary_state_facts({"scene_title": "6号车厢"}, [], [], events)
    assert "没有登记的持有物" in text and "钥匙" not in text


def test_automatic_target_enters_actual_plan_context(client, lobby, bundle):  # noqa: F811
    from test_action_adjudication import modern_response
    from test_agent_runtime import submit, wait_cycle
    from test_batch24_packages import import_bundle
    from test_rooms import ok, prepare

    from app.agents.model import FakeModelAdapter

    imported = ok(import_bundle(client, bundle))
    prefix = lobby["prefix"]
    ok(
        client.patch(
            prefix + "/module-preparation", json={"preparation_id": imported["preparation_id"]}
        )
    )
    prepare(client, lobby)
    ok(client.post(prefix + "/pause"))
    profile = ok(client.post("/api/agent-profiles", json={"role": "keeper", "name": "KP"}), 201)
    ok(
        client.post(
            prefix + "/agent-bindings",
            json={"member_id": lobby["room"]["host_member_id"], "profile_id": profile["id"]},
        )
    )
    teammate = ok(
        client.post("/api/agent-profiles", json={"role": "investigator", "name": "队友"}), 201
    )
    ok(
        client.post(
            prefix + "/agent-bindings",
            json={"member_id": lobby["agent"], "profile_id": teammate["id"]},
        )
    )
    ok(client.post(prefix + "/resume"))
    eid = imported["entity_ids"]["paper"]
    assert eid not in {e["id"] for e in ok(client.get(prefix + "/public-entities"))}

    def response(messages, kwargs):
        import json

        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            context = json.loads(messages[-1]["content"])
            assert eid in {e["id"] for e in context["current_targets"]}
            result["parsed_intent"]["type"] = "investigate"
            result["focus"] = {
                "action": context["triggering_action"]["payload"]["text"],
                "action_target_id": eid,
            }
            result["proposed_reveal_entity_ids"] = [eid]
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, lobby, "我逐一查看墙上的图示。"))
    assert wait_cycle(client, lobby)["status"] == "completed"
    assert eid in {e["id"] for e in ok(client.get(prefix + "/public-entities"))}
    assert not ok(client.get(prefix + "/checks"))
    assert eid not in {i["item_id"] for i in ok(client.get(prefix))["inventory"]}


from test_batch24_packages import bundle  # noqa: E402,F401
from test_rooms import lobby  # noqa: E402,F401
