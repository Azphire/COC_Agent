import asyncio
import base64
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.auth import digest
from app.main import create_app
from app.persistence.room_models import RoomMember
from app.rooms.schemas import MessageRequest
from app.rooms.service import RoomService
from scripts.host_key import initialize


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def create_room(client):
    return ok(client.post("/api/rooms", json={"name": "测试房间"}), 201)


def join(client, invite, name="远程玩家"):
    return ok(
        client.post("/api/rooms/join", json={"invite_code": invite, "display_name": name}), 201
    )


def character(client, name="调查员", finalized=True):
    values = {
        "str": 60,
        "con": 60,
        "siz": 60,
        "dex": 60,
        "app": 50,
        "int": 60,
        "pow": 50,
        "edu": 60,
    }
    draft = ok(
        client.post(
            "/api/characters/point-buy",
            json={
                "ruleset_id": "coc7-character-creation",
                "name": name,
                "age": 25,
                "attributes": {key: {"value": value} for key, value in values.items()},
            },
        ),
        201,
    )
    if not finalized:
        return draft
    draft = ok(
        client.patch(
            f"/api/characters/{draft['id']}",
            json={
                "version": draft["version"],
                "occupation": "professor",
                "selected_occupation_skills": [
                    "accounting",
                    "anthropology",
                    "archaeology",
                    "history",
                ],
                "occupation_skills": {"credit_rating": {"points": 20}},
            },
        )
    )
    return ok(
        client.post(f"/api/characters/{draft['id']}/finalize", json={"version": draft["version"]})
    )


@pytest.fixture
def lobby(client):
    created = create_room(client)
    prefix = f"/api/rooms/{created['room']['id']}"
    remote = join(client, created["invite_code"])
    for name in ("调查员甲", "调查员乙"):
        sheet = character(client, name)
        ok(client.post(prefix + "/character-slots", json={"character_id": sheet["id"]}))
    room = ok(
        client.post(
            prefix + "/members", json={"display_name": "占位队友", "controller_type": "agent"}
        )
    )["room"]
    return {
        "prefix": prefix,
        "created": created,
        "remote": remote,
        "room": room,
        "player": remote["room"]["self_member_id"],
        "agent": next(m["id"] for m in room["members"] if m["controller_type"] == "agent"),
        "slots": [slot["id"] for slot in room["character_slots"]],
    }


def prepare(client, lobby):
    prefix = lobby["prefix"]
    for member, slot in zip([lobby["player"], lobby["agent"]], lobby["slots"], strict=True):
        ok(
            client.post(
                prefix + "/character-assignments", json={"slot_id": slot, "member_id": member}
            )
        )
    ok(
        client.post(
            prefix + "/ready",
            headers=headers(lobby["remote"]["member_token"]),
            json={"ready": True},
        )
    )
    ok(client.post(prefix + "/ready", json={"ready": True, "member_id": lobby["agent"]}))
    return ok(client.post(prefix + "/start"))["room"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/characters"),
        ("POST", "/api/characters/random"),
        ("GET", "/api/rooms"),
        ("POST", "/api/rooms"),
        ("GET", "/api/model/status"),
        ("GET", "/openapi.json"),
        ("POST", "/api/host/unlock"),
    ],
)
def test_host_boundary(client, method, path):
    for token in ("", "wrong-key", "非 ASCII 密钥"):
        response = client.request(
            method,
            path,
            headers=headers(token) if token.isascii() else headers("wrong-unicode-test"),
        )
        assert response.status_code == 401
        assert token == "" or token not in response.text


def test_public_metadata_and_unlock(client):
    assert client.get("/api/health", headers=headers("invalid")).status_code == 200
    assert client.get("/api/character-rulesets", headers=headers("invalid")).status_code == 200
    assert ok(client.post("/api/host/unlock")) == {"unlocked": True}


