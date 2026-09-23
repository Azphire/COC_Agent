"""Current utterance survives planning/projection; targets cannot drift silently."""

import json

import pytest
from test_action_adjudication import facts_for, plan_for
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.action_policy import ActionPolicyValidator
from app.agents.action_runtime import (
    NARRATION_INSTRUCTION,
    PLAN_INSTRUCTION,
    action_messages,
)
from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan, TurnFocus, TurnRequest
from app.agents.generation_contracts import generation_contract
from app.agents.narration import question_parts, response_brief
from app.preparation.dialogue import dialogue_target
from app.preparation.turn_focus import repair_attribution

RAW = "我观察木门和公告牌。门上写着什么？公告牌属于谁？请分别用完整句子回答，不要合成一句。"


def current_context(raw=RAW):
    return {
        "triggering_action": {"seq": 8, "type": "action.submitted",
                              "actor_member_id": "actor", "payload": {"text": raw}},
        "module": {"scene": {"id": "scene", "title": "大厅"}},
        "current_scene_reference": "scene",
        "public_entities": [
            {"id": "door", "type": "clue", "title": "木门", "aliases": ["旧门"],
             "public_summary": "门上写着：请轻推。"},
            {"id": "board", "type": "clue", "title": "公告牌", "public_summary": "所属：车站。"},
        ],
        "profile": {"role": "keeper", "name": "KP", "speaking_style": "平实、克制，使用第二人称。",
                    "background": "PRIVATE_BACKGROUND", "goals": "PRIVATE_GOAL",
                    "personality": "PRIVATE_PERSONALITY"},
        "public_tool_results": {"events": [], "result_facts": [], "observation_completed": True},
    }


def brief_for(context):
    plan = plan_for(context["triggering_action"]["payload"]["text"], target="door")
    plan.focus = TurnFocus(action="我观察木门和公告牌。", question="门上写着什么？",
                           action_target_id="door", answer_basis="facts")
    brief, _ = response_brief(plan, context, context["public_tool_results"])
    return brief


def test_current_questions_and_format_survive_brief_and_final_messages():
    context = current_context()
    brief = brief_for(context)
    assert brief["player_statement"] == RAW
    assert brief["questions"] == ["门上写着什么？", "公告牌属于谁？"]
    context["response_brief"] = brief
    for schema, instruction in [(KeeperPlan, PLAN_INSTRUCTION),
                                (KeeperNarration, NARRATION_INSTRUCTION)]:
        messages = action_messages(context, schema, instruction)
        projected = json.loads(messages[-1]["content"])
        assert projected["triggering_action"]["payload"]["text"] == RAW
        assert RAW in messages[0]["content"]


def test_numeric_question_and_unknown_title_question_remain_separate():
    raw = "托马斯早先估计少了多少本书，他知道具体书名吗？"
    assert question_parts(raw) == ["托马斯早先估计少了多少本书？", "他知道具体书名吗？"]


def test_third_person_earlier_testimony_cannot_become_new_npc_interview():
    action = "我环顾道格拉斯的书房，查看书架上的空档和通向屋外的窗户。"
    history = "托马斯早先估计少了多少本书，他知道具体书名吗？"
    peer = "艾琳，请根据这些旧证词说明下一步还需要核对什么。"
    raw = action + history + peer
    plan = plan_for(raw, target="scene")
    plan.focus = TurnFocus(
        action=action, question=history, addressee_id="aileen", action_target_id="scene",
        requests=[TurnRequest(
            addressee_id="thomas", text=history, source_start=len(action),
            source_end=len(action + history),
        ), TurnRequest(addressee_id="aileen", text=peer,
                       source_start=len(action + history), source_end=len(raw))],
    )
    repair_attribution(plan, raw, {"thomas": "托马斯·金博尔", "aileen": "艾琳"},
                       "actor", npc_ids={"thomas"})
    assert [r.addressee_id for r in plan.focus.requests] == ["aileen"]
    assert plan.focus.action == action
    context = current_context(raw)
    context["current_participants"] = {"members": {"aileen": "艾琳"}}
    context["public_entities"] = [{"id": "thomas", "title": "托马斯·金博尔",
                                   "type": "npc", "aliases": ["托马斯"],
                                   "public_summary": "委托人。"}]
    brief, _ = response_brief(plan, context, {"events": []})
    assert brief["responder"] == {"kind": "keeper"}
    assert brief["questions"] == ["托马斯早先估计少了多少本书？", "他知道具体书名吗？"]
    assert dialogue_target(history, context["public_entities"], previous="thomas") is None


@pytest.mark.parametrize("raw", [
    "托马斯，请告诉我早先估计少了多少本书？", "我问托马斯，他早先估计少了多少本书？",
    "你早先估计少了多少本书？",
])
def test_real_npc_address_and_second_person_followup_remain_conversations(raw):
    npc = {"id": "thomas", "title": "托马斯·金博尔", "aliases": ["托马斯"]}
    assert dialogue_target(raw, [npc], previous="thomas") == npc


@pytest.mark.parametrize("raw", ["托马斯早先估计少了多少本书？", "他刚才说过什么？"])
def test_historical_only_question_clears_invented_npc_binding(raw):
    plan = plan_for(raw, "converse", "thomas")
    plan.focus = TurnFocus(question=raw, addressee_id="thomas")
    repair_attribution(plan, raw, {"thomas": "托马斯·金博尔"}, "actor", npc_ids={"thomas"})
    assert not plan.focus.requests and plan.focus.addressee_id is None


