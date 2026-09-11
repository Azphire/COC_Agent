"""Fake tests validate state and authority, never natural-language understanding."""

import json

import pytest
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_batch12 import conversational, pending
from test_check_narration_policy import policy_case
from test_rooms import lobby, ok  # noqa: F401

from app.agents.action_policy import ActionPolicyValidator
from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
from app.agents.generation_contracts import generation_contract, restore_output
from app.agents.model import FakeModelAdapter
from app.agents.narration import response_brief
from app.agents.schemas import PlannedTool
from app.models.ollama import generation_schema


@pytest.mark.parametrize(
    "question,suggestion,hypothesis",
    [
        ("你昨晚听见什么？", "", ""),
        ("您要先拿回手电吗？", "", ""),
        ("", "我们或许可以把工具交给她", ""),
        ("", "", "如果他同意，我们再进门"),
    ],
)
def test_non_action_clauses_cannot_authorize_tools(question, suggestion, hypothesis):
    facts, intent, _ = policy_case()
    plan = KeeperPlan(
        plan_id=facts.cycle_id,
        cycle_id=facts.cycle_id,
        current_scene_id=facts.scene_id,
        parsed_intent=intent,
        focus=TurnFocus(question=question, suggestion=suggestion, hypothesis=hypothesis),
    )
    verdict = ActionPolicyValidator().validate(
        intent,
        plan,
        facts,
        [
            PlannedTool(name="reveal_entity", arguments={"entity_id": "door"}),
        ],
    )
    assert not verdict.approved_actions
    assert verdict.rejected_actions[0].code == "precondition_failed"


def test_generation_binds_metadata_and_restores_public_candidate_ids():
    from app.agents.adjudication_schemas import KeeperNarration

    context = {
        "action_identifiers": dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="member",
            actor_character_slot_id="slot",
            current_scene_id="s",
            expected_navigation_revision=3,
        ),
        "triggering_action": {"seq": 17, "payload": {"text": "我打开地图"}},
        "current_scene_reference": "s",
        "PUBLIC_CLAIM_OPTIONS": [
            dict(
                claim_id="map",
                category="module_fact",
                statement="地图画有向北的路。",
                entity_ids=["map"],
                evidence_ids=[],
                visibility="public",
            )
        ],
    }
    grammar = generation_schema(generation_contract(KeeperPlan, context).model_json_schema())
    assert "plan_id" not in grammar["properties"]
    assert "actor_member_id" not in grammar["$defs"]["PlayerIntent"]["properties"]
    assert "evidence_quote" not in grammar["$defs"]["PlayerIntent"]["properties"]
    assert "action" not in grammar["$defs"]["TurnFocus"]["properties"]
    selected_schema = generation_contract(KeeperPlan, context)
    selected = restore_output(
        selected_schema(
            parsed_intent={"type": "interact"},
            focus={"action_clause_ids": ["u1"], "question_clause_ids": []},
        ),
        KeeperPlan,
        context,
    )
    assert selected.focus.action == "我打开地图"
    assert selected.focus.question == ""
    context["approved_exits"] = [{"transition_id": "north", "target_scene_node_id": "n"}]
    plan_schema = generation_contract(KeeperPlan, context)
    moved = restore_output(
        plan_schema(
            parsed_intent={"type": "move"},
            focus={"action": "我打开地图", "action_target_id": "s"},
            proposed_transition_id="north",
        ),
        KeeperPlan,
        context,
    )
    assert moved.focus.action_target_id == "n"
    schema = generation_contract(KeeperNarration, context)
    restored = restore_output(
        schema(public_narration="地图标着北面的道路。", claim_ids=["map"]), KeeperNarration, context
    )
    assert restored.grounded_claims[0].statement == "地图画有向北的路。"
    with pytest.raises(Exception, match="未知公开依据"):
        restore_output(schema(claim_ids=["private"]), KeeperNarration, context)


