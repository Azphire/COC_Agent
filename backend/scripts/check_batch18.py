"""Isolated original-model verification; no host state repair or dice resets."""

import os
import sys

import check_batch13 as driver
from module_package import ROOT, read, settings_for, write

DIRECTORY = ROOT / "data/prepared/changan/batch-18" / os.environ.get("BATCH18_RUN", "debug-v1")
if not DIRECTORY.resolve().is_relative_to((ROOT / "data/prepared/changan/batch-18").resolve()):
    raise ValueError("Run directory must stay under batch-18")
driver.DIRECTORY = DIRECTORY
driver.BASE = "http://127.0.0.1:8018/api"
driver.HOST = "local-package-review"


def serve():
    import uvicorn

    from app.main import create_app

    settings = settings_for(DIRECTORY)
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
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8018, log_level="warning")


def setup():
    os.environ["BATCH13_PREPARATION"] = read(DIRECTORY / "load-info.json")["preparation_id"]
    original_request = driver.request

    def named_request(method, path, body=None, **kwargs):
        if method == "POST" and path == "/rooms":
            body = {**body, "name": "第十八批 · 常暗之厢"}
        return original_request(method, path, body, **kwargs)

    driver.request = named_request
    driver.setup()
    driver.request = original_request
    write(
        DIRECTORY / "scenario.json",
        {
            "module": "常暗之厢 · 第十八批正式获批包",
            "start": "6号车厢",
            "characters": ["周岚：真人调查员", "沈砚：原模型AI调查员"],
            "predeclared": True,
            "dice": "server random; no resets",
        },
    )


if __name__ == "__main__":
    driver.serve = serve
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "serve":
        serve()
    elif command == "setup":
        setup()
    elif command == "san-roll":
        prefix = read(DIRECTORY / "session.json")["prefix"]
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
            )["check"]
        )
    elif command == "combat-step":
        prefix = read(DIRECTORY / "session.json")["prefix"]
        action = driver.request("GET", prefix, player=True)["combat"]["pending"]
        result = driver.request(
            "POST",
            prefix + "/combat/step",
            {"action_id": action["id"], "stage": action["stage"], "operation": sys.argv[2]},
            player=True,
        )
        print(result["room"]["combat"])
    else:
        driver.main()
