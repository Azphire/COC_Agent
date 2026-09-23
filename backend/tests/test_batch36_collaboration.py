"""Behavioral ownership, serial scheduling and durable task regressions."""

import json

import pytest
from agent_fixture_setup import bind_fixture_rules
from test_action_adjudication import plan_for
from test_agent_runtime import submit, wait_cycle
from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_module_navigation_runtime import running_navigation  # noqa: F401
from test_rooms import character, lobby, ok, prepare  # noqa: F401

from app.agents.adjudication_schemas import BehaviorState, TeammateDecision, TurnFocus
from app.agents.behavior import TeammateBehaviorPolicy
from app.agents.generation_contracts import utterance_clauses
from app.agents.model import FakeModelAdapter
from app.preparation.action_authority import action_kinds, freeze_action, requested_action_kinds
from app.preparation.runtime_schemas import ModuleRuntimeState
from app.preparation.turn_focus import repair_attribution


@pytest.mark.parametrize(
    "question",
    [
        "陈拓，你知道拉杆是做什么的吗？",
        "陈拓，这根杆应该怎么拉？",
        "陈拓，你觉得应该先动哪根？",
        "陈拓，你认为现在把门打开安全吗？",
    ],
)
def test_information_request_answers_without_operation(question):
    plan = plan_for(question, "interact", "panel")
    plan.focus = TurnFocus(action=question, addressee_id="chen")
    repair_attribution(plan, question, {"chen": "陈拓"}, "actor")
    authority = freeze_action(
        plan, question, "actor", 1, "scene", {}, ModuleRuntimeState(), {"chen": "陈拓"}
    )
    assert not requested_action_kinds(question)
    assert not authority["kinds"] and not plan.focus.action
    assert plan.focus.requests[0].kind == "question"
    policy = TeammateBehaviorPolicy()
    params = dict(
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text=question,
        player_intent=plan.parsed_intent,
        public_ids={"panel"},
        action_seq=1,
        fingerprint="same",
        information_request=True,
    )
    action = TeammateDecision(
        mode="act", action_text="我往上拉动手柄。", confidence=1, related_player_action_seq=1
    )
    assert not policy.validate(action, **params).accepted
    answer = TeammateDecision(
        mode="speak",
        speech_text="标记说明向上减速，但我不清楚车外情况。",
        confidence=1,
        related_player_action_seq=1,
    )
    assert policy.validate(answer, **params).accepted


