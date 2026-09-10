import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.schemas import MemoryArgs
from app.memory.service import build_context, memories, write_memory
from app.persistence.agent_models import AgentRun, ProfileRecord
from app.rooms.service import RoomError


def test_memory_scope_sources_belief_and_supersedes(client, game):  # noqa: F811
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    svc = client.app.state.agent_service

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, game["room"]["id"])
            bindings = await svc.bindings(session, room.id)
            keeper = next(b for b in bindings if b.member_id == room.host_member_id)
            investigator = next(b for b in bindings if b.member_id == game["agent"])
            kp = await session.get(ProfileRecord, keeper.profile_id)
            player = await session.get(ProfileRecord, investigator.profile_id)
            private = await write_memory(
                session,
                svc.rooms,
                room,
                keeper,
                kp,
                MemoryArgs(kind="belief", scope="keeper_only", content="守秘人猜测"),
            )
            own = await write_memory(
                session,
                svc.rooms,
                room,
                investigator,
                player,
                MemoryArgs(kind="belief", scope="agent_private", content="我猜有人来过"),
            )
            await session.flush()
            replacement = await write_memory(
                session,
                svc.rooms,
                room,
                investigator,
                player,
                MemoryArgs(
                    kind="belief",
                    scope="agent_private",
                    content="我修正自己的推测",
                    supersedes_id=own.id,
                ),
            )
            await session.flush()
            visible = await memories(session, room.id, investigator.profile_id)
            assert private.id not in {m.id for m in visible}
            assert own.id not in {m.id for m in visible} and replacement.id in {
                m.id for m in visible
            }
            assert not own.active and replacement.kind == "belief"
            with pytest.raises(RoomError):
                await write_memory(
                    session,
                    svc.rooms,
                    room,
                    investigator,
                    player,
                    MemoryArgs(kind="observation", scope="public", content="我猜的是真相"),
                )
            with pytest.raises(RoomError):
                await write_memory(
                    session,
                    svc.rooms,
                    room,
                    keeper,
                    kp,
                    MemoryArgs(kind="observation", scope="public", content="无来源世界事实"),
                )
            with pytest.raises(RoomError):
                await write_memory(
                    session,
                    svc.rooms,
                    room,
                    investigator,
                    player,
                    MemoryArgs(
                        kind="belief",
                        scope="agent_private",
                        content="越权替代",
                        supersedes_id=private.id,
                    ),
                )
            clue_memory = next(m for m in visible if m.kind == "observation")
            factual = await write_memory(
                session,
                svc.rooms,
                room,
                keeper,
                kp,
                MemoryArgs(
                    kind="observation",
                    scope="public",
                    content="凭空编造",
                    source_event_ids=clue_memory.source_event_ids,
                ),
            )
            assert "凭空编造" not in factual.content and "clue.revealed" in factual.content

    client.portal.call(verify)


def test_filter_before_window_and_no_other_private_memory(client, game):  # noqa: F811
    svc = client.app.state.agent_service
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    for i in range(30):
        ok(
            client.post(
                game["prefix"] + "/messages",
                json={
                    "text": f"仅主机私密记录 {i}",
                    "visibility": "host_only",
                    "client_request_id": str(uuid4()),
                },
            )
        )
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    contexts = [
        json.loads(p[-1]["content"])
        for p in game["adapter"].prompts
        if p[-1]["content"].startswith("{")
    ]
    latest = next(
        c
        for c in reversed(contexts)
        if c.get("role") == "investigator" and c.get("phase") != "summary"
    )
    assert "仅主机私密记录" not in json.dumps(latest, ensure_ascii=False)
    assert any(e["type"] == "clue.revealed" for e in latest["events"])
    assert svc.settings.agent_event_window == 25


def test_context_budget_includes_separators_and_repair_feedback(client, game):  # noqa: F811
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    svc = client.app.state.agent_service

    async def verify():
        async with svc.rooms.database.sessions() as session:
            room = await svc.rooms.room(session, game["room"]["id"])
            binding = next(
                b
                for b in await svc.bindings(session, room.id)
                if b.member_id == room.host_member_id
            )
            profile = await session.get(ProfileRecord, binding.profile_id)
            cycle = await svc.cycle(session, room.id)
            for budget in range(5200, 5300):
                svc.settings.agent_context_chars = budget
                context, _, _ = await build_context(
                    svc,
                    session,
                    room,
                    binding,
                    profile,
                    cycle,
                    phase="repair_action_arguments",
                    additions={"validation_errors": [{"instruction": "先切换到维修间，再检定"}]},
                )
                assert len(json.dumps(context, ensure_ascii=False)) <= budget
                assert context["phase"] == "repair_action_arguments"
                assert context["validation_errors"]

    client.portal.call(verify)


