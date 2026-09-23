"""Current request scope and durable operands survive preflight and child failures."""

from copy import deepcopy
from uuid import uuid4

import pytest
from test_agent_runtime import game  # noqa: F401
from test_rooms import character, lobby, ok, prepare  # noqa: F401

from app.agents.adjudication_schemas import BehaviorState, TurnFocus, TurnRequest
from app.agents.conversation import settle_teammate_tasks
from app.agents.task_receipts import bind_task_operands
from app.persistence.adjudication_models import AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle
from app.preparation.action_authority import requested_action_kinds
from app.preparation.turn_focus import bind_requests, reconcile_requests

ADVICE = "艾琳，请根据这些旧证词说明下一步还需要核对什么，不要声称已经查明窃贼。"
TARGETS = [{"id": "window", "title": "窗锁", "aliases": ["窗户"],
            "fact_scope": "current_scene", "source_event_seq": 20}]
# Public identities and approved snapshot aliases frozen from batch-46/real-04.
REAL_TARGETS = [
    {"id": "2f4775f2-0529-4de2-9128-9b780802cc89", "title": "托马斯·金博尔",
     "aliases": ["托马斯", "金博尔先生"], "type": "npc", "revealed_event_seq": 6,
     "fact_scope": "current_scene"},
    {"id": "3160e2af-5168-4ffd-88b2-e6207ab7c374", "title": "松动的窗锁",
     "aliases": ["窗户", "窗锁"], "type": "clue", "revealed_event_seq": 230,
     "fact_scope": "current_scene"},
]
REAL_TASK = "艾琳，请根据托马斯早先的说法查看书房窗户，核对窗锁是否松动。"


def requests(raw, *, model_kind=None):
    focus = TurnFocus(question=raw, addressee_id="peer")
    if model_kind:
        focus.requests = [TurnRequest(kind=model_kind, addressee_id="peer", text=raw,
                                      source_start=0, source_end=len(raw))]
    return bind_requests(focus, raw, {"peer": "艾琳"}, "player")


@pytest.mark.parametrize("raw", [
    ADVICE, "艾琳，请说说下一步应该查看什么。", "艾琳，请解释还需要检查哪些线索。",
    "艾琳，下一步还需核对什么？",
])
@pytest.mark.parametrize("model_kind", [None, "delegate"])
def test_advice_question_does_not_inherit_nested_action_words(raw, model_kind):
    found = requests(raw, model_kind=model_kind)
    assert len(found) == 1 and found[0].text == raw
    assert found[0].kind == "question" and not found[0].operations
    assert not requested_action_kinds(raw)


@pytest.mark.parametrize("raw", ["艾琳，现在去核对窗锁是否松动。", "艾琳，请查看窗锁。"])
def test_present_delegation_keeps_actual_operation(raw):
    found = requests(raw, model_kind="question")
    assert len(found) == 1 and found[0].kind == "delegate"
    assert found[0].operations == ["observe"]


@pytest.mark.parametrize("raw,kinds", [
    ("艾琳，请说明下一步还需查看什么，然后请现在查看窗锁。", ["question", "delegate"]),
    ("艾琳，请查看窗锁，然后告诉我下一步还需检查什么。", ["delegate", "question"]),
    ("艾琳，你觉得下一步该检查什么？现在请查看窗锁。", ["question", "delegate"]),
    ("艾琳，请说明下一步还需检查什么并现在去核对窗锁是否松动。", ["question", "delegate"]),
    ("艾琳，请查看窗锁并告诉我下一步还需核对什么。", ["delegate", "question"]),
])
def test_mixed_request_preserves_each_original_scope(raw, kinds):
    found = requests(raw, model_kind="delegate")
    assert [r.kind for r in found] == kinds
    assert "".join(r.text for r in found) == raw
    assert all(raw[r.source_start:r.source_end] == r.text for r in found)
    assert [r.operations for r in found] == [
        ["observe"] if kind == "delegate" else [] for kind in kinds
    ]


