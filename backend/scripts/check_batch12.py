"""Manual free conversation driver. No canned player utterances or fixed dice.

All writes are isolated under .cache/batch-12/live. The existing local model is
used by the real HTTP backend. Commands submit exactly the operator's words.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
DIRECTORY = ROOT / ".cache/batch-12/live"
BASE = "http://127.0.0.1:8012/api"
HOST = "batch12-isolated-acceptance-host"


def write(name, data):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    (DIRECTORY / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def request(method, path, body=None, *, player=False):
    token = (
        json.loads((DIRECTORY / "session.json").read_text(encoding="utf-8"))["token"]
        if player
        else HOST
    )
    response = httpx.request(
        method,
        BASE + path,
        json=body,
        headers={"Authorization": "Bearer " + token},
        trust_env=False,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def serve():
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    DIRECTORY.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        host_admin_token=HOST,
        data_dir=DIRECTORY / "data",
        database_url=f"sqlite+aiosqlite:///{(DIRECTORY / 'game.db').as_posix()}",
        knowledge_db_path=DIRECTORY / "knowledge.db",
        checkpoint_db_path=DIRECTORY / "checkpoint.db",
    )
    if settings.model_provider != "ollama" or urlparse(settings.model_base_url).hostname not in {
        "localhost",
        "127.0.0.1",
    }:
        raise ValueError("Existing local Ollama only")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8012, log_level="warning")


def setup():
    if (DIRECTORY / "session.json").exists():
        raise ValueError("Keep the existing room; no reset")
    created = request("POST", "/rooms", {"name": "第十二批 · 停摆的钟楼自由互动"})
    prefix = "/rooms/" + created["room"]["id"]
    joined = request(
        "POST", "/rooms/join", {"invite_code": created["invite_code"], "display_name": "周岚"}
    )
    player_id = joined["room"]["self_member_id"]
    room = request(
        "POST", prefix + "/members", {"display_name": "沈砚", "controller_type": "agent"}
    )["room"]
    teammate = next(m["id"] for m in room["members"] if m["controller_type"] == "agent")
    for name, member in [("周岚", player_id), ("沈砚", teammate)]:
        attrs = dict(str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60)
        card = request(
            "POST",
            "/characters/point-buy",
            {
                "ruleset_id": "coc7-character-creation",
                "name": name,
                "age": 28,
                "attributes": {k: {"value": v} for k, v in attrs.items()},
            },
        )
        card = request(
            "PATCH",
            "/characters/" + card["id"],
            {
                "version": card["version"],
                "occupation": "professor",
                "selected_occupation_skills": [
                    "accounting",
                    "anthropology",
                    "archaeology",
                    "history",
                ],
                "occupation_skills": {"credit_rating": {"points": 20}},
                "interest_skills": {
                    "spot_hidden": {"points": 35},
                    "listen": {"points": 35},
                    "psychology": {"points": 20},
                    "photography": {"points": 30},
                },
            },
        )
        card = request("POST", f"/characters/{card['id']}/finalize", {"version": card["version"]})
        room = request("POST", prefix + "/character-slots", {"character_id": card["id"]})["room"]
        slot = next(s["id"] for s in room["character_slots"] if s["public_summary"]["name"] == name)
        request("POST", prefix + "/character-assignments", {"slot_id": slot, "member_id": member})
    request("POST", prefix + "/module", {"module_id": "stopped-clock"})
    for member, profile in [
        (
            room["host_member_id"],
            {
                "role": "keeper",
                "name": "KP",
                "speaking_style": "简短自然，回应问题，NPC有迟疑也会反问。",
            },
        ),
        (
            teammate,
            {
                "role": "investigator",
                "name": "沈砚",
                "personality": "谨慎务实，喜欢与周岚商量，偶尔用一句冷幽默缓和紧张。",
                "goals": "弄清钟楼为什么停摆",
                "action_tendency": "别人问我时直接接话；提出具体协助，也会自己观察或动手试试。",
            },
        ),
    ]:
        profile = request("POST", "/agent-profiles", profile)
        request(
            "POST", prefix + "/agent-bindings", {"member_id": member, "profile_id": profile["id"]}
        )
    write(
        "session.json",
        {
            "prefix": prefix,
            "token": joined["member_token"],
            "player_id": player_id,
            "teammate": teammate,
            "started": time.time(),
        },
    )
    request("POST", prefix + "/ready", {"ready": True}, player=True)
    request("POST", prefix + "/ready", {"ready": True, "member_id": teammate})
    request("POST", prefix + "/start")
    write(
        "scenario.json",
        {
            "module": "停摆的钟楼（仓库原创调查练习）",
            "start": "钟楼广场，雨夜",
            "characters": ["周岚：真人调查员", "沈砚：AI调查员"],
            "predeclared": True,
            "dice": "server random; no resets",
        },
    )
    print("Prepared one room:", prefix)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "serve",
            "setup",
            "turn",
            "status",
            "roll",
            "accept",
            "save",
            "load",
            "resume",
            "export",
        ],
    )
    parser.add_argument("text", nargs="?", default="")
    args = parser.parse_args()
    if args.command == "serve":
        return serve()
    if args.command == "setup":
        return setup()
    session = json.loads((DIRECTORY / "session.json").read_text(encoding="utf-8"))
    prefix = session["prefix"]
    if args.command == "turn":
        result = request(
            "POST",
            prefix + "/actions",
            {"text": args.text, "client_request_id": str(uuid4())},
            player=True,
        )
        with (DIRECTORY / "inputs.jsonl").open("a", encoding="utf-8") as log:
            log.write(
                json.dumps(
                    {"time": time.time(), "text": args.text, "event": result["event"]},
                    ensure_ascii=False,
                )
                + "\n"
            )
        print("Accepted", result["event"]["seq"])
    elif args.command in {"roll", "accept"}:
        checks = request("GET", prefix + "/checks", player=True)
        check = next(c for c in checks if c["status"] == "pending")
        endpoint, body = (
            ("choice", {"operation": "accept"}) if args.command == "accept" else ("roll", {})
        )
        print(
            request("POST", prefix + f"/checks/{check['id']}/{endpoint}", body, player=True)[
                "check"
            ]
        )
    elif args.command == "save":
        request("POST", prefix + "/pause")
        saved = request("POST", prefix + "/snapshots", {"name": "自由互动中途重启"})["snapshot"]
        write("save.json", saved)
        print("Saved", saved["id"])
    elif args.command == "load":
        saved = json.loads((DIRECTORY / "save.json").read_text(encoding="utf-8"))
        request("POST", prefix + f"/snapshots/{saved['id']}/load")
        request("POST", prefix + "/resume")
        print("Loaded and resumed same room")
    elif args.command == "resume":
        request("POST", prefix + "/resume")
    elif args.command == "export":
        room = request("GET", prefix)
        names = {m["id"]: m["display_name"] for m in room["members"]}
        write("room-final.json", room)
        for role in ("PUBLIC", "HOST_DEBUG"):
            events, after = [], 0
            while True:
                page = request(
                    "GET", prefix + f"/events?after_seq={after}&limit=1000", player=role == "PUBLIC"
                )["events"]
                events.extend(page)
                if len(page) < 1000:
                    break
                after = page[-1]["seq"]
            if role == "PUBLIC":
                events = [e for e in events if e["visibility"] == "public"]
            write(role + "-events.json", events)
            from app.memory.events import story_events

            public = story_events(events)[0] if role == "PUBLIC" else events
            lines = [f"# {role} · 停摆的钟楼", ""]
            for e in public:
                payload = e["payload"]
                speaker = payload.get("actor_name", names.get(e.get("actor_member_id"), e["type"]))
                content = next(
                    (
                        payload[k]
                        for k in (
                            "text",
                            "display_text",
                            "content",
                            "public_summary",
                            "scene_summary",
                            "question",
                        )
                        if payload.get(k)
                    ),
                    None,
                )
                if content or role == "HOST_DEBUG":
                    lines += [
                        f"**#{e['seq']} {speaker} · {e['type']} · {e['occurred_at']}**",
                        str(content)
                        if role == "PUBLIC"
                        else "```json\n"
                        + json.dumps(payload, ensure_ascii=False, indent=2)
                        + "\n```",
                        "",
                    ]
            (DIRECTORY / (role + ".md")).write_text("\n".join(lines), encoding="utf-8")
        runs = request("GET", prefix + "/agent-runs")
        write("runs.json", runs)
        with sqlite3.connect((DIRECTORY / "game.db").as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            calls = [
                {"id": row["id"], "run_id": row["run_id"], **json.loads(row["document"])}
                for row in db.execute("select * from agent_model_calls")
            ]
            plans = [dict(row) for row in db.execute("select * from agent_action_plans")]
            for plan in plans:
                plan["document"] = json.loads(plan["document"])
            cycles = [dict(row) for row in db.execute("select * from agent_cycles")]
            for cycle in cycles:
                cycle["state"] = json.loads(cycle["state"])
        write("HOST_DEBUG-plans.json", plans)
        write("HOST_DEBUG-cycles.json", cycles)
        write("model-calls.json", calls)
        with (DIRECTORY / "HOST_DEBUG.md").open("a", encoding="utf-8") as log:
            log.write(
                "\n## Model calls\n\n| Run | Stage | Status | Latency ms | Tokens |\n"
                "|---|---|---|---:|---|\n"
            )
            for run in runs:
                log.write(
                    f"| {run['id']} | {run['graph_node']} | {run['status']} | "
                    f"{run['latency_ms']} | {run.get('token_usage')} |\n"
                )
        print("Exported", len(calls), "model calls")
    else:
        room = request("GET", prefix, player=True)
        print(json.dumps(room["game"]["cycle"], ensure_ascii=False))
        for e in request("GET", prefix + "/events", player=True)["events"][-28:]:
            if e["type"] in {
                "keeper.narration",
                "npc.spoke",
                "agent.spoke",
                "action.clarification_requested",
                "check.requested",
                "check.resolved",
            }:
                print(e["seq"], e["type"], json.dumps(e["payload"], ensure_ascii=False))


if __name__ == "__main__":
    main()
