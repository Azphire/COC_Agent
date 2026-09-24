"""Replay the frozen batch-48 ownership, bare-hand and reveal failures offline."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_action_adjudication import plan_for
from test_agent_runtime import game  # noqa: F401
from test_rooms import character, lobby, prepare  # noqa: F401

from app.agents.adjudication_schemas import AdjudicationRecord, BehaviorState, KeeperPlan, TurnFocus
from app.agents.conversation import settle_teammate_tasks
from app.agents.results import result_facts
from app.agents.task_receipts import receipt_progress
from app.agents.teammate_eligibility import TeammateEligibilityPolicy
from app.persistence.adjudication_models import ActionPlanRecord, AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle, AgentRun
from app.preparation.action_authority import (
    addressed_spans,
    requested_action_kinds,
    teammate_request,
)
from app.preparation.inventory import asserted_item_uses, bind_item_prose
from app.preparation.turn_focus import repair_attribution
from app.rooms.service import RoomError

EVIDENCE = Path(__file__).resolve().parents[2] / "data/prepared/batch-48/real-01"


def document(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.fixture(scope="module")
def frozen():
    paths = [EVIDENCE / name for name in (
        "delegation-state-01.json", "final-paused-database.json", "investigation-state-01.json",
    )]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    yield [json.loads(p.read_text(encoding="utf-8-sig")) for p in paths]
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def saved_run(state, seq, node="plan_keeper_action"):
    rid = state["launch_drafts"][0]["room_id"]
    return next(r for r in state["agent_runs"] if r["room_id"] == rid
                and r["graph_node"] == node
                and document(r["context"]).get("triggering_action", {}).get("seq") == seq)


def test_frozen_delegation_owns_only_hunter_and_retains_request_key(frozen):
    run = saved_run(frozen[0], 84)
    context = document(run["context"])
    raw = context["triggering_action"]["payload"]["text"]
    plan = KeeperPlan.model_validate(document(run["structured_output"]))
    assert plan.focus.requests[0].kind == "question"  # Original failure stays frozen.
    hunter = plan.focus.requests[0].addressee_id
    repair_attribution(plan, raw, context["current_participants"]["members"],
                       plan.parsed_intent.actor_member_id)
    assert [(r.addressee_id, r.kind) for r in plan.focus.requests] == [(hunter, "delegate")]
    request = plan.focus.requests[0]
    assert request.source_start == 7 and raw[7:request.source_end] == request.text
    assert "search" in request.operations


def test_frozen_editor_is_not_directly_addressed_by_hunter_full_name(frozen):
    run = saved_run(frozen[0], 84, "decide_teammates")
    context = document(run["context"])
    trigger = SimpleNamespace(**context["triggering_action"])
    assert context["addressed_requests"] == []
    assert TeammateEligibilityPolicy().evaluate(
        events=[], trigger=trigger, profile=context["profile"], member_id=run["actor_member_id"],
    ) is None


def test_frozen_four_bare_hand_drafts_do_not_claim_note_possession(frozen):
    rid = frozen[0]["launch_drafts"][0]["room_id"]
    runs = [r for r in frozen[0]["agent_runs"] if r["room_id"] == rid
            and r["graph_node"] in {"decide_teammates", "repair_teammate_decision"}
            and document(r["context"]).get("triggering_action", {}).get("seq") == 84]
    assert len(runs) == 4
    for run in runs:
        context = document(run["context"])
        decision = document(run["structured_output"])
        text = decision["action_text"]
        assert "用手检查" in text
        assert not asserted_item_uses(text, context["inventory_state"], run["actor_member_id"])
        assert bind_item_prose(text, context["inventory_state"], run["actor_member_id"]) == []


def test_frozen_new_reveal_is_a_result_even_when_narration_failed(frozen):
    state = frozen[1]
    rid = state["launch_drafts"][0]["room_id"]
    events = [{**e, "payload": document(e["payload"])} for e in state["room_events"]
              if e["room_id"] == rid]
    failed = next(e for e in events if e["seq"] == 167)
    assert failed["payload"]["result_facts"] == []
    actual = next(e for e in events if e["seq"] == 157)
    facts = result_facts(events)
    found = [f for f in facts if f["source_event_seq"] == actual["seq"]]
    assert len(found) == 1
    assert found[0]["operation"] == "reveal" and found[0]["status"] == "success"
    assert found[0]["target_id"] == actual["payload"]["id"]


def test_frozen_unnamed_note_question_remains_with_keeper(frozen):
    run = saved_run(frozen[2], 26)
    context = document(run["context"])
    plan = KeeperPlan.model_validate(document(run["structured_output"]))
    raw = context["triggering_action"]["payload"]["text"]
    assert plan.focus.addressee_id and plan.focus.requests
    repair_attribution(plan, raw, context["current_participants"]["members"],
                       plan.parsed_intent.actor_member_id)
    assert not plan.focus.requests and not plan.focus.addressee_id
    assert plan.focus.question == "便签具体写着什么？"
    assert plan.focus.action


@pytest.mark.parametrize("raw,expected", [
    ("林修远·猎人，请用手检查便签。", "hunter"),
    ("请林修远·猎人检查便签。", "hunter"),
    ("林修远，请用手检查便签。", "editor"),
])
def test_complete_name_precedes_shared_alias_in_any_member_order(raw, expected):
    for names in ({"editor": "林修远", "hunter": "林修远·猎人（队友1）"},
                  {"hunter": "林修远·猎人（队友1）", "editor": "林修远"}):
        assert [mid for mid, _, _ in addressed_spans(raw, names)] == [expected]
        assert teammate_request(raw, names, "player") == expected
        plan = plan_for(raw, "converse")
        repair_attribution(plan, raw, names, "player")
        assert {r.addressee_id for r in plan.focus.requests} == {expected}
        assert all(r.target_id is None for r in plan.focus.requests)


def test_shared_alias_asks_clarification_without_assigning_or_renaming():
    names = {"one": "林修远·猎人", "two": "林修远·编辑"}
    raw = "林修远，请检查便签。"
    plan = plan_for(raw, "converse")
    repair_attribution(plan, raw, names, "player")
    assert plan.needs_clarification and plan.parsed_intent.requires_clarification
    assert not plan.focus.requests and teammate_request(raw, names, "player") is None
    assert names == {"one": "林修远·猎人", "two": "林修远·编辑"}


@pytest.mark.parametrize("raw,kind", [
    ("林修远·猎人，你觉得检查便签有什么建议？", "question"),
    ("林修远·猎人，能否请你用手检查便签边缘？", "delegate"),
    ("林修远·猎人，不要检查便签。", "cancel"),
    ("林修远·猎人，如果有人来，就检查便签。", "hypothesis"),
])
def test_advice_polite_request_refusal_and_conditional_keep_their_roles(raw, kind):
    plan = plan_for(raw, "converse")
    repair_attribution(plan, raw, {"hunter": "林修远·猎人", "editor": "林修远"}, "player")
    assert plan.focus.requests[0].kind == kind
    assert bool(plan.focus.requests[0].operations) == (kind == "delegate")


def test_quoted_request_is_not_a_fresh_delegation_and_you_followup_remains_question():
    names = {"hunter": "林修远·猎人", "editor": "林修远"}
    quote = "他刚才说：“林修远·猎人，请检查便签。”"
    assert not addressed_spans(quote, names) and teammate_request(quote, names, "player") is None
    raw = "你刚才看见了什么？"
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(question=raw, addressee_id="hunter")
    repair_attribution(plan, raw, names, "player")
    assert plan.focus.requests[0].kind == "question"
    assert plan.focus.requests[0].addressee_id == "hunter"


@pytest.mark.parametrize("raw", [
    "林修远·猎人说：“检查便签。”", "林修远·猎人说：“请你检查便签。”",
    "林修远·猎人说过：‘你检查便签了吗？’", "他说：“林修远·猎人，请检查便签。”",
])
def test_reporting_someones_quoted_instruction_never_addresses_or_delegates(raw):
    names = {"editor": "林修远", "hunter": "林修远·猎人"}
    assert not addressed_spans(raw, names)
    assert teammate_request(raw, names, "player") is None
    assert not requested_action_kinds(raw)
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(question=raw, addressee_id="hunter")
    repair_attribution(plan, raw, names, "player")
    assert not plan.focus.requests and not plan.focus.addressee_id


@pytest.mark.parametrize("raw", [
    "我用手仔细检查便签边缘。", "我徒手查看便签背面。", "我观察门上的便签。",
])
def test_world_object_inspection_never_requires_holding_it(raw):
    view = {"known_items": [{"id": "note", "names": ["便签"]}], "holders": [], "members": []}
    assert bind_item_prose(raw, view, "hunter") == []


@pytest.mark.parametrize("holder", [None, "editor", "hunter"])
def test_real_tool_for_inspection_requires_own_instance(holder):
    view = {"known_items": [{"id": "note", "names": ["便签"]},
                            {"id": "glass", "names": ["放大镜"]}], "members": [],
            "holders": [{"item_id": "glass", "instance_id": "glass:one",
                         "holder_id": holder, "title": "放大镜"}] if holder else []}
    text = "我用放大镜检查便签边缘。"
    if holder == "hunter":
        assert bind_item_prose(text, view, "hunter") == ["glass:one"]
    else:
        with pytest.raises(RoomError):
            bind_item_prose(text, view, "hunter")
    with pytest.raises(RoomError):
        bind_item_prose("我用工具检查便签。", view, "hunter")


@pytest.mark.parametrize("change", [
    {"visibility": "host_only"}, {"payload": {"already_revealed": True}},
    {"payload": {"revealed_event_seq": 1}}, {"payload": {"cycle_id": None}},
])
def test_private_initial_or_reused_reveal_never_becomes_current_result(change):
    event = {"seq": 5, "type": "entity.revealed", "visibility": "public",
             "payload": {"cycle_id": "child", "id": "back", "public_summary": "背面文字"}}
    event.update({k: v for k, v in change.items() if k != "payload"})
    event["payload"].update(change.get("payload", {}))
    assert not result_facts([event])


def test_new_reveal_cannot_substitute_for_different_actor_target_cycle_or_operation():
    request = {"key": "84:7", "kind": "delegate", "addressee_id": "hunter",
               "operations": ["search"], "target_id": "note"}
    fact = {"source_event_seq": 157, "operation": "reveal", "status": "success",
            "actor_id": "editor", "cycle_id": "editor-child", "target_id": "back",
            "action_target_id": "note"}
    assert receipt_progress(request, [fact], actor="hunter", cycle_id="hunter-child",
                            consumed=set())[1] is False
    for mutation in ({"actor_id": "editor"}, {"cycle_id": "old"},
                     {"action_target_id": "other"}, {"operation": "observe"}):
        matched = {**fact, "operation": "search", "actor_id": "hunter",
                   "cycle_id": "hunter-child", **mutation}
        assert receipt_progress(request, [matched], actor="hunter", cycle_id="hunter-child",
                                consumed=set())[1] is False


@pytest.mark.parametrize("case", [
    "autonomous", "old_hunter_key", "unrelated_reveal", "validated_inspection",
    "validated_unknown_structure", "unvalidated_unknown_structure",
])
def test_reveal_settlement_keeps_actual_effect_and_narration_failure_separate(client, game, case):  # noqa: F811
    svc, rid = client.app.state.agent_service, game["room"]["id"]
    parent_id, child_id, run_id = (str(uuid4()) for _ in range(3))

    async def verify():
        async def seed(session, room):
            actor = game["agent"]
            target = (await svc.module(session, room.id)).state["scene_id"]
            valid = case in {"validated_inspection", "validated_unknown_structure"}
            request = {"key": "84:7", "kind": "delegate",
                       "operations": ["search", "observe"] if valid else ["observe"],
                       "text": "请检查便签。", "target_id": target,
                       "executor_member_id": actor}
            pending = [] if case == "autonomous" else [request]
            keys = [request["key"]] if case in {
                "unrelated_reveal", "validated_inspection", "validated_unknown_structure",
                "unvalidated_unknown_structure",
            } else []
            session.add(AgentCycle(id=parent_id, room_id=rid, status="completed", state={}))
            action = svc.rooms.append(session, room, "agent.action_proposed", actor, {
                "cycle_id": parent_id, "text": "我检查便签。", "target_id": target,
            })
            child = AgentCycle(id=child_id, room_id=rid, status="completed", state={
                "triggering_member_id": actor, "triggering_event_seq": action.seq,
                "request_keys": keys, "request_operands": {request["key"]: request},
                "related_player_cycle_id": parent_id,
            })
            session.add(child)
            row = AgentBehaviorRecord(room_id=rid, member_id=actor, document=BehaviorState(
                pending_requests=pending, task_status="proposed", task_cycle_id=child_id,
            ).model_dump(mode="json"))
            session.add(row)
            plan = KeeperPlan(
                plan_id=child_id, cycle_id=child_id, current_scene_id=target,
                parsed_intent={"type": "observe", "actor_member_id": actor,
                               "actor_character_slot_id": "slot", "target_id": target,
                               "evidence_quote": "我检查便签。", "confidence": 1},
                focus={"action": "我检查便签。", "action_target_id": target},
                action_authority={"kinds": ["search"]},
            )
            reveal = svc.rooms.append(session, room, "entity.revealed", room.host_member_id, {
                "cycle_id": child_id, "id": "new-back", "title": "便签背面",
                "public_summary": "背面确有一行新文字。",
            })
            session.add(AgentRun(
                id=run_id, room_id=rid, cycle_id=child_id, profile_id=game["profiles"][0]["id"],
                actor_member_id=room.host_member_id, graph_node="plan_keeper_action",
                status="completed", input_seq_start=action.seq, input_seq_end=action.seq,
                provider="fake", model="test", context={}, tool_results=[],
            ))
            reply = "检查便签后，背面确有一行新文字。" if valid else "本次答复未完整生成。"
            if case.endswith("unknown_structure"):
                reply = "检查便签后，背面确有一行新文字。纸张是否有夹层或折叠，目前尚未确认。"
            session.add(ActionPlanRecord(cycle_id=child_id, room_id=rid, run_id=run_id,
                document=AdjudicationRecord(plan=plan, narration={"public_narration": reply},
                    narration_validation={
                    "valid": valid, "answer_complete": valid,
                }).model_dump(mode="json")))
            svc.rooms.append(session, room, "keeper.narration", room.host_member_id, {
                "cycle_id": child_id, "text": reply,
                "safe_fallback": not valid and case != "unvalidated_unknown_structure",
            })
            await session.flush()
            await settle_teammate_tasks(svc, session, room)
            result = row.document["last_result"]
            assert result["narration_complete"] is valid
            facts = result["result_facts"]
            assert len(facts) == (3 if valid else 1) and facts[0]["source_event_seq"] == reveal.seq
            assert facts[0]["operation"] == "reveal" and facts[0]["status"] == "success"
            assert facts[0]["actor_id"] == actor and facts[0]["cycle_id"] == child_id
            assert facts[0]["target_id"] == "new-back" and facts[0]["action_target_id"] == target
            assert facts[0]["source_action_seq"] == action.seq
            assert reveal.seq in result["event_seqs"]
            assert row.document["task_status"] == (
                "completed" if case == "autonomous" or valid else "attempted")
            if pending and not valid:
                assert row.document["pending_requests"][0]["key"] == "84:7"
                assert not row.document["request_history"]
            if valid:
                assert not row.document["pending_requests"]
                assert row.document["request_history"][0]["key"] == "84:7"
            if case.endswith("unknown_structure"):
                # Completion is the inspected/reported operation, not evidence
                # that the unknown physical structure has a negative state.
                assert "纸张是否有夹层或折叠，目前尚未确认" in result["text"]
                assert "没有夹层" not in json.dumps(facts, ensure_ascii=False)
        await svc.mutate(rid, seed)
    client.portal.call(verify)


def test_committed_reveal_and_original_are_one_attributed_effect():
    source = {"seq": 157, "type": "entity.revealed", "actor_member_id": "keeper",
              "visibility": "public", "payload": {"cycle_id": "child", "id": "back",
                                                   "public_summary": "新发现。"}}
    raw = result_facts([source])[0]
    saved = {**raw, "actor_id": "editor", "source_action_seq": 135, "action_target_id": "note"}
    receipt = {"seq": 166, "type": "action.result", "actor_member_id": "editor",
               "payload": {"cycle_id": "child", "facts": [saved]}}
    assert result_facts([source, receipt]) == [saved]
