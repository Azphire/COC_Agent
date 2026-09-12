"""Manual original-model full-module run. Each turn is chosen after reading its response.

Uses the package already loaded in data/prepared/changan/batch-16/acceptance.
Commands: serve/setup/turn/status/roll/accept/save/load/resume/export (no scripted route).
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import check_batch13 as driver
from module_package import read, settings_for, write

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "data/prepared/changan/batch-16/acceptance"
driver.DIRECTORY = DIRECTORY
driver.BASE = "http://127.0.0.1:8000/api"
driver.HOST = "local-package-review"


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
            for k in (
                "model_provider",
                "model_name",
                "model_context_limit",
                "model_output_limit",
            )
        },
    )
    (DIRECTORY / "server.pid").write_text(str(os.getpid()), encoding="utf8")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


def setup():
    os.environ["BATCH13_PREPARATION"] = read(DIRECTORY / "load-info.json")["preparation_id"]
    driver.setup()
    write(
        DIRECTORY / "scenario.json",
        {
            "module": "常暗之厢 · 第十六批全文准备",
            "characters": ["周岚：真人调查员", "沈砚：原模型AI调查员"],
            "start": "6号车厢",
            "predeclared": True,
            "dice": "server random; no resets",
        },
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve()
    elif len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup()
    elif len(sys.argv) > 1 and sys.argv[1] == "san-roll":
        prefix = read(DIRECTORY / "session.json")["prefix"]
        check = next(
            c
            for c in driver.request("GET", prefix + "/checks", player=True)
            if c["status"] == "pending" and c.get("sanity")
        )
        result = driver.request(
            "POST",
            prefix + f"/sanity/checks/{check['id']}/roll",
            {"expected_stage": check["sanity"]["stage"]},
            player=True,
        )
        print(result["check"]["sanity"])
    elif len(sys.argv) > 1 and sys.argv[1] in {"retry", "host-note", "confirm"}:
        from uuid import uuid4

        prefix = read(DIRECTORY / "session.json")["prefix"]
        if sys.argv[1] == "retry":
            driver.request("POST", prefix + "/agent-cycle/retry", {})
        elif sys.argv[1] == "host-note":
            driver.request(
                "POST",
                prefix + "/messages",
                {
                    "text": sys.argv[2],
                    "visibility": "public",
                    "client_request_id": str(uuid4()),
                },
            )
        else:
            args = read(Path(sys.argv[2]))
            result = driver.request("POST", prefix + "/module-action", args)
            write(DIRECTORY / ("confirm-" + str(args["source_event_seq"]) + ".json"), result)
        print("Recorded", sys.argv[1])
    else:
        driver.main()
