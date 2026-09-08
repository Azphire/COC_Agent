from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health(tmp_path: Path) -> None:
    database_path = tmp_path / "health.db"
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        model_provider="ollama",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "model_provider": "ollama"}
    assert database_path.is_file()
