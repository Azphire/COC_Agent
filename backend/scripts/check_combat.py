"""Manual batch-15 acceptance. Isolated original scene, existing model, never fixed dice."""

import json
import os
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import check_batch13 as driver

ROOT = Path(__file__).resolve().parents[2]
driver.DIRECTORY = ROOT / ".cache/batch-15-combat/live"
driver.BASE = "http://127.0.0.1:8016/api"
driver.HOST = "batch15-combat-isolated-host"


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
        raise ValueError("Use existing local model only")
    app = create_app(settings)
    fixture = load_modules()["stopped-clock"].model_dump(mode="json")
    fixture.update(
        title="扩展检定与战斗：隔离维修工坊",
        public_introduction="你与沈砚在维修工坊寻找机器故障的原因。",
        keeper_brief="原创隔离场景。桌边可进行双方都全力争胜的非战斗掰手腕比赛。机器故障需要同时观察位置与辨听时机才能定位。守卫阻止你们带走记录，双方可谈判也可发生冲突。",
        initial_scene="square",
        scenes=[
            {
                "id": "square",
                "title": "维修工坊",
                "public_description": (
                    "工坊的坚固桌旁摆着维修记录。机器罩内光线昏暗、齿轮持续运转；"
                    "要准确定位故障，必须同时看准位置并听准异响时机。"
                    "沈砚愿意同你全力争胜地掰手腕。"
                    "守卫站在敞开的门边，手持短棍阻止你们带走记录；退路畅通。"
                ),
            }
        ],
        npcs=[
            {
                "id": "guard",
                "name": "守卫",
                "public_description": "一名手持短棍的守卫，态度强硬，站在工坊的门边。",
                "keeper_notes": "主机另行明确创建战斗数值，不从文字推断HP。",
            }
        ],
        clues=[],
        suggested_checks=[],
        completion_conditions=None,
    )
    lifecycle = app.router.lifespan_context

    @asynccontextmanager
    async def with_fixture(application):
        async with lifecycle(application):
            application.state.agent_service.modules["stopped-clock"] = Module.model_validate(
                fixture
            )
            runtime = application.state.agent_service.runtime
            fail = runtime.fail

            async def debug_failure(*args, **kwargs):
                with (directory / "runtime-errors.txt").open("a", encoding="utf-8") as log:
                    traceback.print_exc(file=log)
                return await fail(*args, **kwargs)

            runtime.fail = debug_failure
            generate = runtime.generate_action_run

            async def debug_generation(*args, **kwargs):
                try:
                    return await generate(*args, **kwargs)
                except Exception:
                    with (directory / "generation-errors.txt").open("a", encoding="utf-8") as log:
                        traceback.print_exc(file=log)
                    raise

            runtime.generate_action_run = debug_generation
            yield

    app.router.lifespan_context = with_fixture
    driver.write("fixture.json", fixture)
    driver.write(
        "model-settings.json",
        {
            k: getattr(settings, k)
            for k in ("model_provider", "model_name", "model_context_limit", "model_output_limit")
        },
    )
    (directory / "server.pid").write_text(str(os.getpid()), encoding="utf-8")
    uvicorn.run(app, host="127.0.0.1", port=8016, log_level="warning")


def configure():
    session = json.loads((driver.DIRECTORY / "session.json").read_text(encoding="utf-8"))
    prefix = session["prefix"]
    room = driver.request("GET", prefix)
    driver.request(
        "POST",
        prefix + "/combat/setup",
        {
            "expected_revision": room["revision"],
            "reason": "原创隔离场景的守卫数值，首次掷骰前明确创建",
            "npc": {
                "id": "guard",
                "label": "守卫",
                "scene_id": "square",
                "team": "enemy",
                "attributes": {"dex": 70, "con": 50},
                "skills": {"brawl": 45, "dodge": 30},
                "hp": 8,
                "hp_max": 8,
                "armor": 1,
                "source": "主机原创隔离夹具；棍棒参考1907 PDF372，皮夹克参考PDF92",
                "weapons": [
                    {
                        "id": "baton",
                        "name": "短棍",
                        "skill": "brawl",
                        "damage": "1d6",
                        "source": "1907规则书武器表；基础小棍棒",
                    }
                ],
            },
        },
    )
    print("Configured one sourced guard; no dice have been rolled.")


driver.serve = serve


def control(command):
    session = json.loads((driver.DIRECTORY / "session.json").read_text(encoding="utf-8"))
    prefix = session["prefix"]
    room = driver.request("GET", prefix, player=True)
    if command == "brief":
        print("Cycle:", room["game"]["cycle"])
        print(
            "Combat:",
            json.dumps(
                {k: v for k, v in room["combat"].items() if k not in {"participants", "order"}},
                ensure_ascii=False,
            ),
        )
        print("Own HP:", room["combat"]["participants"].get(session["player_id"], {}).get("hp"))
        events = driver.request("GET", prefix + "/events?after_seq=300", player=True)["events"]
        for e in [
            e
            for e in events
            if e["type"]
            in {"keeper.narration", "combat.resolved", "combat.action_created", "chat.message"}
        ][-8:]:
            print(e["seq"], e["type"], json.dumps(e["payload"], ensure_ascii=False))
    elif command == "host-note":
        from uuid import uuid4

        r = driver.request(
            "POST",
            prefix + "/messages",
            {
                "text": sys.argv[2],
                "client_request_id": str(uuid4()),
            },
        )
        print("Host note", r["event"]["seq"])
    elif command == "inspect":
        events = driver.request("GET", prefix + "/events?after_seq=0", player=True)["events"]
        print(
            json.dumps(
                {
                    "cycle": room["game"]["cycle"],
                    "combat": room["combat"],
                    "checks": room["game"]["checks"],
                    "recent": [
                        e
                        for e in events
                        if e["type"]
                        in {
                            "keeper.narration",
                            "npc.spoke",
                            "action.submitted",
                            "combat.resolved",
                            "combat.action_created",
                            "combat.ended",
                        }
                    ][-8:],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif command == "combat-step":
        p = room["combat"]["pending"]
        body = {"action_id": p["id"], "stage": p["stage"], "operation": sys.argv[2]}
        if len(sys.argv) > 3:
            body["weapon_id"] = sys.argv[3]
        driver.write(f"step-{p['id']}-{p['stage']}.json", body)
        result = driver.request("POST", prefix + "/combat/step", body, player=True)
        print(json.dumps(result["room"]["combat"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "configure":
        configure()
    elif len(sys.argv) > 1 and sys.argv[1] in {"inspect", "combat-step", "brief", "host-note"}:
        control(sys.argv[1])
    else:
        driver.main()