def test_legacy_advice_task_is_corrected_by_original_scope_with_audit():
    old = {"key": "12:0", "kind": "delegate", "operations": ["observe"],
           "text": ADVICE, "source_event_seq": 12, "target_id": None}
    real = {"key": "13:0", "kind": "delegate", "operations": ["observe"],
            "text": "请查看窗锁。", "target_id": "window"}
    behavior = BehaviorState(pending_requests=[old, real])
    reconcile_requests(behavior, [], seq=22, scene_id="study", reachable_ids={"window"})
    assert behavior.pending_requests[0]["key"] == old["key"]
    assert behavior.pending_requests[0]["kind"] == "question"
    assert behavior.pending_requests[0]["operations"] == []
    assert behavior.pending_requests[1] == real
    assert behavior.request_history[0]["kind"] == "delegate"
    assert behavior.request_history[0]["status"] == "reclassified"
    assert behavior.request_history[0]["reason"] == "original_request_is_information_question"
    before = deepcopy(behavior.model_dump())
    reconcile_requests(behavior, [], seq=22, scene_id="study", reachable_ids={"window"})
    assert behavior.model_dump() == before


def test_unrelated_pending_ledger_is_not_truncated_by_new_request():
    old = [{"key": f"{i}:0", "kind": "delegate", "operations": ["observe"],
            "text": f"请查看第{i}扇窗锁。", "target_id": f"window-{i}"} for i in range(20)]
    behavior = BehaviorState(pending_requests=old)
    reconcile_requests(behavior, requests("艾琳，请查看窗锁。"), seq=30, scene_id="study",
                       reachable_ids={r["target_id"] for r in old})
    assert behavior.pending_requests[:20] == old
    assert len(behavior.pending_requests) == 21
    assert BehaviorState.model_validate(behavior.model_dump()).pending_requests == (
        behavior.pending_requests
    )


def test_binding_rejects_invisible_or_ambiguous_model_target_and_records_source():
    request = {"key": "25:0", "text": "请查看窗锁。", "kind": "delegate",
               "operations": ["observe"], "target_id": "hidden", "source_event_seq": 25}
    bound = bind_task_operands(request, {}, "peer", targets=TARGETS)
    assert bound["target_id"] == "window"
    assert bound["target_source"]["source_event_seq"] == 20
    assert bound["target_source"]["request_key"] == "25:0"
    ambiguous = bind_task_operands({**request, "target_id": "window"}, {}, "peer",
                                   targets=[*TARGETS, {**TARGETS[0], "id": "other"}])
    assert ambiguous["target_id"] is None
    assert ambiguous["target_candidates"] == ["other", "window"]
    historical = bind_task_operands(request, {}, "peer",
                                    targets=[{**TARGETS[0], "fact_scope": "historical"}])
    assert historical["target_id"] is None


def test_binding_without_visibility_input_does_not_erase_existing_operand():
    request = {"text": "请查看窗锁。", "operations": ["observe"], "target_id": "window"}
    assert bind_task_operands(request, {}, "peer")["target_id"] == "window"
    assert bind_task_operands(request, {}, "peer", targets=[])["target_id"] is None


def test_visible_model_id_alone_is_not_operand_evidence_but_frozen_source_is():
    request = {"key": "25:0", "text": "请继续观察那里。", "operations": ["observe"],
               "target_id": "window", "source_event_seq": 25}
    rejected = bind_task_operands(request, {}, "peer", targets=TARGETS)
    assert rejected["target_id"] is None
    assert rejected["target_binding_rejection"]["proposed_target_id"] == "window"
    frozen = bind_task_operands({**request, "text": "请查看窗锁。"}, {}, "peer", targets=TARGETS)
    continued = bind_task_operands({**request, "target_source": frozen["target_source"]}, {},
                                   "peer", targets=TARGETS)
    assert continued["target_id"] == "window"
    mismatched = bind_task_operands({**continued, "key": "26:0"}, {}, "peer", targets=TARGETS)
    assert mismatched["target_id"] is None