def test_credentials_invites_and_names(client, character_settings):
    created = create_room(client)
    prefix = f"/api/rooms/{created['room']['id']}"
    joined = join(client, created["invite_code"], "Alice")
    remote_headers = headers(joined["member_token"])
    assert (
        client.post(
            "/api/rooms/join",
            json={"invite_code": created["invite_code"], "display_name": " alice "},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/rooms/join", json={"invite_code": "invalid", "display_name": "A"}
        ).status_code
        == 404
    )
    rotated = ok(client.post(prefix + "/invite/rotate"))["invite_code"]
    assert (
        client.post(
            "/api/rooms/join", json={"invite_code": created["invite_code"], "display_name": "Bob"}
        ).status_code
        == 404
    )
    join(client, rotated, "Bob")
    assert client.get(prefix, headers=remote_headers).status_code == 200
    with sqlite3.connect(character_settings.data_dir / "characters.db") as database:
        stored = database.execute(
            "SELECT token_hash FROM room_members WHERE id=?", (joined["room"]["self_member_id"],)
        ).fetchone()[0]
        assert stored == digest(joined["member_token"])
        assert database.execute("SELECT invite_hash FROM game_rooms").fetchone()[0] == digest(
            rotated
        )
        dump = "\n".join(database.iterdump())
    ordinary = (
        client.get(prefix).text
        + client.get(prefix + "/events").text
        + client.get(prefix + "/logs").text
    )
    for secret in (
        joined["member_token"],
        created["invite_code"],
        rotated,
        character_settings.host_admin_token.get_secret_value(),
    ):
        assert secret not in dump + ordinary
    invalid = client.post(
        "/api/rooms/join",
        json={"invite_code": {"token": joined["member_token"]}, "display_name": "bad"},
    )
    assert invalid.status_code == 422 and joined["member_token"] not in invalid.text


def test_publish_only_finalized_and_immutable(client, character_settings):
    created = create_room(client)
    prefix = f"/api/rooms/{created['room']['id']}"
    draft = character(client, finalized=False)
    assert (
        client.post(prefix + "/character-slots", json={"character_id": draft["id"]}).status_code
        == 422
    )
    sheet = character(client)
    room = ok(client.post(prefix + "/character-slots", json={"character_id": sheet["id"]}))["room"]
    slot = room["character_slots"][0]
    assert slot["character_snapshot"] == sheet
    assert {
        k: v
        for k, v in room["session_state"]["characters"][slot["id"]].items()
        if k not in {"san_max", "sanity", "hp_max", "armor", "injury", "weapons"}
    } == {
        "hp": 12,
        "mp": 10,
        "san": 50,
        "luck": sheet["derived_values"]["luck"],
        "conditions": [],
    }
    with sqlite3.connect(character_settings.data_dir / "characters.db") as database:
        document = json.loads(
            database.execute(
                "SELECT document FROM character_drafts WHERE id=?", (sheet["id"],)
            ).fetchone()[0]
        )
        document["name"] = "源库修改"
        database.execute(
            "UPDATE character_drafts SET document=? WHERE id=?", (json.dumps(document), sheet["id"])
        )
    assert ok(client.get(prefix))["character_slots"][0]["character_snapshot"] == sheet
    assert (
        client.post(prefix + "/character-slots", json={"character_id": sheet["id"]}).status_code
        == 409
    )


