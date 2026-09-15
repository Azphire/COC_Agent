import asyncio
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIStatusError
from openai.types.chat import ChatCompletion

from app.agents.model import AgentModelClient, FakeModelAdapter
from app.agents.schemas import SummaryOutput
from app.config import Settings
from app.main import create_app
from app.models.base import ModelError, ModelFormatError
from app.models.openai_compatible import OpenAICompatibleClient
from app.models.settings import ModelConfiguration, ModelSettings
from app.rooms.service import RoomError


def settings(tmp_path, **kwargs):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        host_admin_token="test-host-key",
        database_url=f"sqlite+aiosqlite:///{tmp_path.as_posix()}/game.db",
        **kwargs,
    )


def api_config(**kwargs):
    return {
        "provider": "openai",
        "model": "test-api-model",
        "base_url": "https://api.example.test/v1/",
        "api_key": "private-api-key",
        **kwargs,
    }


def test_persistence_preserves_credentials_and_nonmodel_settings(tmp_path):
    config = settings(tmp_path, agent_max_calls=7)
    with TestClient(
        create_app(config), headers={"Authorization": "Bearer test-host-key"}
    ) as client:
        result = client.post("/api/model/config", json=api_config())
        assert result.status_code == 200
        assert "private-api-key" not in result.text
        assert result.json()["api_key_set"]
        assert client.get("/api/model/status").json()["state"] == "unverified"
        assert (
            client.post(
                "/api/model/config",
                json={
                    "provider": "ollama",
                    "model": "other-local",
                    "base_url": "http://127.0.0.1:11434/v1/",
                },
            ).status_code
            == 200
        )
        assert client.post("/api/model/config", json=api_config(api_key="")).status_code == 200
        assert config.model_api_key.get_secret_value() == "private-api-key"
        assert (
            config.agent_max_calls == 7
            and config.host_admin_token.get_secret_value() == "test-host-key"
        )
    restored = settings(tmp_path, agent_max_calls=7)
    manager = ModelSettings(restored)
    assert restored.model_provider == "openai" and restored.model_name == "test-api-model"
    assert restored.model_api_key.get_secret_value() == "private-api-key"
    assert manager.revision == 3
    assert manager.verification is None
    assert "private-api-key" not in json.dumps(manager.public())


async def test_real_adapter_replaced_and_generation_reservation_blocks_switch(
    tmp_path, monkeypatch
):
    config = settings(tmp_path)
    manager = ModelSettings(config)
    old = FakeModelAdapter([{"content": "old"}])
    old.close = AsyncMock()
    client = AgentModelClient(config, old)
    client.configuration = manager
    # A minimal service is sufficient here; integrated DB blockers are tested below.
    manager.service = type("Service", (), {"model": client})()

    async def busy():
        return list(manager.operations.values())

    monkeypatch.setattr(manager, "busy", busy)
    await client.generate([], response_schema=SummaryOutput)
    await manager.save(ModelConfiguration(**api_config()))
    old.close.assert_awaited_once()
    assert client.adapter is None
    entered, release = asyncio.Event(), asyncio.Event()

    async def response():
        entered.set()
        await release.wait()
        return {"content": "new"}

    new = FakeModelAdapter([response])
    factory = Mock()

    def make(s):
        assert s.model_provider == "openai" and s.model_name == "test-api-model"
        factory(s.model_name)
        return new

    monkeypatch.setattr("app.agents.model.create_model", make)
    task = asyncio.create_task(client.generate([], response_schema=SummaryOutput))
    await entered.wait()
    with pytest.raises(RoomError):
        await manager.save(ModelConfiguration(**api_config(model="must-not-apply")))
    release.set()
    result, _ = await task
    assert result.structured.content == "new"
    assert client.adapter is new and client.calls[-1]["model"] == "test-api-model"
    assert client.calls[-1]["config_revision"] == 1
    await client.close()


@pytest.mark.parametrize(
    "status", ["waiting_for_roll", "waiting_for_review", "failed", "queued_action", "suspended"]
)
def test_durable_unfinished_cycles_block_switch(tmp_path, status):
    from app.persistence.agent_models import AgentCycle

    app = create_app(settings(tmp_path))
    with TestClient(app, headers={"Authorization": "Bearer test-host-key"}) as client:
        room_id = client.post("/api/rooms", json={"name": "switch test"}).json()["room"]["id"]

        async def insert():
            async with app.state.room_service.transaction() as session:
                session.add(
                    AgentCycle(
                        id="unfinished",
                        room_id=room_id,
                        status=status,
                        state={"pending_check_id": "keep-dice", "model_calls": 4},
                    )
                )

        client.portal.call(insert)
        assert client.post("/api/model/config", json=api_config()).status_code == 409
        assert app.state.settings.model_provider == "ollama"
        # No catalogue request needed to inspect these durable blockers.
        busy = client.portal.call(app.state.model_settings.busy)
        assert any(item.get("status") == status for item in busy)


