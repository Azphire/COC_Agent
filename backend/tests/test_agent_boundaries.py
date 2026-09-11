import asyncio
import json
from uuid import uuid4

from adjudication_helpers import ScenarioAdapter as FakeModelAdapter
from fastapi.testclient import TestClient
from test_agent_runtime import accept_original, game, scenario, submit, wait_cycle  # noqa: F401
from test_rooms import headers, join, lobby, ok  # noqa: F401

from app.agents.model import AgentModelClient
from app.config import Settings
from app.main import create_app
from app.models.base import ModelError, ModelResponse


def test_host_debug_and_profile_boundaries(client, game):  # noqa: F811
    player = headers(game["remote"]["member_token"])
    for path in ["/agent-config", "/agent-runs", "/memories"]:
        assert client.get(game["prefix"] + path, headers=player).status_code == 403
    for path in ["/api/agent-profiles", "/api/modules", "/api/agent-model-presets"]:
        assert client.get(path, headers=player).status_code == 401
    assert client.post(game["prefix"] + "/agent-cycle/cancel", headers=player).status_code == 403
    assert (
        client.post(
            "/api/agent-profiles", json={"role": "keeper", "name": "bad", "api_key": "secret"}
        ).status_code
        == 422
    )
    profile = game["profiles"][0]
    assert (
        client.patch(
            f"/api/agent-profiles/{profile['id']}", json={"role": "investigator", "name": "changed"}
        ).status_code
        == 409
    )


def test_other_player_and_private_check(client, game):  # noqa: F811
    ok(client.post(game["prefix"] + "/pause"))
    outsider = join(client, game["created"]["invite_code"], "另一位真人")

    # A spectator member may read the room but is removed from the start readiness requirement.
    # Keep the game running by leaving the ready state unchanged through this test's DB fixture.
    async def start():
        async with client.app.state.database.sessions() as session:
            room = await client.app.state.room_service.room(session, game["room"]["id"])
            room.status = "running"
            await session.commit()

    client.portal.call(start)
    ok(submit(client, game, "私密侦查检定；我冒着失去平衡的风险尝试。"))
    assert wait_cycle(client, game)["status"] == "waiting_for_roll"
    check = ok(client.get(game["prefix"] + "/checks"))[0]
    assert (
        ok(client.get(game["prefix"] + "/checks", headers=headers(outsider["member_token"]))) == []
    )
    assert (
        client.post(
            game["prefix"] + f"/checks/{check['id']}/roll",
            json={},
            headers=headers(outsider["member_token"]),
        ).status_code
        == 403
    )
    player_view = ok(
        client.get(game["prefix"] + "/checks", headers=headers(game["remote"]["member_token"]))
    )
    assert "clue_id" not in player_view[0]
    for path in ["", "/events", "/logs"]:
        response = client.get(game["prefix"] + path, headers=headers(outsider["member_token"]))
        assert check["id"] not in response.text and "蓝斑卵" not in response.text


