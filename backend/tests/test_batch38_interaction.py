"""Natural NPC expression, durable request relevance and public attribution."""

import json

import pytest
from test_action_adjudication import plan_for
from test_agent_runtime import submit, wait_cycle
from test_batch36_collaboration import responder, team_game  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.adjudication_schemas import (
    BehaviorState,
    KeeperNarration,
    TeammateDecision,
    TurnRequest,
)
from app.agents.behavior import TeammateBehaviorPolicy
from app.agents.generation_contracts import generation_contract
from app.agents.model import FakeModelAdapter
from app.memory.events import public_accounts
from app.models.base import ModelFormatError
from app.preparation.dialogue import current_item_statements
from app.preparation.inventory import bind_item_prose
from app.preparation.turn_focus import reconcile_requests
from app.rooms.service import RoomError


@pytest.mark.parametrize("question", ["能听清我说话吗？", "腿还疼吗？", "您还能撑住吗？",
                                     "要不要歇一会儿再聊？", "我坐这儿陪你一会，好吗？"])
def test_unscripted_present_interaction(question):
    context = {"current_scene_reference": "scene", "response_brief": {
        "responder": {"id": "npc", "kind": "npc", "interaction_state": {"can_speak": True}},
        "questions": [question], "answer_basis": "social",
    }}
    output = generation_contract(KeeperNarration, context).model_validate({"npc_speech": {
        "answers": [{"certainty": "social", "text": "嗯，我还撑得住，先让我缓口气。"}],
    }})
    assert "撑得住" in output.npc_speech.text
    with pytest.raises(ModelFormatError, match="重复问题"):
        generation_contract(KeeperNarration, context).model_validate({"npc_speech": {
            "answers": [{"certainty": "social", "text": question}],
        }})
    context["response_brief"]["questions"] = ["谁拿走了钥匙？"]
    with pytest.raises(ModelFormatError):
        generation_contract(KeeperNarration, context).model_validate({"npc_speech": {
            "answers": [{"certainty": "social", "text": "是司机拿走了钥匙。"}],
        }})


@pytest.mark.parametrize("past", ["逃跑时我的黑包背带断了。", "刚才我的黑包掉在前门。",
                                 "我的黑包在逃跑时掉在前门了。"])
def test_sourced_loss_is_history_but_current_use_still_requires_inventory(past):
    view = {"known_items": [{"id": "bag", "names": ["黑包"]},
                            {"id": "key", "names": ["钥匙"]}], "holders": []}
    source = ["黑包背带断裂，逃跑时掉在前门。"]
    assert not bind_item_prose(current_item_statements(past, source), view, "npc")
    with pytest.raises(RoomError):
        bind_item_prose(current_item_statements(past + "现在我用钥匙开门。", source), view, "npc")


def test_past_custody_does_not_prove_present_absence_either():
    source = "乘务员曾保管驾驶室钥匙；黑包在逃跑时掉在前门。"
    context = {"current_scene_reference": "scene", "response_brief": {
        "responder": {"id": "npc", "kind": "npc", "name": "乘务员"},
        "questions": ["钥匙现在在哪里？"],
        "testimony": [{"entity_id": "fact", "text": source}],
    }}
    with pytest.raises(ModelFormatError, match="过去持有"):
        generation_contract(KeeperNarration, context).model_validate({"npc_speech": {
            "answers": [{"question_index": 0, "certainty": "sourced", "evidence_quote": source,
                         "text": "钥匙我曾经保管过，但现在已经不在身上了。"}],
        }})
    with pytest.raises(ModelFormatError, match="未知"):
        generation_contract(KeeperNarration, context).model_validate({"npc_speech": {
            "answers": [{"question_index": 0, "certainty": "unknown", "evidence_quote": source,
                         "text": "钥匙……我好像已经丢掉了，现在摸不到。"}],
        }})


def request(key, scene="old", target=None, continuity="scene", kind="delegate"):
    return {"key": key, "text": "请查看门口。", "kind": kind, "scene_id": scene,
            "target_id": target, "continuity": continuity, "operations": ["observe"]}


def test_task_relevance_preserves_carried_and_ongoing_work_but_retires_local_work():
    state = BehaviorState(pending_requests=[request("door"), request("bag", target="bag"),
                                           request("goal", continuity="ongoing")],
                          last_result={"kind": "generation_failed"})
    reconcile_requests(state, [], seq=10, scene_id="new", reachable_ids={"bag"})
    assert [r["key"] for r in state.pending_requests] == ["bag", "goal"]
    assert state.request_history[0]["status"] == "unavailable"
    # Repeated scheduling cannot resurrect the retired local task.
    reconcile_requests(state, [], seq=11, scene_id="new", reachable_ids={"bag"})
    assert len(state.pending_requests) == 2
    assert state.last_result["kind"] == "generation_failed"