@pytest.mark.parametrize(
    "raw",
    [
        "周岚，照看乘务员，陈拓，帮我找黑包，我去门口看看。",
        "请周岚照顾乘务员；陈拓，能帮我检查一下黑包吗？我在门口观察。",
    ],
)
def test_multiple_requests_cannot_borrow_each_others_action(raw):
    plan = plan_for(raw, "observe", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene")
    repair_attribution(plan, raw, {"zhou": "周岚", "chen": "陈拓"}, "actor")
    requests = {r.addressee_id: r for r in plan.focus.requests}
    assert "黑包" not in requests["zhou"].text
    assert requests["chen"].kind == "delegate" and "search" in requests["chen"].operations
    assert "照看" not in requests["chen"].text
    assert plan.focus.action.startswith("我") and "门口" in plan.focus.action
    assert all(name not in plan.focus.action for name in ["周岚", "陈拓", "黑包"])
    assert "control" not in action_kinds("我观察拉杆。")
    assert "control" in action_kinds("我拉动右侧拉杆。")


def test_generated_request_cannot_reassign_speakers_own_attempt():
    from app.agents.adjudication_schemas import TurnRequest

    raw = "我搜索黑包。"
    plan = plan_for(raw, "investigate")
    plan.focus = TurnFocus(
        action=raw,
        question=raw,
        addressee_id="zhou",
        requests=[TurnRequest(kind="question", addressee_id="zhou", text=raw, source_end=len(raw))],
    )
    repair_attribution(plan, raw, {"zhou": "周岚", "chen": "陈拓"}, "chen")
    assert not plan.focus.requests and plan.focus.action == raw
    assert plan.parsed_intent.type == "investigate"


@pytest.mark.parametrize("raw", ["我打开门，走进前一节车厢。", "我现在就过去，离开这节车厢。"])
def test_continued_first_person_movement_is_not_a_teammate_request(raw):
    from app.agents.action_policy import explicit_movement
    from app.agents.adjudication_schemas import TurnRequest

    boundary = raw.index("，") + 1
    plan = plan_for(raw, "move", "exit")
    plan.focus = TurnFocus(
        action=raw[:boundary],
        question=raw[boundary:],
        addressee_id="chen",
        requests=[
            TurnRequest(
                kind="delegate",
                addressee_id="chen",
                text=raw[boundary:],
                source_start=boundary,
                source_end=len(raw),
            )
        ],
    )
    repair_attribution(plan, raw, {"chen": "陈拓"}, "player")
    assert not plan.focus.requests and plan.focus.action == raw
    assert explicit_movement(plan.focus.action) and plan.parsed_intent.type == "move"


def test_npc_mentioned_as_care_target_cannot_steal_teammate_request():
    raw = "请周岚照顾乘务员；陈拓，能帮我检查一下附近有没有黑包吗？我在门口观察。"
    plan = plan_for(raw, "observe", "scene")
    plan.focus = TurnFocus(
        action="我在门口观察。",
        question="请周岚照顾乘务员；",
        addressee_id="npc",
        action_target_id="scene",
    )
    repair_attribution(
        plan, raw, {"zhou": "周岚", "chen": "陈拓", "npc": "乘务员"}, "actor", npc_ids={"npc"}
    )
    assert {r.addressee_id for r in plan.focus.requests} == {"zhou", "chen"}
    assert plan.focus.action == "我在门口观察。"
    assert plan.focus.addressee_id == "zhou"


def test_first_person_local_inspection_does_not_become_npc_question():
    raw = "我过去看看有没有黑包。"
    plan = plan_for(raw, "converse", "scene")
    plan.focus = TurnFocus(question=raw, addressee_id="npc", action_target_id="scene")
    repair_attribution(plan, raw, {"chen": "陈拓", "npc": "乘务员"}, "chen", npc_ids={"npc"})
    assert plan.focus.action == raw and not plan.focus.requests
    assert not plan.focus.question and plan.parsed_intent.type == "observe"


def test_plain_care_instruction_does_not_require_an_information_answer():
    from app.agents.adjudication_schemas import TurnRequest

    raw = "周岚，照看乘务员。"
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(
        question="照看乘务员。",
        addressee_id="zhou",
        requests=[
            TurnRequest(
                kind="question",
                addressee_id="zhou",
                text="照看乘务员。",
                source_start=3,
                source_end=len(raw),
            )
        ],
    )
    repair_attribution(plan, raw, {"zhou": "周岚", "npc": "乘务员"}, "actor")
    assert plan.focus.requests[0].kind == "delegate"
    assert plan.focus.requests[0].target_id == "npc"


def test_polite_question_misclassified_by_model_still_delegates():
    from app.agents.adjudication_schemas import TurnRequest

    raw = "陈拓，能帮我检查一下操作面板吗？"
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(
        requests=[
            TurnRequest(
                kind="question",
                addressee_id="chen",
                text=raw[3:],
                source_start=3,
                source_end=len(raw),
            )
        ]
    )
    repair_attribution(plan, raw, {"chen": "陈拓"}, "actor")
    assert plan.focus.requests[0].kind == "delegate"
    assert not plan.focus.action and not plan.proposed_check


def test_npc_answers_preserve_question_and_source_binding_in_published_text():
    from app.agents.action_runtime import generation_prompt
    from app.agents.adjudication_schemas import KeeperNarration
    from app.agents.generation_contracts import generation_contract, restore_output
    from app.models.base import ModelFormatError

    context = {
        "current_scene_reference": "scene",
        "PUBLIC_CLAIM_OPTIONS": [],
        "response_brief": {
            "responder": {"id": "npc", "kind": "npc", "portrayal": "腿部受伤。"},
            "question": "包在哪里？谁拿了钥匙？",
            "questions": ["包在哪里？", "谁拿了钥匙？"],
            "allowed_facts": [{"id": "bag", "text": "黑包落在门边。"}],
            "player_statement": "也许钥匙在箱里。包在哪里？谁拿了钥匙？",
        },
    }
    schema = generation_contract(KeeperNarration, context)
    output = {
        "npc_speech": {
            "answers": [
                {
                    "question_index": 0,
                    "evidence_quote": "黑包落在门边。",
                    "certainty": "sourced",
                    "text": "我的包掉在门边了。",
                },
                {
                    "question_index": 1,
                    "evidence_quote": "",
                    "certainty": "unknown",
                    "text": "钥匙被谁拿走了，我不清楚。",
                },
            ]
        }
    }
    restored = restore_output(schema.model_validate(output), KeeperNarration, context)
    assert restored.npc_speech.text == "我的包掉在门边了。\n钥匙被谁拿走了，我不清楚。"
    prompt = generation_prompt(context, KeeperNarration)
    assert prompt["response_brief"]["player_statement"].startswith("也许")
    output["npc_speech"]["answers"][1].update(
        evidence_quote="钥匙藏在黑包里的箱子。", certainty="sourced"
    )
    with pytest.raises(ModelFormatError):
        schema.model_validate(output)


async def test_failed_model_validation_keeps_usage_and_rejected_output():
    import httpx
    from pydantic import BaseModel, model_validator

    from app.config import Settings
    from app.models.base import ModelFormatError
    from app.models.ollama import OllamaAgentAdapter

    class Rejected(BaseModel):
        text: str

        @model_validator(mode="after")
        def reject(self):
            raise ModelFormatError("source mismatch", [{"field": "text", "code": "source"}])

    adapter = OllamaAgentAdapter(Settings(_env_file=None))
    await adapter.client.aclose()
    adapter.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "message": {"content": '{"text":"未经证实的输出"}', "thinking": "hidden"},
                    "prompt_eval_count": 100,
                    "eval_count": 12,
                },
            )
        ),
        base_url="http://localhost:11434",
    )
    try:
        with pytest.raises(ModelFormatError) as error:
            await adapter.generate([], response_schema=Rejected)
        assert error.value.token_usage == {"input": 100, "output": 12}
        assert error.value.generated_output == {"text": "未经证实的输出"}
    finally:
        await adapter.close()


