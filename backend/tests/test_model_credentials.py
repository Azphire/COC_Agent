import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.credentials import resolve_credential
from app.models.factory import create_model
from app.models.settings import ModelConfiguration, ModelSettings

OFFICIAL = "https://api.openai.com/v1/"


def settings(tmp_path, **kwargs):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path.as_posix()}/game.db",
        host_admin_token="isolated-host",
        **kwargs,
    )


def official(**kwargs):
    return ModelConfiguration(provider="openai", model="gpt-4.1-mini", base_url=OFFICIAL, **kwargs)


@pytest.mark.parametrize("legacy", ["", "ollama", " OLLAMA "])
async def test_environment_fallback_survives_empty_saved_config_and_restart(tmp_path, legacy):
    s = settings(tmp_path, model_api_key=legacy, openai_api_key="env-first")
    manager = ModelSettings(s)
    assert s.model_provider == "ollama" and s.model_name == "qwen3:8b"
    assert resolve_credential(s).key.get_secret_value() == "ollama"
    result = await manager.save(official())
    assert manager.ready() and result["api_key_set"]
    assert result["api_key_source"] == "OPENAI_API_KEY"
    assert "env-first" not in json.dumps(result)
    stored = manager.path.read_text(encoding="utf-8")
    assert "env-first" not in stored
    assert json.loads(stored)["configurations"]["openai"]["api_key"] == ""
    restored = settings(tmp_path, model_api_key=legacy, openai_api_key="env-rotated")
    reopened = ModelSettings(restored)
    assert reopened.ready() and restored.model_name == "gpt-4.1-mini"
    adapter = create_model(restored)
    assert adapter.client.api_key == "env-rotated"
    await adapter.close()


async def test_saved_then_model_environment_then_openai_precedence(tmp_path):
    s = settings(
        tmp_path,
        model_provider="openai",
        model_base_url=OFFICIAL,
        model_api_key="model-env",
        openai_api_key="openai-env",
    )
    manager = ModelSettings(s)
    assert resolve_credential(s).source == "MODEL_API_KEY"
    await manager.save(official())
    assert resolve_credential(s).key.get_secret_value() == "model-env"
    assert "model-env" not in manager.path.read_text()
    await manager.save(official(api_key="explicit"))
    assert resolve_credential(s).key.get_secret_value() == "explicit"
    await manager.save(
        ModelConfiguration(
            provider="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434/v1/"
        )
    )
    assert resolve_credential(s).key.get_secret_value() == "ollama"
    await manager.save(official())
    assert resolve_credential(s).key.get_secret_value() == "explicit"


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/v1/",
        "http://api.openai.com/v1/",
        "https://api.openai.com:444/v1/",
        "https://api.openai.com:0/v1/",
        "https://api.openai.com/v2/",
        "https://api.openai.com.evil.test/v1/",
        "https://evil.test/api.openai.com/v1/",
    ],
)
async def test_openai_key_and_previous_key_never_cross_service(tmp_path, url):
    s = settings(
        tmp_path,
        model_provider="openai",
        model_base_url=OFFICIAL,
        model_api_key="original-service-env",
        openai_api_key="official-env",
    )
    manager = ModelSettings(s)
    await manager.save(official(api_key="official-explicit"))
    await manager.save(ModelConfiguration(provider="openai", model="other", base_url=url))
    assert not manager.ready()
    assert not manager.public()["api_key_set"]
    assert resolve_credential(s).key.get_secret_value() == ""
    assert manager.current.api_key.get_secret_value() == ""


async def test_same_service_url_normalization_retains_explicit_key(tmp_path):
    manager = ModelSettings(settings(tmp_path))
    await manager.save(official(api_key="explicit"))
    await manager.save(
        ModelConfiguration(
            provider="openai", model="other-model", base_url="https://API.OPENAI.COM:443/v1"
        )
    )
    assert resolve_credential(manager.settings).key.get_secret_value() == "explicit"


def test_dotenv_loading_process_precedence_and_secret_repr(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=dotenv-secret\nMODEL_API_KEY=ollama\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = Settings(_env_file=env, model_provider="openai", model_base_url=OFFICIAL)
    assert resolve_credential(s).key.get_secret_value() == "dotenv-secret"
    assert resolve_credential(s).location == ".env"
    monkeypatch.setenv("OPENAI_API_KEY", "process-secret")
    s = Settings(_env_file=env, model_provider="openai", model_base_url=OFFICIAL)
    assert resolve_credential(s).key.get_secret_value() == "process-secret"
    assert resolve_credential(s).location == "process"
    assert "process-secret" not in repr(s) + s.model_dump_json() + repr(resolve_credential(s))


def test_old_empty_configuration_migrates_without_pinning_environment(tmp_path):
    (tmp_path / "host-model-settings.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "revision": 2,
                "configurations": {
                    "openai": {
                        "provider": "openai",
                        "model": "chosen-model",
                        "base_url": OFFICIAL,
                        "api_key": "",
                        "output_mode": "json_object",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    manager = ModelSettings(settings(tmp_path, openai_api_key="environment"))
    assert manager.ready() and manager.current.model == "chosen-model"
    assert manager.public()["api_key_source"] == "OPENAI_API_KEY"


def test_configuration_status_and_factory_agree_without_inference(tmp_path, monkeypatch, caplog):
    s = settings(tmp_path, openai_api_key="must-remain-secret")
    generate = AsyncMock(side_effect=AssertionError("status must never generate"))
    monkeypatch.setattr("app.models.openai_compatible.OpenAICompatibleClient.generate", generate)
    app = create_app(s)
    with TestClient(app, headers={"Authorization": "Bearer isolated-host"}) as client:
        saved = client.post(
            "/api/model/config",
            json={
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "base_url": OFFICIAL,
                "api_key": "",
            },
        )
        config = client.get("/api/model/config")
        status = client.get("/api/model/status")
        assert saved.status_code == 200 and status.json()["state"] == "unverified"
        assert config.json()["api_key_source"] == "OPENAI_API_KEY"
        assert "must-remain-secret" not in saved.text + config.text + status.text + caplog.text
        generate.assert_not_called()
