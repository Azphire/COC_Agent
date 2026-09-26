"""Timing boundaries are observational and keep failed/missing phases honest."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agents.timing import RuntimeTimings, stage_state
from app.persistence.agent_models import AgentCycle, AgentRun, ProfileRecord
from app.persistence.knowledge_models import AgentModelCall
from app.rooms.realtime import Connection
from app.rooms.service import Identity


def rows(timings):
    return [json.loads(line) for line in timings.path.read_text(encoding="utf-8").splitlines()]


def test_span_failure_keeps_reached_phase_and_omits_private_error(tmp_path):
    timings = RuntimeTimings(tmp_path / "phase.jsonl")
    with pytest.raises(ValueError), timings.span("outer", room_id="r", cycle_id="c"):
        with timings.span("prepare", run_id="run"):
            raise ValueError("secret source content")
    captured = rows(timings)
    assert captured[0]["status"] == "started" and captured[0]["elapsed_ms"] is None
    assert captured[-1]["status"] == "failed"
    assert captured[-2]["run_id"] == "run" and captured[-2]["cycle_id"] == "c"
    assert captured[-2]["error_type"] == "ValueError"
    assert "secret source" not in timings.path.read_text(encoding="utf-8")
    assert not any(row["phase"] == "commit" for row in captured)


def test_failed_stage_preserves_start_and_missing_start_is_unknown():
    active = stage_state(None, "running", now=10)
    failed = stage_state(active, "failed", now=12)
    assert failed["started_at"] == 10 and failed["elapsed_ms"] == 2000
    assert stage_state({}, "failed", now=12)["elapsed_ms"] is None


def test_failed_mutation_records_rollback_without_commit_and_releases_lock(client, character_app):
    room_id = client.post("/api/rooms", json={"name": "timing"}).json()["room"]["id"]
    rooms, agents = character_app.state.room_service, character_app.state.agent_service

    async def fail(session, room):
        raise ValueError("private failure")

    with pytest.raises(ValueError):
        client.portal.call(agents.mutate, room_id, fail)
    captured = [row for row in rows(rooms.timings)
                if "test_failed_mutation" in (row.get("callback") or "")]
    assert any(row["phase"] == "transaction.rollback" and row["status"] == "completed"
               for row in captured)
    assert not any(row["phase"] == "transaction.commit" for row in captured)
    assert any(row["phase"] == "mutate.callback" and row["status"] == "failed" for row in captured)
    assert not rooms.lock(room_id).locked()


@pytest.mark.parametrize("broadcast_internal", [True, False], ids=["before", "optimized"])
def test_two_connection_internal_audit_snapshot_measurement(
    client, character_app, tmp_path, monkeypatch, broadcast_internal,
):
    created = client.post("/api/rooms", json={"name": "audit timing"}).json()
    room_id = created["room"]["id"]
    members = [client.post("/api/rooms/join", json={
        "invite_code": created["invite_code"], "display_name": name,
    }).json()["room"]["self_member_id"] for name in ("one", "two")]
    rooms, agents, hub = (character_app.state.room_service, character_app.state.agent_service,
                         character_app.state.room_hub)
    cycle_id, run_id, profile_id = (str(uuid4()) for _ in range(3))

    async def setup():
        async with rooms.transaction() as session:
            session.add(ProfileRecord(id=profile_id, role="keeper", document={}))
            session.add(AgentCycle(id=cycle_id, room_id=room_id, status="completed",
                                   state={"current_node": "finish_cycle"}))
            await session.flush()
            session.add(AgentRun(id=run_id, room_id=room_id, cycle_id=cycle_id,
                                profile_id=profile_id,
                                actor_member_id=created["room"]["host_member_id"],
                                graph_node="generate_keeper_narration", status="completed",
                                input_seq_start=1, input_seq_end=1, provider="fake", model="fake",
                                context={}, structured_output=None))
        hub.connections[room_id] = {Connection(None, Identity(member, False)) for member in members}

    client.portal.call(setup)
    if broadcast_internal:
        original_mutate = agents.mutate

        async def prior_delivery(room_id, callback, **kwargs):
            return await original_mutate(room_id, callback, internal_only=False)

        monkeypatch.setattr(agents, "mutate", prior_delivery)

    async def record():
        with rooms.timings.scope(room_id=room_id, cycle_id=cycle_id, run_id=run_id):
            for _ in range(3):
                await agents.runtime.call_recorder(room_id, run_id)({"latency_ms": 1})
        async with rooms.database.sessions() as session:
            return len(list(await session.scalars(
                select(AgentModelCall).where(AgentModelCall.run_id == run_id),
            )))

    try:
        assert client.portal.call(record) == 3
        captured = [row for row in rows(rooms.timings) if row.get("run_id") == run_id
                    and row["status"] == "completed"]
        snapshots = [row for row in captured if row["phase"] == "snapshot.build"]
        audits = [row for row in captured if row["phase"] == "model.call_audit"]
        mutations = [row for row in captured if row["phase"] == "mutate"]
        assert len(snapshots) == (6 if broadcast_internal else 0) and len(audits) == 3
        assert all(row["event_count"] == 0 for row in mutations)
        measurement = {
            "model_calls": 0, "audit_writes": 3, "connections": 2,
            "mode": "prior_delivery" if broadcast_internal else "internal_audit_skip",
            "snapshot_builds": len(snapshots),
            "snapshot_ms": sum(row["elapsed_ms"] for row in snapshots),
            "audit_wall_ms": sum(row["elapsed_ms"] for row in audits),
            "case": "Same real local SQLite/views and two distinct players; "
            "only explicit internal broadcast flag differs. No model or artificial delay.",
        }
        (tmp_path / "audit-hotspot-measurement.json").write_text(
            json.dumps(measurement, indent=2), encoding="utf-8",
        )
        print(json.dumps(measurement))
    finally:
        hub.connections.pop(room_id, None)


@pytest.mark.asyncio
async def test_lock_wait_span_is_separate_from_hold(tmp_path):
    timings, lock = RuntimeTimings(tmp_path / "lock.jsonl"), asyncio.Lock()
    await lock.acquire()

    async def release():
        await asyncio.sleep(0.02)
        lock.release()

    task = asyncio.create_task(release())
    with timings.span("room_lock.wait"):
        await lock.acquire()
    lock.release()
    await task
    assert rows(timings)[-1]["elapsed_ms"] >= 10
