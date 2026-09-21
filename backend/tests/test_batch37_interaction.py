"""Regressions for the batch-36 action loss and current-turn ownership failures."""

import json

import pytest
from test_action_adjudication import plan_for
from test_agent_runtime import submit, wait_cycle
from test_batch36_collaboration import responder, team_game  # noqa: F401
from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_module_navigation_runtime import running_navigation  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.action_policy import named_move_exits
from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan, TurnFocus, TurnRequest
from app.agents.generation_contracts import generation_contract, restore_output
from app.agents.model import FakeModelAdapter
from app.preparation.turn_focus import repair_attribution


def test_prepared_entities_without_optional_checks_can_start_a_turn(
    client, running_navigation, monkeypatch,  # noqa: F811
):
    from test_module_navigation_runtime import act

    service = client.app.state.agent_service
    original = service.entities.host

    async def without_optional_checks(*args, **kwargs):
        return [{k: v for k, v in e.items() if k != "suggested_checks"}
                for e in await original(*args, **kwargs)]

    monkeypatch.setattr(service.entities, "host", without_optional_checks)
    service.model.adapter = FakeModelAdapter(responder=responder)
    assert act(client, running_navigation, "我看看四周。")['status'] == 'completed'


@pytest.mark.parametrize("text,kind", [
    ("咱们别走散了。", "question"), ("先找人吧。", "question"),
    ("咱们先去找人吧。", "delegate"),
])
def test_group_intention_is_not_a_pending_teammate_question(text, kind):
    from app.preparation.turn_focus import bind_requests, question_request

    focus = TurnFocus(question=text, addressee_id="nurse", requests=[TurnRequest(
        kind=kind, addressee_id="nurse", text=text,
        source_start=0, source_end=len(text),
    )])
    assert not bind_requests(focus, text, {"nurse": "周岚", "player": "林知秋"}, "player")
    assert not question_request(text, "周岚")
    assert question_request("他现在怎么样了？", "周岚")
    assert question_request("周岚，辛苦了。", "周岚")


