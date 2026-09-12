"""Manual batch17 model driver. One isolated room; no route scripting or host repairs."""

import os
import sys
from contextlib import asynccontextmanager

import check_batch13 as driver
from module_package import ROOT, read, settings_for, write

DIRECTORY = ROOT / "data/prepared/changan/batch-17" / os.environ.get("BATCH17_RUN", "acceptance")
if not DIRECTORY.resolve().is_relative_to((ROOT / "data/prepared/changan/batch-17").resolve()):
    raise ValueError("Batch17 isolation required")
driver.DIRECTORY, driver.BASE, driver.HOST = (
    DIRECTORY,
    "http://127.0.0.1:8000/api",
    "local-package-review",
)


def serve():
    import traceback

    import uvicorn

    from app.main import create_app

    settings = settings_for(DIRECTORY)
    app = create_app(settings)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def traced(application):
        async with original(application):
            runtime = application.state.agent_service.runtime
            fail = runtime.fail

            async def log_error(*args, **kwargs):
                with (DIRECTORY / "runtime-errors.txt").open("a", encoding="utf8") as output:
                    traceback.print_exc(file=output)
                return await fail(*args, **kwargs)

            runtime.fail = log_error
            yield

    app.router.lifespan_context = traced
    write(
        DIRECTORY / "model-settings.json",
        {
            k: getattr(settings, k)
            for k in ("model_provider", "model_name", "model_context_limit", "model_output_limit")
        },
    )
    (DIRECTORY / "server.pid").write_text(str(os.getpid()), encoding="utf8")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


def setup():
    os.environ["BATCH13_PREPARATION"] = read(DIRECTORY / "load-info.json")["preparation_id"]
    original_request = driver.request

    def setup_request(method, path, body=None, **kwargs):
        if method == "POST" and path == "/rooms":
            body = {"name": "第十七批 · 常暗之厢最终包验证"}
        if method == "PATCH" and path.startswith("/characters/"):
            body = {
                **body,
                "interest_skills": {
                    k: {"points": v}
                    for k, v in {
                        "spot_hidden": 35,
                        "first_aid": 40,
                        "brawl": 30,
                        "listen": 15,
                    }.items()
                },
            }
        return original_request(method, path, body, **kwargs)

    driver.request = setup_request
    try:
        driver.setup()
    finally:
        driver.request = original_request
    write(
        DIRECTORY / "scenario.json",
        {
            "module": "常暗之厢 · 第十七批 · 未批准补充数值隔离测试",
            "characters": ["周岚：真人", "沈砚：原模型AI"],
            "dice": "server random, no resets",
            "supplement_user_approved": False,
        },
    )


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "serve":
        serve()
    elif command == "setup":
        setup()
    elif command in {"san-roll", "combat-step", "retry"}:
        prefix = read(DIRECTORY / "session.json")["prefix"]
        if command == "san-roll":
            check = next(
                c
                for c in driver.request("GET", prefix + "/checks", player=True)
                if c["status"] == "pending" and c.get("sanity")
            )
            print(
                driver.request(
                    "POST",
                    prefix + f"/sanity/checks/{check['id']}/roll",
                    {"expected_stage": check["sanity"]["stage"]},
                    player=True,
                )["check"]["sanity"]
            )
        elif command == "combat-step":
            room = driver.request("GET", prefix, player=True)
            action = room["combat"]["pending"]
            print(
                driver.request(
                    "POST",
                    prefix + "/combat/step",
                    {"action_id": action["id"], "stage": action["stage"], "operation": sys.argv[2]},
                    player=True,
                )
            )
        else:
            print(driver.request("POST", prefix + "/agent-cycle/retry", {}))
    else:
        driver.main()
