"""Normal HTTP/WS HO acceptance using existing model settings and isolated state."""

import argparse
import json
import os
import secrets
import sqlite3
import time
import traceback
from urllib.parse import urlsplit
from uuid import uuid4

from scripts.prepare_batch42 import OUTPUT, ROOT, SOURCE, read, write


def run(name):
    directory = OUTPUT / name
    directory.mkdir(parents=True, exist_ok=False)
    from app.config import Settings
    from app.models.settings import ModelSettings

    configured = Settings()
    ModelSettings(configured)  # Read and reuse the existing effective provider; never save it.
    endpoint = urlsplit(configured.model_base_url)
    if configured.model_provider != "ollama" or endpoint.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("本批含私密HO的实测只允许现有本机Ollama回环端点")
    token = secrets.token_urlsafe(32)
    overrides = {
        "host_admin_token": token,
        "database_url": "sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
        "checkpoint_db_path": directory / "checkpoint.db",
        "knowledge_db_path": directory / "knowledge.db",
        "model_settings_path": directory / "model-settings.json",
    }
    for key, value in overrides.items():
        os.environ[key.upper()] = str(value)
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = Settings(**{**configured.model_dump(), **overrides})
    app = create_app(settings)
    write(
        directory / "effective-config.json",
        {
            "provider": settings.model_provider,
            "model": settings.model_name,
            "base_url": settings.model_base_url,
            "database": str(directory / "game.db"),
            "model_adapter": "real configured provider; no injection or dice substitute",
        },
    )
    auth = {"host": token}
    members = {}
    with TestClient(app) as client:

        def request(method, path, body=None, actor="host"):
            response = client.request(
                method, "/api" + path, json=body, headers={"Authorization": "Bearer " + auth[actor]}
            )
            if "ndjson" in response.headers.get("content-type", ""):
                result = [json.loads(line) for line in response.text.splitlines() if line]
            else:
                result = response.json()
            if method != "GET" or not response.is_success:
                with (directory / "operations.jsonl").open("a", encoding="utf-8") as stream:
                    # Invitations/member credentials stay in memory, not the shareable audit.
                    stream.write(
                        json.dumps(
                            {
                                "method": method,
                                "path": path,
                                "actor": actor,
                                "status": response.status_code,
                                "body": body
                                if path != "/rooms/join"
                                else {"display_name": body["display_name"]},
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            if not response.is_success:
                raise RuntimeError(
                    f"{method} {path}: {response.status_code} {response.text[:1800]}"
                )
            return result

        imported = request(
            "POST", "/module-preparations/import", read(OUTPUT / "package-reviewed.json")
        )
        pid = imported["preparation_id"]
        repeated = request(
            "POST", "/module-preparations/import", read(OUTPUT / "package-reviewed.json")
        )
        assert repeated["preparation_id"] == pid and repeated["reused"]
        write(directory / "preparation.json", imported)
        created = request("POST", "/rooms", {"name": "第42批 吱乎鲁 HO 真实闭环"})
        room_id = created["room"]["id"]
        prefix = "/rooms/" + room_id
        request("PATCH", prefix + "/module-preparation", {"preparation_id": pid})
        names = ["林月", "许安", "周晟"]
        cards, slots = {}, {}
        for number, character_name in enumerate(names, 1):
            ho = f"HO{number}"
            original = read(SOURCE / "short-run-01" / (character_name + "-frozen.json"))
            card = request(
                "POST",
                "/characters/point-buy",
                {
                    "ruleset_id": original["ruleset_id"],
                    "name": character_name,
                    "age": 19,
                    "attributes": original["attributes"],
                },
            )
            path = "/characters/" + card["id"]
            patch = {
                key: original[key]
                for key in (
                    "occupation",
                    "occupation_group_choices",
                    "selected_specializations",
                    "occupation_skills",
                    "interest_skills",
                    "age_deductions",
                    "era",
                )
            }
            patch.update(
                version=card["version"],
                module_handout={
                    "preparation_id": pid,
                    "handout_id": ho,
                    "attribute_allocations": {"dex": 30} if number == 2 else {},
                },
                background={
                    **original["background"],
                    "appearance": "19岁" + ("男生" if number == 3 else "女生"),
                },
            )
            card = request("PATCH", path, patch)
            write(directory / f"{ho}-preview.json", card)
            assert card["module_handout_approval"] is None
            card = request(
                "PATCH", path, {"version": card["version"], "approve_module_handout": True}
            )
            # Retry the same selection with a fresh version: no repeated arithmetic.
            expected = (card["effective_attributes"], card["skill_values"], card["derived_values"])
            card = request(
                "PATCH",
                path,
                {"version": card["version"], "module_handout": patch["module_handout"]},
            )
            assert expected == (
                card["effective_attributes"],
                card["skill_values"],
                card["derived_values"],
            )
            card = request("POST", path + "/finalize", {"version": card["version"]})
            write(directory / f"{ho}-frozen.json", card)
            cards[ho] = card
            if number != 2:
                joined = request(
                    "POST",
                    "/rooms/join",
                    {"invite_code": created["invite_code"], "display_name": character_name},
                )
                members[ho] = joined["room"]["self_member_id"]
                auth[ho] = joined["member_token"]
            else:
                room = request(
                    "POST",
                    prefix + "/members",
                    {"display_name": character_name, "controller_type": "agent"},
                )["room"]
                members[ho] = next(
                    m["id"] for m in room["members"] if m["display_name"] == character_name
                )
            room = request("POST", prefix + "/character-slots", {"character_id": card["id"]})[
                "room"
            ]
            slots[ho] = next(
                s["id"] for s in room["character_slots"] if s["source_character_id"] == card["id"]
            )
            request(
                "POST",
                prefix + "/character-assignments",
                {"slot_id": slots[ho], "member_id": members[ho]},
            )
            body = {
                "handout_id": ho,
                "slot_id": slots[ho],
                "member_id": members[ho],
                "client_request_id": str(uuid4()),
            }
            request("POST", prefix + "/handout-assignments", body)
            assert request("POST", prefix + "/handout-assignments", body)["already_assigned"]
        assert cards["HO1"]["skill_values"]["psychology"] == 80
        assert cards["HO1"]["skill_values"]["credit_rating"] == 35
        assert cards["HO2"]["effective_attributes"]["dex"] == 90
        assert cards["HO2"]["skill_values"]["dodge"] == 65
        assert cards["HO2"]["skill_values"]["credit_rating"] == 5
        assert cards["HO3"]["skill_values"]["stealth"] == 50
        assert cards["HO3"]["skill_values"]["credit_rating"] == 35

        def privacy(label):
            result = {}
            for ho in ("HO1", "HO3"):
                view = request("GET", prefix, actor=ho)
                assignments = view["handouts"]["assignments"]
                assert [a["handout_id"] for a in assignments] == [ho]
                payloads = {
                    "http": view,
                    "events": request("GET", prefix + "/events", actor=ho),
                    "export": request("GET", prefix + "/logs", actor=ho),
                }
                with client.websocket_connect("/ws/rooms/" + room_id) as socket:
                    socket.send_json(
                        {"type": "auth", "credential_type": "member", "token": auth[ho]}
                    )
                    frames = []
                    while True:
                        frame = socket.receive_json()
                        frames.append(frame)
                        if frame["type"] == "room.synced":
                            break
                    payloads["websocket"] = frames
                for channel, payload in payloads.items():
                    serialized = json.dumps(payload, ensure_ascii=False)
                    for other in set(cards) - {ho}:
                        secret = cards[other]["module_handout"]["definition"]["text"]
                        assert json.dumps(secret, ensure_ascii=False)[1:-1] not in serialized
                    write(directory / f"{label}-{ho}-{channel}.json", payload)
                events = payloads["events"]
                if isinstance(events, dict):
                    events = events["events"]
                assert all(
                    e["actor_member_id"] == created["room"]["host_member_id"]
                    for e in events
                    if e["type"] == "handout.assigned"
                )
                result[ho] = {"http": True, "events": True, "export": True, "websocket": True}
            return result

        permissions_before = privacy("before")
        for ho in cards:
            request(
                "POST",
                prefix + "/ready",
                {"ready": True} if ho in auth else {"ready": True, "member_id": members[ho]},
                actor=ho if ho in auth else "host",
            )
        for role, member, profile_name in [
            ("keeper", created["room"]["host_member_id"], "KP"),
            ("investigator", members["HO2"], "许安"),
        ]:
            profile = request(
                "POST",
                "/agent-profiles",
                {
                    "role": role,
                    "name": profile_name,
                    "background": "现代中国生日宴，三名19岁学生",
                    "personality": "谨慎，简短自然",
                    "goals": "根据自己已知信息行动，保护自己与同伴",
                    "speaking_style": "中文口语",
                },
            )
            request(
                "POST",
                prefix + "/agent-bindings",
                {"member_id": member, "profile_id": profile["id"]},
            )

        def settle():
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                view = request("GET", prefix)
                cycle = (view.get("game") or {}).get("cycle") or {}
                status = cycle.get("status")
                if status not in {"running", "queued", "queued_action", "waiting_for_roll"}:
                    write(directory / "latest-cycle.json", cycle)
                    if status in {"failed", "waiting_for_review"}:
                        write(
                            directory / "real-model-calls.json", app.state.agent_service.model.calls
                        )
                        write(directory / "session-host.json", request("GET", prefix + "/logs"))
                        raise AssertionError(f"Real model cycle requires attention: {status}")
                    return
                if status == "waiting_for_roll":
                    for check in request("GET", prefix + "/checks"):
                        if check["status"] != "pending":
                            continue
                        actor = next(
                            (
                                ho
                                for ho, mid in members.items()
                                if mid == check["target_member_id"] and ho in auth
                            ),
                            "host",
                        )
                        route = prefix + "/checks/" + check["id"]
                        stage = (check.get("settlement") or {}).get("stage")
                        request(
                            "POST",
                            route
                            + ("/choice" if stage in {"choice", "awaiting_choice"} else "/roll"),
                            {"operation": "accept"}
                            if stage in {"choice", "awaiting_choice"}
                            else {},
                            actor=actor,
                        )
                time.sleep(1)
            raise TimeoutError("Real model cycle did not finish; isolated evidence retained")

        request("POST", prefix + "/start")
        print("Created real room", room_id, "with three native HO cards", flush=True)
        settle()
        for action in [
            "我走到前院正在招呼客人的佣人旁，礼貌地问今晚晚宴在哪里举行，之后还有什么安排。",
        ]:
            request(
                "POST",
                prefix + "/actions",
                {"text": action, "client_request_id": str(uuid4())},
                actor="HO1",
            )
            settle()
        # A normal risky action using HO3's adjusted stealth; the model decides
        # whether a check is warranted, and the normal dice/settlement API rolls it.
        request(
            "POST",
            prefix + "/actions",
            {
                "text": "我趁佣人忙着招呼客人，压低身子，沿前院的阴影悄悄绕到别墅门边。"
                "我要避开佣人的视线，不被她发现地潜行过去；被发现就可能遭到拦阻。",
                "client_request_id": str(uuid4()),
            },
            actor="HO3",
        )
        settle()
        checks = request("GET", prefix + "/checks")
        write(directory / "checks.json", checks)
        write(directory / "real-model-calls.json", app.state.agent_service.model.calls)
        write(directory / "session-host.json", request("GET", prefix + "/logs"))
        prompt_checks = []
        for call in app.state.agent_service.model.calls:
            for message in call["input_messages"]:
                try:
                    context = json.loads(message.get("content", ""))
                except (ValueError, TypeError):
                    continue
                if not isinstance(context, dict):
                    continue
                private = context.get("private_handouts", [])
                if context.get("role") == "investigator":
                    assert [h["handout_id"] for h in private] == ["HO2"]
                    for other in ("HO1", "HO3"):
                        secret = cards[other]["module_handout"]["definition"]["text"]
                        assert (
                            json.dumps(secret, ensure_ascii=False)[1:-1] not in message["content"]
                        )
                    prompt_checks.append(
                        {
                            "role": "investigator",
                            "phase": context.get("phase", call["schema"]),
                            "handouts": [h["handout_id"] for h in private],
                        }
                    )
                if call["schema"] == "KeeperNarration" or context.get("phase") in {
                    "narrate_publicly",
                    "generate_keeper_narration",
                    "review_push",
                }:
                    assert not private
        write(directory / "ai-privacy-verification.json", prompt_checks)
        assert prompt_checks, "Need a real investigator prompt carrying only its own HO"
        adjusted_checks = [
            c
            for c in checks
            if (
                c["name"] in {"stealth", "潜行"}
                and c["target_member_id"] == members["HO3"]
                and c["value"] == 50
                and c["status"] == "resolved"
                and c["dice"]
            )
        ]
        assert adjusted_checks, "Need an actual adjusted stealth check, evidence retained"
        before = request("GET", prefix)
        saved = request("POST", prefix + "/snapshots", {"name": "第42批 HO 检定后"})
        snapshot = saved.get("snapshot", saved)
        request("POST", prefix + "/pause")
        for _ in range(2):
            request("POST", prefix + "/snapshots/" + snapshot["id"] + "/load")
        request("POST", prefix + "/resume")
        after = request("GET", prefix)
        assert before["session_state"] == after["session_state"]
        permissions_after = privacy("after")
        write(
            directory / "acceptance.json",
            {
                "status": "passed",
                "room_id": room_id,
                "preparation_id": pid,
                "snapshot_id": snapshot["id"],
                "repeat_import_reused": True,
                "repeat_adjustments_unchanged": True,
                "two_restores_unchanged": True,
                "permissions_before": permissions_before,
                "permissions_after": permissions_after,
                "ai_private_contexts": prompt_checks,
                "adjusted_checks": adjusted_checks,
                "real_model_calls": len(app.state.agent_service.model.calls),
            },
        )
        print("Acceptance passed", directory, flush=True)
    # Confirm the normal and old run databases are readable without opening them for writes.
    inventory = []
    for path in [ROOT / "data/game.db", SOURCE / "short-run-01/game.db"]:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            inventory.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "room_count": db.execute("SELECT count(*) FROM game_rooms").fetchone()[0],
                }
            )
    write(directory / "old-database-readonly-inventory.json", inventory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="run-01")
    run_name = parser.parse_args().name
    try:
        run(run_name)
    except Exception:
        target = OUTPUT / run_name
        if target.is_dir():
            (target / "failure.txt").write_text(traceback.format_exc(), "utf-8")
        raise