def test_inventory_visible_actor_can_bind_without_a_public_entity_projection():
    request = {"text": "请给乘务员包扎。", "operations": ["first_aid"], "target_id": "npc"}
    inventory = {"other_actors": [{"id": "npc", "names": ["乘务员"],
                                    "fact_scope": "current_scene"}]}
    bound = bind_task_operands(request, inventory, "peer", targets=[])
    assert bound["target_id"] == "npc" and bound["target_source"]["target_id"] == "npc"


@pytest.mark.parametrize("text,target", [
    (REAL_TASK, 1),
    ("据托马斯的证词，请核对窗锁。", 1),
    ("请根据托马斯的证词‘查看托马斯’，现在核对窗锁。", 1),
    ("托马斯说：‘窗锁没有问题。’，请检查托马斯。", 0),
    ("请检查托马斯和窗锁。", None),
    ("请根据托马斯的证词检查托马斯和松动的窗锁。", None),
])
def test_reference_speaker_is_not_the_operation_target_but_two_objects_stay_ambiguous(text, target):
    request = {"key": "284:3", "source_event_seq": 284, "text": text,
               "operations": ["observe"], "target_id": None}
    bound = bind_task_operands(request, {}, "peer", targets=REAL_TARGETS)
    assert bound["text"] == text
    if target is None:
        assert bound["target_id"] is None
        assert bound["target_candidates"] == sorted(t["id"] for t in REAL_TARGETS)
    else:
        assert bound["target_id"] == REAL_TARGETS[target]["id"]
        assert bound["target_source"]["request_key"] == "284:3"
        assert bound["target_source"]["revealed_event_seq"] == REAL_TARGETS[target][
            "revealed_event_seq"
        ]


def test_reference_source_does_not_remove_conditional_execution_guard():
    text = "艾琳，根据托马斯的证词，如果窗锁松动，再检查托马斯。"
    assert not requested_action_kinds(text)
    assert all(not r.operations for r in requests(text, model_kind="delegate"))


@pytest.mark.parametrize("case_name,expected", [
    ("revealed", "window"), ("corrected", "window"), ("hidden", None),
    ("historical", None), ("ambiguous", None),
])
def test_approved_aliases_require_current_public_identity_and_unique_target(
    client, game, case_name, expected,  # noqa: F811
):
    from app.agents.task_receipts import current_task_targets
    from app.persistence.preparation_models import RoomEntityState

    svc, rid = client.app.state.agent_service, game["room"]["id"]

    async def verify():
        async def operation(session, room):
            public = [{"id": "window", "title": "松动的窗锁", "aliases": None,
                       "fact_scope": "historical" if case_name == "historical"
                       else "current_scene"}]
            ids = ["window", "unexposed"]
            if case_name == "ambiguous":
                ids.append("other")
                public.append({**public[0], "id": "other"})
            for eid in ids:
                session.add(RoomEntityState(
                    room_id=rid, source_entity_id=eid, entity_type="clue",
                    state=case_name if case_name in {"hidden", "corrected"} else "revealed",
                    snapshot={"id": eid, "title": "松动的窗锁", "aliases": ["窗户", "窗锁"],
                              "keeper_summary": "不可投影的隐藏说明"},
                    frozen_public_summary="窗锁松动。", frozen_source_references=[],
                ))
            await session.flush()
            targets = await current_task_targets(session, rid, public)
            assert all("keeper_summary" not in t and t["id"] != "unexposed" for t in targets)
            bound = bind_task_operands({"key": "5:0", "source_event_seq": 5,
                                       "text": "请核对窗锁是否松动。", "target_id": "window",
                                       "operations": ["observe"]}, {}, "peer", targets=targets)
            assert bound["target_id"] == expected
            if case_name == "ambiguous":
                assert bound["target_candidates"] == ["other", "window"]
            if expected:
                assert bound["target_source"]["request_key"] == "5:0"
            assert public[0]["aliases"] is None  # No shared public projection is mutated.

        await svc.mutate(rid, operation)

    client.portal.call(verify)