@pytest.fixture
def team_game(client, lobby):  # noqa: F811
    prefix = lobby["prefix"]
    sheet = character(client, "陈拓")
    room = ok(client.post(prefix + "/character-slots", json={"character_id": sheet["id"]}))["room"]
    slot = next(s for s in room["character_slots"] if s["source_character_id"] == sheet["id"])
    room = ok(
        client.post(prefix + "/members", json={"display_name": "陈拓", "controller_type": "agent"})
    )["room"]
    chen = next(m["id"] for m in room["members"] if m["display_name"] == "陈拓")
    ok(
        client.post(
            prefix + "/character-assignments", json={"slot_id": slot["id"], "member_id": chen}
        )
    )
    ok(client.post(prefix + "/ready", json={"ready": True, "member_id": chen}))
    prepare(client, lobby)
    ok(client.post(prefix + "/pause"))
    ok(client.post(prefix + "/module", json={"module_id": "stopped-clock"}))
    for role, member, name in [
        ("keeper", room["host_member_id"], "KP"),
        ("investigator", lobby["agent"], "占位队友"),
        ("investigator", chen, "陈拓"),
    ]:
        profile = ok(client.post("/api/agent-profiles", json={"role": role, "name": name}), 201)
        ok(
            client.post(
                prefix + "/agent-bindings", json={"member_id": member, "profile_id": profile["id"]}
            )
        )
    bind_fixture_rules(client, prefix)
    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=responder)
    ok(client.post(prefix + "/resume"))
    return {**lobby, "chen": chen}


def responder(messages, kwargs):
    context = json.loads(messages[-1]["content"])
    schema = kwargs["response_schema"].__name__
    if schema == "KeeperPlan":
        raw = context["triggering_action"]["payload"]["text"]
        clauses = utterance_clauses(raw)
        return {
            "parsed_intent": {"type": "observe"},
            "focus": {
                "action_clause_ids": [c["id"] for c in clauses if c["text"].startswith("我")],
                "action_target_id": context["action_identifiers"]["current_scene_id"],
            },
        }
    if schema == "KeeperNarration":
        return {"public_narration": "你观察入口，暂时没看见有人靠近。"}
    if schema == "TeammateDecision":
        req = context.get("addressed_requests", [])
        if not req:
            return {
                "mode": "pass",
                "related_player_action_seq": context["triggering_action"]["seq"],
                "confidence": 1,
            }
        question = req[0]["kind"] == "question"
        return {
            "mode": "speak" if question else "assist",
            "action_type": "observe",
            "action_text": None if question else "我查看入口。",
            "speech_text": "我不清楚拉杆用途，先读说明比较稳妥。" if question else None,
            "related_player_action_seq": context["triggering_action"]["seq"],
            "confidence": 1,
        }
    return {"content": "调查员查看入口，尚未确认新的线索。"}


