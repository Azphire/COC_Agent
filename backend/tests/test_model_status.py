from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("state", ["present", "missing", "offline"])
def test_status_checks_catalogue_without_inference(monkeypatch, tmp_path: Path, state: str) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url) == "http://127.0.0.1:11434/api/tags"
        assert not request.content
        if state == "offline":
            raise httpx.ConnectError("Ollama is offline", request=request)
        return httpx.Response(
            200, json={"models": [{"name": "qwen3:8b"}] if state == "present" else []}
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.api.model.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    sdk = Mock(side_effect=AssertionError("Status must not create an inference client"))
    monkeypatch.setattr("app.models.openai_compatible.AsyncOpenAI", sdk)
    settings = Settings(
        _env_file=None, data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'status.db').as_posix()}",
        model_api_key="test-secret-not-for-output",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/model/status")
        assert response.status_code == 200
        assert response.json() == {
            "provider": "ollama", "model": "qwen3:8b", "available": state == "present",
        }
        # Health still works independently and must not even query the model catalogue.
        assert client.get("/api/health").json()["status"] == "ok"
    assert len(requests) == 1
    sdk.assert_not_called()
