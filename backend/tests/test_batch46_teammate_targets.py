"""Target binding precedes novelty checks; technical retries keep task identity."""

import pytest
from pydantic import ValidationError
from test_agent_runtime import game  # noqa: F401
from test_rooms import character, lobby, prepare  # noqa: F401

from app.agents import task_receipts
from app.agents.adjudication_schemas import BehaviorState, Cooldown, TeammateDecision
from app.agents.behavior import TeammateBehaviorPolicy, public_fingerprint
from app.agents.generation_contracts import generation_contract

ACTOR = "investigator"
TARGETS = [{"id": "window", "title": "窗锁", "aliases": ["书房窗户"],
            "fact_scope": "current_scene", "type": "clue", "public_summary": "窗锁松动"}]
INVENTORY = {"known_items": [{"id": "lamp", "names": ["手电"]}],
             "holders": [{"instance_id": "lamp-one", "item_id": "lamp", "holder_id": ACTOR}],
             "members": [], "other_actors": []}
REQUEST = {"key": "51:0", "kind": "delegate", "text": "请查看书房窗户，核对窗锁是否松动。",
           "operations": ["observe"], "source_event_seq": 51, "target_id": None}
CONTEXT = {"public_state": {"scene": "study"}, "public_entities": TARGETS, "checks": []}


def decision(**updates):
    return TeammateDecision(mode="act", action_text="我查看书房窗户，核对窗锁是否松动。",
                            related_player_action_seq=51, confidence=0.9).model_copy(update=updates)


def normalize(value, requests=None, targets=None):
    return task_receipts.normalize_teammate_target(
        value, [REQUEST] if requests is None else requests, INVENTORY, ACTOR, "player",
        targets=TARGETS if targets is None else targets,
    )


@pytest.mark.parametrize("mode", ["act", "assist"])
def test_chosen_attempt_binds_unique_current_target_before_fingerprint(mode):
    value = decision(mode=mode)
    old = public_fingerprint(CONTEXT, value.target_id, ACTOR)
    bound = normalize(value)
    assert value.target_id == "window" and "window" in value.related_public_entity_ids
    assert bound[0]["target_id"] == "window" and REQUEST["target_id"] is None
    assert public_fingerprint(CONTEXT, value.target_id, ACTOR) != old


@pytest.mark.parametrize("mode", ["speak", "pass"])
def test_discussion_and_declining_do_not_gain_action_target(mode):
    value = decision(mode=mode, speech_text="我建议先询问房主。", action_text=None)
    normalize(value)
    assert value.target_id is None and value.mode == mode


def test_ambiguous_and_historical_targets_are_not_guessed():
    duplicate = {**TARGETS[0], "id": "other-window"}
    value = decision()
    bound = normalize(value, targets=[*TARGETS, duplicate])
    assert value.target_id is None
    assert set(bound[0]["target_candidates"]) == {"window", "other-window"}
    old = decision()
    normalize(old, targets=[{**TARGETS[0], "fact_scope": "historical"}])
    assert old.target_id is None


def test_action_selects_one_of_multiple_requested_targets_but_not_wrong_explicit_target():
    other = {**TARGETS[0], "id": "door", "title": "门锁", "aliases": ["大门"]}
    requests = [REQUEST, {**REQUEST, "key": "51:1", "text": "查看门锁。"}]
    value = decision()
    normalize(value, requests, [*TARGETS, other])
    assert value.target_id == "window"
    chosen = decision(target_id="door")
    normalize(chosen, [REQUEST], [*TARGETS, other])
    assert chosen.target_id == "door"


def test_contract_distinguishes_entity_targets_from_owned_item_instances():
    context = {"triggering_action": {"seq": 51}, "self_identity": {"member_id": ACTOR},
               "public_entities": TARGETS, "inventory_state": INVENTORY,
               "addressed_requests": [REQUEST]}
    contract = generation_contract(TeammateDecision, context)
    good = decision(target_id="window", item_instance_ids=["lamp-one"]).model_dump()
    assert contract.model_validate(good).target_id == "window"
    with pytest.raises(ValidationError):
        contract.model_validate({**good, "item_instance_ids": ["window"]})
    with pytest.raises(ValidationError):
        contract.model_validate({**good, "target_id": "lamp-one"})


def policy_result(value, state, active_requests=None, **kwargs):
    return TeammateBehaviorPolicy().validate(
        value, state=state, recent_outputs=[], other_outputs=[],
        player_text=REQUEST["text"], player_intent="observe", public_ids={"window"},
        action_seq=51, fingerprint=public_fingerprint(CONTEXT, value.target_id, ACTOR),
        explicit_action_request=True, requested_operations=["observe"],
        actor_id=ACTOR, active_requests=[{**REQUEST, "target_id": "window"}]
        if active_requests is None else active_requests, **kwargs,
    )


def failed_state(**request_updates):
    pending = {**REQUEST, "target_id": "window", "last_result_kind": "generation_failed",
               "technical_failure": {"cycle_id": "failed-child", "target_id": "window",
                                     "operations": ["observe"], "executed": False},
               **request_updates}
    cooldown = Cooldown.model_validate({
        "action_type": "observe", "target_id": "window", "remaining_cycles": 2,
        "state_fingerprint": public_fingerprint(CONTEXT, "window", ACTOR),
        "request_keys": [REQUEST["key"]], "operations": ["observe"],
        "task_cycle_id": "failed-child",
    })
    return BehaviorState(pending_requests=[pending], cooldowns=[cooldown],
                         last_result={"kind": "generation_failed"})