@pytest.mark.parametrize("raw", ["如果门打开，就拿走钥匙。", "假如发现伤口，再包扎。"])
def test_conditional_continuation_never_acquires_execution_authority(raw):
    assert not requested_action_kinds(raw)


@pytest.mark.parametrize("target", [None, "chosen-target"])
def test_child_failure_fills_only_missing_or_none_frozen_values(client, game, target):  # noqa: F811
    svc, rid, cid = client.app.state.agent_service, game["room"]["id"], str(uuid4())

    async def verify():
        async def seed(session, room):
            actor = game["agent"]
            trigger = svc.rooms.append(session, room, "action.submitted", actor,
                                       {"text": "请查看窗锁。", "cycle_id": cid})
            request = {"key": "window-task", "kind": "delegate", "text": "请查看窗锁。",
                       "operations": ["observe"], "source_event_seq": trigger.seq,
                       "target_id": target, "executor_member_id": None,
                       "remaining_quantity": 0, "item_instance_ids": []}
            frozen = {**request, "target_id": "window", "executor_member_id": actor,
                      "remaining_quantity": 2, "item_instance_ids": ["old-instance"]}
            session.add(AgentCycle(id=cid, room_id=rid, status="failed", state={
                "request_keys": [request["key"]], "related_player_cycle_id": cid,
                "triggering_event_seq": trigger.seq,
                "request_operands": {request["key"]: frozen},
                "teammate_attempt": {"target_id": "window", "operations": ["observe"]},
            }))
            session.add(AgentBehaviorRecord(room_id=rid, member_id=actor, document=BehaviorState(
                pending_requests=[request], task_status="proposed", task_cycle_id=cid,
            ).model_dump(mode="json")))
            await session.flush()
            await settle_teammate_tasks(svc, session, room)
            row = await session.get(AgentBehaviorRecord, (rid, actor))
            pending = row.document["pending_requests"][0]
            assert pending["key"] == "window-task"
            assert pending["target_id"] == (target or "window")
            assert pending["executor_member_id"] == actor
            assert pending["remaining_quantity"] == 0 and pending["item_instance_ids"] == []
            assert row.document["task_status"] == "generation_failed"
            before = deepcopy(row.document)
            await settle_teammate_tasks(svc, session, room)
            assert row.document == before

        await svc.mutate(rid, seed)

    client.portal.call(verify)