def test_seats_privacy_assignments_ready_and_start(client, lobby):
    p = lobby["prefix"]
    remote_headers = headers(lobby["remote"]["member_token"])
    room = ok(client.get(p, headers=remote_headers))
    assert all("character_snapshot" not in s for s in room["character_slots"])
    assert room["session_state"]["characters"] == {}
    assert client.get("/api/characters", headers=remote_headers).status_code == 401
    assert client.post(p + "/start").status_code == 409
    assert (
        client.post(p + "/ready", headers=remote_headers, json={"ready": True}).status_code == 409
    )
    assert (
        client.post(
            p + "/character-assignments",
            headers=remote_headers,
            json={"slot_id": lobby["slots"][0], "member_id": lobby["agent"]},
        ).status_code
        == 403
    )
    local = ok(client.post(p + "/members", json={"display_name": "本地玩家"}))["room"]["members"][
        -1
    ]
    assert (local["access_type"], local["controller_type"]) == ("host_managed", "human")
    ok(client.patch(p + "/members/" + local["id"], json={"active": False}))
    prepare(client, lobby)
    assert client.post(p + "/start").status_code == 409
    room = ok(client.get(p, headers=remote_headers))
    assert "character_snapshot" in room["character_slots"][0]
    assert "character_snapshot" not in room["character_slots"][1]
    assert list(room["session_state"]["characters"]) == [lobby["slots"][0]]
    ok(client.post(p + "/pause"))
    assert (
        client.post(
            p + "/character-assignments",
            json={"slot_id": lobby["slots"][0], "member_id": lobby["agent"]},
        ).status_code
        == 409
    )
    assert (
        client.delete(
            p + "/character-assignments/" + lobby["slots"][1], headers=remote_headers
        ).status_code
        == 403
    )
    ok(client.delete(p + "/character-assignments/" + lobby["slots"][0], headers=remote_headers))
    assert not next(m for m in ok(client.get(p))["members"] if m["id"] == lobby["player"])["ready"]
    assert client.post(p + "/resume").status_code == 409


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("POST", "/invite/rotate", None),
        ("POST", "/members", {"display_name": "intruder"}),
        ("POST", "/character-slots", {"character_id": str(uuid4())}),
        ("POST", "/start", None),
        ("POST", "/pause", None),
        ("POST", "/resume", None),
        ("POST", "/end", None),
        ("POST", "/snapshots", {"name": "bad"}),
        ("GET", "/snapshots", None),
        ("POST", f"/snapshots/{uuid4()}/load", None),
        ("PATCH", "/session-state", {"expected_revision": 1, "state": {}}),
    ],
)
def test_non_host_commands_forbidden(client, method, suffix, body):
    created = create_room(client)
    remote = join(client, created["invite_code"])
    response = client.request(
        method,
        f"/api/rooms/{created['room']['id']}" + suffix,
        json=body,
        headers=headers(remote["member_token"]),
    )
    assert response.status_code == 403


@pytest.mark.parametrize("action", ["pause", "resume", "end"])
def test_invalid_lobby_transition(client, action):
    room = create_room(client)["room"]
    assert client.post(f"/api/rooms/{room['id']}/{action}").status_code == 409
    assert client.post(f"/api/rooms/{room['id']}/start").status_code == 409


def test_all_transitions_and_ended_readonly(client, lobby):
    p = lobby["prefix"]
    prepare(client, lobby)
    assert client.post(p + "/resume").status_code == 409
    ok(client.post(p + "/pause"))
    assert client.post(p + "/pause").status_code == 409
    ok(client.post(p + "/resume"))
    ended = ok(client.post(p + "/end"))["room"]
    for suffix, body in [
        ("/messages", {"text": "bad", "client_request_id": str(uuid4())}),
        ("/invite/rotate", None),
        ("/members", {"display_name": "bad"}),
        ("/snapshots", {"name": "bad"}),
        ("/resume", None),
        ("/leave", None),
    ]:
        assert client.post(p + suffix, json=body).status_code == 409
    assert ok(client.get(p)) == ended
    assert client.get(p + "/logs").status_code == 200