def test_new_question_keeps_unfinished_action_and_cancel_retires_it():
    state = BehaviorState(pending_requests=[request("old-question", kind="question"),
                                           request("work", target="door")])
    fresh = TurnRequest(addressee_id="chen", text="你现在有什么判断？", kind="question")
    reconcile_requests(state, [fresh], seq=10, scene_id="old", reachable_ids={"door"})
    assert [r["key"] for r in state.pending_requests] == ["work", "10:0"]
    assert state.request_history[0]["status"] == "superseded"
    cancel = TurnRequest(addressee_id="chen", text="不用查门了。", kind="cancel", target_id="door")
    reconcile_requests(state, [cancel], seq=11, scene_id="old", reachable_ids={"door"})
    assert "work" not in {r["key"] for r in state.pending_requests}
    assert state.request_history[-1]["status"] == "cancelled"


def test_cancel_and_replacement_keep_separate_original_spans():
    from app.agents.adjudication_schemas import TurnFocus
    from app.preparation.turn_focus import repair_attribution

    raw = "陈拓，刚才的车门先别查了。麻烦你检查一下前门那堆行李。"
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(question=raw, addressee_id="chen")
    repair_attribution(plan, raw, {"chen": "陈拓"}, "player")
    assert [r.kind for r in plan.focus.requests] == ["cancel", "delegate"]
    assert "行李" in plan.focus.requests[1].text
    assert "别查" not in plan.focus.requests[1].text


def test_npc_salutation_owns_only_prefix_of_mixed_request():
    from app.agents.adjudication_schemas import TurnFocus
    from app.preparation.dialogue import npc_addressed_spans
    from app.preparation.turn_focus import repair_attribution

    raw = "师傅，能听清我说话吗？腿还疼吗？周岚，麻烦你检查一下他的伤口和呼吸。"
    people = {"nurse": "周岚", "chen": "陈拓", "npc": "乘务员"}
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(question=raw[3:16], addressee_id="chen", requests=[
        TurnRequest(kind="question", addressee_id="chen", text=raw[3:16],
                    source_start=3, source_end=16),
    ])
    spans = npc_addressed_spans(raw, [{"id": "npc", "title": "乘务员"}],
                                {"nurse": "周岚", "chen": "陈拓"})
    repair_attribution(plan, raw, people, "player", npc_ids={"npc"}, explicit_spans=spans)
    assert [r.addressee_id for r in plan.focus.requests] == ["npc", "nurse"]
    assert "腿还疼" in plan.focus.requests[0].text
    assert "伤口" in plan.focus.requests[1].text
    assert not npc_addressed_spans(
        "我检查他的口袋，然后问乘务员：听得清吗？",
        [{"id": "npc", "title": "乘务员"}], {},
    )


def test_question_labels_repair_from_selected_interlocutor_without_erasing_real_action():
    from app.agents.adjudication_schemas import KeeperPlan
    from app.agents.generation_contracts import restore_output, utterance_clauses
    from app.preparation.dialogue import npc_addressed_spans
    from app.preparation.turn_focus import repair_attribution

    raw = "我蹲在乘务员旁边，轻声问：师傅，听得清我说话吗？您腿现在疼得怎么样，还撑得住吗？"
    context = {"action_identifiers": {
        "plan_id": "p", "cycle_id": "c", "actor_member_id": "00000000-0000-0000-0000-000000000001",
        "actor_character_slot_id": "slot", "current_scene_id": "scene",
        "expected_navigation_revision": 0,
    }, "triggering_action": {"seq": 1, "payload": {"text": raw}},
        "current_targets": [{"id": "npc", "type": "npc", "title": "乘务员"}]}
    generated = generation_contract(KeeperPlan, context).model_validate({
        "parsed_intent": {"type": "interact"}, "focus": {
            "action_clause_ids": [c["id"] for c in utterance_clauses(raw)],
            "question_clause_ids": [], "addressee_id": "npc", "action_target_id": "npc",
        },
    })
    plan = restore_output(generated, KeeperPlan, context)
    repair_attribution(plan, raw, {"npc": "乘务员"}, "player", npc_ids={"npc"},
                       explicit_spans=npc_addressed_spans(raw, context["current_targets"], {}))
    assert "听得清" not in plan.focus.action and "撑得住" not in plan.focus.action
    assert plan.focus.addressee_id == "npc"
    assert "听得清" in plan.focus.question and "撑得住" in plan.focus.question


def test_ongoing_remote_task_waits_for_access_and_keeps_identity():
    state = BehaviorState(pending_requests=[request("work", target="door", continuity="ongoing")])
    reconcile_requests(state, [], seq=1, scene_id="new", reachable_ids=set())
    assert state.pending_requests[0]["available"] is False
    reconcile_requests(state, [], seq=2, scene_id="old", reachable_ids={"door"})
    assert state.pending_requests[0].get("available", True) is True
    assert state.pending_requests[0]["key"] == "work"


