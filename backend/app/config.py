from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


def resolve_backend_path(value: str | Path) -> Path:
    """Resolve relative configuration paths from backend/, independent of the shell."""
    path = Path(value).expanduser()
    return (BACKEND_DIR / path).resolve() if not path.is_absolute() else path.resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Path("../data")

    model_provider: Literal["ollama", "openai"] = "ollama"
    model_base_url: str = "http://127.0.0.1:11434/v1"
    model_name: str = "qwen3:8b"
    model_api_key: SecretStr = SecretStr("")

    database_url: str = "sqlite+aiosqlite:///../data/game.db"
    checkpoint_db_path: Path = Path("../data/agent_checkpoints.db")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("data_dir", "checkpoint_db_path", mode="before")
    @classmethod
    def resolve_paths(cls, value: str | Path) -> Path:
        return resolve_backend_path(value)

    @field_validator("database_url")
    @classmethod
    def resolve_database_url(cls, value: str) -> str:
        url = make_url(value)
        if url.drivername != "sqlite+aiosqlite" or not url.database:
            raise ValueError("DATABASE_URL must specify a sqlite+aiosqlite database file")
        if url.database == ":memory:":
            raise ValueError("DATABASE_URL must point to a file")
        path = resolve_backend_path(url.database)
        return url.set(database=path.as_posix()).render_as_string(hide_password=False)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
