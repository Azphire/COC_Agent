"""Local live verification. Actions and automatic roll choices use player-visible data only."""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

import check_batch13 as driver
from module_package import ROOT, read, settings_for, write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["serve", "setup", "turn", "status", "save", "load", "export"]
    )
    parser.add_argument("--run", required=True)
    parser.add_argument("--port", type=int, default=8019)
    parser.add_argument("--text", default="")
    args = parser.parse_args()
    directory = (ROOT / "data/prepared/changan/batch-19" / args.run).resolve()
    assert directory.is_relative_to((ROOT / "data/prepared/changan/batch-19").resolve())
    assert (directory / "load-info.json").exists()
    driver.DIRECTORY, driver.BASE, driver.HOST = (
        directory,
        f"http://127.0.0.1:{args.port}/api",
        "local-package-review",
    )

    def log(value):
        with (directory / "live-ledger.jsonl").open("a", encoding="utf8") as stream:
            stream.write(
                json.dumps(
                    {"utc": datetime.now(timezone.utc).isoformat(), **value}, ensure_ascii=False
                )
                + "\n"
            )

    if args.command == "serve":
        import uvicorn

        from app.main import create_app

        settings = settings_for(directory)
        assert settings.model_provider == "ollama" and settings.model_name == "qwen3:8b"
        log({"kind": "process_start", "pid": os.getpid()})
        (directory / "server.pid").write_text(str(os.getpid()), encoding="utf8")
        write(
            directory / "model-settings.json",
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
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")
        return
    if args.command == "setup":
        os.environ["BATCH13_PREPARATION"] = read(directory / "load-info.json")["preparation_id"]
        original = driver.request

        def named(method, path, body=None, **kwargs):
            if method == "POST" and path == "/rooms":
                body = {**body, "name": "第十九批 · " + args.run}
            return original(method, path, body, **kwargs)

        driver.request = named
        driver.setup()
        log({"kind": "normal_setup", "dice_policy": "accept all actual results; no reset"})
        return
    session = read(directory / "session.json")
    prefix = session["prefix"]
    request = driver.request
    if args.command in {"save", "load", "export"}:
        log({"kind": args.command + "_start"})
        if args.command == "save":
            write(directory / "before-restart.json", request("GET", prefix))
            write(directory / "before-restart-checks.json", request("GET", prefix + "/checks"))
        sys.argv = [sys.argv[0], args.command]
        driver.main()
        if args.command == "load":
            after = request("GET", prefix)
            checks = request("GET", prefix + "/checks")
            before = read(directory / "before-restart.json")
            comparison = {
                k: before["session_state"][k] == after["session_state"][k]
                for k in (
                    "characters",
                    "module_runtime",
                    "combat",
                    "time_receipts",
                    "game_minute",
                    "game_round",
                    "sanity_day",
                )
            }
            comparison["checks"] = read(directory / "before-restart-checks.json") == checks
            write(directory / "after-restart.json", after)
            write(directory / "restore-comparison.json", comparison)
            assert all(comparison.values()), comparison
        log({"kind": args.command + "_end"})
        return
    room = request("GET", prefix, player=True)
    if args.command == "status":
        print(
            json.dumps(
                {"room": room, "events": request("GET", prefix + "/events?limit=30", player=True)},
                ensure_ascii=False,
            )
        )
        return
    start, after_seq = time.monotonic(), room["revision"]
    log({"kind": "interaction_start", "text": args.text, "visible_revision": after_seq})
    if args.text:
        result = request(
            "POST",
            prefix + "/actions",
            {"text": args.text, "client_request_id": str(uuid4())},
            player=True,
        )
        log({"kind": "action_accepted", "event": result["event"]})
    handled = set()
    while time.monotonic() - start < 600:
        room = request("GET", prefix, player=True)
        cycle = (room.get("game") or {}).get("cycle") or {}
        checks = request("GET", prefix + "/checks", player=True)
        for c in checks:
            if c["status"] != "pending" or c.get("target_member_id") != session["player_id"]:
                continue
            sanity = c.get("sanity")
            stage = (sanity or c.get("settlement") or {}).get("stage", "initial")
            key = (c["id"], stage)
            if key in handled or stage == "symptom":
                continue
            if sanity:
                path, body = f"/sanity/checks/{c['id']}/roll", {"expected_stage": stage}
            elif stage == "choice":
                path, body = f"/checks/{c['id']}/choice", {"operation": "accept"}
            else:
                path, body = f"/checks/{c['id']}/roll", {}
            response = request("POST", prefix + path, body, player=True)
            log(
                {"kind": "player_rule", "path": path, "body": body, "result": response.get("check")}
            )
            handled.add(key)
        if cycle.get("status") in {None, "completed", "cancelled", "withdrawn", "superseded"}:
            break
        if cycle.get("status") == "failed":
            log({"kind": "failure", "cycle": cycle})
            raise RuntimeError(cycle)
        if cycle.get("status", "").startswith("waiting") and not any(
            c["status"] == "pending" for c in checks
        ):
            break
        time.sleep(0.8)  # Only waiting for an active model/rule operation, never to fill test time.
    else:
        raise TimeoutError("Preserved unfinished live cycle")
    elapsed = time.monotonic() - start
    events = request("GET", prefix + f"/events?after_seq={after_seq}&limit=1000", player=True)[
        "events"
    ]
    write(directory / f"visible-after-{after_seq}.json", {"room": room, "events": events})
    for e in events:
        if e["type"] in {
            "keeper.narration",
            "npc.spoke",
            "agent.spoke",
            "module.interaction",
            "scene.updated",
            "check.resolved",
            "action.clarification_requested",
            "agent.action_proposed",
        }:
            p = e["payload"]
            print(
                e["seq"],
                e["type"],
                p.get("text") or p.get("scene_summary") or p.get("display_text") or p,
            )
    print("RESOURCES", json.dumps(room["session_state"]["characters"], ensure_ascii=False))
    print("ITEMS", json.dumps(room.get("inventory"), ensure_ascii=False))
    print("CYCLE", cycle)
    log(
        {
            "kind": "interaction_end",
            "elapsed_seconds": elapsed,
            "revision": room["revision"],
            "cycle": cycle,
        }
    )


if __name__ == "__main__":
    main()
