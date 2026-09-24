"""Batch 49 fixed natural cases; explicit CLI stages, no preparation regeneration.

--prepare performs only read-only SQLite backups. --setup uses ordinary room
APIs and retained legal cards/profiles. Only --case submits a natural model turn.
Run stages from backend/ with the repository venv. Each case runs once; preserve
failures and use a separately named directory for an authorized affected recheck.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from check_character_creation import ROOT, TEST_HOST
from websockets.sync.client import connect

SOURCE = ROOT / "data/prepared/batch-48/real-01"
OLD_ROOM = "fbd1485a-fd91-41a9-9f28-e4f37d36c9bd"
DIRECTORY = ROOT / "data/prepared/batch-49/real-01"
PORT = 8149
ACTIVE = {"running", "queued", "queued_action", "suspended", "waiting_for_roll",
          "waiting_for_review"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def rows(db, query, args=()):
    result = []
    for row in db.execute(query, args):
        item = dict(row)
        for key, value in list(item.items()):
            if isinstance(value, str) and value[:1] in {"{", "["}:
                try:
                    item[key] = json.loads(value)
                except ValueError:
                    pass
        result.append(item)
    return result


def connection(directory=None):
    directory = directory or DIRECTORY
    db = sqlite3.connect(f"file:{(directory / 'game.db').as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def prepare():
    DIRECTORY.mkdir(parents=True, exist_ok=False)
    paths = [SOURCE / name for name in (
        "game.db", "game.db-wal", "checkpoint.db", "checkpoint.db-wal", "knowledge.db",
        "knowledge.db-wal", "model-settings.json",
    )]
    paths += [ROOT / name for name in (
        ".env", "data/game.db", "data/game.db-wal", "data/host-model-settings.json",
        "data/prepared/changan/batch-21/package-approved.json",
    )]
    before = {str(p): digest(p) for p in paths}
    for name in ("game.db", "checkpoint.db", "knowledge.db"):
        original = SOURCE / name
        with sqlite3.connect(f"file:{original.as_posix()}?mode=ro", uri=True) as old:
            with sqlite3.connect(DIRECTORY / name) as new:
                old.backup(new)
    config = SOURCE / "model-settings.json"
    if not config.exists():
        config = ROOT / "data/host-model-settings.json"
    if config.exists():
        shutil.copy2(config, DIRECTORY / "model-settings.json")
    assert before == {str(p): digest(p) for p in paths}
    write(DIRECTORY / "clone-provenance.json", {
        "source": str(SOURCE), "source_room": OLD_ROOM, "source_sha256": before,
        "method": "SQLite mode=ro backup including committed WAL", "model_calls": 0,
        "retained": "old cards, raw dice, profiles, rooms, saves, preparation and failures",
    })
    print("Prepared read-only source backups; no model calls", flush=True)


def serve():
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        host_admin_token=TEST_HOST, data_dir=ROOT / "data",
        database_url=f"sqlite+aiosqlite:///{(DIRECTORY / 'game.db').as_posix()}",
        checkpoint_db_path=DIRECTORY / "checkpoint.db",
        knowledge_db_path=DIRECTORY / "knowledge.db",
        model_settings_path=DIRECTORY / "model-settings.json",
    )
    write(DIRECTORY / "effective-config.json", {key: getattr(settings, key) for key in (
        "model_provider", "model_name", "model_context_limit", "model_output_limit",
        "agent_max_calls", "agent_context_chars", "model_timeout_seconds",
    )})
    uvicorn.run(create_app(settings), host="127.0.0.1", port=PORT, log_level="warning")


def request(client, method, path, body=None, *, token=TEST_HOST):
    response = client.request(method, path, json=body,
                              headers={"Authorization": "Bearer " + token})
    value = response.json()
    with (DIRECTORY / "http-audit.jsonl").open("a", encoding="utf-8") as log:
        safe = {k: ("[local play credential]" if k == "member_token" else v)
                for k, v in value.items()} if isinstance(value, dict) else value
        log.write(json.dumps({"at": time.time(), "method": method, "path": path,
                              "body": body, "status": response.status_code, "response": safe},
                             ensure_ascii=False) + "\n")
    response.raise_for_status()
    return value


def setup():
    state_path = DIRECTORY / "setup-state.json"
    state = read(state_path) if state_path.exists() else {"members": {}, "slots": {}, "steps": []}
    with connection() as db:
        old = rows(db, "SELECT * FROM game_rooms WHERE id=?", (OLD_ROOM,))[0]
        members = rows(db, "SELECT * FROM room_members WHERE room_id=? AND active=1", (OLD_ROOM,))
        slots = rows(db, "SELECT * FROM room_character_slots WHERE room_id=?", (OLD_ROOM,))
        bindings = rows(db, "SELECT * FROM room_agent_bindings WHERE room_id=?", (OLD_ROOM,))
        knowledge = rows(db, "SELECT * FROM room_knowledge_bindings WHERE room_id=?",
                         (OLD_ROOM,))[0]["document"]
        prep = rows(db, "SELECT * FROM room_module_preparation_bindings WHERE room_id=?",
                    (OLD_ROOM,))[0]
    with httpx.Client(base_url=f"http://127.0.0.1:{PORT}", trust_env=False, timeout=90) as client:
        if not state.get("room_id"):
            created = request(client, "POST", "/api/rooms", {"name": "第49批自然短局"})
            state["room_id"] = created["room"]["id"]
            state["members"][old["host_member_id"]] = created["room"]["host_member_id"]
            write(state_path, state)
        prefix = "/api/rooms/" + state["room_id"]

        def step(key, method, path, body=None):
            if key in state["steps"]:
                return None
            value = request(client, method, prefix + path, body)
            state["steps"].append(key)
            write(state_path, state)
            return value

        step("preparation", "PATCH", "/module-preparation", {
            "preparation_id": prep["preparation_id"],
        })
        step("knowledge", "PATCH", "/knowledge", knowledge)
        for member in members:
            if member["role"] != "player":
                continue
            if member["id"] not in state["members"]:
                result = request(client, "POST", prefix + "/members", {
                    "display_name": member["display_name"],
                    "controller_type": member["controller_type"],
                })
                assigned = next(m for m in result["room"]["members"]
                                if m["display_name"] == member["display_name"])
                state["members"][member["id"]] = assigned["id"]
                write(state_path, state)
            new_id = state["members"][member["id"]]
            slot = next(s for s in slots if s["member_id"] == member["id"])
            if slot["id"] not in state["slots"]:
                result = request(client, "POST", prefix + "/character-slots", {
                    "character_id": slot["source_character_id"],
                })
                published = next(s for s in result["room"]["character_slots"]
                                 if s["source_character_id"] == slot["source_character_id"])
                state["slots"][slot["id"]] = published["id"]
                write(state_path, state)
            step("assign:" + new_id, "POST", "/character-assignments", {
                "slot_id": state["slots"][slot["id"]], "member_id": new_id,
            })
            step("ready:" + new_id, "POST", "/ready", {"member_id": new_id, "ready": True})
            if member["controller_type"] == "human":
                state["player_id"] = new_id
        for binding in bindings:
            step("binding:" + binding["id"], "POST", "/agent-bindings", {
                "member_id": state["members"][binding["member_id"]],
                "profile_id": binding["profile_id"],
            })
        session = request(client, "POST", prefix + "/play-session", {
            "member_id": state["player_id"],
        })
        state["player_token"] = session["member_token"]
        write(state_path, state)
        step("start", "POST", "/start")
        public = request(client, "GET", prefix + "/public-entities", token=state["player_token"])
        write(DIRECTORY / "preflight-public.json", public)
    with connection() as db:
        entities = rows(db, "SELECT * FROM room_entity_states WHERE room_id=?", (state["room_id"],))
        back = [e for e in entities if (e.get("snapshot") or {}).get("title") == "便签背面"]
        assert back and all(e["state"] == "hidden" for e in back), "Must begin before new reveal"
        write(DIRECTORY / "preflight-hidden-reveal.json", back)
    print(json.dumps({"room_id": state["room_id"], "ready": True}, ensure_ascii=False), flush=True)


def snapshot(room_id):
    with connection() as db:
        tables = ("room_events", "agent_cycles", "agent_runs", "agent_model_calls",
                  "agent_action_plans", "agent_behavior_states", "room_character_slots")
        return {table: rows(db, (
            "SELECT c.* FROM agent_model_calls c JOIN agent_runs r ON r.id=c.run_id "
            "WHERE r.room_id=?" if table == "agent_model_calls"
            else f'SELECT * FROM "{table}" WHERE room_id=?'
        ), (room_id,)) for table in tables}


def case(name):
    target = DIRECTORY / (name + "-case.json")
    assert not target.exists(), "Preserve original case; never silently resubmit"
    state = read(DIRECTORY / "setup-state.json")
    original = read(SOURCE / ("investigation-state-01.json" if name == "read"
                              else "delegation-state-01.json"))
    seq = 26 if name == "read" else 84
    event = next(e for e in original["room_events"] if e["room_id"] == OLD_ROOM and e["seq"] == seq)
    raw = event["payload"]["text"]
    started, cursor, reconnected, frames = time.time(), 0, set(), []
    sock = None

    def open_socket():
        nonlocal sock
        sock = connect(f"ws://127.0.0.1:{PORT}/ws/rooms/{state['room_id']}",
                       proxy=None, ping_interval=None, max_size=64 * 1024 * 1024)
        sock.send(json.dumps({"type": "auth", "credential_type": "member",
                              "token": state["player_token"], "after_seq": cursor}))

    open_socket()
    capture = {"request": raw, "started_at": started, "status": "started"}
    write(target, capture)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{PORT}",
                          trust_env=False, timeout=90) as client:
            response = request(client, "POST", f"/api/rooms/{state['room_id']}/actions", {
                "text": raw, "client_request_id": str(uuid4()),
            }, token=state["player_token"])
            cid = response["event"]["payload"]["cycle_id"]
            capture.update(cycle_id=cid, submission=response)
            write(target, capture)
            deadline, ping_at, poll_at, settled_at = time.monotonic() + 600, 0, 0, None
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now - ping_at > 15:
                    sock.send(json.dumps({"type": "ping"}))
                    ping_at = now
                try:
                    frame = json.loads(sock.recv(timeout=0.25))
                    frames.append({"at": time.time(), **frame})
                    data = frame.get("data") or {}
                    if frame["type"] == "room.event":
                        cursor = max(cursor, data["seq"])
                    if frame["type"] == "keeper.stream.delta":
                        key = (data.get("cycle_id"), data.get("stream_id"), data.get("attempt"))
                        if key not in reconnected:
                            reconnected.add(key)
                            sock.close()
                            frames.append({"at": time.time(), "type": "observer.reconnect",
                                           "data": {"key": list(key), "after_seq": cursor}})
                            open_socket()
                except TimeoutError:
                    pass
                if now - poll_at > 1:
                    audit = snapshot(state["room_id"])
                    cycles = [c for c in audit["agent_cycles"] if c["id"] == cid
                              or c["state"].get("related_player_cycle_id") == cid]
                    waiting = [c for c in cycles if c["status"] in {
                        "waiting_for_roll", "waiting_for_review"}]
                    if waiting:
                        capture["waiting_cycles"] = waiting
                        break  # No invented roll or automatic host ruling.
                    if cycles and all(c["status"] not in ACTIVE for c in cycles):
                        settled_at = settled_at or now
                        if now - settled_at > 2:
                            break
                    poll_at = now
            capture.update(status="captured", elapsed_seconds=time.time() - started,
                           reconnects=[list(key) for key in reconnected])
    finally:
        if sock:
            sock.close()
        write(DIRECTORY / (name + "-frames.json"), frames)
        capture["audit"] = snapshot(state["room_id"])
        write(target, capture)
        provenance = read(DIRECTORY / "clone-provenance.json")
        protection = {p: digest(Path(p)) == value
                      for p, value in provenance["source_sha256"].items()}
        write(DIRECTORY / (name + "-source-protection.json"), protection)
        assert all(protection.values()), "Source changed"
    print(json.dumps({k: v for k, v in capture.items() if k not in {"audit", "submission"}},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DIRECTORY)
    group = parser.add_mutually_exclusive_group(required=True)
    for option in ("prepare", "serve", "setup"):
        group.add_argument("--" + option, action="store_true")
    group.add_argument("--case", choices=["read", "delegate"])
    args = parser.parse_args()
    DIRECTORY = args.directory.resolve()
    assert DIRECTORY.is_relative_to((ROOT / "data/prepared/batch-49").resolve())
    if args.prepare:
        prepare()
    elif args.serve:
        serve()
    elif args.setup:
        setup()
    else:
        case(args.case)