def test_tool_receipt_idempotency(client, game):  # noqa: F811
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    svc = client.app.state.agent_service

    async def verify():
        async with svc.rooms.transaction() as session:
            cycle = await svc.cycle(session, game["room"]["id"])
            cycle.status = "running"
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.cycle_id == cycle.id, AgentRun.graph_node == "plan_keeper_action"
                )
            )
            rid, room_id = run.id, run.room_id
        first = await svc.runtime.tools.execute(
            room_id, rid, 0, "reveal_clue", {"clue_id": "notice"}
        )
        second = await svc.runtime.tools.execute(
            room_id, rid, 0, "reveal_clue", {"clue_id": "notice"}
        )
        assert first == second and first["ok"]
        with pytest.raises(RoomError):
            await svc.runtime.tools.execute(room_id, rid, 0, "reveal_clue", {"clue_id": "pin"})

    before = ok(client.get(game["prefix"] + "/events"))["latest_seq"]
    client.portal.call(verify)
    assert ok(client.get(game["prefix"] + "/events"))["latest_seq"] == before


def test_concurrent_human_actions_one_cycle(client, game):  # noqa: F811
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: submit(client, game, "我冒着失去平衡的风险调查并请求侦查检定"), range(2)
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert wait_cycle(client, game)["status"] == "waiting_for_roll"
    check = ok(client.get(game["prefix"] + "/checks"))[0]
    path = game["prefix"] + f"/checks/{check['id']}/roll"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ok(client.post(path, json={})), range(2)))
    assert results[0]["check"]["dice"] == results[1]["check"]["dice"]
    assert wait_cycle(client, game)["status"] == "completed"
    assert len(game["adapter"].prompts) == 2


def test_profile_draft_is_not_persisted_until_confirmation(client, game):  # noqa: F811
    count = len(ok(client.get("/api/agent-profiles")))
    game["adapter"].responses.append({"role": "keeper", "name": "生成草稿", "personality": "谨慎"})
    result = ok(
        client.post(
            "/api/agent-profiles/generate-draft", json={"role": "keeper", "concept": "谨慎的守秘人"}
        )
    )
    assert result["requires_confirmation"] and result["draft"]["name"] == "生成草稿"
    assert len(ok(client.get("/api/agent-profiles"))) == count
    result["draft"]["name"] = "用户修改后的档案"
    ok(client.post("/api/agent-profiles", json=result["draft"]), 201)
    assert len(ok(client.get("/api/agent-profiles"))) == count + 1
    game["adapter"].responses.extend([{"broken": True}, {"broken": True}])
    assert (
        client.post(
            "/api/agent-profiles/generate-draft", json={"role": "keeper", "concept": "保留此输入"}
        ).status_code
        == 422
    )
    assert len(ok(client.get("/api/agent-profiles"))) == count + 1


def test_save_rejects_changed_profile_and_accepts_restored_config(client, game):  # noqa: F811
    prefix = game["prefix"]
    ok(client.post(prefix + "/pause"))
    saved = ok(client.post(prefix + "/snapshots", json={"name": "profile consistency"}))["snapshot"]
    bindings = ok(client.get(prefix + "/agent-config"))["bindings"]
    keeper = next(b for b in bindings if b["role"] == "keeper")
    ok(client.delete(prefix + f"/agent-bindings/{keeper['id']}"))
    profile = game["profiles"][0]
    original = {k: v for k, v in profile.items() if k not in {"id", "created_at", "updated_at"}}
    path = f"/api/agent-profiles/{profile['id']}"
    ok(client.patch(path, json={**original, "personality": "modified after saving"}))
    load_path = prefix + f"/snapshots/{saved['id']}/load"
    assert client.post(load_path).status_code == 409
    ok(client.patch(path, json=original))
    ok(client.post(load_path))
