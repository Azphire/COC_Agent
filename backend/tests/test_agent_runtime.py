import asyncio
import json
import random
import time
from uuid import uuid4

import pytest
from adjudication_helpers import ScenarioAdapter as FakeModelAdapter
from pydantic import ValidationError
from test_rooms import headers, join, lobby, ok, prepare  # noqa: F401

from app.agents.model import AgentModelClient
from app.agents.modules import Module, content_hash, load_modules
from app.agents.schemas import AgentDecision, CheckRequest
from app.agents.tools import TOOLS, validate
from app.config import Settings
from app.dice.service import DiceService
from app.models.base import ModelError, ModelResponse
from app.rules.checks import check_value, judge, roll_check, threshold


def scenario(messages, kwargs):
    context = json.loads(messages[-1]["content"])
    if context.get("phase") == "generate_keeper_narration" or context.get("response_brief"):
        checks = [
            e["payload"]
            for e in context.get("public_tool_results", {}).get("events", [])
            if e["type"] == "check.resolved"
        ]
        check = checks[-1] if checks else None
        return {
            "content": f"本次检定出目 {check['result']['total']}，"
            f"目标 {check['result']['threshold']}。"
            if check
            else "你们查看现场，继续讨论下一步行动。"
        }
    if context.get("phase") == "summary":
        return {"content": "调查员检查了现场；尚未核实的判断仍是推测。"}
    if context["role"] == "investigator":
        return {
            "tools": [
                {"name": "propose_action", "arguments": {"text": "我检查入口，留意是否有人靠近。"}}
            ]
        }
    if context["phase"] == "execute_state_tools":
        check = context["checks"][-1]
        tools = [{"name": "reveal_clue", "arguments": {"clue_id": "logbook"}}]
        if check["result"]["passed"]:
            tools.append({"name": "reveal_clue", "arguments": {"clue_id": "pin"}})
        tools.append(
            {
                "name": "send_narration",
                "arguments": {
                    "text": f"检定结果是{check['result']['total']}，"
                    f"{'通过' if check['result']['passed'] else '未通过'}。你们继续查看交接记录。"
                },
            }
        )
        return {"tools": tools}
    action = context["triggering_action"]
    if "检定" in action["payload"]["text"]:
        return {
            "tools": [
                {
                    "name": "request_skill_check",
                    "arguments": {
                        "target_member_id": action["actor_member_id"],
                        "kind": "skill",
                        "name": "spot_hidden",
                        "reason": "仔细检查当前现场",
                        "visibility": "actor_and_host"
                        if "私密" in action["payload"]["text"]
                        else "public",
                    },
                },
            ]
        }
    return {
        "tools": [
            {"name": "reveal_clue", "arguments": {"clue_id": "notice"}},
            {
                "name": "send_narration",
                "arguments": {"text": "公告的纸角在风中颤动，你们可以沿石阶进入维修间。"},
            },
        ]
    }


@pytest.fixture
def game(client, lobby):  # noqa: F811
    prepare(client, lobby)
    prefix = lobby["prefix"]
    ok(client.post(prefix + "/pause"))
    ok(client.post(prefix + "/module", json={"module_id": "stopped-clock"}))
    profiles = []
    for role, member in [
        ("keeper", lobby["room"]["host_member_id"]),
        ("investigator", lobby["agent"]),
    ]:
        profile = ok(client.post("/api/agent-profiles", json={"role": role, "name": role}), 201)
        profiles.append(profile)
        ok(
            client.post(
                prefix + "/agent-bindings", json={"member_id": member, "profile_id": profile["id"]}
            )
        )
    ok(client.post(prefix + "/resume"))
    adapter = FakeModelAdapter(responder=scenario)
    client.app.state.agent_service.model.adapter = adapter
    return {**lobby, "adapter": adapter, "profiles": profiles}


def submit(client, game, text="检查公告", request_id=None):
    return client.post(
        game["prefix"] + "/actions",
        headers=headers(game["remote"]["member_token"]),
        json={"text": text, "client_request_id": request_id or str(uuid4())},
    )


def accept_original(client, game, check_id):
    """Existing scenarios explicitly accept the new optional post-roll choice."""
    check = next(c for c in ok(client.get(game["prefix"] + "/checks")) if c["id"] == check_id)
    if check["status"] == "pending" and (check.get("settlement") or {}).get("stage") == "choice":
        check = ok(
            client.post(
                game["prefix"] + f"/checks/{check_id}/choice",
                json={"operation": "accept"},
                headers=headers(game["remote"]["member_token"]),
            )
        )["check"]
    return check