def test_automatic_local_target_is_visible_before_its_real_check(client, running_navigation):  # noqa: F811
    from test_module_navigation_runtime import act

    d = running_navigation
    target = d["entities"][3]["id"]

    def answer(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            return {
                "parsed_intent": {"type": "move"},
                "focus": {"action_clause_ids": ["u1"], "action_target_id": target,
                          "obstacle": "需要安静通过守卫所在区域。"},
                "proposed_check": {"kind": "skill", "name": "stealth",
                                   "difficulty": "regular", "target_entity_id": target,
                                   "basis_entity_id": target,
                                   "success_effect": "安静通过守卫所在区域。",
                                   "failure_consequence": "脚步声惊动守卫，未能安静通过。",
                                   "transition_id": d["forward"]["transition_id"]},
                "proposed_reveal_entity_ids": [target],
                "proposed_transition_id": d["forward"]["transition_id"],
            }
        if kwargs["response_schema"].__name__ == "TeammateDecision":
            return {"mode": "pass", "confidence": 1,
                    "related_player_action_seq": context["triggering_action"]["seq"]}
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    cycle = act(client, d, "我悄悄走进Future，尽量不惊动守卫。")
    trace = [json.loads(line) for line in client.get(d["room_prefix"] + "/logs").text.splitlines()]
    assert cycle["status"] == "waiting_for_roll", [
        e["payload"] for e in trace if e["type"] == "agent.action_validated"
    ]
    events = ok(client.get(d["room_prefix"] + "/events"))["events"]
    reveal = next(e for e in events if e["type"] == "entity.revealed"
                  and e["payload"].get("id") == target)
    requested = next(e for e in events if e["type"] == "check.requested")
    assert reveal["seq"] < requested["seq"]
    checks = ok(client.get(d["room_prefix"] + "/checks"))
    assert len(checks) == 1 and checks[0]["status"] == "pending"
    assert not checks[0].get("result")


def test_route_method_does_not_reopen_an_already_settled_obstacle():
    from app.preparation.adjudication import settled_route_obstacle
    from app.preparation.runtime_schemas import ModuleRuntimeState

    plan = plan_for("我趁空当走进前门。", "move", "next")
    plan.proposed_transition_id = "route"
    plan.action_authority = {"route": {
        "transition_id": "route", "required_flags": {"window": True},
    }}
    runtime = ModuleRuntimeState(flags={"window": True})
    assert settled_route_obstacle(plan, runtime)
    runtime.flags["window"] = False
    assert not settled_route_obstacle(plan, runtime)
    runtime.flags["window"] = True
    plan.proposed_transition_id = "another-route"
    assert not settled_route_obstacle(plan, runtime)


@pytest.mark.parametrize("intent, text, wrong_clue", [
    ("observe", "我过去看看车门有没有异常。", False),
    ("move", "我过去看看车门情况。", False),
    ("observe", "我过去看看车门情况。", True),
])
def test_local_door_inspection_reaches_public_detail_without_a_party_move(
    client, running_navigation, intent, text, wrong_clue,  # noqa: F811
):
    from test_module_navigation_runtime import act

    d = running_navigation

    def answer(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        schema = kwargs["response_schema"].__name__
        if schema == "KeeperPlan":
            return {
                "parsed_intent": {"type": intent},
                "focus": {"action_clause_ids": ["u1"],
                          "action_target_id": (d["entities"][3]["id"] if wrong_clue else
                                               d["forward"]["target_scene_node_id"]
                                               if intent == "move" else
                                               context["action_identifiers"]["current_scene_id"])},
                "proposed_transition_id": d["forward"]["transition_id"],
                "proposed_reveal_entity_ids": [d["entities"][3 if wrong_clue else 1]["id"]],
            }
        if schema == "KeeperNarration":
            return {"observed_detail": "门面漆层边角有些磨损，门框附近没有明显的新痕迹。"}
        if schema == "TeammateDecision":
            return {"mode": "pass", "confidence": 1,
                    "related_player_action_seq": context["triggering_action"]["seq"]}
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    cycle = act(client, d, text)
    assert cycle["status"] == "completed" and not cycle["requires_clarification"]
    events = ok(client.get(d["room_prefix"] + "/events"))["events"]
    assert any(e["type"] == "keeper.narration" and "门面漆层" in e["payload"]["text"]
               for e in events)
    assert sum(e["type"] == "scene.updated" for e in events) == 1
    assert not ok(client.get(d["room_prefix"] + "/checks"))


def test_new_npc_question_about_just_now_does_not_replay_old_dialogue():
    from app.memory.facts import readonly_recall

    assert not readonly_recall(
        "我蹲在受伤的人旁边问：您听得见吗？刚才发生了什么，列车为什么一直不停？"
    )
    assert readonly_recall("回顾一下，乘务员刚才说过什么？请复述原话。")


def test_ordinary_observation_keeps_matching_memory_without_replaying_old_search():
    from app.agents.action_runtime import generation_prompt

    context = {"response_brief": {
        "ordinary_observation": True, "attempt": "我过去看看车门情况。",
        "responder": {"kind": "keeper"},
        "incidental_memories": [
            {"text": "车门边缘留着磨痕。", "fact_scope": "current_scene"},
            {"text": "你翻开报纸，读到列车惨案。", "fact_scope": "current_scene"},
        ],
        "allowed_facts": [{"id": "scene", "text": "空车厢。"},
                          {"id": "paper", "text": "报纸记载惨案。"}],
        "recent_dialogue": [{"type": "action.submitted", "text": "我去车门，翻翻报纸。"}],
    }}
    sent = generation_prompt(context, KeeperNarration)["response_brief"]
    assert [d["text"] for d in sent["incidental_memories"]] == ["车门边缘留着磨痕。"]
    assert not sent["recent_dialogue"]
    assert [d["id"] for d in sent["allowed_facts"]] == ["scene"]
    assert len(context["response_brief"]["incidental_memories"]) == 2


def test_arrival_prompt_preserves_actual_scene_result_instead_of_previous_narration():
    from app.agents.action_runtime import generation_prompt

    context = {"response_brief": {
        "responder": {"kind": "keeper"},
        "current_scene": {"id": "next", "public_description": "一名伤者倒在地上。"},
        "recent_dialogue": [{"text": "旧车厢空无一人。"}],
        "incidental_memories": [{"text": "旧车厢空无一人。", "fact_scope": "current_scene"}],
    }, "public_tool_results": {"events": [{"type": "scene.updated",
                                           "payload": {"scene_summary": "一名伤者倒在地上。"}}]}}
    sent = generation_prompt(context, KeeperNarration)
    assert not sent["response_brief"]["recent_dialogue"]
    assert not sent["response_brief"]["incidental_memories"]
    assert sent["public_tool_results"] == context["public_tool_results"]
    assert sent["response_brief"]["current_scene"] == context["response_brief"]["current_scene"]


@pytest.mark.parametrize("result_kind, accepted", [("generation_failed", True), ("blocked", False)])
def test_unexecuted_request_can_retry_but_real_failure_is_not_erased(result_kind, accepted):
    from app.agents.adjudication_schemas import BehaviorState, Cooldown, TeammateDecision
    from app.agents.behavior import TeammateBehaviorPolicy

    text = "我过去看看车门有没有异常。"
    decision = TeammateDecision(mode="act", action_type="investigate", action_text=text,
                                target_id="door", related_player_action_seq=2, confidence=1)
    state = BehaviorState(
        task_status=result_kind, last_result={"kind": result_kind},
        pending_requests=[{"key": "1:0", "text": "请你检查车门。", "kind": "delegate"}],
        cooldowns=[Cooldown(action_type="investigate", target_id="door",
                            remaining_cycles=2, state_fingerprint="same")],
    )
    result = TeammateBehaviorPolicy().validate(
        decision, state=state, recent_outputs=[text], other_outputs=[],
        player_text="陈拓，请你检查车门。", player_intent=None, public_ids={"door"},
        action_seq=2, fingerprint="same", explicit_action_request=True,
        requested_operations=["observe"],
    )
    assert result.accepted is accepted


@pytest.mark.parametrize("promise", [
    "乘务员师傅，我先帮您处理伤口，我得先止血。",
    "好的，我立刻过去帮他处理伤口。",
])
def test_promised_treatment_after_salutation_requires_characters_own_attempt(promise):
    from app.agents.adjudication_schemas import BehaviorState, TeammateDecision
    from app.agents.behavior import TeammateBehaviorPolicy

    decision = TeammateDecision(
        mode="speak",
        speech_text=promise,
        related_player_action_seq=1,
        confidence=1,
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text="周岚，请你帮乘务员止血。",
        player_intent=None,
        public_ids=set(),
        action_seq=1,
        fingerprint="same",
        explicit_action_request=True,
        requested_operations=["first_aid"],
    )
    assert result.accepted
    assert decision.mode == "act" and decision.action_text.startswith("我")
    assert decision.goal_status == "continue"  # a queued attempt, not a completed task


def test_stop_bleeding_uses_existing_treatment_route_only_for_the_actual_actor():
    from types import SimpleNamespace

    from app.rooms.combat_service import CombatService

    room = SimpleNamespace(session_state={})
    assert CombatService.route(room, "我立刻帮乘务员止血。")
    assert not CombatService.route(
        room,
        "周岚，请你帮乘务员止血。我问乘务员：您听得见吗？",
        members={"player": "林知秋", "nurse": "周岚"},
        actor="player",
    )


@pytest.mark.parametrize("raw", [
    "这节也没人……我蹲下翻看座位间遗留的东西，看看能不能弄明白这里发生过什么。",
    "我翻翻座位旁留下的东西，说不定有乘客落下的线索。",
])
def test_prefaced_attempt_is_not_reassigned_to_an_unnamed_teammate(client, team_game, raw):  # noqa: F811
    from app.agents.generation_contracts import utterance_clauses
    from app.preparation.action_authority import speaker_action

    assert speaker_action("他说：“等等……我检查门。”") is None

    def answer(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            ids = [c["id"] for c in utterance_clauses(raw)]
            return {
                "parsed_intent": {"type": "investigate"},
                "focus": {
                    "action_clause_ids": ids,
                    "action_target_id": context["action_identifiers"]["current_scene_id"],
                    "addressee_id": team_game["chen"],
                    "requests": [
                        {"kind": "question", "addressee_id": team_game["chen"], "clause_ids": ids}
                    ],
                },
            }
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            return {"observed_detail": "遗留物表面蒙着灰，有几片边角发皱的纸屑。"}
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    ok(submit(client, team_game, raw))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any(e["type"] == "keeper.narration" and "纸屑" in e["payload"]["text"] for e in events)
    assert not any(e["type"] == "agent.spoke" for e in events)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("我朝车头方向走。", "front"),
        ("我进入前一节车厢。", "front"),
        ("我退回刚才那节车厢。", "back"),
        ("我走向另一端。", "front"),
        ("我不去车头。", None),
        ("他建议我朝车头走。", None),
    ],
)
def test_direction_uses_edges_and_previous_scene_not_number_order(raw, expected):
    exits = [
        {
            "transition_id": "back",
            "target_public_title": "甲室",
            "target_description": "后方的隔间。",
            "is_previous_scene": True,
        },
        {
            "transition_id": "front",
            "target_public_title": "乙室",
            "target_description": "地板有灰尘。",
            "is_previous_scene": False,
        },
    ]
    assert [e["transition_id"] for e in named_move_exits(raw, exits)] == (
        [expected] if expected else []
    )


def test_corridor_axis_survives_returning_from_either_side_and_stops_at_junction():
    from app.agents.action_policy import corridor_directions

    edges = [{"source_scene_node_id": a, "target_scene_node_id": b}
             for a, b in [("rear", "73"), ("73", "2"), ("2", "head")]]
    directions = corridor_directions(edges, {"rear": ("尾室", "后方的隔间。")})
    assert directions[("73", "2")] == "forward"
    assert directions[("2", "73")] == "backward"
    exits = [
        {"transition_id": "back", "target_public_title": "73室", "direction": "backward"},
        {"transition_id": "front", "target_public_title": "先头", "direction": "forward",
         "is_previous_scene": True},
    ]
    assert named_move_exits("我继续朝车头走。", exits) == [exits[1]]
    assert named_move_exits("我退回刚才那里。", exits) == [exits[1]]
    edges.append({"source_scene_node_id": "2", "target_scene_node_id": "side"})
    branched = corridor_directions(edges, {"rear": ("尾室", "后方的隔间。")})
    assert ("2", "head") not in branched and ("2", "side") not in branched
    conflicting = corridor_directions(edges[:3], {
        "rear": ("尾室", "后方的隔间。"), "head": ("另一尾室", "后方的隔间。"),
    })
    assert not conflicting


def test_complete_move_survives_redundant_scene_reveal_and_model_clarification():
    raw = "我现在走进3号车厢。"
    plan = plan_for(raw, "move", "there")
    plan.focus = TurnFocus(action=raw, action_target_id="there")
    plan.parsed_intent.requires_clarification = True
    plan.proposed_reveal_entity_ids = ["here", "destination-scene"]
    plan.proposed_transition_id = "exit"
    restored = restore_output(
        plan,
        KeeperPlan,
        {
            "triggering_action": {"payload": {"text": raw}},
            "action_identifiers": {"current_scene_id": "here", "actor_member_id": "actor"},
            "approved_exits": [
                {
                    "transition_id": "exit",
                    "target_scene_node_id": "there",
                    "target_entity_id": "destination-scene",
                    "target_public_title": "3号车厢",
                }
            ],
        },
    )
    assert not restored.parsed_intent.requires_clarification
    assert not restored.proposed_reveal_entity_ids
    assert restored.focus.action == raw
    assert restored.focus.action_target_id == "there"


@pytest.mark.parametrize("opening", ["我可以帮你做些什么？", "我能帮你做些什么？"])
def test_first_person_question_is_not_split_from_nurse_addressee(opening):
    raw = "周岚，" + opening + "他的情况有没有办法先稳定下来？"
    plan = plan_for(raw, "assist", "npc")
    plan.focus = TurnFocus(
        action=raw[3:],
        question=raw[3:],
        addressee_id="zhou",
        requests=[TurnRequest(addressee_id="zhou", text=raw[:3], source_start=0, source_end=3)],
    )
    repair_attribution(plan, raw, {"zhou": "周岚", "npc": "乘务员"}, "player")
    assert not plan.focus.action
    assert plan.focus.requests[0].text == raw
    assert plan.focus.requests[0].kind == "question"


@pytest.mark.parametrize("raw", [
    "周岚，麻烦你帮他止血。我蹲在乘务员旁边问：您听得见吗？是谁伤了您？",
    "周岚，快帮这位师傅处理一下腿上的伤口。我蹲在他旁边问：师傅，您听得见吗？是谁把您伤成这样的？",
])
def test_narrated_npc_question_keeps_its_owner_after_a_nurse_request(raw):
    question = raw[raw.index("我蹲"):]
    plan = plan_for(raw, "interact", "npc")
    plan.focus = TurnFocus(
        action=raw[3:raw.index("我蹲")], question=question, addressee_id="zhou",
        requests=[TurnRequest(
            kind="question", addressee_id="zhou", text=question,
            source_start=raw.index(question), source_end=len(raw),
        )],
    )
    repair_attribution(plan, raw, {"zhou": "周岚", "npc": "乘务员"}, "player", npc_ids={"npc"})
    assert not plan.focus.action and not plan.proposed_check
    assert [(r.addressee_id, r.kind) for r in plan.focus.requests] == [
        ("zhou", "delegate"), ("npc", "question"),
    ]
    assert plan.focus.requests[1].text == question


def test_npc_generation_only_receives_its_own_current_question():
    from app.agents.action_runtime import generation_prompt
    from app.agents.narration import response_brief

    current = "我问乘务员：怎么进驾驶室？"
    raw = "周岚，他能挪动吗？" + current
    plan = plan_for(raw, "converse", "npc")
    plan.focus = TurnFocus(question=raw, addressee_id="npc", requests=[
        TurnRequest(addressee_id="zhou", text=raw[:raw.index(current)]),
        TurnRequest(addressee_id="npc", text=current),
    ])
    context = {
        "triggering_action": {"seq": 1, "payload": {"text": raw}},
        "public_entities": [{"id": "npc", "title": "乘务员", "type": "npc",
                             "public_summary": "腿部受伤。"}],
        "current_participants": {"members": {"zhou": "周岚"}},
        "recent_dialogue": [{"type": "action.submitted", "text": "旧问题"},
                            {"type": "npc.spoke", "text": "旧答案把钥匙说成还在身上"}],
    }
    context["response_brief"], _ = response_brief(plan, context, {"events": []})
    sent = generation_prompt(context, KeeperNarration)
    assert sent["triggering_action"]["payload"]["text"] == current
    assert "旧问题" not in json.dumps(sent, ensure_ascii=False)
    assert "旧答案" not in json.dumps(sent, ensure_ascii=False)
    assert "周岚" in context["triggering_action"]["payload"]["text"]


def test_object_inspection_cannot_be_replaced_by_the_room_introduction():
    from app.models.base import ModelFormatError

    context = {"current_scene_reference": "scene", "response_brief": {
        "ordinary_observation": True, "attempt": "我检查钥匙是否完好。",
        "observation_subject": {"id": "keys", "type": "item", "title": "钥匙"},
        "current_scene": {"public_description": "行李散落满地，前方通往另一节车厢。"},
    }}
    schema = generation_contract(KeeperNarration, context)
    with pytest.raises(ModelFormatError):
        schema.model_validate({"observed_detail": "行李散落满地，前方通往另一节车厢。"})
    good = schema.model_validate({"observed_detail": "金属表面有些划痕，齿口没有明显缺损。"})
    assert "齿口" in restore_output(good, KeeperNarration, context).public_narration


@pytest.mark.parametrize("passed, expected", [(False, "blocked"), (True, "completed")])
def test_treatment_receipt_updates_saved_teammate_result(client, team_game, passed, expected):  # noqa: F811
    from uuid import uuid4

    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.conversation import settle_teammate_tasks
    from app.persistence.adjudication_models import AgentBehaviorRecord
    from app.persistence.agent_models import AgentCycle

    service = client.app.state.agent_service
    cycle_id = str(uuid4())

    async def settle():
        async def operation(session, room):
            session.add(AgentCycle(id=cycle_id, room_id=room.id, status="completed", state={
                "origin": "teammate", "combat_decision": {"operation": "first_aid"},
            }))
            session.add(AgentBehaviorRecord(
                room_id=room.id, member_id=team_game["chen"],
                document=BehaviorState(
                    task_status="proposed", task_cycle_id=cycle_id,
                ).model_dump(mode="json"),
            ))
            service.rooms.append(session, room, "combat.resolved", room.host_member_id, {
                "cycle_id": cycle_id, "operation": "first_aid",
                "rolls": {"treatment": {"result": {"passed": passed}}},
                "summary": "止血成功" if passed else "止血失败",
            })
            await session.flush()
            await settle_teammate_tasks(service, session, room)

        await service.mutate(team_game["room"]["id"], operation)

    client.portal.call(settle)
    saved = next(b for b in ok(client.get(team_game["prefix"] + "/teammate-behavior"))
                 if b["member_id"] == team_game["chen"])
    assert saved["task_status"] == expected
    assert saved["last_result"]["kind"] == expected
    assert saved["last_result"]["text"] == ("止血成功" if passed else "止血失败")


def test_failed_observation_generation_keeps_the_teammate_request(client, team_game):  # noqa: F811
    def broken_narration(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            return {"observed_detail": "凭空出现的隐藏宝箱打开了。", "claim_ids": ["nonexistent"]}
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=broken_narration)
    ok(submit(client, team_game, "陈拓，帮我查看入口。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    saved = next(b for b in ok(client.get(team_game["prefix"] + "/teammate-behavior"))
                 if b["member_id"] == team_game["chen"])
    assert saved["task_status"] == "generation_failed"
    assert saved["pending_requests"]
    assert not saved["last_result"]["text"]


def test_conscious_npc_can_answer_greeting_without_a_scripted_quote():
    context = {
        "current_scene_reference": "scene",
        "response_brief": {
            "responder": {"id": "npc", "kind": "npc", "portrayal": "腿部受伤，清醒。"},
            "questions": ["您听得见吗？"],
            "question": "您听得见吗？",
        },
    }
    schema = generation_contract(KeeperNarration, context)
    output = schema.model_validate(
        {
            "npc_speech": {
                "answers": [
                    {
                        "question_index": 0,
                        "certainty": "social",
                        "text": "听得见……先别碰我的腿，疼得厉害。",
                    }
                ]
            }
        }
    )
    restored = restore_output(output, KeeperNarration, context)
    assert "听得见" in restored.npc_speech.text


def test_past_possession_cannot_become_current_possession_in_an_npc_answer():
    from app.models.base import ModelFormatError

    context = {"current_scene_reference": "scene", "response_brief": {
        "responder": {"id": "npc", "kind": "npc", "portrayal": "清醒。"},
        "questions": ["钥匙在您身上吗？"],
        "allowed_facts": [{"id": "keys", "text": "乘务员曾保管钥匙，黑包逃跑时掉在前门。"}],
    }}
    schema = generation_contract(KeeperNarration, context)
    with pytest.raises(ModelFormatError, match="过去持有"):
        schema.model_validate({"npc_speech": {"answers": [{
            "evidence_id": "keys", "certainty": "inference",
            "text": "钥匙在我这里，但我动不了。",
        }]}})
    result = schema.model_validate({"npc_speech": {"answers": [{
        "evidence_id": "keys", "certainty": "inference",
        "text": "我原先保管钥匙，黑包逃跑时掉在前门了，可以去那里找找。",
    }]}})
    assert "原先保管" in result.npc_speech.text


def test_npc_past_belongings_do_not_require_present_inventory_or_authorize_use():
    from app.preparation.dialogue import current_item_statements
    from app.preparation.inventory import bind_item_prose
    from app.rooms.service import RoomError

    view = {"known_items": [{"id": "bag", "names": ["黑包"]},
                            {"id": "key", "names": ["钥匙"]}], "holders": []}
    past = "逃跑时我的黑包背带断裂，钥匙掉在了门边。"
    assert not bind_item_prose(current_item_statements(past), view, "npc")
    # The actual-action guard remains unchanged, and present use cannot borrow
    # the historical qualifier from another clause or an attributive phrase.
    with pytest.raises(RoomError):
        bind_item_prose(past, view, "npc")
    for text in ("我用以前的钥匙开门。", "以前我的黑包丢了，现在用钥匙开门。"):
        with pytest.raises(RoomError):
            bind_item_prose(current_item_statements(text), view, "npc")


def test_partial_npc_fallback_keeps_original_valid_answer_over_repair(client, team_game):  # noqa: F811
    attempts = []

    def answer(messages, kwargs):
        schema = kwargs["response_schema"].__name__
        if schema == "KeeperPlan":
            return {"parsed_intent": {"type": "converse"},
                    "focus": {"question_clause_ids": ["u1", "u2"],
                              "addressee_id": "caretaker"}}
        if schema == "KeeperNarration":
            attempts.append(messages)
            return {"npc_speech": {"answers": [
                {"certainty": "social", "text": "听得见，你说吧。" if len(attempts) == 1
                 else "听得见，请接着说。"},
                {"certainty": "social", "text": "我听得见。"},
            ]}}
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    ok(submit(client, team_game, "您听得见吗？钥匙是谁拿走的？"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    spoken = [e["payload"]["text"] for e in events if e["type"] == "npc.spoke"]
    assert len(attempts) == 2 and spoken
    assert "听得见，你说吧。" in spoken[-1]
    assert "请接着说" not in spoken[-1]
    assert "钥匙是谁拿走的" in attempts[1][-1]["content"]


def test_npc_source_identity_supplies_original_evidence_without_copying():
    context = {
        "current_scene_reference": "scene",
        "response_brief": {
            "responder": {"id": "npc", "kind": "npc", "portrayal": "清醒。"},
            "questions": ["包在哪？"],
            "allowed_facts": [{"id": "bag", "text": "黑包落在前门边。"}],
        },
    }
    schema = generation_contract(KeeperNarration, context)
    output = schema.model_validate(
        {
            "npc_speech": {
                "answers": [
                    {
                        "question_index": 0,
                        "evidence_id": "bag",
                        "certainty": "sourced",
                        "text": "我的黑包落在前门边了。",
                    }
                ]
            }
        }
    )
    answer = restore_output(output, KeeperNarration, context).npc_speech.answers[0]
    assert answer.evidence_quote == "黑包落在前门边。"


def test_unrelated_hidden_search_does_not_block_an_ordinary_inspection():
    from app.preparation.search import unresolved_search_plan

    plan = plan_for("我检查另一端的门。", "investigate", "door")
    plan.focus = TurnFocus(action="我检查另一端的门。", action_target_id="door")
    context = {
        "check_requirements": [
            {
                "entity_id": "newspaper",
                "title": "报纸",
                "access_policy": "requires_check",
                "successful_check": {"kind": "skill", "name": "spot_hidden"},
            }
        ]
    }
    assert not unresolved_search_plan(plan, context)
    plan.focus.action_target_id = "newspaper"
    assert unresolved_search_plan(plan, context)


def test_valid_search_check_survives_an_unrelated_model_focus():
    from uuid import uuid4

    from app.agents.check_policy import CheckProposal

    raw = "我蹲下翻看座位间遗留的东西。"
    actor = str(uuid4())
    plan = plan_for(raw, "investigate", "event")
    plan.focus = TurnFocus(action=raw, action_target_id="event")
    plan.proposed_check = CheckProposal(
        kind="skill", name="spot_hidden", difficulty="regular",
        target_entity_id="paper", basis_entity_id="paper",
        target_member_id=actor, reason=raw,
    )
    plan.proposed_reveal_entity_ids = ["event"]
    restored = restore_output(plan, KeeperPlan, {
        "triggering_action": {"payload": {"text": raw}},
        "action_identifiers": {"actor_member_id": actor, "current_scene_id": "scene"},
        "current_targets": [{"id": "event", "title": "远方爆炸", "type": "clue"}],
        "check_requirements": [{
            "entity_id": "paper", "title": "遗留报纸", "access_policy": "requires_check",
            "successful_check": {"kind": "skill", "name": "spot_hidden", "difficulty": "regular"},
        }],
    })
    assert restored.focus.action_target_id == "paper"
    assert restored.proposed_check.clue_id == "paper"
    assert "event" not in restored.proposed_reveal_entity_ids


def test_looking_through_glass_does_not_cross_a_door_or_lose_the_observation():
    from app.agents.action_policy import explicit_movement
    from app.preparation.action_authority import action_kinds

    raw = "先别往前冲。我贴近通往2号车厢的门，透过玻璃看看那边有没有人。"
    assert not explicit_movement(raw)
    assert action_kinds(raw) == ["observe"]
    assert not action_kinds("我没有查看门外。")
    plan = plan_for(raw, "investigate", "scene")
    plan.focus = TurnFocus(action=raw[6:], action_target_id="scene")
    plan.proposed_transition_id = "exit"
    restored = restore_output(plan, KeeperPlan, {
        "triggering_action": {"payload": {"text": raw}},
        "action_identifiers": {"actor_member_id": "actor", "current_scene_id": "scene"},
    })
    assert not restored.proposed_transition_id
    assert restored.focus.action_target_id == "scene"


def test_observation_publishes_perception_without_granting_items(client, team_game):  # noqa: F811
    def answer(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            return {
                "observed_detail": "门边是皱起的纸屑，表面沾了灰，看不清字迹。",
                "incidental_details": ["门边是皱起的纸屑，表面沾了灰，看不清字迹。"],
            }
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    ok(submit(client, team_game, "我看看门边散落的杂物是什么。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any(e["type"] == "keeper.narration" and "纸屑" in e["payload"]["text"] for e in events)
    assert not any(e["type"] in {"item.acquired", "action.clarification_requested"} for e in events)


@pytest.mark.parametrize("gesture", [
    "我跟上，看看有没有能帮上忙的。",
    "我跟上你，先看看车头有没有什么异常。",
    "我跟上，检查车头有没有什么能帮上忙的。",
    "我跟上你，先检查一下车厢有没有异常。",
    "我注意到你试图穿过前门，但没有成功。我们得先看看情况。",
])
def test_following_in_same_scene_has_no_empty_action_subcycle(client, team_game, gesture):  # noqa: F811
    def answer(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "TeammateDecision":
            return {
                "mode": "act",
                "action_type": "move",
                "action_text": gesture,
                "related_player_action_seq": context["triggering_action"]["seq"],
                "confidence": 1,
            }
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    ok(submit(client, team_game, "陈拓，你跟上来就好。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any(e["type"] == "agent.spoke" for e in events)
    assert not any(
        e["type"] in {"agent.action_proposed", "action.clarification_requested"} for e in events
    )


def test_current_nurse_question_reaches_answer_without_keeper_replaying_old_turn(client, team_game):  # noqa: F811
    seen = []

    def answer(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            return {
                "parsed_intent": {"type": "assist"},
                "focus": {
                    "action_clause_ids": ["u2"],
                    "question_clause_ids": ["u2"],
                    "addressee_id": team_game["chen"],
                    "requests": [
                        {
                            "kind": "question",
                            "addressee_id": team_game["chen"],
                            "clause_ids": ["u1"],
                        }
                    ],
                },
            }
        if kwargs["response_schema"].__name__ == "TeammateDecision":
            seen.extend(context.get("addressed_requests", []))
            if context.get("addressed_requests"):
                return {
                    "mode": "speak",
                    "speech_text": "帮我守着入口，我先判断伤口情况。",
                    "confidence": 1,
                    "related_player_action_seq": context["triggering_action"]["seq"],
                }
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    ok(submit(client, team_game, "陈拓，我可以帮你做些什么？"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any("我可以帮你做些什么" in r["text"] for r in seen)
    assert any(
        e["type"] == "agent.spoke" and e["actor_member_id"] == team_game["chen"] for e in events
    )
    assert not any(
        e["type"] in {"keeper.narration", "agent.action_proposed", "action.clarification_requested"}
        for e in events
    )