def test_result_reply_cannot_invent_inscription_and_chat_preserves_receipt():
    state = BehaviorState(last_attempt_result={"kind": "completed", "text": "黑包背带断裂。"})
    decision = TeammateDecision(mode="speak", related_player_action_seq=5, confidence=1,
                                speech_text="便签上写着‘驾驶室钥匙在3号车厢’。")
    policy = TeammateBehaviorPolicy()
    outcome = policy.validate(
        decision, state=state, recent_outputs=[], other_outputs=[], player_text="检查结果呢？",
        player_intent=plan_for("检查结果呢？", "converse").parsed_intent,
        public_ids=set(), action_seq=5, fingerprint="same",
    )
    assert outcome.reason == "unsupported_quotation"
    decision.speech_text = "我看到黑包的背带断了。"
    updated = policy.advance(state, decision, cycle_id="chat", fingerprint="same", safe_goal="")
    assert updated.last_attempt_result == state.last_attempt_result
    decision.speech_text = "师傅，您先别担心，我来帮您处理伤口。"
    result = policy.validate(
        decision, state=state, recent_outputs=[], other_outputs=[], player_text="听得清吗？",
        player_intent=plan_for("听得清吗？", "converse").parsed_intent,
        public_ids=set(), action_seq=5, fingerprint="same",
    )
    assert result.reason == "accepted_task_needs_attempt"


def test_attribution_and_corrected_opinion_keep_original_events():
    events = [
        {"seq": 1, "type": "agent.spoke", "actor_member_id": "nurse",
         "payload": {"text": "钥匙还在他身上。", "actor_name": "周岚"}},
        {"seq": 2, "type": "action.submitted", "actor_member_id": "player",
         "payload": {"text": "我检查座位下。", "cycle_id": "c"}},
        {"seq": 3, "type": "check.resolved", "actor_member_id": "host",
         "payload": {"display_text": "侦查失败。", "cycle_id": "c"}},
        {"seq": 4, "type": "npc.spoke", "actor_member_id": "host",
         "payload": {"text": "我曾保管钥匙，现在不确定在哪。", "entity_id": "npc"}},
    ]
    original = json.dumps(events)
    rows = public_accounts(events, "nurse", {"player": "林知秋"},
                           [{"id": "key", "title": "钥匙"}])
    assert rows[0]["status"] == "historical_superseded" and rows[0]["superseded_by"] == 4
    assert rows[2]["actor_id"] == "player" and rows[2]["ownership"] == "other"
    assert json.dumps(events) == original
    decision = TeammateDecision(mode="speak", speech_text="我刚才检查了座位下，没找到东西。",
                                confidence=1, related_player_action_seq=5)
    outcome = TeammateBehaviorPolicy().validate(
        decision, state=BehaviorState(), recent_outputs=[], other_outputs=[], player_text="如何？",
        player_intent=plan_for("如何？", "converse").parsed_intent, public_ids=set(),
        action_seq=5, fingerprint="same", public_accounts=rows,
    )
    assert outcome.reason == "borrowed_experience"
    decision.mode = "act"
    decision.action_text = decision.speech_text = "我过去看看车门。"
    outcome = TeammateBehaviorPolicy().validate(
        decision, state=BehaviorState(), recent_outputs=[], other_outputs=[],
        player_text="看看门。",
        player_intent=plan_for("看看门。", "observe").parsed_intent, public_ids=set(),
        action_seq=5, fingerprint="same", public_accounts=rows,
    )
    assert outcome.accepted


def test_current_inventory_rejects_teammate_repeating_outdated_npc_possession():
    view = {"known_items": [{"id": "key", "names": ["钥匙"]}], "holders": [],
            "other_actors": [{"id": "npc", "names": ["乘务员"]}]}
    with pytest.raises(RoomError):
        bind_item_prose("乘务员说钥匙在他身上，但不能确定。", view, "nurse")
    assert not bind_item_prose("乘务员不能确定钥匙是否还在他身上。", view, "nurse")


def test_mixed_result_question_does_not_turn_another_teammates_new_task_into_recall(
    client, team_game,  # noqa: F811
):
    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=responder)
    ok(submit(client, team_game, "占位队友，刚才你做了什么？陈拓，帮我查看入口。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any(e["type"] == "agent.action_proposed"
               and e["actor_member_id"] == team_game["chen"] for e in events)


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_npc_repair_keeps_valid_answers_and_only_publishes_real_unknown(
    client, team_game, repair_succeeds,  # noqa: F811
):
    calls = []

    def answer(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            return {"parsed_intent": {"type": "converse"}, "focus": {
                "question_clause_ids": ["u1", "u2"], "addressee_id": "caretaker"}}
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            calls.append(messages)
            return {"npc_speech": {"answers": [
                {"question_index": 0, "certainty": "social",
                 "text": "听得清，你慢慢说。" if len(calls) == 1 else "你说吧。"},
                {"question_index": 1, "certainty": "unknown", "text": "是谁拿走的，我没看见。"}
                if repair_succeeds and len(calls) > 1 else
                {"question_index": 1, "certainty": "social", "text": "钥匙被司机拿走了。"},
            ]}}
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    ok(submit(client, team_game, "能听清我说话吗？钥匙是谁拿走的？"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    speech = [e["payload"]["text"] for e in events if e["type"] == "npc.spoke"][-1]
    assert speech.startswith("听得清，你慢慢说。")
    assert ("我没看见" in speech) == repair_succeeds
    assert "你说吧" not in speech
    assert "不知道" not in speech and "至于" not in speech
