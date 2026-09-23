"""The original Agent start/resume routes share the wizard's prerequisites."""

import asyncio

import pytest
from test_batch48_launch import launch  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import character, create_room, ok

from app.agents.model import FakeModelAdapter
from app.rooms.service import RoomError


@pytest.fixture
def unprepared_agent_room(client):
    created = create_room(client)
    prefix = "/api/rooms/" + created["room"]["id"]
    room = ok(client.post(prefix + "/members", json={
        "display_name": "本地调查员", "controller_type": "human",
    }))["room"]
    member = next(m["id"] for m in room["members"] if m["role"] == "player")
    card = character(client)
    room = ok(client.post(prefix + "/character-slots", json={
        "character_id": card["id"],
    }))["room"]
    ok(client.post(prefix + "/character-assignments", json={
        "slot_id": room["character_slots"][0]["id"], "member_id": member,
    }))
    ok(client.post(prefix + "/ready", json={"member_id": member, "ready": True}))
    ok(client.post(prefix + "/module", json={"module_id": "stopped-clock"}))
    keeper = ok(client.post("/api/agent-profiles", json={
        "role": "keeper", "name": "KP",
    }), 201)
    ok(client.post(prefix + "/agent-bindings", json={
        "member_id": room["host_member_id"], "profile_id": keeper["id"],
    }))
    return prefix


def bind_rules(client, prefix):
    knowledge = client.app.state.agent_service.knowledge
    source_path = client.app.state.settings.data_dir / "rules" / "CoC7-start.txt"
    source_path.parent.mkdir(exist_ok=True)
    source_path.write_text("第七版检定结果不超过技能值时成功。", encoding="utf-8")
    knowledge.indexer.index("rules")
    source = next(s for s in knowledge.repository.sources() if s.title == source_path.name)
    body = {"enabled": True, "module": None, "rules": [
        {"source_id": source.source_id, "source_hash": source.source_hash},
    ]}
    ok(client.patch(prefix + "/knowledge", json=body))
    return body


def test_agent_without_preparation_requires_valid_rules_for_start_and_resume(
    client, unprepared_agent_room,
):
    prefix = unprepared_agent_room
    client.app.state.agent_service.model.adapter = FakeModelAdapter()
    blocked = client.post(prefix + "/start")
    assert blocked.status_code == 422 and "第七版规则来源" in blocked.text
    assert ok(client.get(prefix))["status"] == "lobby"
    rules = bind_rules(client, prefix)
    ok(client.post(prefix + "/start"))
    ok(client.post(prefix + "/pause"))
    ok(client.patch(prefix + "/knowledge", json={**rules, "enabled": False}))
    blocked = client.post(prefix + "/resume")
    assert blocked.status_code == 422 and "第七版规则来源" in blocked.text
    assert ok(client.get(prefix))["status"] == "paused"
    ok(client.patch(prefix + "/knowledge", json=rules))
    ok(client.post(prefix + "/resume"))
    assert not client.app.state.agent_service.model.calls


def test_formal_agent_start_checks_model_and_reuses_success_until_configuration_changes(
    client, unprepared_agent_room, monkeypatch,
):
    prefix = unprepared_agent_room
    bind_rules(client, prefix)
    models = client.app.state.model_settings
    calls, available = [], False

    async def catalogue():
        calls.append(models.revision)
        return {"models": [models.current.model + ":latest"] if available else [],
                "error": None if available else "测试模型离线"}

    monkeypatch.setattr("app.api.model.catalogue", catalogue)
    # No FakeModel adapter: exercise the availability gate without real I/O.
    assert client.app.state.agent_service.model.adapter is None
    assert models.verification is None
    blocked = client.post(prefix + "/start")
    assert blocked.status_code == 422 and "测试模型离线" in blocked.text
    assert ok(client.get(prefix))["status"] == "lobby"
    assert len(calls) == 1
    available = True
    configuration = {"provider": "ollama", "model": "fixture-model",
                     "base_url": "http://127.0.0.1:11434/v1/"}
    ok(client.post("/api/model/config", json=configuration))
    ok(client.post(prefix + "/start"))
    ok(client.post(prefix + "/pause"))
    ok(client.post(prefix + "/resume"))
    assert len(calls) == 2
    ok(client.post(prefix + "/pause"))
    ok(client.post("/api/model/config", json={**configuration, "model": "another-model"}))
    ok(client.post(prefix + "/resume"))
    assert len(calls) == 3 and len(set(calls)) == 3
    assert not client.app.state.agent_service.model.calls


def test_parallel_wizard_model_checks_share_one_availability_request(client, monkeypatch):
    service = client.app.state.launch_service
    calls = []

    async def catalogue():
        calls.append(service.models.revision)
        await asyncio.sleep(0)
        return {"models": [service.models.current.model], "error": None}

    monkeypatch.setattr("app.api.model.catalogue", catalogue)

    async def concurrent():
        await asyncio.gather(service.check_model(), service.check_model(), service.check_model())
        await service.check_model()

    client.portal.call(concurrent)
    assert len(calls) == 1 and service.models.verification != "failed"
    assert not service.agents.model.calls


def test_configuration_cannot_change_during_adoption_or_final_start(
    client, launch, monkeypatch,  # noqa: F811
):
    service = client.app.state.launch_service
    original_adopt, original_apply = service._adopt, service.rooms.apply
    revision = service.models.revision
    checked = []

    async def attempt_switch(stage):
        with pytest.raises(RoomError) as rejected:
            await service.models.save(service.models.current.model_copy(update={
                "model": "configuration-race-test",
            }))
        assert rejected.value.status == 409
        assert service.models.revision == revision
        checked.append(stage)

    async def adopting(draft_id):
        await attempt_switch("adoption")
        return await original_adopt(draft_id)

    async def applying(session, room, identity, action, body, target):
        if action == "start":
            await attempt_switch("final_start")
        return await original_apply(session, room, identity, action, body, target)

    monkeypatch.setattr(service, "_adopt", adopting)
    monkeypatch.setattr(service.rooms, "apply", applying)
    started = ok(client.post(launch["path"] + "/start", json={}))
    assert started["status"] == "started"
    assert checked == ["adoption", "final_start"]
    assert not service.models.operations and not service.models.switching
    # The operation guard is released after commit, so ordinary setting changes
    # remain possible once no generation or game turn is pending.
    saved = client.portal.call(service.models.save, service.models.current)
    assert saved["revision"] == revision + 1