def wait_cycle(client, game, expected=("completed", "failed", "waiting_for_roll"), *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cycle = ok(client.get(game["prefix"] + "/agent-cycle"))
        if cycle and cycle["status"] in expected:
            # Wait for the task/checkpoint to settle, not merely a pre-interrupt DB commit.
            if not client.app.state.agent_service.runtime.tasks:
                return cycle
        time.sleep(0.02)
    pytest.fail(f"cycle did not settle: {cycle}")


@pytest.mark.parametrize(
    "value,difficulty,expected",
    [
        (55, "regular", 55),
        (55, "hard", 27),
        (55, "extreme", 11),
        (49, "hard", 24),
        (1, "extreme", 0),
    ],
)
def test_thresholds(value, difficulty, expected):
    assert threshold(value, difficulty) == expected


@pytest.mark.parametrize(
    "value,difficulty,total,level,passed",
    [
        (0, "extreme", 1, "critical", True),
        (55, "hard", 96, "fumble", False),
        (55, "regular", 96, "failure", False),
        (50, "regular", 99, "failure", False),
        (49, "regular", 96, "fumble", False),
        (99, "regular", 100, "fumble", False),
        (55, "hard", 27, "hard", True),
        (55, "hard", 28, "regular", False),
        (55, "extreme", 11, "extreme", True),
        (55, "extreme", 12, "hard", False),
    ],
)
def test_judgement(value, difficulty, total, level, passed):
    result = judge(value, difficulty, total)
    assert (result["level"], result["passed"]) == (level, passed)


@pytest.mark.parametrize("bonus,penalty", [(0, 0), (1, 0), (2, 0), (0, 1), (0, 2), (2, 1), (2, 2)])
def test_bonus_penalty(bonus, penalty):
    detail, result = roll_check(DiceService(random.Random(9)), 55, "regular", bonus, penalty)
    assert len(detail["tens"]) == 1 + abs(bonus - penalty)
    assert result["total"] == (min if bonus >= penalty else max)(detail["candidates"])


def test_zeros_are_one_hundred():
    class Rng:
        def randint(self, *args):
            return 10

    detail, result = roll_check(DiceService(Rng()), 99, "regular", 2, 0)
    assert detail["units"] == 0 and detail["tens"] == [0, 0, 0]
    assert result["total"] == 100 and result["level"] == "fumble"


def test_snapshot_values_and_unknown_fields():
    card = {
        "ruleset_id": "coc7-character-creation",
        "effective_attributes": {"dex": 60},
        "skill_values": {"spot_hidden": 25},
    }
    assert check_value(card, "attribute", "dex") == 60
    assert check_value(card, "skill", "spot_hidden") == 25
    with pytest.raises(ValueError):
        check_value(card, "skill", "imaginary")
    with pytest.raises(ValidationError):
        CheckRequest(target_member_id=uuid4(), name="spot_hidden", reason="test", total=1)


def test_module_validation_hash():
    module = load_modules()["stopped-clock"]
    data = module.model_dump()
    assert content_hash(data) == content_hash(json.loads(json.dumps(data)))
    data["initial_scene"] = "missing"
    with pytest.raises(ValidationError):
        Module.model_validate(data)


@pytest.mark.parametrize("name", list(TOOLS))
def test_tool_arguments_preserve_validation_except_empty_reads(name):
    spec = TOOLS[name]
    if spec.read_only and not spec.arguments.model_fields:
        assert validate(name, {"forged_result": 1}, next(iter(spec.roles))).model_dump() == {}
        return
    with pytest.raises(ValidationError):
        validate(name, {"forged_result": 1}, next(iter(spec.roles)))


def test_no_check_cycle_and_private_context(client, game):
    ok(submit(client, game))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    # Teammate act/assist is now resolved in its own KP cycle.
    assert cycle["state"]["origin"] == "teammate"
    assert cycle["state"]["call_count"] == 2
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "agent.action_proposed" for e in events) == 1
    assert any(e["type"] == "clue.revealed" for e in events)
    assert ok(client.get(game["prefix"] + "/memories"))
    teammate = next(
        json.loads(prompt[-1]["content"])
        for prompt in game["adapter"].prompts
        if json.loads(prompt[-1]["content"]).get("role") == "investigator"
    )
    encoded = json.dumps(teammate, ensure_ascii=False)
    assert (
        "keeper_brief" not in encoded and "蓝斑卵" not in encoded and "keeper_notes" not in encoded
    )
    view = ok(client.get(game["prefix"], headers=headers(game["remote"]["member_token"])))
    assert "keeper_module" not in view["game"]
    assert "蓝斑卵" not in json.dumps(view, ensure_ascii=False)