def test_brief_keeps_profile_separate_from_testimony_and_current_receipt():
    facts, intent, _ = policy_case()
    plan = KeeperPlan(
        plan_id="p",
        cycle_id=facts.cycle_id,
        current_scene_id=facts.scene_id,
        parsed_intent=intent,
        focus=TurnFocus(
            question="爆炸前你听到了什么？",
            addressee_id="porter",
            public_fact_ids=["porter", "hidden"],
        ),
    )
    context = {
        "triggering_action": {"seq": 91, "payload": {"text": "爆炸前你听到了什么？"}},
        "public_entities": [
            dict(id="porter", type="npc", title="程叔", public_summary="愿意出借雨伞。"),
        ],
        "PUBLIC_CLAIM_OPTIONS": [
            dict(
                claim_id="entity_porter",
                statement="愿意出借雨伞。",
                entity_ids=["porter"],
                category="module_fact",
            ),
        ],
    }
    brief, _ = response_brief(plan, context, {"events": [], "failed_tools": []})
    assert brief["trigger_seq"] == 91
    assert brief["allowed_facts"] == []
    assert brief["responder"]["unrecorded_testimony"] == "unknown"
    assert brief["withdrawal"] is None
    plan.focus.question = "为什么有私有的紫月暗号？"
    plan.focus.purpose = "寻找私有的紫月暗号"
    plan.focus.action = "寻找私有的紫月暗号"
    plan.next_decision = "决定是否寻找私有的紫月暗号"
    brief, _ = response_brief(plan, context, {"events": [], "failed_tools": []})
    assert "紫月" not in json.dumps(brief, ensure_ascii=False)


def test_rejected_plan_cannot_publish_private_clarification():
    facts, intent, _ = policy_case()
    intent.requires_clarification = True
    intent.clarification_question = "你发现暗格里的紫月暗号。"
    plan = KeeperPlan(
        plan_id="p", cycle_id=facts.cycle_id, current_scene_id=facts.scene_id, parsed_intent=intent
    )
    verdict = ActionPolicyValidator().validate(intent, plan, facts, [])
    assert verdict.status == "clarification_required"
    assert "紫月" not in verdict.clarification_question


def test_natural_withdrawal_preserves_same_message_question(client, game):  # noqa: F811
    seen = []

    def response(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        schema = kwargs["response_schema"].__name__
        if schema == "KeeperNarration" and context["response_brief"].get("withdrawal"):
            seen.append(context)
            return {"npc_speech": {"entity_id": "caretaker", "text": "那会儿的声音，我记不准。"}}
        result = conversational(messages, kwargs)
        text = context.get("triggering_action", {}).get("payload", {}).get("text", "")
        if schema == "KeeperPlan" and "先听他说完" in text:
            result.update(
                pending_action="withdraw",
                focus={
                    "question": "先听他说完",
                    "addressee_id": "caretaker",
                },
            )
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "我想辨认那阵脚步"))
    wait_cycle(client, game)
    old = pending(client, game)
    ok(submit(client, game, "不掷了，先听他说完。"))
    assert wait_cycle(client, game)["status"] == "completed"
    assert seen and seen[0]["response_brief"]["withdrawal"]["withdrawn"]
    assert seen[0]["triggering_action"]["payload"]["text"] == "不掷了，先听他说完。"
    assert "recent_dialogue" not in seen[0] and "module" not in seen[0]
    checks = ok(client.get(game["prefix"] + "/checks"))
    assert next(c for c in checks if c["id"] == old["id"])["status"] == "cancelled"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert any(e["type"] == "npc.spoke" for e in events)


def test_mixed_npc_question_and_item_action_have_distinct_targets(client, game):  # noqa: F811
    seen = []

    def response(messages, kwargs):
        c = json.loads(messages[-1]["content"])
        schema = kwargs["response_schema"].__name__
        if schema == "KeeperNarration":
            seen.append(c["response_brief"])
            return {"npc_speech": {"entity_id": "apprentice", "text": "你们还用得上就先留着吧。"}}
        result = conversational(messages, kwargs)
        if schema == "KeeperPlan":
            result["focus"] = dict(
                question="手电要先拿回去吗？",
                addressee_id="apprentice",
                action="我把手电照向门边",
                action_target_id="square",
            )
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "小禾，手电要先拿回去吗？我把手电照向门边。"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed"
    assert seen[0]["responder"]["id"] == "apprentice"
    assert seen[0]["attempt"] == "我把手电照向门边"
    assert not ok(client.get(game["prefix"] + "/checks"))