def completion(content, usage=True):
    return ChatCompletion.model_validate(
        {
            "id": "response",
            "object": "chat.completion",
            "created": 0,
            "model": "test-api-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
            if usage
            else None,
        }
    )


async def test_api_format_repair_usage_and_original_budget(tmp_path, monkeypatch):
    config = settings(tmp_path, model_provider="openai", model_api_key="private-api-key")
    adapter = OpenAICompatibleClient(config)
    create = AsyncMock(side_effect=[completion("invalid"), completion('{"content":"ok"}')])
    monkeypatch.setattr(adapter.client.chat.completions, "create", create)
    gateway = AgentModelClient(config, adapter)
    count = 0

    async def budget():
        nonlocal count
        count += 1
        if count > 2:
            raise ModelError("budget exhausted")

    try:
        result, _ = await gateway.generate([], response_schema=SummaryOutput, on_call=budget)
        assert result.structured.content == "ok" and count == 2
        assert all(
            c["token_usage"] == {"input": 11, "output": 7, "total": 18} for c in gateway.calls
        )
        request = create.call_args.kwargs
        assert request["response_format"] == {"type": "json_object"}
        assert "JSON schema" in request["messages"][0]["content"]
        with pytest.raises(ModelError, match="budget exhausted"):
            await gateway.generate([], response_schema=SummaryOutput, on_call=budget)
        assert create.await_count == 2
    finally:
        await gateway.close()


@pytest.mark.parametrize(
    "code,label",
    [(401, "认证"), (403, "认证"), (429, "限流"), (400, "参数"), (404, "不存在"), (503, "失败")],
)
async def test_api_failures_are_safe_without_retry(tmp_path, monkeypatch, code, label):
    config = settings(tmp_path, model_provider="openai", model_api_key="private-api-key")
    adapter = OpenAICompatibleClient(config)
    error = APIStatusError(
        "private-api-key sensitive provider body",
        response=httpx.Response(code, request=httpx.Request("POST", "https://example.test")),
        body={},
    )
    create = AsyncMock(side_effect=error)
    monkeypatch.setattr(adapter.client.chat.completions, "create", create)
    gateway = AgentModelClient(config, adapter)
    try:
        with pytest.raises(ModelError, match=label) as raised:
            await gateway.generate([], response_schema=SummaryOutput)
        assert "private" not in str(raised.value) and create.await_count == 1
        assert gateway.calls[0]["token_usage"] is None
    finally:
        await gateway.close()


def test_unknown_usage_and_invalid_tools_are_format_errors():
    parsed = OpenAICompatibleClient._parse_response(
        completion('{"content":"ok"}', False), SummaryOutput
    )
    assert parsed.token_usage is None
    bad = ChatCompletion.model_validate(
        {
            **completion(None).model_dump(),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call1",
                                "type": "function",
                                "function": {"name": "inspect", "arguments": "[]"},
                            }
                        ],
                    },
                }
            ],
        }
    )
    with pytest.raises(ModelFormatError) as raised:
        OpenAICompatibleClient._parse_response(bad, None)
    assert raised.value.token_usage["total"] == 18


