"""Transient narration delivery uses only already validated public paragraphs."""

import asyncio

import pytest

from app.rooms.realtime import Connection, RoomHub
from app.rooms.service import Identity


def drain(connection):
    result = []
    while not connection.queue.empty():
        result.append(connection.queue.get_nowait())
    return result


@pytest.mark.asyncio
async def test_stream_coalesces_and_snapshot_covers_pending_without_database():
    hub = RoomHub(None)
    connection = Connection(None, Identity("member", False))
    hub.connections["room"] = {connection}
    stream_id = await hub.stream_start("room", "cycle")
    assert await hub.stream_delta("room", "cycle", stream_id, "公开😀。")
    assert await hub.stream_delta("room", "cycle", stream_id, "第二句。")
    assert hub.stream_snapshot("room")["data"]["text"] == "公开😀。第二句。"
    assert hub.stream_snapshot("room")["data"]["index"] == 2
    await asyncio.sleep(0.06)
    frames = drain(connection)
    assert [frame["type"] for frame in frames] == ["keeper.stream.start", "keeper.stream.delta"]
    assert frames[-1]["data"]["offset"] == 0
    assert frames[-1]["data"]["text"] == "公开😀。第二句。"
    assert all("revision" not in frame["data"] for frame in frames)


@pytest.mark.asyncio
async def test_cancel_and_retry_drop_pending_and_late_callbacks():
    hub = RoomHub(None)
    connection = Connection(None, Identity("member", False))
    hub.connections["room"] = {connection}
    old = await hub.stream_start("room", "cycle")
    await hub.stream_delta("room", "cycle", old, "先前正文。")
    assert hub.invalidate_stream("room", reason="cancelled")
    assert not await hub.stream_delta("room", "cycle", old, "迟到正文。")
    assert not await hub.stream_replace("room", "cycle", old, "迟到替换。")
    current = await hub.stream_start("room", "cycle", attempt=2)
    assert not await hub.stream_delta("room", "cycle", old, "旧回调。")
    assert await hub.stream_delta("room", "cycle", current, "重试正文。", attempt=2)
    await asyncio.sleep(0.06)
    frames = drain(connection)
    assert all("迟到" not in str(frame) and "先前正文" not in str(frame) for frame in frames)
    assert frames[-1]["data"]["attempt"] == 2
    assert frames[-1]["data"]["text"] == "重试正文。"


@pytest.mark.asyncio
async def test_stream_buffer_bounds_and_expiration_have_explicit_state():
    hub = RoomHub(None)
    hub.stream_max_chars = 8
    hub.stream_max_rooms = 2
    stream_id = await hub.stream_start("room", "cycle")
    await hub.stream_delta("room", "cycle", stream_id, "已经校验。")
    assert not await hub.stream_delta("room", "cycle", stream_id, "超过缓冲上限。")
    snapshot = hub.stream_snapshot("room")["data"]
    assert snapshot["text"] == "" and snapshot["status"] == "waiting"
    assert snapshot["reason"] == "buffer_limit"
    await hub.stream_start("second", "cycle-2")
    await hub.stream_start("third", "cycle-3")
    assert len(hub.streams) == 2 and "room" not in hub.streams
    hub.streams["second"].updated -= hub.stream_ttl + 1
    snapshot = hub.stream_snapshot("second")["data"]
    assert snapshot["status"] == "interrupted" and snapshot["reason"] == "expired"
    hub.invalidate_stream("third")


@pytest.mark.asyncio
async def test_slow_connection_compacts_drafts_and_keeps_pong_and_formal_event():
    hub = RoomHub(None)
    connection = Connection(None, Identity("member", False))
    hub.connections["room"] = {connection}
    stream_id = await hub.stream_start("room", "cycle")
    connection.put({"type": "pong", "data": {}})
    connection.put({"type": "room.event", "data": {"seq": 9}})
    for _ in range(100):
        await hub.stream_delta("room", "cycle", stream_id, "句。")
        hub._flush_stream("room", hub.streams["room"])
    connection.put({"type": "pong", "data": {"latest": True}})
    frames = drain(connection)
    assert len(frames) < 36
    assert frames[0] == {"type": "pong", "data": {"latest": True}}
    assert frames[1]["type"] == "pong"
    assert frames[2] == {"type": "room.event", "data": {"seq": 9}}
    assert any(frame["type"] == "keeper.stream.snapshot" for frame in frames)
    assert await hub.stream_end("room", "cycle", stream_id, event_seq=10)
    assert drain(connection)[-1]["data"]["event_seq"] == 10
    assert not await hub.stream_delta("room", "cycle", stream_id, "late")


