import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.rules.loader import load_rulesets


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Tests must not contact Ollama or the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)


@pytest.fixture
def rulesets():
    return load_rulesets()


@pytest.fixture
def development(rulesets):
    return rulesets["development-character-creation"]


@pytest.fixture
def character_settings(tmp_path):
    return Settings(
        _env_file=None,
        host_admin_token="test-host-credential-for-isolated-database-only",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'characters.db').as_posix()}",
    )


@pytest.fixture
def character_app(character_settings):
    return create_app(character_settings)


@pytest.fixture
def client(character_app):
    token = character_app.state.settings.host_admin_token.get_secret_value()
    with TestClient(
        character_app,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