def test_host_boundary_and_validation_never_echo_keys(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/api/model/config").status_code == 401
        assert client.post("/api/model/config", json=api_config()).status_code == 401
        response = client.post(
            "/api/model/config",
            headers={"Authorization": "Bearer test-host-key"},
            json=api_config(base_url="https://user:private-api-key@example.test/v1"),
        )
        assert response.status_code == 422 and "private-api-key" not in response.text


async def test_save_failure_keeps_configuration_and_can_recover(tmp_path, monkeypatch):
    manager = ModelSettings(settings(tmp_path))
    original = manager.current
    import os

    replace = os.replace
    monkeypatch.setattr(
        "app.models.settings.os.replace", lambda *_: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(RoomError):
        await manager.save(ModelConfiguration(**api_config()))
    assert manager.current == original and manager.switching is False
    monkeypatch.setattr("app.models.settings.os.replace", replace)
    await manager.save(ModelConfiguration(**api_config()))
    assert manager.current.model == "test-api-model"


def test_explicit_test_uses_game_dynamic_contract(tmp_path):
    app = create_app(settings(tmp_path, model_provider="openai", model_api_key="test-key"))
    adapter = FakeModelAdapter(
        responder=lambda messages, kwargs: {
            "public_narration": "房间为空，窗户关闭。",
        }
    )
    app.state.agent_model_adapter = adapter
    with TestClient(app, headers={"Authorization": "Bearer test-host-key"}) as client:
        assert client.get("/api/model/status").json()["state"] == "unverified"
        assert not adapter.prompts
        response = client.post("/api/model/test")
        assert response.status_code == 200
        assert response.json()["state"] == "available", response.text
        assert response.json()["calls"][0]["schema"] == "KeeperNarration"
        assert client.get("/api/model/status").json()["state"] == "available"


async def test_preparation_and_summary_between_calls_block_switch(tmp_path):
    app = create_app(settings(tmp_path))
    async with app.router.lifespan_context(app):
        svc, manager = app.state.agent_service, app.state.model_settings
        svc.preparation.generating.add("prepared-module")
        with pytest.raises(RoomError):
            await manager.save(ModelConfiguration(**api_config()))
        svc.preparation.generating.clear()
        lock = asyncio.Lock()
        svc.summary_recovery.locks["room"] = lock
        async with lock:
            assert any(t["kind"] == "摘要重建" for t in await manager.busy())
            with pytest.raises(RoomError):
                await manager.save(ModelConfiguration(**api_config()))
        await manager.save(ModelConfiguration(**api_config()))


async def test_admission_during_adapter_close_and_rollback(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path))
    async with app.router.lifespan_context(app):
        manager = app.state.model_settings
        entered, release = asyncio.Event(), asyncio.Event()

        async def close():
            entered.set()
            await release.wait()

        monkeypatch.setattr(app.state.agent_service.model, "close", close)
        saving = asyncio.create_task(manager.save(ModelConfiguration(**api_config())))
        await entered.wait()
        with pytest.raises(RoomError):
            with manager.operation("new HTTP/WS work"):
                pytest.fail("new work entered during switch")
        with pytest.raises(RoomError):
            await manager.save(ModelConfiguration(**api_config(model="other")))
        release.set()
        await saving
        assert manager.current.model == "test-api-model"


async def test_status_recovers_after_safe_api_failure(tmp_path, monkeypatch):
    config = settings(tmp_path, model_provider="openai", model_api_key="private-key")
    manager = ModelSettings(config)
    adapter = OpenAICompatibleClient(config)
    create = AsyncMock(side_effect=[ModelError("request failed"), completion('{"content":"ok"}')])
    monkeypatch.setattr(adapter.client.chat.completions, "create", create)
    gateway = AgentModelClient(config, adapter)
    gateway.configuration = manager
    try:
        with pytest.raises(ModelError):
            await gateway.generate([], response_schema=SummaryOutput)
        assert manager.verification == "failed"
        await gateway.generate([], response_schema=SummaryOutput)
        assert manager.verification == "available" and manager.error is None
        assert create.await_count == 2 and config.model_provider == "openai"
    finally:
        await gateway.close()


async def test_dynamic_api_schema_omits_server_fields_and_keeps_rule_validation(
    tmp_path, monkeypatch
):
    from app.agents.adjudication_schemas import KeeperNarration
    from app.agents.generation_contracts import generation_contract
    from app.models.openai_compatible import strict_schema

    contract = generation_contract(KeeperNarration, {"current_scene_reference": "bound-scene"})
    config = settings(tmp_path, model_provider="openai", model_api_key="private-key")
    adapter = OpenAICompatibleClient(config)
    create = AsyncMock(return_value=completion('{"public_narration":"ok"}'))
    monkeypatch.setattr(adapter.client.chat.completions, "create", create)
    try:
        result = await adapter.generate([], response_schema=contract)
        assert result.structured.current_scene_reference == "bound-scene"
        schema = json.loads(
            create.call_args.kwargs["messages"][0]["content"].split("JSON schema: ")[1]
        )
        assert "current_scene_reference" not in schema["properties"]
        assert "public_narration" in schema["required"]
        create.return_value = completion(json.dumps({"public_narration": "x" * 2001}))
        with pytest.raises(ModelFormatError):
            await adapter.generate([], response_schema=contract)
        with pytest.raises(ModelError, match="JSON"):
            strict_schema({"type": "object", "additionalProperties": True})
    finally:
        await adapter.close()