def test_two_direct_requests_execute_serially_and_information_does_not_act(client, team_game):
    svc = client.app.state.agent_service
    svc.model.adapter = FakeModelAdapter(responder=responder)
    ok(
        submit(
            client, team_game, "占位队友，帮我查看入口。陈拓，你知道拉杆有什么用吗？我观察门口。"
        )
    )
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any(
        e["type"] == "agent.action_proposed" and e["actor_member_id"] == team_game["agent"]
        for e in events
    )
    assert any(
        e["type"] == "agent.spoke" and e["actor_member_id"] == team_game["chen"] for e in events
    )
    assert not any(
        e["type"] == "agent.action_proposed" and e["actor_member_id"] == team_game["chen"]
        for e in events
    )
    assert any(e["type"] == "agent.teammate_task_updated" for e in events)
    assert not any(e["type"] == "check.requested" for e in events)
    assert (
        sum(
            e["type"] == "agent.teammate_decision" and not e["payload"]["deterministically_skipped"]
            for e in events
        )
        == 2
    )


def test_generation_failure_retains_request_without_role_refusal_and_restore(client, team_game):
    from app.models.base import ModelError

    svc = client.app.state.agent_service

    def failing(messages, kwargs):
        if kwargs["response_schema"].__name__ == "TeammateDecision":
            raise ModelError("budget test failure")
        return responder(messages, kwargs)

    svc.model.adapter = FakeModelAdapter(responder=failing)
    ok(submit(client, team_game, "陈拓，帮我查看入口。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    behavior = ok(client.get(team_game["prefix"] + "/teammate-behavior"))
    encoded = json.dumps(behavior, ensure_ascii=False)
    assert "generation_failed" in encoded and "pending_requests" in encoded
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert not any(e["type"] in {"agent.spoke", "agent.action_proposed"} for e in events)
    snapshot = ok(
        client.post(team_game["prefix"] + "/snapshots", json={"name": "pending request"})
    )["snapshot"]
    ok(client.post(team_game["prefix"] + "/pause"))
    ok(client.post(team_game["prefix"] + f"/snapshots/{snapshot['id']}/load"))
    assert ok(client.get(team_game["prefix"] + "/teammate-behavior")) == behavior
    svc.model.adapter = FakeModelAdapter(responder=responder)
    ok(client.post(team_game["prefix"] + "/resume"))
    ok(submit(client, team_game, "我观察四周。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "agent.action_proposed" for e in events) == 1


def test_door_opinion_repairs_to_answer_instead_of_unheld_tool_inventory(client, team_game):
    attempts = []

    def advice(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ != "TeammateDecision" or not context.get(
            "addressed_requests"
        ):
            return responder(messages, kwargs)
        attempts.append(context)
        decision = {
            "mode": "assist",
            "action_text": "我用撬棍把门撬开。",
            "speech_text": "我有工具，可以撬门。",
            "confidence": 1,
            "related_player_action_seq": context["triggering_action"]["seq"],
        }
        if context.get("behavior_rejection"):
            assert context["behavior_rejection"]["reason"] == "question_requires_answer_not_action"
            decision.update(
                mode="speak",
                action_text=None,
                speech_text="光听响声不能确定是否锁着，先看门缝和锁舌，避免硬撬发出声音。",
            )
        return decision

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=advice)
    ok(submit(client, team_game, "陈拓，你觉得这门是锁着还是被东西卡住了？有没有安静点的办法？"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    said = [e["payload"]["text"] for e in events if e["type"] == "agent.spoke"]
    assert len(attempts) == 2 and len(said) == 1 and "门缝和锁舌" in said[0]
    assert not any(e["type"] in {"agent.action_proposed", "check.requested"} for e in events)
    assert not any("随身物" in text for text in said)


def test_information_grammar_uses_shared_request_without_changing_persisted_schema():
    from app.agents.generation_contracts import generation_contract
    from app.preparation.inventory import inventory_question

    context = {"triggering_action": {"seq": 1}, "addressed_requests": [{"kind": "question"}]}
    grammar = generation_contract(TeammateDecision, context).model_json_schema()
    assert grammar["properties"]["mode"]["enum"] == ["speak"]
    context["addressed_requests"] = [{"kind": "delegate"}]
    assert (
        "assist"
        in generation_contract(TeammateDecision, context).model_json_schema()["properties"]["mode"][
            "enum"
        ]
    )
    assert "act" in TeammateDecision.model_json_schema()["properties"]["mode"]["enum"]
    inventory = {"known_items": [{"names": ["铜灯"]}]}
    assert inventory_question("你有铜灯吗？", inventory)
    assert not inventory_question("你知道铜灯有什么用途吗？", inventory)
    assert not inventory_question("你觉得能用铜灯挡住门吗？", inventory)


def test_new_question_does_not_execute_or_discard_an_older_pending_assignment(client, team_game):
    from app.models.base import ModelError

    service = client.app.state.agent_service

    def failing(messages, kwargs):
        if kwargs["response_schema"].__name__ == "TeammateDecision":
            raise ModelError("temporary generation failure")
        return responder(messages, kwargs)

    service.model.adapter = FakeModelAdapter(responder=failing)
    ok(submit(client, team_game, "陈拓，帮我查看入口。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    before = next(
        s
        for s in ok(client.get(team_game["prefix"] + "/teammate-behavior"))
        if s["member_id"] == team_game["chen"]
    )
    service.model.adapter = FakeModelAdapter(responder=responder)
    ok(submit(client, team_game, "陈拓，你知道拉杆有什么用吗？"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    after = next(
        s
        for s in ok(client.get(team_game["prefix"] + "/teammate-behavior"))
        if s["member_id"] == team_game["chen"]
    )
    assert (
        after["pending_requests"] == before["pending_requests"]
        and after["task_status"] == "pending"
    )
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any(e["type"] == "agent.spoke" for e in events)
    assert not any(e["type"] == "agent.action_proposed" for e in events)
    ok(submit(client, team_game, "我观察四周。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "agent.action_proposed" for e in events) == 1


def test_restore_excludes_abandoned_dialogue_from_final_narration_prompt(client, team_game):
    svc = client.app.state.agent_service
    seen = []

    def recording(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            seen.append(json.loads(messages[-1]["content"]))
        return responder(messages, kwargs)

    svc.model.adapter = FakeModelAdapter(responder=recording)
    snapshot = ok(client.post(team_game["prefix"] + "/snapshots", json={"name": "before"}))[
        "snapshot"
    ]
    ok(submit(client, team_game, "我观察被撤销的红色花瓶。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    ok(client.post(team_game["prefix"] + "/pause"))
    ok(client.post(team_game["prefix"] + f"/snapshots/{snapshot['id']}/load"))
    ok(client.post(team_game["prefix"] + "/resume"))
    ok(submit(client, team_game, "我查看门口。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    assert seen and "被撤销" not in json.dumps(seen[-1]["response_brief"], ensure_ascii=False)


def test_rejected_narration_claim_cannot_crash_partial_fallback(client, team_game):
    def invalid_claim(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            return {"public_narration": "我找到了不存在的物件。", "claim_ids": ["unknown"]}
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=invalid_claim)
    ok(submit(client, team_game, "我查看门口。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    narration = [e["payload"] for e in events if e["type"] == "keeper.narration"]
    assert len(narration) == 1 and narration[0]["safe_fallback"]
    assert "不存在的物件" not in narration[0]["text"]


def test_restore_keeps_future_disclosure_as_history_even_in_same_scene(
    client,
    running_navigation,  # noqa: F811
):
    from app.module_ir.facts import relevant_public_facts

    data = running_navigation
    svc = client.app.state.agent_service
    prefix = data["room_prefix"]
    saved = ok(client.post(prefix + "/snapshots", json={"name": "before disclosure"}))["snapshot"]
    entity_id = data["entities"][2]["id"]

    async def reveal():
        async def operation(session, room):
            await svc.entities.reveal(
                session, room, entity_id, room.host_member_id, host_override=True
            )

        await svc.mutate(data["room"]["id"], operation)

    client.portal.call(reveal)
    before = ok(client.get(prefix))["game"]["public_entities"]
    assert next(e for e in before if e["id"] == entity_id)["fact_scope"] == "current_scene"
    ok(client.post(prefix + "/pause"))
    ok(client.post(prefix + f"/snapshots/{saved['id']}/load"))
    after = ok(client.get(prefix))["game"]["public_entities"]
    assert next(e for e in after if e["id"] == entity_id)["fact_scope"] == "historical"
    assert entity_id not in {e["id"] for e in relevant_public_facts(after, "我观察四周。")}
    events = ok(client.get(prefix + "/events"))["events"]
    assert any(e["type"] == "entity.revealed" and e["seq"] > saved["event_seq"] for e in events)
