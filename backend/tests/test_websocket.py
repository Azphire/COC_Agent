from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_websocket_json_echo(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        host_admin_token="echo-test-host",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'websocket.db').as_posix()}",
    )
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(
                {"type": "auth", "credential_type": "host", "token": "echo-test-host"}
            )
            assert websocket.receive_json()["type"] == "connected"
            message = {"type": "test", "text": "你好，CoC！", "payload": {"roll": [1, 20]}}
            websocket.send_json(message)
            assert websocket.receive_json() == {"type": "echo", "data": message}