def test_checkpoint_restart_pending_save_load(client, game, character_settings):  # noqa: F811
    ok(submit(client, game, "我冒着失去平衡的风险调查并请求侦查检定"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "waiting_for_roll"
    save = ok(client.post(game["prefix"] + "/snapshots", json={"name": "等骰存档"}))["snapshot"]
    check = ok(client.get(game["prefix"] + "/checks"))[0]
    client.__exit__(None, None, None)
    restarted = create_app(character_settings)
    adapter = FakeModelAdapter(responder=scenario)
    restarted.state.agent_model_adapter = adapter
    with TestClient(
        restarted, headers=headers(character_settings.host_admin_token.get_secret_value())
    ) as second:
        current = ok(second.get(game["prefix"] + "/agent-cycle"))
        assert current["id"] == cycle["id"] and current["status"] == "waiting_for_roll"
        ok(second.post(game["prefix"] + "/pause"))
        ok(second.post(game["prefix"] + f"/snapshots/{save['id']}/load"))
        ok(second.post(game["prefix"] + "/resume"))
        ok(second.post(game["prefix"] + f"/checks/{check['id']}/roll", json={}))
        accept_original(second, game, check["id"])
        assert wait_cycle(second, game)["status"] == "completed"
        assert len(adapter.prompts) == 1
        result = ok(second.get(game["prefix"] + "/checks"))[0]
        ok(second.post(game["prefix"] + "/pause"))
        ok(second.post(game["prefix"] + f"/snapshots/{save['id']}/load"))
        ok(second.post(game["prefix"] + "/resume"))
        assert (
            ok(second.post(game["prefix"] + f"/checks/{check['id']}/roll", json={}))["check"][
                "dice"
            ]
            == result["dice"]
        )
        accept_original(second, game, check["id"])
        assert wait_cycle(second, game)["status"] == "completed"
        ok(submit(second, game, "再次查看现场"))
        assert wait_cycle(second, game)["status"] == "completed"


def test_player_plan_cannot_roll_for_teammate_without_teammate_action(client, game):  # noqa: F811
    game["adapter"].responses.append(
        {
            "tools": [
                {
                    "name": "request_skill_check",
                    "arguments": {
                        "target_member_id": game["agent"],
                        "kind": "attribute",
                        "name": "dex",
                        "reason": "保持平衡",
                    },
                }
            ]
        }
    )
    ok(submit(client, game, "我请同伴冒着失去平衡的风险保持稳定。"))
    assert wait_cycle(client, game)["status"] == "completed"
    assert ok(client.get(game["prefix"] + "/checks")) == []
    # The teammate must decide and enter its own KP cycle (batch-12 integration).


def test_tool_rejections_are_structured_and_do_not_corrupt_room(client, game):  # noqa: F811
    game["adapter"].responses.append(
        {
            "tools": [
                {"name": "update_scene", "arguments": {"scene_id": "no-such-scene"}},
                {"name": "reveal_clue", "arguments": {"clue_id": "pin"}},
                {"name": "reveal_clue", "arguments": {"clue_id": "nest"}},
                {"name": "send_narration", "arguments": {"text": "雨夜的调查仍在继续。"}},
            ]
        }
    )
    ok(submit(client, game))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    run = ok(client.get(game["prefix"] + "/agent-runs"))[0]
    assert run["tool_results"] == []
    validation = ok(client.get(game["prefix"] + f"/cycles/{cycle['id']}/validation"))["validation"]
    assert len(validation["rejected_actions"]) == 3
    assert ok(client.get(game["prefix"]))["game"]["module"]["scene"]["id"] == "square"


def test_investigator_cannot_use_keeper_tools_and_only_one_speech(client, game):  # noqa: F811
    game["adapter"].responses.extend(
        [
            {"tools": []},
            {"content": "你们在门口停下脚步。"},
            {
                "tools": [
                    {"name": "update_scene", "arguments": {"scene_id": "platform"}},
                    {"name": "speak", "arguments": {"text": "我守着门。"}},
                    {"name": "propose_action", "arguments": {"text": "我再走一次。"}},
                    {
                        "name": "write_private_memory",
                        "arguments": {"content": "我猜钟楼有人来过。"},
                    },
                ]
            },
        ]
    )
    ok(submit(client, game, "investigator，请告诉我你的看法。"))
    assert wait_cycle(client, game)["status"] == "completed"
    run = ok(client.get(game["prefix"] + "/agent-runs"))[-1]
    assert run["structured_output"]["mode"] == "speak" and run["tool_results"] == []
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "agent.spoke" for e in events) == 1
    assert not any(
        e["type"] in {"scene.updated", "agent.action_proposed"} and e["payload"].get("cycle_id")
        for e in events
    )


def test_max_tool_calls_repairs_before_effects(client, game):  # noqa: F811
    invalid = {"tools": [{"name": "send_narration", "arguments": {"text": "不能执行"}}] * 5}
    game["adapter"].responses.extend([invalid, invalid])
    ok(submit(client, game))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "failed" and cycle["state"]["call_count"] == 2
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert not any(e["type"] == "keeper.narration" for e in events)


def test_call_budget_and_cancel_during_model(client, game):  # noqa: F811
    svc = client.app.state.agent_service
    svc.settings.agent_max_calls = 1
    ok(submit(client, game))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed" and len(game["adapter"].prompts) == 1
    assert client.post(game["prefix"] + "/agent-cycle/retry").status_code == 409
    svc.settings.agent_max_calls = 6

    async def slow():
        await asyncio.sleep(5)
        return ModelResponse(structured={"tools": []})

    game["adapter"].responses.append(slow)
    ok(submit(client, game))
    ok(client.post(game["prefix"] + "/agent-cycle/cancel"))
    assert wait_cycle(client, game, expected=("cancelled",))["status"] == "cancelled"


def test_credentials_and_hidden_reasoning_removed(client, game):  # noqa: F811
    token = game["remote"]["member_token"]
    host = client.app.state.settings.host_admin_token.get_secret_value()
    ok(submit(client, game, f"检查公告 {host} {token} {game['created']['invite_code']}"))
    assert wait_cycle(client, game)["status"] == "completed"
    public_prompts = [
        json.loads(p[-1]["content"])
        for p in game["adapter"].prompts
        if json.loads(p[-1]["content"]).get("phase") == "generate_keeper_narration"
    ]
    assert public_prompts
    for prompt in public_prompts:
        encoded = json.dumps(prompt, ensure_ascii=False)
        assert "keeper_brief" not in encoded and "keeper_notes" not in encoded
        assert "蓝斑卵" not in encoded and "守秘真相" not in encoded
    for value in [
        json.dumps(game["adapter"].prompts),
        client.get(game["prefix"] + "/agent-runs").text,
        client.get(game["prefix"] + "/logs").text,
    ]:
        assert (
            token not in value and host not in value and game["created"]["invite_code"] not in value
        )
    game["adapter"].responses.extend(
        [{"tools": [{"name": "speak", "arguments": {"text": "<think>秘密推理</think>"}}]}] * 2
    )
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "failed"
    assert "秘密推理" not in client.get(game["prefix"] + "/agent-runs").text


def test_summary_and_memory_survive_event_window(client, game):  # noqa: F811
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    for i in range(35):
        ok(
            client.post(
                game["prefix"] + "/messages",
                json={"text": f"公开现场记录 {i}", "client_request_id": str(uuid4())},
            )
        )
    ok(submit(client, game, "继续调查"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    records = ok(client.get(game["prefix"] + "/memories"))
    summary = next(m for m in records if m["kind"] == "summary")
    assert summary["coverage_start"] <= summary["coverage_end"]
    runs = ok(client.get(game["prefix"] + "/agent-runs"))
    assert (
        sum(r["graph_node"] == "update_summary" and r["cycle_id"] == cycle["id"] for r in runs) == 1
    )
    for run in runs:
        if run["graph_node"] != "update_summary":
            assert len(run["context"].get("events", [])) <= 25
        assert (
            len(json.dumps(run["context"], ensure_ascii=False))
            <= client.app.state.settings.agent_context_chars
        )
    kp = next(r for r in reversed(runs) if r["graph_node"] == "plan_keeper_action")
    assert any(m["kind"] == "observation" for m in kp["context"]["memories"])


def test_summary_failure_does_not_fail_cycle(client, game):  # noqa: F811
    # Enough real story events, independently of initialization/management records.
    settings = client.app.state.settings
    for i in range(settings.agent_event_window + settings.summary_event_threshold):
        ok(
            client.post(
                game["prefix"] + "/messages",
                json={"text": f"记录 {i}", "client_request_id": str(uuid4())},
            )
        )

    def responder(messages, kwargs):
        if json.loads(messages[-1]["content"]).get("phase") == "summary":
            raise ModelError("summary unavailable")
        return scenario(messages, kwargs)

    game["adapter"].responder = responder
    ok(submit(client, game))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed"
    assert any(
        r["graph_node"] == "update_summary" and r["status"] == "failed"
        for r in ok(client.get(game["prefix"] + "/agent-runs"))
    )


async def test_global_model_semaphore_across_clients():
    async def slow():
        await asyncio.sleep(0.01)
        return ModelResponse(text="done")

    adapter = FakeModelAdapter([slow, slow, slow])
    first = AgentModelClient(Settings(_env_file=None), adapter)
    second = AgentModelClient(Settings(_env_file=None), adapter)
    await asyncio.gather(first.generate([]), second.generate([]), first.generate([]))
    assert adapter.max_active == 1
