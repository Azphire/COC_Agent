import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_action_adjudication import facts_for, modern_response, plan_for
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_module_navigation_runtime import act, running_navigation  # noqa: F401
from test_rooms import headers, lobby, ok  # noqa: F401

from app.agents.action_policy import ActionPolicyValidator, error_category
from app.agents.adjudication_schemas import (
    BehaviorState,
    ContextGap,
    SummaryRecoveryState,
    TeammateDecision,
)
from app.agents.behavior import TeammateBehaviorPolicy
from app.agents.model import FakeModelAdapter
from app.agents.schemas import PlannedTool
from app.memory.service import build_context
from app.models.base import ModelError
from app.models.ollama import ModelFormatError
from app.persistence.adjudication_models import ActionPlanRecord, SummaryRecoveryRecord
from app.persistence.agent_models import AgentCycle, AgentMemory, AgentRun, ProfileRecord
from app.rooms.service import RoomError


@pytest.mark.parametrize(
    "category", ["context_missing", "permission_denied", "precondition_failed", "internal_error"]
)
def test_tool_recovery_is_bounded_and_receipt_preserves_effect(
    client,
    running_navigation,  # noqa: F811
    monkeypatch,
    category,
):  # noqa: F811
    d, svc = running_navigation, client.app.state.agent_service
    original = svc.runtime.tools.dispatch
    calls = []

    async def injected(session, room, run, binding, profile, name, args):
        if name == "open_module_node":
            calls.append(name)
            if len(calls) == 1:
                if category == "internal_error":
                    raise RuntimeError("C:/private/secret.db password=hidden")
                raise RoomError(category, 403 if category == "permission_denied" else 409)
        return await original(session, room, run, binding, profile, name, args)

    monkeypatch.setattr(svc.runtime.tools, "dispatch", injected)

    def response(messages, kwargs):
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            result["proposed_tool_calls"] = [
                {"name": "open_module_node", "arguments": {"node_id": d["nodes"]["Opening"]}}
            ]
        return result

    svc.model.adapter = FakeModelAdapter(responder=response)
    cycle = act(client, d, "我查看当前房间")
    assert cycle["status"] == "completed", cycle
    assert len(calls) == (2 if category == "context_missing" else 1)
    doc = ok(client.get(d["room_prefix"] + f"/cycles/{cycle['id']}/validation"))
    assert doc["supplement_attempted"] == (category == "context_missing")
    assert "secret.db" not in client.get(d["room_prefix"] + "/agent-runs").text
    if category == "context_missing":
        run = next(
            r
            for r in ok(client.get(d["room_prefix"] + "/agent-runs"))
            if r["graph_node"] == "plan_keeper_action"
        )
        assert len(run["tool_results"]) == 2 and run["tool_results"][-1]["ok"]

        async def receipt():
            async def active(session, room):
                record = await session.get(AgentCycle, cycle["id"])
                record.status = "running"

            await svc.mutate(d["room"]["id"], active)
            for _ in range(2):
                result = await svc.runtime.tools.execute(
                    d["room"]["id"],
                    run["id"],
                    0,
                    "open_module_node",
                    {"node_id": d["nodes"]["Opening"]},
                    recovery=True,
                )
                assert result["ok"]

        client.portal.call(receipt)
        assert len(calls) == 2


