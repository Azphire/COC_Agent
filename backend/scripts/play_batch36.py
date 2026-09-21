"""Isolated real Ollama session through the normal HTTP API; no model or dice substitutes.

Commands are bounded observations, never a time/turn limit on the game. Public
events drive player choices; host audits are exported separately for debugging.
"""

import argparse
import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data/prepared/changan/batch-36"
URL = "http://127.0.0.1:8036/api"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def clean(value):
    if isinstance(value, dict):
        return {
            k: (
                "[redacted]"
                if k in {"member_token", "invite_code", "host_admin_token", "token", "api_key"}
                else clean(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def serve(directory, *, port=8036):
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    auth_path = directory / "private-auth.json"
    if not auth_path.exists():
        write(auth_path, {"host": secrets.token_urlsafe(32)})
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    settings = Settings(
        _env_file=None,
        host_admin_token=auth["host"],
        data_dir=ROOT / "data",
        database_url="sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
        checkpoint_db_path=directory / "checkpoint.db",
        knowledge_db_path=directory / "knowledge.db",
        model_settings_path=directory / "model-settings.json",
        model_provider="ollama",
        model_name="qwen3:8b",
        model_base_url="http://127.0.0.1:11434/v1/",
        model_api_key="ollama",
        openai_api_key="",
        model_context_limit=8192,
        model_output_limit=900,
        model_timeout_seconds=240,
        model_think=False,
    )
    write(
        directory / "effective-config.json",
        {
            k: str(getattr(settings, k))
            for k in (
                "database_url",
                "checkpoint_db_path",
                "knowledge_db_path",
                "model_settings_path",
                "data_dir",
                "model_provider",
                "model_name",
                "model_context_limit",
                "model_output_limit",
            )
        },
    )
    stop = directory / "stop-service"
    stop.unlink(missing_ok=True)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port))

    def watch():
        while not stop.exists():
            time.sleep(0.25)
        server.should_exit = True

    threading.Thread(target=watch, daemon=True).start()
    server.run()


class Session:
    def __init__(self, directory):
        self.directory = directory
        self.auth = json.loads((directory / "private-auth.json").read_text(encoding="utf-8"))
        self.http = httpx.Client(trust_env=False, timeout=120)
        state = directory / "session.json"
        self.state = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}

    def save(self):
        write(self.directory / "session.json", self.state)

    def req(self, method, path, body=None, player=False):
        token = self.state["player_token"] if player else self.auth["host"]
        started = time.monotonic()
        response = self.http.request(
            method, URL + path, json=body, headers={"Authorization": "Bearer " + token}
        )
        try:
            result = (
                [json.loads(line) for line in response.text.splitlines() if line.strip()]
                if "ndjson" in response.headers.get("content-type", "")
                else response.json()
            )
        except ValueError:
            result = {"body": response.text}
        if method != "GET" or not response.is_success:
            with (self.directory / "operations.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "method": method,
                            "path": path,
                            "identity": "player" if player else "host",
                            "body": clean(body),
                            "status": response.status_code,
                            "result": clean(result),
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1500]}")
        return result

    @property
    def prefix(self):
        return "/rooms/" + self.state["room_id"]

    def card(self, name, occupation, groups, priority, *, era="modern", age=29, equipment=None,
             occupation_attribute=None):
        cards = self.state.setdefault("cards", {})
        if name not in cards:
            card = self.req(
                "POST",
                "/characters/random",
                {"ruleset_id": "coc7-character-creation", "name": name, "age": age},
            )
            cards[name] = card["id"]
            self.save()
            write(self.directory / (name + "-original-rolls.json"), card["roll_records"])
        card = self.req("GET", "/characters/" + cards[name])
        if card["status"] == "finalized":
            return card
        path = "/characters/" + card["id"]
        card = self.req(
            "PATCH",
            path,
            {
                "version": card["version"],
                "occupation": occupation,
                "era": era,
                **({"equipment": equipment} if equipment is not None else {}),
                **({"occupation_attribute": occupation_attribute} if occupation_attribute else {}),
                "occupation_group_choices": groups,
                "selected_specializations": [
                    s
                    for items in groups.values()
                    for s in items
                    if s.startswith(("language_", "art_"))
                ],
            },
        )
        occupation_alloc = {"credit_rating": {"points": 15}}
        remaining = card["remaining_points"]["occupation"] - 15
        skills = list(dict.fromkeys([*priority, *card["effective_occupation_skills"]]))
        for target in (60, 70, 75):
            for skill in skills:
                if skill == "credit_rating" or skill not in card["effective_occupation_skills"]:
                    continue
                old = occupation_alloc.get(skill, {}).get("points", 0)
                points = min(
                    remaining, max(0, target - card["skill_base_values"].get(skill, 0) - old)
                )
                if points:
                    occupation_alloc[skill] = {"points": old + points}
                    remaining -= points
        interest = {}
        remaining_i = card["remaining_points"]["interest"]
        for target in (45, 55, 65):
            for skill in (
                "spot_hidden",
                "listen",
                "dodge",
                "first_aid",
                "stealth",
                "jump",
                "persuade",
            ):
                old = interest.get(skill, {}).get("points", 0)
                base = card["skill_base_values"].get(skill, 0) + occupation_alloc.get(
                    skill, {}
                ).get("points", 0)
                points = min(remaining_i, max(0, target - base - old))
                if points:
                    interest[skill] = {"points": old + points}
                    remaining_i -= points
        card = self.req(
            "PATCH",
            path,
            {
                "version": card["version"],
                "occupation_skills": occupation_alloc,
                "interest_skills": interest,
            },
        )
        write(self.directory / (name + "-draft.json"), card)
        card = self.req("POST", path + "/finalize", {"version": card["version"]})
        original = json.loads(
            (self.directory / (name + "-original-rolls.json")).read_text(encoding="utf-8")
        )
        assert original == card["roll_records"], "Creation dice must remain unchanged"
        write(self.directory / (name + "-frozen.json"), card)
        return card

    def setup(
        self,
        *,
        package_path=None,
        room_name=None,
        investigators=None,
        era="modern",
        background="普通乘客",
        goals="照顾同伴，弄清眼前情况，平安回家。",
        character_options=None,
    ):
        self.room_name = room_name or "第36批 常暗之厢 本地真实局"
        player_name = investigators[0][0] if investigators else "林知秋"
        if not self.state.get("room_id"):
            created = self.req("POST", "/rooms", {"name": self.room_name})
            self.state["room_id"] = created["room"]["id"]
            joined = self.req(
                "POST",
                "/rooms/join",
                {"invite_code": created["invite_code"], "display_name": player_name},
            )
            self.state["player_token"] = joined["member_token"]
            self.state["player_id"] = joined["room"]["self_member_id"]
            self.save()
        profiles = [
            (
                "林知秋",
                "librarian",
                {
                    "language": ["language_english"],
                    "academic": ["history", "psychology", "law", "persuade"],
                },
                ["library_use", "history", "psychology", "persuade"],
                "谨慎好奇，普通图书馆员",
            ),
            (
                "周岚",
                "nurse",
                {"social": ["persuade"]},
                ["first_aid", "medicine", "psychology", "listen"],
                "稳重直接的社区护士，遇事先看同伴和伤者的状况，也会指出冒险计划的不妥",
            ),
            (
                "陈拓",
                "craftsperson",
                {
                    "art_craft": ["art_carpentry", "art_technical_drawing"],
                    "personal": ["electrical_repair", "locksmith"],
                },
                ["mechanical_repair", "spot_hidden", "electrical_repair", "locksmith"],
                "话少务实的修车工，喜欢检查实物，嘴硬但不愿丢下同伴",
            ),
        ]
        for name, occupation, groups, priority, personality in investigators or profiles:
            card = self.card(name, occupation, groups, priority, era=era,
                             **(character_options or {}).get(name, {}))
            room = self.req("GET", self.prefix)
            member = next((m for m in room["members"] if m["display_name"] == name), None)
            if not member:
                room = self.req(
                    "POST",
                    self.prefix + "/members",
                    {"display_name": name, "controller_type": "agent"},
                )["room"]
                member = next(m for m in room["members"] if m["display_name"] == name)
            if not any(s.get("member_id") == member["id"] for s in room["character_slots"]):
                if name == player_name:
                    export = self.req("GET", "/characters/" + card["id"] + "/export")
                    submitted = self.req(
                        "POST",
                        self.prefix + "/character-submissions",
                        {
                            "document": export,
                            "expected_version": 0,
                            "client_request_id": str(uuid4()),
                        },
                        player=True,
                    )
                    write(self.directory / "player-submission.json", submitted)
                    submissions = self.req("GET", self.prefix + "/character-submissions")
                    submission = submissions[-1]
                    self.req(
                        "POST",
                        self.prefix + "/character-submissions/" + submission["id"] + "/review",
                        {
                            "decision": "accept",
                            "expected_version": submission["version"],
                            "client_request_id": str(uuid4()),
                        },
                    )
                else:
                    room = self.req(
                        "POST", self.prefix + "/character-slots", {"character_id": card["id"]}
                    )["room"]
                    slot = next(
                        s for s in room["character_slots"] if s["source_character_id"] == card["id"]
                    )
                    self.req(
                        "POST",
                        self.prefix + "/character-assignments",
                        {"slot_id": slot["id"], "member_id": member["id"]},
                    )
            if name != player_name and name not in self.state.setdefault("bound", []):
                profile = self.req(
                    "POST",
                    "/agent-profiles",
                    {
                        "role": "investigator",
                        "name": name,
                        "background": background + "，职业为" + occupation,
                        "personality": personality,
                        "goals": goals,
                        "speaking_style": "简短自然的中文口语",
                        "action_tendency": (
                            "根据公开情况和自己的能力，尝试具体事情；结果交给KP裁决。"
                        ),
                    },
                )
                self.req(
                    "POST",
                    self.prefix + "/agent-bindings",
                    {"member_id": member["id"], "profile_id": profile["id"]},
                )
                self.state["bound"].append(name)
                self.save()
            self.req(
                "POST",
                self.prefix + "/ready",
                {"ready": True}
                if name == player_name
                else {"ready": True, "member_id": member["id"]},
                player=name == player_name,
            )
        if not self.state.get("keeper"):
            room = self.req("GET", self.prefix)
            p = self.req(
                "POST",
                "/agent-profiles",
                {"role": "keeper", "name": "KP", "speaking_style": "自然中文，清楚回应本次行动"},
            )
            self.req(
                "POST",
                self.prefix + "/agent-bindings",
                {"member_id": room["host_member_id"], "profile_id": p["id"]},
            )
            self.state["keeper"] = p["id"]
            self.save()
        if not self.state.get("preparation"):
            package = json.loads(
                (
                    Path(package_path)
                    if package_path
                    else ROOT / "data/prepared/changan/batch-21/package-approved.json"
                ).read_text(encoding="utf-8")
            )
            imported = self.req("POST", "/module-preparations/import", package)
            self.req(
                "PATCH",
                self.prefix + "/module-preparation",
                {"preparation_id": imported["preparation_id"]},
            )
            self.state["preparation"] = imported["preparation_id"]
            self.save()
        if package_path:
            package_file = Path(package_path)
            package = json.loads(package_file.read_text(encoding="utf-8"))
            logs = self.req("GET", self.prefix + "/logs")
            logs = logs.get("events", logs.get("logs", [])) if isinstance(logs, dict) else logs
            binding = next(e["payload"] for e in reversed(logs) if e["type"] == "preparation.bound")
            assert binding["preparation_id"] == self.state["preparation"]
            assert binding["source_hash"] == package["knowledge"]["source"]["source_hash"]
            assert binding["title"] == package["title"]
            write(
                self.directory / "binding-verification.json",
                {
                    **binding,
                    "package_file_sha256": hashlib.sha256(package_file.read_bytes()).hexdigest(),
                    "source_files": package["knowledge"]["source"]["files"],
                    "verified_before_start": True,
                },
            )
        self.req("POST", self.prefix + "/start")
        print("Started", self.state["room_id"], flush=True)
        self.observe()

    def observe(self):
        room = self.req("GET", self.prefix, player=True)
        events = self.req("GET", self.prefix + "/logs", player=True)
        write(self.directory / "public-room.json", room)
        if isinstance(events, dict):
            events = events.get("events", events.get("logs", []))
        write(self.directory / "public-events.json", events)
        last = self.state.get("seen_seq", 0)
        for e in events:
            if (
                e["seq"] > last
                and e["type"] not in {"agent.cycle_status", "agent.status"}
                and (
                    e["payload"].get("text")
                    or e["type"].startswith(
                        ("check.", "scene.", "entity.", "action.", "module.", "snapshot.", "game.")
                    )
                )
            ):
                payload = e["payload"]
                print(
                    json.dumps(
                        {
                            "seq": e["seq"],
                            "type": e["type"],
                            "speaker": payload.get("actor_name", e.get("actor_member_id")),
                            "text": payload.get("text")
                            or payload.get("display_text")
                            or payload.get("public_summary")
                            or payload.get("scene_summary")
                            or payload.get("question"),
                            "scene": payload.get("scene_title"),
                            "fallback": payload.get("safe_fallback"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        if events:
            self.state["seen_seq"] = events[-1]["seq"]
            self.save()
        print(
            json.dumps(
                {
                    "status": room["status"],
                    "cycle": room.get("game", {}).get("cycle"),
                    "scene": room.get("session_state", {}).get("scene_title"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def poll(self):
        until = time.monotonic() + 45
        while time.monotonic() < until:
            checks = self.req("GET", self.prefix + "/checks")
            for check in checks:
                if check["status"] != "pending":
                    continue
                cid = check["id"]
                player = check.get("target_member_id") == self.state["player_id"]
                settlement = check.get("settlement") or {}
                if settlement.get("stage") in {"choice", "awaiting_choice"}:
                    self.req(
                        "POST",
                        self.prefix + f"/checks/{cid}/choice",
                        {"operation": "accept"},
                        player=player,
                    )
                elif check.get("sanity"):
                    if check["sanity"]["stage"] not in {"san", "loss", "int", "duration"}:
                        continue  # Symptom selection belongs to the existing KP flow.
                    self.req(
                        "POST",
                        self.prefix + f"/sanity/checks/{cid}/roll",
                        {"expected_stage": check["sanity"]["stage"]},
                        player=player,
                    )
                else:
                    self.req("POST", self.prefix + f"/checks/{cid}/roll", {}, player=player)
            room = self.req("GET", self.prefix, player=True)
            cycle = room.get("game", {}).get("cycle") or {}
            if cycle.get("status") not in {"running", "queued", "waiting_for_roll"}:
                break
            time.sleep(1)
        self.observe()

    def export(self):
        for suffix, filename in [
            ("/logs", "host-events.json"),
            ("/agent-runs", "agent-runs.json"),
            ("/checks", "checks.json"),
            ("/teammate-behavior", "teammate-behavior.json"),
        ]:
            write(self.directory / filename, clean(self.req("GET", self.prefix + suffix)))
        self.observe()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["serve", "setup", "observe", "poll", "action", "export", "request"]
    )
    parser.add_argument("run")
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()
    directory = (BASE / args.run).resolve()
    assert directory.is_relative_to(BASE.resolve()) and directory != BASE.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if args.command == "serve":
        serve(directory)
        return
    session = Session(directory)
    if args.command == "action":
        session.req(
            "POST",
            session.prefix + "/actions",
            {"text": " ".join(args.args), "client_request_id": str(uuid4())},
            player=True,
        )
        session.poll()
    elif args.command == "request":
        method, suffix, *body = args.args
        print(
            json.dumps(
                clean(
                    session.req(
                        method, session.prefix + suffix, json.loads(body[0]) if body else None
                    )
                ),
                ensure_ascii=False,
            )
        )
    else:
        getattr(session, args.command)()


if __name__ == "__main__":
    main()
