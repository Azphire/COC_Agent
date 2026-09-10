"""Batch 9 boundaries; all sources and model responses in this file are Fake."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_knowledge_runtime import rag_game  # noqa: F401
from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_module_navigation_runtime import act, running_navigation  # noqa: F401
from test_rooms import headers, lobby, ok  # noqa: F401

from app.agents.model import FakeModelAdapter
from app.knowledge.service import KnowledgeContextBuilder
from app.memory.events import story_events
from app.module_ir.facts import scoped_statement
from app.persistence.adjudication_models import SummaryRecoveryRecord
from app.persistence.agent_models import AgentCycle, AgentMemory, CheckRecord
from app.persistence.preparation_models import RoomEntityState
from app.rules.topics import RuleTopicRegistry


def ask(client, d, text, request_id=None):
    return client.post(
        d["prefix"] + "/actions",
        headers=headers(d["remote"]["member_token"]),
        json={
            "text": text,
            "category": "rule_question",
            "client_request_id": request_id or str(uuid4()),
        },
    )


def public_events(client, prefix):
    return ok(client.get(prefix + "/events"))["events"]


def test_explicit_questions_have_no_gameplay_effects_and_keep_audit(client, rag_game):  # noqa: F811
    d, svc = rag_game, client.app.state.agent_service
    adapter = FakeModelAdapter(responder=lambda *_: pytest.fail("Rule answers must bypass plans"))
    svc.model.adapter = adapter

    async def snapshot():
        async with svc.rooms.database.sessions() as session:
            module = await svc.module(session, d["room"]["id"])
            return {
                "module": module.state,
                "bindings": [
                    (b.id, b.status, b.last_consumed_event_seq)
                    for b in await svc.bindings(session, module.room_id)
                ],
                "memories": [m.id for m in await session.scalars(select(AgentMemory))],
                "checks": [c.id for c in await session.scalars(select(CheckRecord))],
            }

    before = client.portal.call(snapshot)
    request_id = str(uuid4())
    first = ok(ask(client, d, "奖励骰怎么用？另外超光速航行规则？", request_id))
    cycle = wait_cycle(client, d)
    assert cycle["status"] == "completed" and cycle["state"]["call_count"] == 0
    assert (
        ok(ask(client, d, "奖励骰怎么用？另外超光速航行规则？", request_id))["event"]
        == first["event"]
    )
    assert ask(client, d, "不同内容", request_id).status_code == 409
    assert submit(client, d, "奖励骰怎么用？另外超光速航行规则？", request_id).status_code == 409
    assert before == client.portal.call(snapshot)
    events = [e for e in public_events(client, d["prefix"]) if e["seq"] >= first["event"]["seq"]]
    assert {e["type"] for e in events} == {
        "rules.question",
        "rules.answered",
        "agent.cycle_changed",
    }
    answer = next(e["payload"] for e in events if e["type"] == "rules.answered")
    assert [a["status"] for a in answer["answers"]] == ["related_excerpt", "not_found"]
    assert "紫月" not in json.dumps(answer, ensure_ascii=False)
    runs = ok(client.get(d["prefix"] + "/agent-runs"))
    assert len(runs) == 1 and set(runs[0]["context"]) == {"question", "RULE_EVIDENCE"}
    audit_path = d["prefix"] + f"/agent-runs/{runs[0]['id']}/retrievals"
    audits = ok(client.get(audit_path))
    assert len(audits) == 2 and all(a["source_filters"]["kind"] == "rules" for a in audits)
    assert client.get(audit_path, headers=headers(d["remote"]["member_token"])).status_code == 403


def test_exact_version_topics_and_other_version_fallback(client, rag_game, monkeypatch):  # noqa: F811
    # Deliberately attach the registry to an original Fake fixture, never a real report.
    d = rag_game
    monkeypatch.setattr(RuleTopicRegistry, "source_hash", d["binding"]["rules"][0]["source_hash"])
    ok(ask(client, d, "奖励骰和惩罚骰怎么使用？困难成功与极难成功有什么区别？"))
    assert wait_cycle(client, d)["status"] == "completed"
    payload = next(
        e["payload"]
        for e in reversed(public_events(client, d["prefix"]))
        if e["type"] == "rules.answered"
    )
    assert len(payload["answers"]) == 2
    assert all(a["status"] == "topic_reference" for a in payload["answers"])
    assert {c["physical_page"] for c in payload["citations"]} == {73, 79}
    monkeypatch.setattr(RuleTopicRegistry, "source_hash", "different-version")
    ok(ask(client, d, "奖励骰怎么使用？"))
    assert wait_cycle(client, d)["status"] == "completed"
    payload = next(
        e["payload"]
        for e in reversed(public_events(client, d["prefix"]))
        if e["type"] == "rules.answered"
    )
    assert payload["answers"][0]["status"] == "related_excerpt"


def test_rule_question_waits_for_active_round_and_paused_room(client, game):  # noqa: F811
    ok(submit(client, game, "对工作台进行侦查检定；我冒着失去平衡的风险尝试。"))
    assert wait_cycle(client, game)["status"] == "waiting_for_roll"
    assert ask(client, game, "奖励骰怎么使用？").status_code == 409
    ok(client.post(game["prefix"] + "/agent-cycle/cancel"))
    ok(client.post(game["prefix"] + "/pause"))
    assert ask(client, game, "奖励骰怎么使用？").status_code == 409


def test_fact_candidates_prioritize_identity_and_qualify_history():
    entities = [
        {
            "id": "old",
            "type": "clue",
            "title": "便签",
            "public_summary": "旧的公开信息。",
            "fact_scope": "historical",
        },
        {
            "id": "here",
            "type": "clue",
            "title": "便签",
            "public_summary": "眼前的公开信息。",
            "fact_scope": "current_scene",
        },
        {
            "id": "unknown",
            "type": "item",
            "title": "钥匙",
            "public_summary": "已知钥匙信息。",
            "fact_scope": "unknown",
        },
    ]
    context = {
        "public_entities": entities,
        "module": {"scene": {"id": "scene", "public_description": "当前场景。"}},
        "triggering_action": {"payload": {"text": "查看便签"}},
    }
    choices = KnowledgeContextBuilder.public_claim_options(context)
    assert choices[0]["entity_ids"] == ["here"]
    assert not any("old" in c["entity_ids"] or "unknown" in c["entity_ids"] for c in choices)
    context["triggering_action"]["payload"]["text"] = "回顾之前的便签和钥匙"
    context["fact_target"] = "old"
    choices = KnowledgeContextBuilder.public_claim_options(context)
    assert choices[0]["entity_ids"] == ["old"]
    assert choices[0]["statement"] == scoped_statement(entities[0], entities[0]["public_summary"])
    assert next(c for c in choices if c["entity_ids"] == ["unknown"])["statement"].startswith(
        "已知信息（位置未确认）"
    )
    context["intent_type"] = "recall"
    choices = KnowledgeContextBuilder.public_claim_options(context)
    assert [c["entity_ids"] for c in choices] == [["old"]]


def test_transition_scopes_multiscene_and_restored_projection(client, running_navigation):  # noqa: F811
    d, svc = running_navigation, client.app.state.agent_service
    prefix = d["room_prefix"]

    async def reveal():
        async def operation(session, room):
            for e in d["entities"][2:]:
                await svc.entities.reveal(
                    session, room, e["id"], room.host_member_id, host_override=True
                )
            original = await svc.entities.entity(session, room.id, d["entities"][2]["id"])
            unknown_id = str(uuid4())
            session.add(
                RoomEntityState(
                    room_id=room.id,
                    source_entity_id=unknown_id,
                    entity_type="npc",
                    snapshot={**original.snapshot, "id": unknown_id},
                    state="hidden",
                    frozen_public_summary="An unlocated NPC with the same name.",
                    frozen_source_references=[],
                )
            )
            await session.flush()
            await svc.entities.reveal(
                session, room, unknown_id, room.host_member_id, host_override=True
            )

        await svc.mutate(d["room"]["id"], operation)

    client.portal.call(reveal)
    before = ok(client.get(prefix))["game"]["public_entities"]
    assert (
        next(e for e in before if e["public_summary"].startswith("An unlocated"))["fact_scope"]
        == "unknown"
    )
    assert all(
        e["fact_scope"] == "current_scene"
        for e in before
        if not e["public_summary"].startswith("An unlocated")
    )
    saved = ok(client.post(prefix + "/snapshots", json={"name": "before transition"}))["snapshot"]
    assert act(client, d, "我进入 Future move")["status"] == "completed"
    public = ok(client.get(prefix))["game"]["public_entities"]
    by_title = {e["title"]: e for e in public if not e["public_summary"].startswith("An unlocated")}
    assert by_title["Guard"]["fact_scope"] == "current_scene"  # bound in both scenes
    assert by_title["Notice"]["fact_scope"] == "historical"
    assert all("node_id" not in e and "bindings" not in e for e in public)
    from test_action_adjudication import modern_response

    narration_attempts = 0

    def unqualified_history(messages, kwargs):
        nonlocal narration_attempts
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            # Reproduce the actual local model's classification of a pure recall
            # as an investigation. The server must retain a read-only boundary.
            result["parsed_intent"].update(type="investigate", target_id=by_title["Notice"]["id"])
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            narration_attempts += 1
            statement = by_title["Notice"]["public_summary"]
            result.update(
                public_narration=statement,
                grounded_claims=[
                    {
                        "claim_id": "old_as_new",
                        "category": "module_fact",
                        "statement": statement,
                        "entity_ids": [by_title["Notice"]["id"]],
                    }
                ],
            )
            if narration_attempts > 1:
                # Reproduce the later real repair: valid history plus an unrelated
                # current scene arrival description must still fail the recall scope.
                scene = json.loads(messages[-1]["content"])["module"]["scene"]
                result["grounded_claims"][0]["statement"] = scoped_statement(
                    by_title["Notice"], statement
                )
                result["grounded_claims"].append(
                    {
                        "claim_id": "unrelated_scene",
                        "category": "module_fact",
                        "statement": scene["public_description"],
                        "entity_ids": [scene["id"]],
                    }
                )
                result["public_narration"] = "\n".join(
                    c["statement"] for c in result["grounded_claims"]
                )
        return result

    svc.model.adapter = FakeModelAdapter(responder=unqualified_history)
    recalled = act(client, d, "我回顾先前 Notice")
    assert recalled["status"] == "completed"
    recall_events = [
        e for e in public_events(client, prefix) if e["payload"].get("cycle_id") == recalled["id"]
    ]
    narration = next(e["payload"] for e in recall_events if e["type"] == "keeper.narration")
    assert narration["safe_fallback"] and "先前获知（当前位置未确认）" in narration["text"]
    assert narration["text"] == scoped_statement(
        by_title["Notice"], by_title["Notice"]["public_summary"]
    )
    assert not any(
        e["type"] in {"check.requested", "entity.revealed", "scene.updated", "npc.spoke"}
        for e in recall_events
    )
    ok(client.post(prefix + "/pause"))
    ok(client.post(prefix + f"/snapshots/{saved['id']}/load", json={}))
    restored = ok(client.get(prefix))["game"]["public_entities"]
    assert [e for e in restored if e["id"] in {b["id"] for b in before}] == before
    assert next(e for e in restored if e["title"] == "Future")["fact_scope"] == "historical"
    # Selection follows the restored branch and preserves real event sequence numbers.
    selected, audit = story_events(public_events(client, prefix))
    assert all(e["seq"] <= saved["event_seq"] for e in selected)
    assert audit["excluded_by_type"]["abandoned_after_snapshot"] > 0
    assert svc is not None


def test_summary_mixed_events_contiguous_coverage_and_oversized_recovery(client, game):  # noqa: F811
    svc, d = client.app.state.agent_service, game
    ok(ask(client, d, "未知规则"))
    cycle = wait_cycle(client, d)
    eligible = [e["seq"] for e in story_events(public_events(client, d["prefix"]))[0]]

    async def seed():
        async def operation(session, room):
            for i in range(55):
                svc.rooms.append(
                    session,
                    room,
                    "character.published",
                    room.host_member_id,
                    {"name": "Unassigned", "text": "ignored" * 100},
                )
                e = svc.rooms.append(
                    session,
                    room,
                    "chat.message",
                    room.host_member_id,
                    {"text": f"玩家观察现场，记录线索 {i}。"},
                )
                eligible.append(e.seq)

        await svc.mutate(d["room"]["id"], operation)

    client.portal.call(seed)
    calls = []

    def response(messages, kwargs):
        c = json.loads(messages[-1]["content"])
        calls.append(c)
        assert all(e["type"] == "chat.message" for e in c["events"])
        assert "Unassigned" not in json.dumps(c["current_participants"])
        return {"content": "玩家查看了现场，线索仍需核实。"}

    svc.model.adapter = FakeModelAdapter(responder=response)
    client.portal.call(svc.summary_recovery.update, d["room"]["id"], cycle["id"])
    assert len(calls) == 1
    seqs = [e["seq"] for e in calls[0]["events"]]
    assert seqs == eligible[: len(seqs)]  # no holes despite administrative events
    assert max(seqs) < eligible[-1]  # recent window remains unsummarized
    memories = ok(client.get(d["prefix"] + "/memories"))
    summary = next(m for m in memories if m["kind"] == "summary")
    assert summary["coverage_end"] == max(seqs)
    summary_profile = summary["profile_id"]

    # Force an oversized first event on the keeper's remaining prefix. It must
    # neither be skipped nor cause a model request or an untracked retry loop.
    async def oversized():
        async def operation(session, room):
            from app.persistence.room_models import RoomEvent

            event = await session.get(RoomEvent, (room.id, eligible[len(seqs)]))
            event.payload = {"text": "很长的真实剧情" * 2000}
            bindings = await svc.bindings(session, room.id)
            for b in bindings:
                if b.profile_id != summary_profile:
                    b.enabled = False

        await svc.mutate(d["room"]["id"], operation)
        await svc.summary_recovery.update(d["room"]["id"], cycle["id"], manual=True)
        async with svc.rooms.database.sessions() as session:
            row = await session.get(SummaryRecoveryRecord, (d["room"]["id"], summary_profile))
            return row.document

    recovery = client.portal.call(oversized)
    assert len(calls) == 1
    assert recovery["stale"] and recovery["failure_count"] == 1
    assert recovery["pending_start_seq"] == eligible[len(seqs)]
    assert recovery["last_successful_summary_seq"] == max(seqs)


def test_story_selection_initialization_management_and_real_seq():
    types = [
        "room.created",
        "scene.updated",
        "entity.revealed",
        "character.published",
        "character.assigned",
        "game.started",
        "action.submitted",
        "knowledge.bound",
        "rules.question",
        "rules.answered",
        "check.resolved",
        "scene.updated",
        "entity.corrected",
        "npc.spoke",
        "agent.spoke",
        "chat.message",
    ]
    events = [
        {"seq": i * 2, "type": t, "payload": {"text": "test"}} for i, t in enumerate(types, 1)
    ]
    chosen, audit = story_events(events)
    assert [e["seq"] for e in chosen] == [14, 22, 24, 26, 28, 30, 32]
    assert audit["candidate_count"] == 7 and audit["excluded_count"] == 9


def test_management_growth_alone_never_summarizes(client, game):  # noqa: F811
    svc = client.app.state.agent_service
    ok(ask(client, game, "未收录的问题"))
    cycle = wait_cycle(client, game)

    async def management():
        async def seed(session, room):
            for i in range(100):
                svc.rooms.append(
                    session,
                    room,
                    "character.published",
                    room.host_member_id,
                    {"name": "Unassigned", "text": str(i) * 300},
                )
            row = await session.get(AgentCycle, cycle["id"])
            row.state = {**row.state, "request_category": "investigation"}

        await svc.mutate(game["room"]["id"], seed)
        await svc.summary_recovery.update(game["room"]["id"], cycle["id"])

    client.portal.call(management)
    assert ok(client.get(game["prefix"] + "/memories")) == []
    assert len(ok(client.get(game["prefix"] + "/agent-runs"))) == 1