def test_same_request_target_operation_unexecuted_technical_retry_is_allowed():
    assert policy_result(decision(target_id="window"), failed_state()).accepted


@pytest.mark.parametrize("updates", [
    {"key": "old-unrelated:0"},
    {"technical_failure": {"cycle_id": "failed-child", "target_id": "door",
                           "operations": ["observe"], "executed": False}},
    {"technical_failure": {"cycle_id": "failed-child", "target_id": "window",
                           "operations": ["search"], "executed": False}},
    {"technical_failure": {"cycle_id": "failed-child", "target_id": "window",
                           "operations": ["observe"], "executed": True}},
    {"completion_event_seqs": [40]},
])
def test_unrelated_changed_or_executed_task_never_grants_cooldown_exemption(updates):
    result = policy_result(decision(target_id="window"), failed_state(**updates))
    assert not result.accepted and result.reason == "target_action_cooldown"


def test_legacy_cooldown_without_request_provenance_cannot_be_laundered_by_failed_task():
    state = failed_state()
    state.cooldowns = [Cooldown(action_type="observe", target_id="window", remaining_cycles=2,
                                state_fingerprint=public_fingerprint(CONTEXT, "window", ACTOR))]
    result = policy_result(decision(target_id="window"), state)
    assert not result.accepted and result.reason == "target_action_cooldown"


def test_policy_recomputes_fingerprint_after_normalization_for_cooldown_check():
    value = decision(target_id="window")
    state = failed_state(technical_failure={})
    result = TeammateBehaviorPolicy().validate(
        value, state=state, recent_outputs=[], other_outputs=[], player_text=REQUEST["text"],
        player_intent="observe", public_ids={"window"}, action_seq=51,
        fingerprint=public_fingerprint(CONTEXT, None, ACTOR), fingerprint_context=CONTEXT,
        actor_id=ACTOR, explicit_action_request=True, requested_operations=["observe"],
    )
    assert not result.accepted and result.reason == "target_action_cooldown"


def test_advance_records_request_operation_and_execution_cycle_provenance():
    value = decision(target_id="window")
    state = TeammateBehaviorPolicy().advance(
        BehaviorState(), value, cycle_id="parent", fingerprint="fingerprint", safe_goal="查看窗锁",
        requests=[{**REQUEST, "target_id": "window"}], task_cycle_id="child",
    )
    cooldown = state.cooldowns[0]
    assert cooldown.request_keys == [REQUEST["key"]]
    assert cooldown.operations == ["observe"] and cooldown.task_cycle_id == "child"


def test_adjust_label_does_not_allow_same_executed_attempt_without_new_evidence():
    state = failed_state(technical_failure={})
    state.last_result = {"kind": "blocked"}
    result = policy_result(decision(target_id="window", goal_status="adjust"), state)
    assert not result.accepted and result.reason == "target_action_cooldown"


@pytest.mark.parametrize("receipt_type", [None, "check.resolved", "module.interaction"])
def test_settlement_scopes_technical_retry_and_preserves_actual_attempt_restrictions(
    client, game, receipt_type,  # noqa: F811
):
    from uuid import uuid4

    from app.agents.conversation import settle_teammate_tasks
    from app.persistence.adjudication_models import AgentBehaviorRecord
    from app.persistence.agent_models import AgentCycle

    svc, rid, cid = client.app.state.agent_service, game["room"]["id"], str(uuid4())

    async def verify():
        async def seed(session, room):
            actor = game["agent"]
            state = failed_state(technical_failure={})
            state.pending_requests.append({**REQUEST, "key": "unrelated", "target_id": "door"})
            state.task_status, state.task_cycle_id = "proposed", cid
            state.cooldowns[0].task_cycle_id = cid
            session.add(AgentBehaviorRecord(room_id=rid, member_id=actor,
                                            document=state.model_dump(mode="json")))
            trigger = svc.rooms.append(session, room, "action.submitted", actor,
                                       {"text": REQUEST["text"], "cycle_id": cid})
            session.add(AgentCycle(id=cid, room_id=rid, status="failed", state={
                "request_keys": [REQUEST["key"]], "related_player_cycle_id": cid,
                "triggering_event_seq": trigger.seq, "safe_error": "generation interrupted",
                "teammate_attempt": {"target_id": "window", "operations": ["observe"]},
                "request_operands": {REQUEST["key"]: {**REQUEST, "target_id": "window"}},
            }))
            if receipt_type:
                svc.rooms.append(session, room, receipt_type, actor, {
                    "cycle_id": cid, "target_id": "window", "operation": "observe",
                    "passed": False, "result": {"passed": False},
                    "display_text": "本次尝试未成功。", "text": "本次尝试未成功。",
                })
            await session.flush()
            await settle_teammate_tasks(svc, session, room)
            row = await session.get(AgentBehaviorRecord, (rid, actor))
            saved = BehaviorState.model_validate(row.document)
            pending = next(r for r in saved.pending_requests if r["key"] == REQUEST["key"])
            unrelated = next(r for r in saved.pending_requests if r["key"] == "unrelated")
            assert "technical_failure" not in unrelated
            if receipt_type is None:
                assert saved.task_status == "generation_failed"
                assert pending["technical_failure"] == {
                    "cycle_id": cid, "target_id": "window", "operations": ["observe"],
                    "executed": False,
                }
                assert policy_result(decision(target_id="window"), saved).accepted
            else:
                assert saved.task_status != "generation_failed"
                assert not pending.get("technical_failure")
                assert not policy_result(decision(target_id="window"), saved).accepted

        await svc.mutate(rid, seed)

    client.portal.call(verify)
