"""Host-owned model configuration; game state is never rewritten on switching."""

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select

from app.models.base import ModelError
from app.rooms.service import RoomError


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: Literal["ollama", "openai"]
    model: str = Field(max_length=200)
    base_url: str = Field(max_length=1000)
    api_key: SecretStr = SecretStr("")
    output_mode: Literal["json_schema", "json_object"] = "json_object"

    @field_validator("base_url")
    @classmethod
    def clean_url(cls, value):
        if not value:
            return value
        try:
            url = urlsplit(value)
            valid = (
                url.scheme in {"https", "http"}
                and url.hostname
                and not (url.username or url.password or url.query or url.fragment)
                and not any(c.isspace() or ord(c) < 32 for c in value)
            )
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("服务地址须为无凭据、查询参数或片段的 HTTP(S) 地址")
        return value.rstrip("/") + "/"

    def public(self):
        return {
            **self.model_dump(exclude={"api_key"}),
            "api_key_set": bool(self.api_key.get_secret_value())
            and self.api_key.get_secret_value() != "ollama",
        }


class ModelSettings:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.model_settings_path or settings.data_dir / "host-model-settings.json"
        self.configurations = {}
        self.audit = []
        self.revision = 0
        self.switching = False
        self.operations = {}
        self.service = None
        self.error = None
        self.verification = None
        current = ModelConfiguration(
            provider=settings.model_provider,
            model=settings.model_name,
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            output_mode=settings.model_output_mode,
        )
        self.configurations[current.provider] = current
        self.current = current
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                configs = {
                    k: ModelConfiguration.model_validate(v)
                    for k, v in data["configurations"].items()
                }
                self.current = configs[data["provider"]]
                self.configurations.update(configs)
                self.revision = int(data["revision"])
                self.audit = data.get("audit", [])
            except (ValueError, KeyError, TypeError, OSError):
                # Keep the UI usable, but do not silently use a different credential/model.
                self.error = "模型设置文件无法读取；请在模型设置中重新保存"
                self.current = current.model_copy(update={"model": ""})
        self.apply_fields(self.current)

    def apply_fields(self, config):
        for key, value in {
            "model_provider": config.provider,
            "model_name": config.model,
            "model_base_url": config.base_url,
            "model_api_key": config.api_key,
            "model_output_mode": config.output_mode,
        }.items():
            setattr(self.settings, key, value)

    @contextmanager
    def operation(self, label):
        # Admission is synchronous on the application's event loop. Saving sets the
        # flag before its first await, so HTTP, WS and model work cannot race it.
        if self.switching:
            raise RoomError("模型正在切换，请稍后重试", 409)
        token = object()
        self.operations[token] = label
        try:
            yield
        finally:
            self.operations.pop(token, None)

    async def busy(self):
        from app.persistence.agent_models import AgentCycle, AgentRun

        tasks = [{"kind": label} for label in self.operations.values()]
        svc = self.service
        if svc:
            tasks += [{"kind": "模组准备", "id": str(key)} for key in svc.preparation.generating]
            tasks += [
                {"kind": "回合调度", "room_id": key}
                for key, task in svc.runtime.tasks.items()
                if not task.done()
            ]
            tasks += [
                {"kind": "摘要重建", "room_id": str(key)}
                for key, lock in svc.summary_recovery.locks.items()
                if lock.locked()
            ]
            async with svc.rooms.database.sessions() as session:
                cycles = await session.scalars(
                    select(AgentCycle).where(
                        AgentCycle.status.in_(
                            [
                                "running",
                                "waiting_for_roll",
                                "waiting_for_review",
                                "failed",
                                "queued",
                                "queued_action",
                                "suspended",
                            ]
                        )
                    )
                )
                tasks += [
                    {
                        "kind": "未完成回合",
                        "id": c.id,
                        "room_id": c.room_id,
                        "status": c.status,
                        "stage": c.state.get("wait_reason"),
                    }
                    for c in cycles
                ]
                runs = await session.scalars(select(AgentRun).where(AgentRun.status == "running"))
                tasks += [{"kind": "生成", "id": r.id, "stage": r.graph_node} for r in runs]
        return tasks

    def public(self):
        return {
            **self.current.public(),
            "revision": self.revision,
            "configurations": {k: v.public() for k, v in self.configurations.items()},
            "audit": self.audit[-20:],
        }

    def ready(self):
        c = self.current
        return bool(
            c.model and c.base_url and (c.provider == "ollama" or c.public()["api_key_set"])
        )

    async def save(self, config):
        if self.switching:
            raise RoomError("模型正在切换", 409)
        self.switching = True
        try:
            busy = await self.busy()
            if busy:
                raise RoomError("请先完成或取消未完成回合，并等待所有生成任务结束", 409)
            if config.provider == "ollama":
                from app.models.factory import local_ollama_origin

                try:
                    local_ollama_origin(config.base_url)
                except ModelError:
                    raise RoomError("Ollama 地址须为本机 11434 端口 /v1/", 422) from None
            previous = self.configurations.get(config.provider)
            if not config.api_key.get_secret_value() and previous:
                config = config.model_copy(update={"api_key": previous.api_key})
            configs = {**self.configurations, config.provider: config}
            audit = [
                *self.audit,
                {
                    "revision": self.revision + 1,
                    "at": datetime.now(UTC).isoformat(),
                    "provider": config.provider,
                    "model": config.model,
                    "output_mode": config.output_mode,
                },
            ]
            data = {
                "provider": config.provider,
                "revision": self.revision + 1,
                "configurations": {
                    k: {
                        **v.model_dump(exclude={"api_key"}),
                        "api_key": v.api_key.get_secret_value(),
                    }
                    for k, v in configs.items()
                },
                "audit": audit,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, filename = tempfile.mkstemp(prefix=".model-settings-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                if self.service:
                    await self.service.model.close()
                os.replace(filename, self.path)
            finally:
                if os.path.exists(filename):
                    os.unlink(filename)
            self.current, self.configurations = config, configs
            self.audit, self.revision = audit, self.revision + 1
            self.apply_fields(config)
            self.error = self.verification = None
            return self.public()
        except OSError:
            raise RoomError("模型设置保存失败，请检查本地文件权限；原配置仍生效", 503) from None
        finally:
            self.switching = False