def test_narrator_receives_only_public_speaking_style_projection():
    context = current_context()
    context["response_brief"] = brief_for(context)
    projected = json.loads(action_messages(
        context, KeeperNarration, NARRATION_INSTRUCTION,
    )[-1]["content"])
    assert projected["response_brief"].get("public_style") == {
        "speaking_style": "平实、克制，使用第二人称。",
    }
    assert "PRIVATE_" not in json.dumps(projected)
    assert "profile" not in projected


@pytest.mark.parametrize("raw", ["我查看木门上公开的文字。", "我查看旧门上公开的文字。"])
def test_explicit_object_cannot_be_replaced_with_unrelated_visible_board(raw):
    context = current_context(raw)
    facts = facts_for(raw)
    facts.approved_entities = {e["id"]: e for e in context["public_entities"]}
    facts.local_entity_ids = facts.visible_entity_ids = {"door", "board"}
    plan = plan_for(raw, target="board")
    result = ActionPolicyValidator().validate(plan.parsed_intent, plan, facts, [])
    assert result.status == "clarification_required"
    assert result.clarification_question
    assert not result.approved_actions


@pytest.mark.parametrize("target", ["door", "board", "scene"])
def test_multiple_current_objects_preserve_determinate_observation(target):
    context = current_context()
    facts = facts_for(RAW)
    facts.approved_entities = {e["id"]: e for e in context["public_entities"]}
    facts.local_entity_ids = facts.visible_entity_ids = {"door", "board"}
    plan = plan_for(RAW, target=target)
    plan.focus = TurnFocus(action="我观察木门和公告牌。", action_target_id=target)
    assert ActionPolicyValidator().validate_intent(plan.parsed_intent, plan, facts) is None


def test_named_object_in_separate_question_cannot_override_current_action():
    raw = "我查看公告牌。木门的暗号是谁提供的？"
    context = current_context(raw)
    facts = facts_for(raw)
    facts.approved_entities = {e["id"]: e for e in context["public_entities"]}
    facts.local_entity_ids = facts.visible_entity_ids = {"door", "board"}
    plan = plan_for(raw, target="board")
    plan.focus = TurnFocus(action="我查看公告牌。", action_target_id="board")
    assert ActionPolicyValidator().validate_intent(plan.parsed_intent, plan, facts) is None


def test_request_routing_preserves_raw_format_without_answering_for_teammate():
    raw = "我查看木门。周岚，公告牌是谁写的？木门上写着什么？请分句回答。"
    context = current_context(raw)
    context["current_participants"] = {"members": {"zhou": "周岚"}}
    plan = plan_for(raw, target="door")
    plan.focus = TurnFocus(
        action="我查看木门。", action_target_id="door", addressee_id="zhou",
        question="公告牌是谁写的？", requests=[TurnRequest(
            kind="question", addressee_id="zhou", text="周岚，公告牌是谁写的？",
        )],
    )
    brief, _ = response_brief(plan, context, {"events": []})
    assert brief["responder"] == {"kind": "keeper"}
    assert brief["questions"] == ["木门上写着什么？"]
    assert brief["player_statement"] == raw


@pytest.mark.parametrize("text", [
    "门上的字迹已淡了。公告牌署名车站。",
    "门上的字迹已淡了。公告牌署名车站。落款还留有一道蓝色边框。",
])
def test_public_observation_body_does_not_force_fixed_sentence_count(text):
    context = current_context()
    context["response_brief"] = brief_for(context)
    generated = generation_contract(KeeperNarration, context).model_validate({
        "observed_detail": text,
    })
    assert generated.public_narration == text


def test_runtime_projects_only_keeper_style_and_preserves_current_utterance(client, game):  # noqa: F811
    keeper = game["profiles"][0]
    style = "平实、克制，使用第二人称。"
    ok(client.post(game["prefix"] + "/pause"))
    binding = next(item for item in ok(client.get(game["prefix"]))["game"]["bindings"]
                   if item["profile_id"] == keeper["id"])
    ok(client.delete(game["prefix"] + "/agent-bindings/" + binding["id"]))
    ok(client.patch("/api/agent-profiles/" + keeper["id"], json={
        "role": "keeper", "name": "KP", "speaking_style": style,
        "background": "PRIVATE_BACKGROUND", "personality": "PRIVATE_PERSONALITY",
        "goals": "PRIVATE_GOAL",
    }))
    ok(client.post(game["prefix"] + "/agent-bindings", json={
        "member_id": binding["member_id"], "profile_id": keeper["id"],
    }))
    ok(client.post(game["prefix"] + "/resume"))
    raw = "我观察现场。公告上写着什么？钟面有什么特点？请分别用完整句子回答。"
    ok(submit(client, game, raw))
    assert wait_cycle(client, game)["status"] == "completed"
    contexts = [json.loads(messages[-1]["content"]) for messages in game["adapter"].prompts
                if messages[-1]["content"].startswith("{")]
    public = [context for context in contexts if context.get("response_brief")]
    assert public
    human = [context for context in public
             if context["triggering_action"]["actor_member_id"] == game["player"]]
    assert human and all(context["triggering_action"]["payload"]["text"] == raw
                         for context in human)
    for context in public:
        assert context["response_brief"]["public_style"] == {"speaking_style": style}
        assert "PRIVATE_" not in json.dumps(context)
