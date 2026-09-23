"""Durable launch retries, start gates and the host's ordinary player projection."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_batch42_handout_privacy import assign, catalog, until_synced
from test_module_preparation import approve_opening, preparation  # noqa: F401
from test_rooms import character, create_room, headers, join, ok

from app.persistence.agent_models import RoomAgentBinding
from app.persistence.launch_models import LaunchDraft
from app.persistence.room_models import GameRoom


@pytest.fixture
def launch(client, preparation):  # noqa: F811
    approved = approve_opening(client, preparation)
    svc = client.app.state.agent_service
    folder = svc.settings.data_dir / "rules"
    folder.mkdir(exist_ok=True)
    (folder / "CoC7.txt").write_text(
        "第七版规则：普通检定结果不高于技能值时成功。", encoding="utf-8",
    )
    svc.knowledge.indexer.index("rules")
    card = character(client, "本地主角")
    body = {"client_request_id": str(uuid4()), "preparation_id": approved["id"],
            "character_id": card["id"], "name": "向导测试", "acknowledge_unspent": True}
    draft = ok(client.post("/api/launch-drafts", json=body))
    return {"draft": draft, "body": body, "path": "/api/launch-drafts/" + draft["id"]}


def test_create_assemble_start_retry_does_not_duplicate_any_committed_step(client, launch):
    first = launch["draft"]
    assert ok(client.post("/api/launch-drafts", json=launch["body"]))["id"] == first["id"]
    conflict = {**launch["body"], "name": "不同选择"}
    assert client.post("/api/launch-drafts", json=conflict).status_code == 409
    assert ok(client.post(launch["path"] + "/preflight"))["can_start"]
    body = {"expected_version": first["version"]}
    assembled = ok(client.post(launch["path"] + "/assemble", json=body))
    assert assembled["status"] == "assembled", assembled
    repeated = ok(client.post(launch["path"] + "/assemble", json=body))
    assert repeated["room_id"] == assembled["room_id"]
    prefix = "/api/rooms/" + assembled["room_id"]
    room = ok(client.get(prefix))
    assert len([m for m in room["members"] if m["role"] == "player"]) == 1
    assert len(room["character_slots"]) == 1
    assert ok(client.post(prefix + "/play-session", json={}))["selected_member_id"] == (
        assembled["local_member_id"]
    )
    started = ok(client.post(launch["path"] + "/start", json={}))
    assert started["status"] == "started", started
    assert ok(client.post(launch["path"] + "/start", json={})) == started
    events = ok(client.get(prefix + "/events"))["events"]
    assert sum(e["type"] == "game.started" for e in events) == 1
    openings = [e for e in events if e["payload"].get("opening")]
    assert len(openings) == 1 and openings[0]["payload"]["text"] == "你们站在安静的候车厅。"
    assert not any(e["type"] == "action.submitted" for e in events)
    assert client.patch(launch["path"], json={"version": started["version"],
                                           "character_id": launch["body"]["character_id"]}
                        ).status_code == 409


def test_missing_keeper_blocks_both_wizard_preflight_and_existing_start(client, launch):
    assembled = ok(client.post(launch["path"] + "/assemble", json={}))
    prefix = "/api/rooms/" + assembled["room_id"]

    async def remove_keeper():
        svc = client.app.state.room_service
        async with svc.transaction() as session:
            room = await svc.room(session, assembled["room_id"])
            await session.execute(delete(RoomAgentBinding).where(
                RoomAgentBinding.room_id == room.id,
                RoomAgentBinding.member_id == room.host_member_id,
            ))

    client.portal.call(remove_keeper)
    check = ok(client.post(launch["path"] + "/preflight"))
    assert any(i["code"] == "agent_config" for i in check["issues"])
    ok(client.post(prefix + "/ready", json={"member_id": assembled["local_member_id"],
                                           "ready": True}))
    assert client.post(prefix + "/start").status_code == 422
    assert ok(client.get(prefix))["status"] == "lobby"
    started = ok(client.post(launch["path"] + "/start", json={}))
    assert started["status"] == "assembled" and started["issues"]
    ok(client.post(launch["path"] + "/repair"))
    assert ok(client.post(launch["path"] + "/start", json={}))["status"] == "started"
    assert client.post(launch["path"] + "/repair").status_code == 409


def test_remote_player_must_choose_card_and_confirm_own_readiness(client, launch):
    assembled = ok(client.post(launch["path"] + "/assemble", json={}))
    prefix = "/api/rooms/" + assembled["room_id"]
    guest = join(client, assembled["invite_code"], "朋友")
    member_id = guest["room"]["self_member_id"]
    card = character(client, "朋友的角色")
    published = ok(client.post(prefix + "/character-slots", json={"character_id": card["id"]}))
    slot = next(s for s in published["room"]["character_slots"] if not s["member_id"])
    ok(client.post(prefix + "/character-assignments", headers=headers(guest["member_token"]),
                   json={"slot_id": slot["id"], "member_id": member_id}))
    assert client.post(prefix + "/ready", json={"member_id": member_id, "ready": True}
                       ).status_code == 403
    blocked = ok(client.post(launch["path"] + "/start", json={}))
    assert any(i["code"] == "ready" and i["repair"] == "invite" for i in blocked["issues"])
    assert not next(m for m in ok(client.get(prefix))["members"] if m["id"] == member_id)["ready"]
    ok(client.post(prefix + "/ready", headers=headers(guest["member_token"]), json={"ready": True}))
    started = ok(client.post(launch["path"] + "/start", json={}))
    assert started["status"] == "started", started


def test_failed_room_assembly_can_retry_without_an_orphan_room(client, launch, monkeypatch):
    svc = client.app.state.launch_service
    original = svc.agents.entities.bind

    async def interrupted(*args, **kwargs):
        raise RuntimeError("interruption after create before binding")

    monkeypatch.setattr(svc.agents.entities, "bind", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        client.post(launch["path"] + "/assemble", json={})

    async def persisted():
        async with svc.rooms.database.sessions() as session:
            return list(await session.scalars(select(GameRoom))), await session.get(
                LaunchDraft, launch["draft"]["id"],
            )

    rooms, draft = client.portal.call(persisted)
    assert not rooms and draft.room_id is None
    monkeypatch.setattr(svc.agents.entities, "bind", original)
    recovered = ok(client.post(launch["path"] + "/assemble", json={}))
    assert recovered["status"] == "assembled"
    assert ok(client.get(launch["path"]))["room_id"] == recovered["room_id"]


def test_model_failure_preserves_assembled_room_and_recovers_on_retry(client, launch):
    assembled = ok(client.post(launch["path"] + "/assemble", json={}))
    models = client.app.state.model_settings
    models.verification, models.error = "failed", "测试：当前模型不可用"
    blocked = ok(client.post(launch["path"] + "/start", json={}))
    assert blocked["room_id"] == assembled["room_id"] and blocked["status"] == "assembled"
    assert any(i["code"] == "model" for i in blocked["issues"])
    models.verification, models.error = "available", None
    recovered = ok(client.post(launch["path"] + "/start", json={}))
    assert recovered["room_id"] == assembled["room_id"]
    assert recovered["status"] == "started", recovered["issues"]


def test_start_rechecks_room_revision_and_draft_version(client, launch):
    first = launch["draft"]
    changed = ok(client.patch(launch["path"], json={"version": first["version"],
                                                 "name": "更新后的调查"}))
    assert client.post(launch["path"] + "/assemble", json={"expected_version": first["version"]}
                       ).status_code == 409
    assembled = ok(client.post(launch["path"] + "/assemble", json={
        "expected_version": changed["version"],
    }))
    prefix = "/api/rooms/" + assembled["room_id"]
    ok(client.post(prefix + "/ready", json={"member_id": assembled["local_member_id"],
                                           "ready": True}))
    assert client.post(launch["path"] + "/start", json={
        "expected_room_revision": assembled["room_revision"],
    }).status_code == 409
    assert ok(client.get(prefix))["status"] == "lobby"
    assert ok(client.post(launch["path"] + "/start", json={}))["status"] == "started"


def test_host_play_token_projects_only_own_ho_and_cannot_enter_management(client):
    created = create_room(client)
    prefix = "/api/rooms/" + created["room"]["id"]
    local = ok(client.post(prefix + "/members", json={"display_name": "本地主角",
                                                       "controller_type": "human"}))["room"]
    local_id = next(m["id"] for m in local["members"] if m["role"] == "player")
    remote = join(client, created["invite_code"], "朋友")
    remote_id = remote["room"]["self_member_id"]
    slots = []
    for index, member_id in enumerate([local_id, remote_id], 1):
        card = character(client, f"调查员{index}")
        room = ok(client.post(prefix + "/character-slots", json={
            "character_id": card["id"],
        }))["room"]
        slot = next(s["id"] for s in room["character_slots"] if not s["member_id"])
        slots.append(slot)
        ok(client.post(prefix + "/character-assignments", json={"slot_id": slot,
                                                                "member_id": member_id}))
    catalog(client, created["room"]["id"])
    ok(assign(client, prefix, local_id, slots[0], 1)[0])
    ok(assign(client, prefix, remote_id, slots[1], 2)[0])
    played = ok(client.post(prefix + "/play-session", json={}))
    assert played["selected_member_id"] == local_id and played["room"]["is_host"] is False
    assert "PRIVATE-HO-1" in json.dumps(played) and "PRIVATE-HO-2" not in json.dumps(played)
    local_auth = headers(played["member_token"])
    for suffix in ("", "/events", "/logs"):
        response = client.get(prefix + suffix, headers=local_auth)
        assert response.status_code == 200
        assert "PRIVATE-HO-1" in response.text and "PRIVATE-HO-2" not in response.text
    with client.websocket_connect("/ws/rooms/" + created["room"]["id"]) as socket:
        socket.send_json({"type": "auth", "credential_type": "member",
                          "token": played["member_token"], "after_seq": 0})
        synced = json.dumps(until_synced(socket))
        assert "PRIVATE-HO-1" in synced and "PRIVATE-HO-2" not in synced
    assert client.post(prefix + "/play-session", headers=local_auth, json={}).status_code == 401
    assert client.post(prefix + "/pause", headers=local_auth).status_code == 403
    other = create_room(client)
    assert client.get("/api/rooms/" + other["room"]["id"], headers=local_auth).status_code == 401
    assert client.get(prefix, headers=headers(played["member_token"] + "x")).status_code == 401
    assert "PRIVATE-HO-2" in client.get(prefix).text
    assert "PRIVATE-HO-1" not in client.get(prefix, headers=headers(remote["member_token"])).text


def test_remote_identity_cannot_be_requested_as_a_host_play_identity(client):
    created = create_room(client)
    prefix = "/api/rooms/" + created["room"]["id"]
    ok(client.post(prefix + "/members", json={"display_name": "本地主角",
                                             "controller_type": "human"}))
    remote = join(client, created["invite_code"], "朋友")
    response = client.post(prefix + "/play-session", json={
        "member_id": remote["room"]["self_member_id"],
    })
    view = ok(response)
    assert view["selected_member_id"] != remote["room"]["self_member_id"]
    assert view["room"]["self_member_id"] == view["selected_member_id"]
    assert not view["room"]["is_host"]
