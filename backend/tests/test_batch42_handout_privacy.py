"""Permission regressions use an isolated text-only approved-catalog fixture.

The real package/source audit and numeric HO acceptance are covered separately.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_batch42_character_handouts import (
    base_card,
    select,
    trusted_preparation,  # noqa: F401
)
from test_rooms import character, create_room, headers, join, lobby, ok  # noqa: F401

from app.rooms.handouts import character_view
from app.rooms.service import RoomError


def test_private_character_projection_rejects_a_different_member():
    room = SimpleNamespace(session_state={})
    slot = SimpleNamespace(member_id="owner", character_snapshot={"background": "private"})
    with pytest.raises(RoomError) as error:
        character_view(room, slot, "other")
    assert error.value.status == 403
    assert character_view(room, slot, "keeper", keeper=True) == slot.character_snapshot


def catalog(client, room_id):
    value = {
        "preparation_id": "privacy-fixture",
        "source_id": "privacy-source",
        "source_hash": "a" * 64,
        "preparation_version": 1,
        "handouts": [
            {
                "id": f"ho-{index}", "title": f"私人 HO {index}",
                "text": f"PRIVATE-HO-{index} 只属于该接收者的过去。",
                "source_hash": "a" * 64, "source_pages": [index],
                "source_block_ids": [f"private-block-{index}"], "adjustments": None,
            }
            for index in (1, 2)
        ],
    }

    async def seed():
        rooms = client.app.state.room_service
        async with rooms.transaction() as session:
            room = await rooms.room(session, room_id)
            room.session_state = {**room.session_state, "handout_catalog": value}

    client.portal.call(seed)
    return value


def two_members(client):
    created = create_room(client)
    room_id = created["room"]["id"]
    prefix = f"/api/rooms/{room_id}"
    members, slots = [], []
    for index in (1, 2):
        joined = join(client, created["invite_code"], f"接收者{index}")
        members.append(joined)
        sheet = character(client, f"调查员{index}")
        room = ok(client.post(
            prefix + "/character-slots", json={"character_id": sheet["id"]},
        ))["room"]
        slot = room["character_slots"][-1]["id"]
        slots.append(slot)
        ok(client.post(prefix + "/character-assignments", json={
            "slot_id": slot, "member_id": joined["room"]["self_member_id"],
        }))
    catalog(client, room_id)
    return created, prefix, members, slots


def assign(client, prefix, member_id, slot_id, index, request_id=None):
    body = {"handout_id": f"ho-{index}", "member_id": member_id, "slot_id": slot_id,
            "client_request_id": request_id or str(uuid4())}
    return client.post(prefix + "/handout-assignments", json=body), body


def until_synced(socket):
    messages = []
    while True:
        message = socket.receive_json()
        messages.append(message)
        if message["type"] == "room.synced":
            return messages


def test_handouts_http_socket_replay_logs_and_real_actor(client):
    created, prefix, members, slots = two_members(client)
    room_id = created["room"]["id"]
    with client.websocket_connect(f"/ws/rooms/{room_id}") as first:
        first.send_json({"type": "auth", "credential_type": "member",
                         "token": members[0]["member_token"]})
        assert "PRIVATE-HO" not in json.dumps(until_synced(first))
        with client.websocket_connect(f"/ws/rooms/{room_id}") as second:
            second.send_json({"type": "auth", "credential_type": "member",
                              "token": members[1]["member_token"]})
            assert "PRIVATE-HO" not in json.dumps(until_synced(second))
            response, body = assign(
                client, prefix, members[0]["room"]["self_member_id"], slots[0], 1,
            )
            result = ok(response)
            event = result["event"]
            assert event["actor_member_id"] == created["room"]["host_member_id"]
            assert event["recipient_member_id"] == body["member_id"]
            assert event["visibility"] == "recipient_and_host"
            assert "PRIVATE-HO-1" in json.dumps(until_synced(first))
            assert "PRIVATE-HO" not in json.dumps(until_synced(second))
    ok(assign(client, prefix, members[1]["room"]["self_member_id"], slots[1], 2)[0])
    for index, member in enumerate(members, 1):
        auth = headers(member["member_token"])
        for path in ("", "/events", "/logs", "/logs?format=markdown"):
            response = client.get(prefix + path, headers=auth)
            assert response.status_code == 200
            assert f"PRIVATE-HO-{index}" in response.text
            assert f"PRIVATE-HO-{3-index}" not in response.text
            assert f"private-block-{3-index}" not in response.text
        view = ok(client.get(prefix, headers=auth))
        assert view["handouts"]["available"] == []
        assert view["session_state"]["handout_catalog"] is None
        assert len(view["handouts"]["assignments"]) == 1
        assert "character_snapshot" not in next(
            s for s in view["character_slots"] if s["member_id"] != view["self_member_id"]
        )
        assert client.post(
            prefix + "/handout-assignments", headers=auth, json=body,
        ).status_code == 403
        with client.websocket_connect(f"/ws/rooms/{room_id}") as socket:
            socket.send_json({"type": "auth", "credential_type": "member",
                              "token": member["member_token"], "after_seq": 0})
            replay = json.dumps(until_synced(socket))
            assert f"PRIVATE-HO-{index}" in replay
            assert f"PRIVATE-HO-{3-index}" not in replay
    host = ok(client.get(prefix))
    assert len(host["handouts"]["available"]) == 2
    assert len(host["handouts"]["assignments"]) == 2


def test_handout_restore_retry_and_recipient_lock(client):
    _, prefix, members, slots = two_members(client)
    for member in members:
        ok(client.post(
            prefix + "/ready", headers=headers(member["member_token"]), json={"ready": True},
        ))
    ok(client.post(prefix + "/start"))
    ok(client.post(prefix + "/pause"))
    before = ok(client.post(prefix + "/snapshots", json={"name": "分发前"}))["snapshot"]["id"]
    response, body = assign(client, prefix, members[0]["room"]["self_member_id"], slots[0], 1)
    first = ok(response)
    same = ok(client.post(prefix + "/handout-assignments", json=body))
    assert same["room"]["revision"] == first["room"]["revision"]
    after = ok(client.post(prefix + "/snapshots", json={"name": "分发后"}))["snapshot"]["id"]
    room = ok(client.post(prefix + f"/snapshots/{before}/load"))["room"]
    assert not room["handouts"]["assignments"]
    repeated = ok(client.post(prefix + "/handout-assignments", json=body))
    assert repeated["room"]["revision"] > room["revision"]
    assert len(repeated["room"]["handouts"]["assignments"]) == 1
    assert repeated["handout_assignment"] == first["handout_assignment"]
    events = ok(client.get(prefix + "/events"))["events"]
    assert sum(e["type"] == "handout.assigned" for e in events) == 1
    assert sum(e["type"] == "handout.restored" for e in events) == 1
    assert assign(
        client, prefix, members[1]["room"]["self_member_id"], slots[1], 1,
    )[0].status_code == 409
    ok(client.delete(prefix + f"/character-assignments/{slots[0]}"))
    ok(client.delete(prefix + f"/character-assignments/{slots[1]}"))
    assert client.post(prefix + "/character-assignments", json={
        "slot_id": slots[0], "member_id": members[1]["room"]["self_member_id"],
    }).status_code == 409
    restored = ok(client.post(prefix + f"/snapshots/{after}/load"))["room"]
    assert restored["handouts"]["assignments"] == first["room"]["handouts"]["assignments"]
    state = restored["session_state"]
    state["handout_assignments"] = []
    assert client.patch(prefix + "/session-state", json={
        "expected_revision": restored["revision"], "state": state,
    }).status_code == 422


def test_ho_card_cannot_be_claimed_or_read_before_private_delivery(
    client, trusted_preparation,  # noqa: F811
):
    created, prefix, members, slots = two_members(client)
    card = ok(select(client, base_card(client), 1, approve=True))
    card = ok(client.post(f"/api/characters/{card['id']}/finalize", json={
        "version": card["version"],
    }))
    published = ok(client.post(prefix + "/character-slots", json={"character_id": card["id"]}))
    private_slot = published["room"]["character_slots"][-1]["id"]

    async def seed_catalog():
        rooms = client.app.state.room_service
        async with rooms.transaction() as session:
            room = await rooms.room(session, created["room"]["id"])
            room.session_state = {**room.session_state, "handout_catalog": {
                "preparation_id": "batch42-prep", "source_id": "batch42-source",
                "source_hash": "a" * 64, "preparation_version": 1,
                "handouts": [h.model_dump(mode="json") for h in trusted_preparation],
            }}

    client.portal.call(seed_catalog)
    auth = headers(members[0]["member_token"])
    ok(client.delete(prefix + f"/character-assignments/{slots[0]}", headers=auth))
    assert client.post(prefix + "/character-assignments", headers=auth, json={
        "slot_id": private_slot,
    }).status_code == 403
    ok(client.post(prefix + "/character-assignments", json={
        "slot_id": private_slot, "member_id": members[0]["room"]["self_member_id"],
    }))
    before = ok(client.get(prefix, headers=auth))
    assert "Private handout" not in json.dumps(before)
    own = next(s for s in before["character_slots"] if s["id"] == private_slot)
    assert own["character_snapshot"]["module_handout"] is None
    assert own["character_snapshot"]["skill_values"]["psychology"] == 80
    ok(client.post(prefix + "/handout-assignments", json={
        "handout_id": "HO1", "member_id": members[0]["room"]["self_member_id"],
        "slot_id": private_slot, "client_request_id": str(uuid4()),
    }))
    assert "Private handout 1" in client.get(prefix, headers=auth).text
    assert "Private handout" not in client.get(
        prefix, headers=headers(members[1]["member_token"]),
    ).text


def test_agents_only_receive_own_ho_and_public_narrator_receives_none(client, game):  # noqa: F811
    prefix = game["prefix"]
    ok(client.post(prefix + "/pause"))
    catalog(client, game["room"]["id"])
    ok(assign(client, prefix, game["player"], game["slots"][0], 1)[0])
    ok(assign(client, prefix, game["agent"], game["slots"][1], 2)[0])
    saved = ok(client.post(prefix + "/snapshots", json={"name": "AI 私密记忆"}))["snapshot"]["id"]
    ok(client.post(prefix + f"/snapshots/{saved}/load"))
    ok(client.post(prefix + "/resume"))
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    contexts = [json.loads(p[-1]["content"]) for p in game["adapter"].prompts
                if p[-1]["content"].startswith("{")]
    investigator = [c for c in contexts if c.get("role") == "investigator"]
    assert investigator
    for context in investigator:
        text = json.dumps(context)
        assert "PRIVATE-HO-1" not in text
        if context.get("phase") != "summary":
            assert "PRIVATE-HO-2" in text
            assert context["private_handouts"][0]["member_id"] == game["agent"]
    keeper = [c for c in contexts if c.get("role") == "keeper" and c.get("private_handouts")]
    assert keeper and all(
        [h["member_id"] for h in c["private_handouts"]]
        == [c["triggering_action"]["actor_member_id"]] for c in keeper
    )
    assert all(len(c["private_handout_index"]) == 2 for c in keeper)
    narration = [c for c in contexts if c.get("response_brief")]
    assert narration and all("PRIVATE-HO" not in json.dumps(c) for c in narration)