def receive_type(socket, kind):
    while True:
        message = socket.receive_json()
        if message["type"] == kind:
            return message["data"]


def authenticate(socket, token, after_seq=0):
    socket.send_json({"type": "auth", "credential_type": "member", "token": token,
                      "after_seq": after_seq})
    assert socket.receive_json()["type"] == "auth.ok"
    receive_type(socket, "room.synced")
    return receive_type(socket, "keeper.stream.snapshot")


def test_two_members_reconnect_snapshot_is_atomic_with_subscription(client, character_app,
                                                                   monkeypatch):
    created = client.post("/api/rooms", json={"name": "流式测试"}).json()
    room_id = created["room"]["id"]
    members = [client.post("/api/rooms/join", json={
        "invite_code": created["invite_code"], "display_name": name,
    }).json() for name in ["甲", "乙"]]
    hub = character_app.state.room_hub
    before = client.get(f"/api/rooms/{room_id}").json()["latest_seq"]
    stream_id = client.portal.call(hub.stream_start, room_id, "cycle")
    client.portal.call(hub.stream_delta, room_id, "cycle", stream_id, "第一句。")
    path = f"/ws/rooms/{room_id}"
    with client.websocket_connect(path) as first, client.websocket_connect(path) as second:
        assert authenticate(first, members[0]["member_token"])["text"] == "第一句。"
        assert authenticate(second, members[1]["member_token"])["text"] == "第一句。"
        first.send_json({"type": "ping"})
        assert receive_type(first, "pong") == {}
    original_send = hub.send
    injected = False

    async def inject_during_replay(socket, message):
        nonlocal injected
        if message["type"] == "room.synced" and not injected:
            injected = True
            room_lock = character_app.state.room_service.lock(room_id)
            await asyncio.wait_for(room_lock.acquire(), timeout=0.5)
            room_lock.release()
            await hub.stream_delta(room_id, "cycle", stream_id, "重连期间的第二句。")
        await original_send(socket, message)

    monkeypatch.setattr(hub, "send", inject_during_replay)
    with client.websocket_connect(path) as reconnect:
        snapshot = authenticate(reconnect, members[0]["member_token"], before)
        assert snapshot["text"] == "第一句。"
        client.portal.call(hub.stream_delta, room_id, "cycle", stream_id, "订阅后的第三句。")
        # The short merge may overlap the snapshot; offset/index make that deduplicable.
        accepted, index = snapshot["text"], snapshot["index"]
        while index < 3:
            delta = receive_type(reconnect, "keeper.stream.delta")
            if delta["index"] <= index:
                continue
            assert delta["offset"] <= len(accepted)
            overlap = len(accepted) - delta["offset"]
            assert accepted[delta["offset"]:] == delta["text"][:overlap]
            accepted += delta["text"][overlap:]
            index = delta["index"]
        assert accepted == "第一句。重连期间的第二句。订阅后的第三句。"
    assert client.get(f"/api/rooms/{room_id}").json()["latest_seq"] == before
    assert "第一句" not in client.get(f"/api/rooms/{room_id}/events").text

    async def publish_final():
        async def append(session, room):
            return character_app.state.room_service.append(
                session, room, "keeper.narration", None,
                {"cycle_id": "cycle", "stream_id": stream_id, "stream_attempt": 1,
                 "text": "第一句。重连期间的第二句。订阅后的第三句。"},
            )
        return await character_app.state.agent_service.mutate(room_id, append)

    with client.websocket_connect(path) as current:
        authenticate(current, members[0]["member_token"], before)
        official = client.portal.call(publish_final)
        event = receive_type(current, "room.event")
        terminal = receive_type(current, "keeper.stream.end")
        assert event["seq"] == terminal["event_seq"] == official.seq
        assert event["type"] == "keeper.narration"
    assert hub.stream_snapshot(room_id)["data"]["status"] == "ended"
    assert hub.stream_snapshot(room_id)["data"]["text"] == ""
