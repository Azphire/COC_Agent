"""Synthetic controls: designated fields, chunk recovery, atomic save/rewind."""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_agent_runtime import game  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.model import FakeModelAdapter
from app.memory.segments import (
    active_segments,
    canonical,
    make_segment,
    select_chunks,
    validate_segment,
)
from app.persistence.adjudication_models import SummaryRecoveryRecord
from app.persistence.agent_models import AgentCycle, AgentMemory


def event(seq, kind="chat.message", payload=None, visibility="public"):
    return {
        "seq": seq,
        "type": kind,
        "payload": payload or {"text": f"记录{seq}"},
        "visibility": visibility,
        "actor_member_id": "actor-a",
        "occurred_at": "2026-09-21T10:00:00Z",
    }


def chunks(events, **kwargs):
    return select_chunks(
        events, {"phase": "summary"}, lambda c: len(canonical(c)) <= 1600, **kwargs
    )[1]


def test_exact_field_coverage_rejects_reference_only_or_changed_payload():
    events = [
        event(1, "game.started"),
        event(
            2, "clue.revealed", {"content": "原文：密码 0073-蓝；不要倒置。", "scene_id": "hall"}
        ),
        event(
            3,
            "check.resolved",
            {"display_text": "侦查成功", "result": {"passed": True, "total": 17}},
        ),
        event(
            4,
            "module.interaction",
            {
                "text": "物品转交完成",
                "instance_id": "key-1",
                "from_member_id": "actor-a",
                "to_member_id": "actor-b",
            },
        ),
    ]
    doc = make_segment("模型漏掉了密码和检定数值。", events, chunks(events[1:], all_events=events))
    assert "0073-蓝" in canonical(doc["required_facts"])
    source = next(r for r in doc["required_facts"] if r["id"] == "source:3")
    assert source["scene_id"] == "hall" and source["payload"]["result"]["total"] == 17
    assert source["occurred_at"] == events[2]["occurred_at"]
    broken = deepcopy(doc)
    next(r for r in broken["required_facts"] if r["id"] == "source:3")["payload"]["result"][
        "passed"
    ] = False
    with pytest.raises(ValueError, match="Required source fields"):
        validate_segment(broken, events)
    broken = deepcopy(doc)
    broken["required_facts"] = [{"id": r["id"]} for r in broken["required_facts"]]
    with pytest.raises(ValueError, match="Required source fields"):
        validate_segment(broken, events)


def test_long_event_contiguous_chunks_and_full_exact_payload_only_at_completion():
    original = event(
        7, "clue.revealed", {"content": "前文。" * 1800 + "密码：0099", "scene_id": "old"}
    )
    progress, previous, docs = 0, [], []
    while progress < len(canonical(original)):
        selected = chunks([original], partial_seq=7, partial_offset=progress)
        doc = make_segment("独立片段。", [original], selected, prior_chunks=previous)
        docs.append(doc)
        previous.extend(selected)
        progress = selected[-1]["end"]
        assert doc["coverage"]["fully_covered_seqs"] == (
            [7] if progress == len(canonical(original)) else []
        )
    assert len(docs) >= 3
    assert previous[0]["start"] == 0
    assert all(a["end"] == b["start"] for a, b in zip(previous, previous[1:]))
    assert (
        next(r for r in docs[-1]["required_facts"] if r["id"] == "source:7")["payload"]
        == original["payload"]
    )
    broken = deepcopy(docs[-1])
    broken["prior_chunks"] = broken["prior_chunks"][1:]
    with pytest.raises(ValueError, match="gap"):
        validate_segment(broken, [original])


def test_private_sources_and_abandoned_branch_never_reuse_segment_prose():
    public = event(2, "chat.message", {"text": "公开说法"})
    secret = event(3, "chat.message", {"text": "私人 HO 密码"}, "host_only")
    events = [event(1, "game.started"), public, secret]
    doc = make_segment("含私人信息的混合段。", events, chunks(events[1:], all_events=events))
    memory = SimpleNamespace(
        kind="summary_segment",
        id="segment-one",
        active=True,
        content=json.dumps(doc, ensure_ascii=False),
    )
    assert active_segments([memory], events)
    assert not active_segments([memory], events[:2])
    restored = [*events, event(4, "snapshot.loaded", {"source_seq": 2})]
    assert not active_segments([memory], restored)
    # A stream transport frame is not a formal memory event.
    assert active_segments([memory], [*events, event(5, "agent.narration.chunk", {"text": "草稿"})])


