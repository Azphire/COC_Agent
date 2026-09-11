"""Original-model manual acceptance, isolated DBs and a declared original fixture.

Commands inherit check_batch13: serve, setup, turn TEXT, status, roll, accept,
save, load, resume, export. No fixed dice, automatic player input or user DBs.
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import check_batch13 as driver

ROOT = Path(__file__).resolve().parents[2]
driver.DIRECTORY = ROOT / ".cache/batch-15/live"
driver.BASE = "http://127.0.0.1:8015/api"
driver.HOST = "batch15-isolated-acceptance-host"


def serve():
    import uvicorn

    from app.agents.modules import Module, load_modules
    from app.config import Settings
    from app.main import create_app

    directory = driver.DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        host_admin_token=driver.HOST,
        data_dir=directory / "data",
        database_url=f"sqlite+aiosqlite:///{(directory / 'game.db').as_posix()}",
        knowledge_db_path=directory / "knowledge.db",
        checkpoint_db_path=directory / "checkpoint.db",
    )
    if settings.model_provider != "ollama" or urlparse(settings.model_base_url).hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("Existing local Ollama only")
    app = create_app(settings)
    # Keep source modules untouched. This declared fixture is frozen by normal room binding.
    original = load_modules()["stopped-clock"]
    fixture = original.model_dump(mode="json")
    fixture["title"] = "第十五批隔离验收：维修工坊"
    fixture["public_introduction"] = "你与沈砚来到维修工坊，试验一台故障的电动涡轮。"
    fixture["keeper_brief"] = "隔离验收场景：只处理非战斗行动，不引入原钟楼的调查线索。"
    fixture["scenes"] = [
        {
            "id": "square",
            "title": "维修工坊",
            "public_description": (
                "维修工坊里有一张坚固桌子，你与沈砚已同意进行一次友好的掰手腕，双方都要争胜，"
                "这是非战斗比赛。旁边的电动涡轮传动齿轮错位，供电线路也有断点，"
                "两处故障都必须修好才能运转。这里有工具，但天黑前只有一次维修机会。"
            ),
        }
    ]
    fixture.update(
        initial_scene="square", npcs=[], clues=[], suggested_checks=[], completion_conditions=None
    )
    lifecycle = app.router.lifespan_context

    @asynccontextmanager
    async def with_fixture(application):
        async with lifecycle(application):
            application.state.agent_service.modules["stopped-clock"] = Module.model_validate(
                fixture
            )
            yield

    app.router.lifespan_context = with_fixture
    driver.write("fixture.json", fixture)
    driver.write(
        "model-settings.json",
        {
            key: getattr(settings, key)
            for key in ("model_provider", "model_name", "model_context_limit", "model_output_limit")
        },
    )
    (directory / "server.pid").write_text(str(os.getpid()), encoding="utf-8")
    uvicorn.run(app, host="127.0.0.1", port=8015, log_level="warning")


driver.serve = serve
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    driver.main()