def test_scene_bound_test_npc_converse_and_private_summary_never_public(client, running_navigation):  # noqa: F811
    from app.module_ir.schemas import EntityNodeBinding
    from app.persistence.module_ir_models import ApprovedStructure
    from app.persistence.preparation_models import RoomEntityState
    from app.preparation.schemas import EntityFields

    d, svc = running_navigation, client.app.state.agent_service
    npc_id = str(uuid4())
    public_text = "我只知道这里是候车厅。"
    private_text = "UNREVEALED_NPC_PRIVATE_SENTINEL"

    async def seed_npc():
        async with svc.rooms.transaction() as session:
            nav = await svc.navigation.state(session, d["room"]["id"])
            snapshot, _ = await svc.navigation.snapshot(session, nav)
            snapshot.entity_bindings.append(
                EntityNodeBinding(
                    binding_id=str(uuid4()),
                    entity_id=npc_id,
                    node_id=nav.current_scene_node_id,
                    source_hash=snapshot.source_hash,
                )
            )
            record = await session.get(ApprovedStructure, snapshot.snapshot_id)
            record.document = snapshot.model_dump(mode="json")
            fields = EntityFields(
                type="npc",
                title="测试乘客",
                public_summary=public_text,
                keeper_summary=private_text,
                tags=["host_authored_test"],
            )
            session.add(
                RoomEntityState(
                    room_id=d["room"]["id"],
                    source_entity_id=npc_id,
                    entity_type="npc",
                    snapshot={
                        **fields.model_dump(mode="json"),
                        "id": npc_id,
                        "status": "approved",
                        "generated_by": "host",
                        "version": 1,
                        "source_references": [],
                    },
                    state="revealed",
                    frozen_public_summary=public_text,
                    frozen_source_references=[],
                )
            )
            await session.flush()
            room = await svc.rooms.room(session, d["room"]["id"])
            await svc.navigation.refresh(session, room, nav, snapshot)
            await svc.navigation.persist(session, nav)

    client.portal.call(seed_npc)
    leak = False

    def response(messages, kwargs):
        result = modern_response(messages, kwargs)
        schema = kwargs["response_schema"].__name__
        if schema == "KeeperPlan":
            result["parsed_intent"].update(type="converse", target_kind="npc", target_id=npc_id)
        elif schema == "KeeperNarration":
            assert private_text not in json.dumps(messages)
            claim = {
                "claim_id": "npc_public",
                "category": "module_fact",
                "statement": public_text,
                "entity_ids": [npc_id],
            }
            result.update(
                public_narration=public_text,
                grounded_claims=[claim],
                npc_speech={"entity_id": npc_id, "text": private_text} if leak else None,
            )
        elif schema == "TeammateDecision":
            assert private_text not in json.dumps(messages)
        return result

    svc.model.adapter = FakeModelAdapter(responder=response)
    view = ok(client.get(d["room_prefix"]))["game"]
    target = next(t for t in view["conversation_targets"] if t["id"] == npc_id)
    assert target["origin"] == "host_authored_test"
    assert act(client, d, "我与测试乘客交谈")["status"] == "completed"
    leak = True
    assert act(client, d, "我询问测试乘客其他情况")["status"] == "completed"
    events = ok(
        client.get(d["room_prefix"] + "/events", headers=headers(d["remote"]["member_token"]))
    )["events"]
    assert private_text not in json.dumps(events)
    speech = [e for e in events if e["type"] == "npc.spoke"]
    assert len(speech) == 1 and speech[0]["payload"]["text"] == public_text


@pytest.mark.parametrize(
    "problem",
    ["invisible", "duplicate", "out_of_character", "bad_skill", "permission", "future_node"],
)
def test_policy_boundaries(problem):
    plan, facts = plan_for("查看门"), facts_for("查看门")
    facts.approved_entities = {i: {"id": i, "title": "门", "type": "item"} for i in ["a", "b"]}
    facts.local_entity_ids = {"a", "b"}
    facts.visible_entity_ids = {"a"}
    actions = [PlannedTool(name="reveal_entity", arguments={"entity_id": "a"})]
    if problem == "invisible":
        plan.parsed_intent.target_id = "unseen"
    elif problem == "duplicate":
        plan.parsed_intent.target_text = "门"
    else:
        facts.approved_entities.pop("b")
        if problem == "out_of_character":
            plan.parsed_intent.type = "out_of_character"
        elif problem == "bad_skill":
            actions = [
                PlannedTool(
                    name="request_skill_check",
                    arguments={
                        "target_member_id": str(uuid4()),
                        "name": "imaginary",
                        "reason": "测试",
                    },
                )
            ]
        elif problem == "permission":
            actions = [PlannedTool(name="write_private_memory", arguments={"content": "secret"})]
        elif problem == "future_node":
            plan.source_node_ids = ["future"]
    result = ActionPolicyValidator().validate(plan.parsed_intent, plan, facts, actions)
    assert not result.approved_actions


@pytest.mark.parametrize(
    "error,expected",
    [
        (RoomError("denied", 403), "permission_denied"),
        (RoomError("missing", 404), "entity_not_found"),
        (RoomError("context_missing", 409), "context_missing"),
        (RoomError("revision", 409), "revision_conflict"),
        (RoomError("condition", 409), "precondition_failed"),
        (ModelError("模型请求超时"), "model_timeout"),
        (ModelFormatError("format"), "model_schema_error"),
        (RuntimeError("C:/private/path"), "internal_error"),
    ],
)
def test_error_taxonomy(error, expected):
    assert error_category(error) == expected