def test_atomic_segments_failure_cursor_and_expired_generation(client, game):  # noqa: F811
    svc = client.app.state.agent_service
    svc.settings.agent_event_window = 2
    svc.settings.agent_context_chars = 3000
    svc.settings.model_context_limit = 32000
    svc.model.adapter = FakeModelAdapter(responder=lambda *_: {"content": "人物仍有未完成调查。"})
    room_id, profile_id, cycle_id = game["room"]["id"], game["profiles"][0]["id"], str(uuid4())

    async def seed():
        async def operation(session, room):
            for binding in await svc.bindings(session, room.id):
                binding.enabled = binding.profile_id == profile_id
            session.add(
                AgentCycle(
                    id=cycle_id, room_id=room.id, status="completed",
                    state={"call_count": 0, "current_node": "completed"}
                )
            )
            for scene in range(5):
                svc.rooms.append(
                    session,
                    room,
                    "chat.message",
                    room.host_member_id,
                    {
                        "text": f"场景{scene}仍待调查，原文密码 00{scene}7。" * 12,
                        "scene_id": f"scene-{scene}",
                    },
                )
            for _ in range(2):
                svc.rooms.append(
                    session, room, "chat.message", room.host_member_id, {"text": "最近完整对话"}
                )

        await svc.mutate(room_id, operation)

    async def state():
        async with svc.rooms.database.sessions() as session:
            row = await session.get(SummaryRecoveryRecord, (room_id, profile_id))
            memories = list(
                await session.scalars(
                    select(AgentMemory).where(
                        AgentMemory.room_id == room_id,
                        AgentMemory.kind == "summary_segment",
                        AgentMemory.active.is_(True),
                    )
                )
            )
            return deepcopy(row.document) if row else {}, memories

    client.portal.call(seed)
    for _ in range(3):
        client.portal.call(svc.summary_recovery.update, room_id, cycle_id, True)
    before, segments = client.portal.call(state)
    assert len(segments) == 3 and before["segment_version"] == 1
    assert all(json.loads(s.content)["coverage"]["validated"] for s in segments)
    assert all(s.scope == "public" for s in segments)
    assert all(
        json.loads(prompt[-1]["content"])["previous_summary"] is None
        for prompt in svc.model.adapter.prompts
    )
    # The existing save format restores both independent segment rows and the
    # JSON recovery cursor, including a later save loaded after an earlier one.
    ok(client.post(game["prefix"] + "/pause"))
    saved_one = ok(client.post(game["prefix"] + "/snapshots", json={"name": "three segments"}))[
        "snapshot"
    ]
    client.portal.call(svc.summary_recovery.update, room_id, cycle_id, True)
    four, segments_four = client.portal.call(state)
    assert len(segments_four) == 4
    saved_two = ok(client.post(game["prefix"] + "/snapshots", json={"name": "four segments"}))[
        "snapshot"
    ]
    client.portal.call(svc.summary_recovery.update, room_id, cycle_id, True)
    for saved, expected_state, count in [
        (saved_one, before, 3),
        (saved_two, four, 4),
        (saved_one, before, 3),
    ]:
        ok(client.post(game["prefix"] + f"/snapshots/{saved['id']}/load"))
        restored_state, restored_rows = client.portal.call(state)
        assert restored_state == expected_state and len(restored_rows) == count
    svc.model.adapter = FakeModelAdapter(responses=[{"content": "伪造 seq:999999"}])
    client.portal.call(svc.summary_recovery.update, room_id, cycle_id, True)
    failed, after = client.portal.call(state)
    assert len(after) == 3 and failed["stale"]
    assert failed["last_successful_summary_seq"] == before["last_successful_summary_seq"]

    async def restore_during_generation():
        async def operation(session, room):
            row = await session.get(SummaryRecoveryRecord, (room_id, profile_id))
            row.document = before
            svc.rooms.append(
                session,
                room,
                "snapshot.loaded",
                room.host_member_id,
                {"source_seq": before["last_successful_summary_seq"]},
            )

        await svc.mutate(room_id, operation)
        return {"content": "这一过期生成不可提交。"}

    svc.model.adapter = FakeModelAdapter(responses=[restore_during_generation])
    client.portal.call(svc.summary_recovery.update, room_id, cycle_id, True)
    restored, after = client.portal.call(state)
    assert restored == before and len(after) == 3