def test_dice_chat_idempotency_visibility_and_exports(client, lobby):
    p = lobby["prefix"]
    other = join(client, lobby["created"]["invite_code"], "另一玩家")
    remote_headers = headers(lobby["remote"]["member_token"])
    other_headers = headers(other["member_token"])
    for kind, body in [
        ("messages", {"text": "私密调查"}),
        ("rolls", {"expression": "3d6+2", "reason": "私密检定"}),
    ]:
        body |= {"visibility": "actor_and_host", "client_request_id": str(uuid4())}
        result = ok(client.post(p + "/" + kind, headers=remote_headers, json=body))
        repeated = ok(client.post(p + "/" + kind, headers=remote_headers, json=body))
        assert repeated["event"] == result["event"]
        assert repeated["room"]["revision"] == result["room"]["revision"]
        body["visibility"] = "public"
        assert client.post(p + "/" + kind, headers=remote_headers, json=body).status_code == 409
        if kind == "rolls":
            payload = result["event"]["payload"]
            assert len(payload["dice"]) == 3 and all(1 <= d <= 6 for d in payload["dice"])
            assert payload["modifier"] == 2 and payload["total"] == sum(payload["dice"]) + 2
            assert payload["operator_member_id"] == lobby["player"]
    host_only = ok(
        client.post(
            p + "/messages",
            json={
                "text": "主持人笔记",
                "visibility": "host_only",
                "client_request_id": str(uuid4()),
            },
        )
    )["event"]
    assert host_only["seq"] not in [
        e["seq"] for e in ok(client.get(p + "/events", headers=remote_headers))["events"]
    ]
    assert "私密调查" not in client.get(p + "/events", headers=other_headers).text
    for format in ("jsonl", "markdown"):
        own = client.get(p + f"/logs?format={format}", headers=remote_headers)
        assert own.status_code == 200 and "私密调查" in own.text and "主持人笔记" not in own.text
        assert (
            "私密调查" not in client.get(p + f"/logs?format={format}", headers=other_headers).text
        )
        assert "主持人笔记" in client.get(p + f"/logs?format={format}").text
        assert lobby["remote"]["member_token"] not in own.text
        if format == "jsonl":
            assert all("seq" in json.loads(line) for line in own.text.splitlines())
        else:
            assert own.text.startswith("# 房间")
    assert (
        client.post(
            p + "/rolls",
            json={"expression": "1d100", "total": 1, "client_request_id": str(uuid4())},
        ).status_code
        == 422
    )
    assert (
        client.post(
            p + "/rolls", json={"expression": "__import__('os')", "client_request_id": str(uuid4())}
        ).status_code
        == 422
    )
    assert (
        client.post(
            p + "/messages",
            headers=remote_headers,
            json={"text": "bad", "visibility": "host_only", "client_request_id": str(uuid4())},
        ).status_code
        == 403
    )
    assert (
        client.post(
            p + "/messages",
            json={
                "text": "fake agent",
                "actor_member_id": lobby["agent"],
                "client_request_id": str(uuid4()),
            },
        ).status_code
        == 403
    )


def test_snapshot_restore_append_only_credentials_and_restart(client, lobby, character_settings):
    p = lobby["prefix"]
    room = prepare(client, lobby)
    original_state = room["session_state"]
    save = ok(client.post(p + "/snapshots", json={"name": "检查点"}))["snapshot"]
    assert client.post(p + f"/snapshots/{save['id']}/load").status_code == 409
    ok(client.post(p + "/pause"))
    room = ok(client.get(p))
    room = ok(
        client.post(
            p + "/resources/correct",
            json={
                "expected_revision": room["revision"],
                "slot_id": lobby["slots"][0],
                "resource": "hp",
                "value": 1,
                "reason": "存档测试中的主机资源更正",
            },
        )
    )["room"]
    changed = json.loads(json.dumps(original_state))
    changed["scene_title"] = "未来的场景"
    changed["characters"][lobby["slots"][0]]["hp"] = 1
    changed["characters"][lobby["slots"][0]]["conditions"] = ["疲惫"]
    ok(
        client.patch(
            p + "/session-state", json={"expected_revision": room["revision"], "state": changed}
        )
    )
    invite = ok(client.post(p + "/invite/rotate"))["invite_code"]
    before = ok(client.get(p + "/events"))["events"]
    loaded = ok(client.post(p + f"/snapshots/{save['id']}/load"))["room"]
    assert loaded["status"] == "paused" and loaded["session_state"] == original_state
    after = ok(client.get(p + "/events"))["events"]
    assert after[:-1] == before and after[-1]["type"] == "snapshot.loaded"
    assert client.get(p, headers=headers(lobby["remote"]["member_token"])).status_code == 200
    with sqlite3.connect(character_settings.data_dir / "characters.db") as db:
        assert db.execute("SELECT invite_hash FROM game_rooms").fetchone()[0] == digest(invite)
        document = json.loads(db.execute("SELECT document FROM room_snapshots").fetchone()[0])
        assert set(document) == {"format_version", "state", "assignments"}
    with TestClient(create_app(character_settings), headers=client.headers) as restarted:
        assert ok(restarted.get(p)) == loaded
        assert ok(restarted.get(p + "/events"))["events"] == after
        assert ok(restarted.get(p + "/snapshots")) == [save]
        assert restarted.get(p, headers=headers(lobby["remote"]["member_token"])).status_code == 200