@pytest.mark.parametrize("alias_origin", ["public", "approved_snapshot", "reference_source"])
def test_registered_operand_survives_budget_refusal_and_snapshot_load(
    client, game, monkeypatch, alias_origin,  # noqa: F811
):
    from app.agents.adjudication_schemas import AdjudicationRecord, KeeperPlan
    from app.agents.conversation import initial_state
    from app.persistence.adjudication_models import ActionPlanRecord
    from app.persistence.agent_models import AgentRun
    from app.rooms.service import RoomError

    svc, rid, cid = client.app.state.agent_service, game["room"]["id"], str(uuid4())
    attempts = []
    target_id = REAL_TARGETS[1]["id"] if alias_origin == "reference_source" else "window"

    async def public(*args, **kwargs):
        if alias_origin == "reference_source":
            return [{**{k: v for k, v in t.items() if k != "aliases"},
                     "public_summary": t["title"]} for t in REAL_TARGETS]
        target = {**TARGETS[0], "type": "clue", "public_summary": "窗锁松动。"}
        if alias_origin == "approved_snapshot":
            target.update(title="松动的窗锁", aliases=[])
        return [target]

    async def budget_refusal(state, binding_id, node, schema, instruction, additions):
        async with svc.rooms.database.sessions() as session:
            saved = (await session.get(AgentBehaviorRecord, (rid, game["agent"]))).document
        # This check runs before generation, not merely after failure handling.
        request = saved["pending_requests"][0]
        assert request["target_id"] == target_id
        assert request["executor_member_id"] == game["agent"]
        assert request["target_source"]["request_key"] == request["key"]
        attempts.append(deepcopy(request))
        raise RoomError("当前行动必需历史证据超过记忆选取预算")

    monkeypatch.setattr(svc.entities, "public", public)
    monkeypatch.setattr(svc.runtime, "generate_action_run", budget_refusal)

    async def run():
        async def seed(session, room):
            if alias_origin in {"approved_snapshot", "reference_source"}:
                from app.persistence.preparation_models import RoomEntityState

                snapshots = REAL_TARGETS if alias_origin == "reference_source" else [{
                    "id": "window", "type": "clue", "title": "松动的窗锁",
                    "aliases": ["窗户", "窗锁"], "revealed_event_seq": 20,
                }]
                for target in snapshots:
                    session.add(RoomEntityState(
                        room_id=rid, source_entity_id=target["id"], entity_type=target["type"],
                        state="revealed", snapshot=target,
                        frozen_public_summary=target["title"], frozen_source_references=[],
                        revealed_event_seq=target["revealed_event_seq"],
                    ))
            actor = room.host_member_id
            text = REAL_TASK if alias_origin == "reference_source" else "艾琳，请查看窗锁。"
            trigger = svc.rooms.append(session, room, "action.submitted", actor,
                                       {"text": text, "cycle_id": cid})
            bindings = [b.id for b in await svc.bindings(session, rid)
                        if b.member_id == game["agent"]]
            state = initial_state(rid, cid, actor, trigger.seq, bindings)
            session.add(AgentCycle(id=cid, room_id=rid, status="running", state=state))
            module = await svc.module(session, rid)
            plan = KeeperPlan(
                plan_id=cid, cycle_id=cid, current_scene_id=module.state["scene_id"],
                parsed_intent={"type": "converse", "actor_member_id": actor,
                               "actor_character_slot_id": "slot", "evidence_quote": text,
                               "confidence": 1},
                focus={"question": text, "addressee_id": game["agent"], "requests": [{
                    "kind": "delegate", "addressee_id": game["agent"], "text": text,
                    "source_start": 0, "source_end": len(text), "operations": ["observe"],
                    "target_id": None,
                }]},
            )
            run_id = str(uuid4())
            session.add(AgentRun(id=run_id, room_id=rid, cycle_id=cid,
                                 profile_id=game["profiles"][0]["id"], actor_member_id=actor,
                                 graph_node="plan_keeper_action", status="completed",
                                 input_seq_start=trigger.seq, input_seq_end=trigger.seq,
                                 provider="fake", model="test", context={}))
            session.add(ActionPlanRecord(cycle_id=cid, room_id=rid, run_id=run_id,
                                         document=AdjudicationRecord(plan=plan).model_dump()))
            return state

        state = await svc.mutate(rid, seed)
        await svc.runtime.decide_teammates(state)

        async def finish(session, room):
            cycle = await session.get(AgentCycle, cid)
            cycle.status = "completed"
            cycle.state = {**cycle.state, "status": "completed"}
            return deepcopy((await session.get(
                AgentBehaviorRecord, (rid, game["agent"]),
            )).document)

        return await svc.mutate(rid, finish)

    saved = client.portal.call(run)
    assert len(attempts) == 1
    assert saved["task_status"] == "generation_failed"
    request = saved["pending_requests"][0]
    assert request["key"] == attempts[0]["key"]
    assert request["target_id"] == target_id and request["target_source"] == attempts[0][
        "target_source"
    ]
    snapshot = ok(client.post(game["prefix"] + "/snapshots", json={"name": "budget-failure"}))[
        "snapshot"
    ]
    ok(client.post(game["prefix"] + "/pause"))

    async def change_and_read(replace=False):
        async def operation(session, room):
            row = await session.get(AgentBehaviorRecord, (rid, game["agent"]))
            if replace:
                row.document = BehaviorState().model_dump(mode="json")
            return deepcopy(row.document)
        return await svc.mutate(rid, operation)

    client.portal.call(change_and_read, True)
    ok(client.post(game["prefix"] + f"/snapshots/{snapshot['id']}/load"))
    restored = client.portal.call(change_and_read)
    assert restored == saved
    if alias_origin != "reference_source":
        return

    # Run the actual enqueue/freeze/settlement path after restoration. No model
    # or new reveal is needed: only this child's validated official observation
    # may complete the request, and the testimony speaker cannot replace its target.
    async def execute_observation():
        from app.agents.conversation import enqueue_teammate

        async def operation(session, room):
            parent = await session.get(AgentCycle, cid)
            actor = game["agent"]
            binding = next(b for b in await svc.bindings(session, rid) if b.member_id == actor)
            behavior = await session.get(AgentBehaviorRecord, (rid, actor))
            proposal = svc.rooms.append(session, room, "agent.action_proposed", actor, {
                "cycle_id": cid, "text": "我核对窗锁。", "target_id": target_id,
            })
            child_id = await enqueue_teammate(
                svc, session, room, parent, proposal, binding,
                behavior.document["pending_requests"],
            )
            await session.flush()
            child = await session.get(AgentCycle, child_id)
            frozen = child.state["request_operands"][request["key"]]
            assert frozen["target_id"] == target_id
            assert frozen["target_source"] == request["target_source"]
            assert child.state["request_keys"] == [request["key"]]
            assert child.state["teammate_attempt"]["target_id"] == target_id
            child.status = "completed"
            behavior.document = {**behavior.document, "task_cycle_id": child_id,
                                 "task_status": "proposed"}
            module = await svc.module(session, rid)
            plan = KeeperPlan(
                plan_id=child_id, cycle_id=child_id, current_scene_id=module.state["scene_id"],
                parsed_intent={"type": "observe", "actor_member_id": actor,
                               "actor_character_slot_id": "slot", "target_id": target_id,
                               "evidence_quote": "我核对窗锁。", "confidence": 1},
                focus={"action": "我核对窗锁。", "action_target_id": target_id},
                action_authority={"kinds": ["observe"]},
            )
            run_id = str(uuid4())
            session.add(AgentRun(
                id=run_id, room_id=rid, cycle_id=child_id, profile_id=game["profiles"][0]["id"],
                actor_member_id=actor, graph_node="plan_keeper_action", status="completed",
                input_seq_start=proposal.seq, input_seq_end=proposal.seq,
                provider="fake", model="test", context={},
            ))
            body = "窗锁目前仍然松动。"
            session.add(ActionPlanRecord(
                cycle_id=child_id, room_id=rid, run_id=run_id,
                document=AdjudicationRecord(
                    plan=plan, narration={"public_narration": body},
                    narration_validation={"valid": True, "answer_complete": True},
                ).model_dump(mode="json"),
            ))
            formal = svc.rooms.append(session, room, "keeper.narration", room.host_member_id, {
                "cycle_id": child_id, "text": body, "safe_fallback": False,
            })
            await session.flush()
            await settle_teammate_tasks(svc, session, room)
            assert behavior.document["task_status"] == "completed"
            assert not behavior.document["pending_requests"]
            history = behavior.document["request_history"][-1]
            assert history["key"] == request["key"] and history["target_id"] == target_id
            assert history["result_cycle_id"] == child_id
            assert formal.seq in history["result_event_seqs"]
            facts = behavior.document["last_result"]["result_facts"]
            assert any(f.get("target_id") == target_id and f["source_event_seq"] == formal.seq
                       and f["cycle_id"] == child_id for f in facts)
        await svc.mutate(rid, operation)

    client.portal.call(execute_observation)