@pytest.mark.parametrize(
    "comparison", ["exact", "punctuation", "bigram", "other", "player", "move", "hidden"]
)
def test_behavior_rejects_unsafe_or_repeated_candidates(comparison):
    candidate = TeammateDecision(
        mode="act",
        action_type="investigate",
        target_id="door",
        action_text="我仔细查看门框上的痕迹",
        related_player_action_seq=10,
        confidence=1,
    )
    recent, others, player = [], [], "我查看地上的脚印"
    if comparison == "exact":
        recent = [candidate.action_text]
    elif comparison == "punctuation":
        recent = ["我仔细查看，门框上的痕迹！"]
    elif comparison == "bigram":
        recent = ["我仔细查看门框上的旧痕迹"]
    elif comparison == "other":
        others = [candidate.action_text]
    elif comparison == "player":
        player = candidate.action_text
    elif comparison == "move":
        candidate.action_type = "move"
    else:
        candidate.target_id = "hidden"
    checked = TeammateBehaviorPolicy().validate(
        candidate,
        state=BehaviorState(),
        recent_outputs=recent,
        other_outputs=others,
        player_text=player,
        player_intent=None,
        public_ids={"door"},
        action_seq=10,
        fingerprint="same",
    )
    assert not checked.accepted


@pytest.mark.parametrize("repair_success", [True, False])
def test_teammate_one_repair_then_adopt_or_pass(client, game, repair_success):  # noqa: F811
    def response(messages, kwargs):
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "TeammateDecision":
            c = json.loads(messages[-1]["content"])
            result.update(mode="speak", speech_text="四周很安静")
            if c.get("behavior_rejection") and repair_success:
                result["speech_text"] = "我留在原地留意脚步声，等你检查完再交换发现。"
        return result

    adapter = FakeModelAdapter(responder=response)
    client.app.state.agent_service.model.adapter = adapter
    ok(submit(client, game, "我查看当前环境"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    adopted = [e for e in events if e["type"] == "agent.spoke"]
    assert len(adopted) == int(repair_success)
    assert not any(
        e["payload"].get("text") == "四周很安静" for e in events if e["visibility"] == "public"
    )
    audit = next(e for e in events if e["type"] == "agent.teammate_decision")["payload"]
    assert audit["repair_count"] == 1 and audit["mode"] == ("speak" if repair_success else "pass")
    assert len(adapter.prompts) == 4
    saved_behavior = ok(client.get(game["prefix"] + "/teammate-behavior"))
    snapshot = ok(client.post(game["prefix"] + "/snapshots", json={"name": "behavior"}))["snapshot"]
    assert (
        client.get(
            game["prefix"] + "/teammate-behavior", headers=headers(game["remote"]["member_token"])
        ).status_code
        == 403
    )
    ok(client.post(game["prefix"] + f"/teammate-behavior/{game['agent']}/reset", json={}))
    ok(client.post(game["prefix"] + "/pause"))
    ok(client.post(game["prefix"] + f"/snapshots/{snapshot['id']}/load", json={}))
    assert ok(client.get(game["prefix"] + "/teammate-behavior")) == saved_behavior


def test_narration_failure_retry_does_not_repeat_reveal(client, game):  # noqa: F811
    failed = True

    def response(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperNarration" and failed:
            raise ModelError("模型请求超时")
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            result["proposed_reveal_entity_ids"] = ["notice"]
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "我查看公告"))
    first = wait_cycle(client, game)
    assert first["status"] == "failed"
    failed = False
    ok(client.post(game["prefix"] + "/agent-cycle/retry", json={}))
    assert wait_cycle(client, game)["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "clue.revealed" for e in events) == 1
    assert sum(e["type"] == "keeper.narration" for e in events) == 1
    assert len([e for e in events if e["type"] == "agent.action_validated"]) >= 2


def test_supplement_once_current_scope_and_revision_once(client, running_navigation):  # noqa: F811
    d, svc = running_navigation, client.app.state.agent_service
    svc.model.adapter = FakeModelAdapter(responder=modern_response)
    cycle_view = act(client, d, "我查看现场")
    assert cycle_view["status"] == "completed"

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            cycle = await session.get(AgentCycle, cycle_view["id"])
            record = await session.get(ActionPlanRecord, cycle.id)
            run = await session.get(AgentRun, record.run_id)
            gaps = [
                ContextGap(
                    missing_kind="node",
                    requested_target=d["nodes"][title],
                    current_scene=d["nodes"]["Opening"],
                    attempted_tool="open_module_node",
                    reason="test",
                )
                for title in ["Opening", "Future"]
            ]
            result = await svc.adjudication.supplements.supplement(session, room, run, record, gaps)
            assert [n["node_id"] for n in result["nodes"]] == [d["nodes"]["Opening"]]
            assert "FUTURE_KEEPER" not in json.dumps(result)
            with pytest.raises(RoomError):
                await svc.adjudication.supplements.supplement(session, room, run, record, gaps)
            nav = await svc.navigation.state(session, room.id)
            nav.navigation_revision += 1
            await svc.navigation.persist(session, nav)
            assert await svc.adjudication.recover_revision(session, room, cycle)
            assert cycle.state["navigation_revision"] == nav.navigation_revision
            assert not await svc.adjudication.recover_revision(session, room, cycle)

    client.portal.call(verify)


def test_summary_stale_pending_retry_stop_and_manual_atomic_rebuild(client, game):  # noqa: F811
    svc = client.app.state.agent_service
    svc.settings.summary_max_failures = 2
    broken = False

    def response(messages, kwargs):
        if kwargs["response_schema"].__name__ == "SummaryOutput" and broken:
            return {"content": "不存在的事件 seq:999999 以及 entity_forged"}
        return modern_response(messages, kwargs)

    svc.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "我查看环境"))
    first = wait_cycle(client, game)
    assert first["status"] == "completed"
    keeper_profile = game["profiles"][0]["id"]
    old_id = str(uuid4())

    async def seed():
        async def operation(session, room):
            for i in range(35):
                svc.rooms.append(
                    session, room, "chat.message", room.host_member_id, {"text": f"现场观察记录{i}"}
                )
            session.add(
                AgentMemory(
                    id=old_id,
                    room_id=room.id,
                    profile_id=keeper_profile,
                    kind="summary",
                    scope="keeper_only",
                    content="旧的可信摘要",
                    source_event_ids=[],
                    salience=9,
                    coverage_start=0,
                    coverage_end=0,
                    active=True,
                )
            )
            session.add(
                SummaryRecoveryRecord(
                    room_id=room.id,
                    profile_id=keeper_profile,
                    document=SummaryRecoveryState(stale=True).model_dump(mode="json"),
                )
            )

        await svc.mutate(game["room"]["id"], operation)

    client.portal.call(seed)
    broken = True
    ok(client.post(game["prefix"] + "/summary-rebuild", json={}))
    status = next(
        s
        for s in ok(client.get(game["prefix"] + "/summary-status"))
        if s["profile_id"] == keeper_profile
    )
    assert (
        status["stale"]
        and status["failure_count"] == 1
        and status["pending_end_seq"] >= status["pending_start_seq"]
    )

    async def pending_context():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, game["room"]["id"])
            binding = next(
                b for b in await svc.bindings(session, room.id) if b.profile_id == keeper_profile
            )
            profile = await session.get(ProfileRecord, keeper_profile)
            cycle = await session.get(AgentCycle, first["id"])
            context, _, _ = await build_context(
                svc, session, room, binding, profile, cycle, phase="plan_keeper_action"
            )
            assert context["summary_status"]["stale"]
            assert any(e["seq"] == status["pending_start_seq"] for e in context["events"])
            assert (await session.get(AgentMemory, old_id)).active

    client.portal.call(pending_context)
    ok(submit(client, game, "我继续查看现场的地面"))
    second = wait_cycle(client, game)
    assert second["status"] == "completed"
    status = next(
        s
        for s in ok(client.get(game["prefix"] + "/summary-status"))
        if s["profile_id"] == keeper_profile
    )
    assert status["automatic_retry_stopped"] and status["failure_count"] == 2
    before = len(svc.model.adapter.prompts)
    client.portal.call(svc.summary_recovery.update, game["room"]["id"], second["id"])
    assert len(svc.model.adapter.prompts) == before
    broken = False
    ok(client.post(game["prefix"] + "/summary-rebuild", json={}))
    status = next(
        s
        for s in ok(client.get(game["prefix"] + "/summary-status"))
        if s["profile_id"] == keeper_profile
    )
    assert not status["stale"] and status["failure_count"] == 0

    async def atomic():
        async with svc.rooms.database.sessions() as session:
            rows = list(
                await session.scalars(
                    select(AgentMemory).where(
                        AgentMemory.room_id == game["room"]["id"],
                        AgentMemory.profile_id == keeper_profile,
                        AgentMemory.kind == "summary",
                    )
                )
            )
            assert sum(r.active for r in rows) == 1
            assert not (await session.get(AgentMemory, old_id)).active
            state = await session.get(SummaryRecoveryRecord, (game["room"]["id"], keeper_profile))
            assert SummaryRecoveryState.model_validate(state.document).pending_start_seq is None

    client.portal.call(atomic)
