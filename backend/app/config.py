import os
from pathlib import Path
from typing import Literal

from pydantic import Field, PrivateAttr, SecretStr, field_validator
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
    host_admin_token: SecretStr = SecretStr("")
    data_dir: Path = Path("../data")

    model_provider: str = "ollama"
    model_base_url: str = "http://127.0.0.1:11434/v1/"
    model_name: str = "qwen3:8b"
    model_api_key: SecretStr = SecretStr("ollama")
    openai_api_key: SecretStr = SecretStr("")
    # Keep initial environment values separate from runtime/UI model fields.
    _environment_model_api_key: SecretStr = PrivateAttr(default=SecretStr(""))
    _environment_model_service: tuple = PrivateAttr(default=())
    _saved_model_api_key: SecretStr = PrivateAttr(default=SecretStr(""))
    _saved_model_service: tuple = PrivateAttr(default=())
    _credential_sources: dict[str, str] = PrivateAttr(default_factory=dict)
    model_timeout_seconds: float = Field(default=120.0, gt=0)
    model_temperature: float = Field(default=0.3, ge=0, le=2)
    # Budget includes the provider schema, system messages and output reserve.
    model_context_limit: int = Field(default=16384, ge=2048, le=32768)
    model_output_limit: int = Field(default=900, ge=128, le=4096)
    model_keep_alive: str = "5m"
    model_think: bool = False
    model_output_mode: Literal["json_schema", "json_object"] = "json_object"
    model_settings_path: Path | None = None
    summary_event_threshold: int = Field(default=20, ge=1, le=200)
    summary_context_threshold: int = Field(default=12000, ge=2000, le=100000)
    agent_max_calls: int = Field(default=12, ge=1, le=24)
    teammate_similarity_threshold: float = Field(default=0.65, ge=0.5, le=1)
    teammate_cooldown_cycles: int = Field(default=2, ge=1, le=10)
    summary_max_failures: int = Field(default=3, ge=1, le=10)
    agent_context_chars: int = Field(default=12000, ge=4000, le=24000)
    agent_event_window: int = Field(default=25, ge=20, le=30)

    database_url: str = "sqlite+aiosqlite:///../data/game.db"
    checkpoint_db_path: Path | None = None
    knowledge_db_path: Path | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    def __init__(self, **values):
        super().__init__(**values)
        from app.models.credentials import service_identity

        self._environment_model_api_key = self.model_api_key
        self._environment_model_service = service_identity(self.model_provider, self.model_base_url)
        self._credential_sources = {
            name: (
                "configuration"
                if name in values
                else "process"
                if name.upper() in os.environ
                else ".env"
                if name in self.model_fields_set
                else "default"
            )
            for name in ("model_api_key", "openai_api_key")
        }

    @field_validator(
        "data_dir", "checkpoint_db_path", "knowledge_db_path", "model_settings_path", mode="before"
    )
    @classmethod
    def resolve_paths(cls, value: str | Path | None) -> Path | None:
        return resolve_backend_path(value) if value is not None else None

    @property
    def agent_checkpoint_path(self) -> Path:
        return self.checkpoint_db_path or Path(make_url(self.database_url).database).with_suffix(
            ".checkpoints.db"
        )

    @property
    def knowledge_path(self) -> Path:
        return self.knowledge_db_path or self.data_dir / "knowledge" / "knowledge.db"

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
