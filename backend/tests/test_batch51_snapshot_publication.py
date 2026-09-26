"""Projection reuse preserves each connection's privacy and formal delivery."""

import json
import time

from test_batch51_runtime_timings import rows

from app.rooms.realtime import Connection
from app.rooms.service import Identity


def drain(connection):
    result = []
    while not connection.queue.empty():
        result.append(connection.queue.get_nowait())
    return result


def test_three_same_player_connections_projection_measurement(
    client, character_app, monkeypatch, tmp_path,
):
    created = client.post("/api/rooms", json={"name": "same projection measurement"}).json()
    room_id = created["room"]["id"]
    player = client.post("/api/rooms/join", json={
        "invite_code": created["invite_code"], "display_name": "one",
    }).json()["room"]["self_member_id"]
    rooms, hub = character_app.state.room_service, character_app.state.room_hub
    connections = [Connection(None, Identity(player, False)) for _ in range(3)]
    hub.connections[room_id] = set(connections)
    measured = []
    original_view = rooms.view

    async def measure_view(session, room, identity):
        started = time.perf_counter()
        result = await original_view(session, room, identity)
        measured.append((time.perf_counter() - started) * 1000)
        return result

    monkeypatch.setattr(rooms, "view", measure_view)

    async def prior_projections():
        # The old per-connection projection loop, on the same unchanged room,
        # identities and database session. Compare view work, not network timing.
        async with rooms.lock(room_id):
            async with rooms.database.sessions() as session:
                room = await rooms.room(session, room_id)
                return [await rooms.view(session, room, connection.identity)
                        for connection in connections]

    try:
        before = client.portal.call(prior_projections)
        prior_ms = list(measured)
        measured.clear()
        client.portal.call(hub.broadcast, room_id, [])
        frames = [drain(connection) for connection in connections]
        snapshots = [next(row["data"] for row in group if row["type"] == "room.snapshot")
                     for group in frames]
        assert snapshots == before and len(prior_ms) == 3 and len(measured) == 1
        assert all(any(row["type"] == "room.synced" for row in group) for group in frames)
        trace = [row for row in rows(rooms.timings) if row["status"] == "completed"
                 and row.get("connection_id") in {c.connection_id for c in connections}]
        assert sum(row["phase"] == "snapshot.build" for row in trace) == 1
        assert sum(row["phase"] == "snapshot.reuse" for row in trace) == 2
        measurement = {
            "model_calls": 0, "connections": 3, "distinct_player_identities": 1,
            "before": {"snapshot_builds": len(prior_ms), "view_ms": round(sum(prior_ms), 3)},
            "after": {"snapshot_builds": len(measured), "view_ms": round(sum(measured), 3),
                      "reuses": 2, "delivered_snapshots": len(snapshots)},
            "bodies_equal": snapshots == before,
            "method": "Same room/identities; compare original per-connection view loop to "
            "one-broadcast reuse. Views timed with perf_counter; no provider or network timing.",
        }
        (tmp_path / "projection-reuse-measurement.json").write_text(
            json.dumps(measurement, indent=2), encoding="utf-8",
        )
        print(json.dumps(measurement))
    finally:
        hub.connections.pop(room_id, None)


def test_same_identity_reuses_snapshot_but_other_player_and_host_stay_separate(
    client, character_app,
):
    created = client.post("/api/rooms", json={"name": "projection reuse"}).json()
    room_id = created["room"]["id"]
    players = [client.post("/api/rooms/join", json={
        "invite_code": created["invite_code"], "display_name": name,
    }).json()["room"]["self_member_id"] for name in ("one", "two")]
    rooms, hub = character_app.state.room_service, character_app.state.room_hub
    first = Connection(None, Identity(players[0], False))
    same = Connection(None, Identity(players[0], False))
    other = Connection(None, Identity(players[1], False))
    host = Connection(None, Identity(created["room"]["host_member_id"], True))
    connections = [first, same, other, host]
    hub.connections[room_id] = set(connections)
    try:
        client.portal.call(hub.broadcast, room_id, [])
        frames = [drain(connection) for connection in connections]
        snapshots = [next(row["data"] for row in group if row["type"] == "room.snapshot")
                     for group in frames]
        assert snapshots[0] is snapshots[1]
        assert snapshots[0] is not snapshots[2] and snapshots[0] is not snapshots[3]
        assert snapshots[0]["self_member_id"] == players[0]
        assert snapshots[2]["self_member_id"] == players[1]
        assert not snapshots[0]["is_host"] and snapshots[3]["is_host"]
        trace = [row for row in rows(rooms.timings) if row["status"] == "completed"
                 and row.get("connection_id") in {c.connection_id for c in connections}]
        assert sum(row["phase"] == "snapshot.build" for row in trace) == 3
        assert sum(row["phase"] == "snapshot.reuse" for row in trace) == 1
        enqueues = [row for row in trace if row["phase"] == "snapshot.enqueue"]
        assert len(enqueues) == 4
        assert len({row["source_snapshot_span_id"] for row in enqueues}) == 3

        # A new broadcast sees current committed state instead of retaining a cache.
        async def publish():
            async def operation(session, room):
                return rooms.append(session, room, "keeper.narration", players[0],
                                    {"cycle_id": "original", "text": "当时的检查结果。"},
                                    "actor_and_host")

            return await character_app.state.agent_service.mutate(
                room_id, operation, internal_only=True,
            )

        event = client.portal.call(publish)
        latest = [drain(connection) for connection in connections]
        for index, group in enumerate(latest):
            official = [row for row in group if row["type"] == "room.event"]
            assert bool(official) == (index != 2)
            fresh = next(row["data"] for row in group if row["type"] == "room.snapshot")
            assert fresh is not snapshots[index] and fresh["latest_seq"] == event.seq
            assert any(row["type"] == "room.synced" and row["data"]["seq"] == event.seq
                       for row in group)
        trace = rows(rooms.timings)
        assert any(row["phase"] == "mutate.broadcast" and row.get("forced_by_event")
                   for row in trace)
        published = [row for row in trace if row["phase"] == "formal.publish_enqueue"
                     and row["status"] == "completed" and row.get("event_seq") == event.seq]
        assert len(published) == 3
        assert len({row["connection_id"] for row in published}) == 3
    finally:
        hub.connections.pop(room_id, None)