def test_snapshot_skips_inactive_members_and_keeps_current_credentials(client, lobby):
    p = lobby["prefix"]
    prepare(client, lobby)
    save = ok(client.post(p + "/snapshots", json={"name": "before leave"}))["snapshot"]
    ok(client.post(p + "/pause"))
    ok(client.post(p + "/leave", headers=headers(lobby["remote"]["member_token"])))
    restored = ok(client.post(p + f"/snapshots/{save['id']}/load"))["room"]
    assert restored["character_slots"][0]["member_id"] is None
    assert not next(m for m in restored["members"] if m["id"] == lobby["player"])["active"]
    assert client.get(p, headers=headers(lobby["remote"]["member_token"])).status_code == 401


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: state.update(version=True),
        lambda state: state.update(arbitrary={"json": 1}),
        lambda state: state.update(round_number=True),
        lambda state: state.update(active_slot_id=str(uuid4())),
        lambda state: state.update(characters={}),
        lambda state: next(iter(state["characters"].values())).update(hp=-1),
        lambda state: next(iter(state["characters"].values())).update(san="50"),
        lambda state: next(iter(state["characters"].values())).update(conditions=["x"] * 31),
    ],
)
def test_session_state_validated(client, lobby, mutation):
    room = prepare(client, lobby)
    state = room["session_state"]
    mutation(state)
    assert (
        client.patch(
            lobby["prefix"] + "/session-state",
            json={"expected_revision": room["revision"], "state": state},
        ).status_code
        == 422
    )


def test_stale_state_and_cross_room_credentials(client, lobby):
    room = prepare(client, lobby)
    assert (
        client.patch(
            lobby["prefix"] + "/session-state",
            json={"expected_revision": 1, "state": room["session_state"]},
        ).status_code
        == 409
    )
    another = create_room(client)
    assert (
        client.get(
            f"/api/rooms/{another['room']['id']}", headers=headers(lobby["remote"]["member_token"])
        ).status_code
        == 401
    )


def test_concurrent_http_commands_and_idempotency(client):
    room = create_room(client)["room"]
    p = f"/api/rooms/{room['id']}"
    body = {"expression": "2d6", "client_request_id": str(uuid4())}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ok(client.post(p + "/rolls", json=body)), range(12)))
        list(
            pool.map(
                lambda i: ok(
                    client.post(
                        p + "/messages", json={"text": str(i), "client_request_id": str(uuid4())}
                    )
                ),
                range(12),
            )
        )
    assert len({r["event"]["seq"] for r in results}) == 1
    events = ok(client.get(p + "/events"))["events"]
    assert [e["seq"] for e in events] == list(range(1, 15))


def test_database_serializes_independent_services(client, character_app):
    created = create_room(client)
    room_id = created["room"]["id"]
    token = character_app.state.settings.host_admin_token.get_secret_value()

    async def run():
        # Separate lock dictionaries prove correctness does not depend on one Python lock.
        services = [
            RoomService(character_app.state.database, character_app.state.settings)
            for _ in range(8)
        ]
        await asyncio.gather(
            *(
                svc.command(
                    room_id,
                    token,
                    "message",
                    MessageRequest(text="concurrent", client_request_id=uuid4()),
                )
                for svc in services
            )
        )

    client.portal.call(run)
    events = ok(client.get(f"/api/rooms/{room_id}/events"))["events"]
    assert [e["seq"] for e in events] == list(range(1, 10))


def socket_auth(ws, token, kind="member", after_seq=0):
    ws.send_json({"type": "auth", "credential_type": kind, "token": token, "after_seq": after_seq})
    assert ws.receive_json()["type"] == "auth.ok"
    snapshot = ws.receive_json()
    assert snapshot["type"] == "room.snapshot"
    replay = until_synced(ws)
    return snapshot["data"], replay