def test_interrupt_roll_resume_and_duplicate(client, game):
    request_id = str(uuid4())
    ok(submit(client, game, "对工作台进行侦查检定；我冒着失去平衡的风险尝试。", request_id))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "waiting_for_roll", cycle
    assert len(game["adapter"].prompts) == 1
    ok(submit(client, game, "对工作台进行侦查检定；我冒着失去平衡的风险尝试。", request_id))
    # Conversation while waiting is covered separately; duplicate receipt adds no cycle.
    check = ok(client.get(game["prefix"] + "/checks"))[0]
    path = game["prefix"] + f"/checks/{check['id']}/roll"
    assert client.post(path, json={"total": 1}).status_code == 422
    ok(client.post(path, json={}, headers=headers(game["remote"]["member_token"])))
    accept_original(client, game, check["id"])
    final = wait_cycle(client, game)
    assert final["status"] == "completed", final
    assert final["id"] == cycle["id"] and final["state"]["call_count"] == 2
    resolved = ok(client.post(path, json={}))
    assert resolved["check"]["status"] == "resolved"
    assert len(game["adapter"].prompts) == 2
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "check.resolved" for e in events) == 1


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_argument_repair_is_bounded_and_precedes_tools(client, game, repair_succeeds):
    def responder(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "ArgumentRepair":
            assert "tool_schema" in context and "action_identifiers" not in context
            args = dict(context["arguments"])
            if repair_succeeds:
                args.pop("forged_result")
            return {"arguments": args}
        plan = scenario(messages, kwargs)
        if context["phase"] == "plan_keeper_action":
            plan["tools"][0]["arguments"]["forged_result"] = 1
        return plan

    game["adapter"].responder = responder
    ok(submit(client, game, "对当前现场进行侦查检定；我冒着失去平衡的风险尝试。"))
    cycle = wait_cycle(client, game)
    assert cycle["state"]["call_count"] == (2 if repair_succeeds else 3)
    assert cycle["status"] == ("waiting_for_roll" if repair_succeeds else "completed"), cycle
    checks = ok(client.get(game["prefix"] + "/checks"))
    if repair_succeeds:
        assert len(checks) == 1
        ok(client.post(game["prefix"] + f"/checks/{checks[0]['id']}/roll", json={}))
        accept_original(client, game, checks[0]["id"])
        final = wait_cycle(client, game)
        assert final["status"] == "completed", final
        assert final["state"]["call_count"] == 3
    else:
        assert checks == []


def test_model_failure_retry_and_cancel(client, game):
    game["adapter"].responses.append(ModelError("offline"))
    ok(submit(client, game))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "failed"
    ok(client.post(game["prefix"] + "/agent-cycle/retry"))
    assert wait_cycle(client, game)["status"] == "completed"
    ok(submit(client, game, "做一次侦查检定；我冒着失去平衡的风险尝试。"))
    assert wait_cycle(client, game)["status"] == "waiting_for_roll"
    ok(client.post(game["prefix"] + "/agent-cycle/cancel"))
    assert ok(client.get(game["prefix"] + "/agent-cycle"))["status"] == "cancelled"
    assert ok(client.get(game["prefix"] + "/checks"))[-1]["status"] == "cancelled"


async def test_model_repair_once_and_serialization():
    settings = Settings(_env_file=None)
    adapter = FakeModelAdapter([{"invalid": True}, {"tools": []}])
    client = AgentModelClient(settings, adapter)
    result, _ = await client.generate([], response_schema=AgentDecision)
    assert result.structured.tools == [] and len(adapter.prompts) == 2
    adapter.responses.extend([{"invalid": True}, {"invalid": True}])
    with pytest.raises(ModelError):
        await client.generate([], response_schema=AgentDecision)
    assert len(adapter.prompts) == 4


async def test_model_text_tools_timeout_cancel():
    settings = Settings(_env_file=None, model_timeout_seconds=0.02)

    async def slow():
        await asyncio.sleep(1)
        return ModelResponse(text="late")

    adapter = FakeModelAdapter([ModelResponse(text="text"), slow])
    model = AgentModelClient(settings, adapter)
    assert (await model.generate([]))[0].text == "text"
    with pytest.raises(ModelError, match="超时"):
        await model.generate([])
    adapter.responses.append(slow)
    task = asyncio.create_task(model.generate([]))
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