def until_synced(ws):
    messages = []
    while True:
        message = ws.receive_json()
        messages.append(message)
        if message["type"] == "room.synced":
            return messages


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "ping"},
        {"type": "auth", "credential_type": "member", "token": "invalid"},
        {"type": "auth", "credential_type": "host", "token": "invalid"},
        ["bad frame"],
    ],
)
def test_socket_auth_before_any_data(client, frame):
    room = create_room(client)["room"]
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        ws.send_json(frame)
        response = ws.receive_json()
        assert response["type"] == "error" and room["name"] not in json.dumps(response)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_socket_filter_reconnect_and_presence(client, lobby, character_app):
    p = lobby["prefix"]
    other = join(client, lobby["created"]["invite_code"], "第三个客户端")
    path = f"/ws/rooms/{lobby['room']['id']}"
    host_token = character_app.state.settings.host_admin_token.get_secret_value()
    remote_token = lobby["remote"]["member_token"]
    with (
        client.websocket_connect(path) as host,
        client.websocket_connect(path) as player,
        client.websocket_connect(path) as outsider,
    ):
        socket_auth(host, host_token, "host")
        own, _ = socket_auth(player, remote_token)
        public, _ = socket_auth(outsider, other["member_token"])
        assert all("character_snapshot" not in slot for slot in public["character_slots"])
        private = ok(
            client.post(
                p + "/messages",
                headers=headers(remote_token),
                json={
                    "text": "socket private",
                    "visibility": "actor_and_host",
                    "client_request_id": str(uuid4()),
                },
            )
        )["event"]
        host_frames, own_frames, other_frames = (
            until_synced(host),
            until_synced(player),
            until_synced(outsider),
        )
        assert "socket private" in json.dumps(host_frames) and "socket private" in json.dumps(
            own_frames
        )
        assert "socket private" not in json.dumps(other_frames)
        assert remote_token not in json.dumps(host_frames + own_frames + other_frames)
        player.send_json({"type": "ping"})
        while player.receive_json()["type"] != "pong":
            pass
        assert own["self_member_id"] in character_app.state.room_hub.presence(lobby["room"]["id"])
    missing = ok(
        client.post(
            p + "/messages", json={"text": "after disconnect", "client_request_id": str(uuid4())}
        )
    )["event"]
    with client.websocket_connect(path) as player:
        _, replay = socket_auth(player, remote_token, after_seq=private["seq"])
        assert [m["data"]["seq"] for m in replay if m["type"] == "room.event"] == [missing["seq"]]
        assert character_app.state.room_hub.presence(lobby["room"]["id"]) == [lobby["player"]]


def test_socket_revocation_and_snapshot_presence(client, lobby, character_app):
    p = lobby["prefix"]
    prepare(client, lobby)
    token = lobby["remote"]["member_token"]
    with client.websocket_connect(f"/ws/rooms/{lobby['room']['id']}") as ws:
        socket_auth(ws, token)
        save = ok(client.post(p + "/snapshots", json={"name": "presence"}))["snapshot"]
        until_synced(ws)
        ok(client.post(p + "/pause"))
        until_synced(ws)
        loaded = ok(client.post(p + f"/snapshots/{save['id']}/load"))
        frames = until_synced(ws)
        assert any(m["type"] == "room.snapshot" and m["data"]["status"] == "paused" for m in frames)
        assert lobby["player"] in character_app.state.room_hub.presence(lobby["room"]["id"])
        assert loaded["room"]["members"][1]["last_seen_at"] is not None
        ok(client.patch(p + "/members/" + lobby["player"], json={"active": False}))
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()


def test_event_pagination_advances_across_hidden_gaps(client):
    created = create_room(client)
    p = f"/api/rooms/{created['room']['id']}"
    remote = join(client, created["invite_code"])
    ok(
        client.post(
            p + "/messages",
            json={
                "text": "hidden tail",
                "visibility": "host_only",
                "client_request_id": str(uuid4()),
            },
        )
    )
    page = ok(client.get(p + "/events?limit=1", headers=headers(remote["member_token"])))
    assert page["next_seq"] == 1
    tail = ok(
        client.get(p + "/events?after_seq=2&limit=1", headers=headers(remote["member_token"]))
    )
    assert tail["events"] == [] and tail["next_seq"] == tail["latest_seq"] == 3


async def test_only_active_remote_members_have_credentials(client, character_app, lobby):
    async with character_app.state.database.sessions() as session:
        members = list(await session.scalars(select(RoomMember)))
    assert all((m.token_hash is not None) == (m.access_type == "remote") for m in members)


@pytest.mark.parametrize("setting", ["", "HOST_ADMIN_TOKEN=\n", 'HOST_ADMIN_TOKEN=""\n'])
def test_host_key_initialization_preserves_settings(tmp_path, setting):
    path = tmp_path / ".env"
    path.write_text("MODEL_NAME=preserve-this\n" + setting, encoding="utf-8")
    initialize(path)
    content = path.read_text(encoding="utf-8")
    assert "MODEL_NAME=preserve-this\n" in content
    token = content.split("HOST_ADMIN_TOKEN=", 1)[1].strip()
    assert len(base64.urlsafe_b64decode(token + "=")) >= 32
    initialize(path)
    assert path.read_text(encoding="utf-8") == content


def test_local_actor_audit_and_unauthorized_ready(client, lobby):
    p = lobby["prefix"]
    local_room = ok(client.post(p + "/members", json={"display_name": "本地真人"}))["room"]
    local = next(m for m in local_room["members"] if m["display_name"] == "本地真人")
    result = ok(
        client.post(
            p + "/messages",
            json={
                "text": "我来调查",
                "actor_member_id": local["id"],
                "client_request_id": str(uuid4()),
            },
        )
    )["event"]
    assert result["actor_member_id"] == local["id"]
    assert result["payload"]["operator_member_id"] == local_room["host_member_id"]
    assert (
        client.post(p + "/ready", json={"ready": False, "member_id": lobby["player"]}).status_code
        == 403
    )
    assert (
        client.patch(
            p + "/members/" + lobby["agent"],
            headers=headers(lobby["remote"]["member_token"]),
            json={"active": False},
        ).status_code
        == 403
    )


def test_load_restores_changed_assignments_atomically(client, lobby):
    p = lobby["prefix"]
    original = prepare(client, lobby)
    saved = ok(client.post(p + "/snapshots", json={"name": "assignments"}))["snapshot"]
    ok(client.post(p + "/pause"))
    for slot in lobby["slots"]:
        ok(client.delete(p + "/character-assignments/" + slot))
    for member, slot in zip([lobby["agent"], lobby["player"]], lobby["slots"], strict=True):
        ok(client.post(p + "/character-assignments", json={"slot_id": slot, "member_id": member}))
    loaded = ok(client.post(p + f"/snapshots/{saved['id']}/load"))["room"]
    assert loaded["character_slots"] == original["character_slots"]
    assert all(not m["ready"] for m in loaded["members"] if m["role"] == "player")
    other = create_room(client)
    assert (
        client.post(f"/api/rooms/{other['room']['id']}/snapshots/{saved['id']}/load").status_code
        == 409
    )


def test_rename_duplicate_deactivate_rejoin_and_slot_remove(client, lobby):
    p = lobby["prefix"]
    token = lobby["remote"]["member_token"]
    assert (
        client.patch(
            p + "/members/" + lobby["player"],
            headers=headers(token),
            json={"display_name": "占位队友"},
        ).status_code
        == 409
    )
    ok(
        client.patch(
            p + "/members/" + lobby["player"],
            headers=headers(token),
            json={"display_name": "新名字"},
        )
    )
    ok(client.post(p + "/leave", headers=headers(token)))
    again = join(client, lobby["created"]["invite_code"], "新名字")
    assert again["room"]["self_member_id"] != lobby["player"]
    assert client.get(p, headers=headers(token)).status_code == 401
    ok(client.delete(p + "/character-slots/" + lobby["slots"][0]))
    room = ok(client.get(p))
    assert len(room["character_slots"]) == 1
    assert lobby["slots"][0] not in room["session_state"]["characters"]


def test_authenticated_echo_rejects_repeated_credentials(client, character_app):
    token = character_app.state.settings.host_admin_token.get_secret_value()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "credential_type": "host", "token": token})
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "auth", "credential_type": "host", "token": token})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_cors_authorization_and_delete(client):
    response = client.options(
        "/api/rooms",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert "Authorization" in response.headers["access-control-allow-headers"]
