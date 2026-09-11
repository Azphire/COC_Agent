"""Fake output tests exercise publication, permissions and durable quote retrieval."""

import json

import pytest
from pydantic import ValidationError
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_batch10 import restore, save
from test_batch12 import conversational
from test_check_narration_policy import policy_case
from test_rooms import lobby, ok  # noqa: F401

from app.agents.action_policy import ActionPolicyValidator
from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan, TurnFocus
from app.agents.generation_contracts import generation_contract, restore_output
from app.agents.model import FakeModelAdapter
from app.agents.narration import fallback_narration, response_brief
from app.agents.schemas import Empty, PlannedTool
from app.agents.tools import TOOLS, ToolDefinition, validate
from app.memory.events import epistemic_event, relevant_incidental_memories
from app.models.ollama import generation_schema


def event(seq, text, *, kind="keeper.narration", scene="square", **payload):
    return dict(
        seq=seq,
        type=kind,
        visibility="public",
        actor_member_id="kp",
        payload={
            "text": text,
            "incidental_details": [text],
            "scene_id": scene,
            "incidental_source": "kp_improvisation",
            "actor_name": "林先生",
            **payload,
        },
    )


def install(client, narration, seen=None, basis="improvise"):
    def respond(messages, kwargs):
        c = json.loads(messages[1]["content"])
        schema = kwargs["response_schema"].__name__
        if seen is not None:
            seen.append((schema, c))
        if schema == "KeeperNarration":
            return narration(c) if callable(narration) else narration
        if schema == "TeammateDecision":
            return {"mode": "pass", "confidence": 1}
        result = conversational(messages, kwargs)
        if schema == "KeeperPlan":
            result.update(
                focus={
                    "question": c["triggering_action"]["payload"]["text"],
                    "addressee_id": "caretaker",
                    "answer_basis": basis,
                },
                needs_host_review=True,
                proposed_tool_calls=[
                    {"name": "inspect_public_state", "arguments": {"scene_id": "irrelevant"}}
                ],
            )
            if c["triggering_action"]["payload"]["text"].startswith("队友"):
                member = next(
                    mid
                    for mid, name in c["current_participants"]["members"].items()
                    if "队友" in name
                )
                result["focus"].update(addressee_id=member, answer_basis="teammate")
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=respond)


def test_improvisation_generation_limits():
    c = {"current_scene_reference": "square"}
    schema = generation_contract(KeeperNarration, c)
    grammar = generation_schema(schema.model_json_schema())
    assert grammar["properties"]["incidental_details"]["maxItems"] == 2
    output = restore_output(schema(incidental_details=["风吹响门边的铃铛。"]), KeeperNarration, c)
    assert output.incidental_details == ["风吹响门边的铃铛。"]
    for details in (["一", "二", "三"], ["字" * 161], [""]):
        with pytest.raises(ValidationError):
            schema(incidental_details=details)


def test_quotes_require_public_actual_speech_and_active_branch():
    good = event(1, "窗沿放着绿色瓷杯。")
    draft = event(2, "只说了风声。", incidental_details=["草稿里有红色信封。"])
    secret = {**event(3, "秘密暗号藏在杯底。"), "visibility": "host_only"}
    rejected = event(4, "紫色花瓶。", safe_fallback=True)
    wrong_type = event(5, "草稿中的黄铜勺。", kind="agent.action_proposed")
    abandoned = event(6, "窗沿放着蓝色瓷杯。")
    loaded = dict(seq=7, type="snapshot.loaded", visibility="public", payload={"source_seq": 5})
    events = [good, draft, secret, rejected, wrong_type, abandoned, loaded]
    rows = relevant_incidental_memories(events, "窗沿的瓷杯呢？", "square")
    assert [r["text"] for r in rows] == [good["payload"]["text"]]
    assert rows[0]["source_event_seq"] == 1 and rows[0]["speaker_id"] == "kp"
    assert rows[0]["fact_scope"] == "current_scene"
    historical = relevant_incidental_memories(events, "瓷杯呢？", "workshop")
    assert historical[0]["fact_scope"] == "historical"
    assert not relevant_incidental_memories(events, "码头？", "workshop")
    assert epistemic_event(good)["incidental_sources"][0]["source"] == "kp_improvisation"


def test_question_retrieval_precedes_recency_and_is_bounded():
    old = event(1, "窗沿放着绿色瓷杯。", scene="old-room")
    recent = [event(seq, f"屋里第{seq}盏油灯亮着。") for seq in range(2, 40)]
    rows = relevant_incidental_memories([old, *recent], "绿色瓷杯在哪里？", "square")
    assert rows[0]["source_event_seq"] == 1
    assert len(rows) <= 6 and len(json.dumps(rows, ensure_ascii=False)) < 1450


def test_missing_plan_fact_selection_keeps_readable_public_text():
    facts, intent, _ = policy_case()
    plan = KeeperPlan(
        plan_id="p",
        cycle_id=facts.cycle_id,
        current_scene_id=facts.scene_id,
        parsed_intent=intent,
        focus=TurnFocus(question="我读公告上的文字。"),
    )
    c = {
        "triggering_action": {"seq": 1, "payload": {"text": plan.focus.question}},
        "public_entities": [
            {"id": "notice", "type": "clue", "title": "公告", "fact_scope": "current_scene"}
        ],
        "PUBLIC_CLAIM_OPTIONS": [
            {
                "claim_id": "entity_notice",
                "entity_ids": ["notice"],
                "category": "module_fact",
                "statement": "请从东门入内。",
            }
        ],
    }
    brief, options = response_brief(plan, c, {"events": [], "failed_tools": []})
    assert brief["allowed_facts"][0]["text"] == "请从东门入内。"
    assert options[0]["claim_id"] == "entity_notice"
    fallback = fallback_narration("investigate", {"events": []}, "", rejected=True, brief=brief)
    assert "请从东门入内。" in fallback and "怎么做由你决定" in fallback
    assert "无法完成这项行动" not in fallback


def test_empty_read_tools_normalize_without_weakening_other_schemas(monkeypatch):
    for name, spec in TOOLS.items():
        if spec.read_only and not spec.arguments.model_fields:
            assert validate(name, {"wrong": 7}, next(iter(spec.roles))).model_dump() == {}
    with pytest.raises(ValidationError):
        validate("inspect_character", {"wrong": 7}, "keeper")
    with pytest.raises(ValidationError):
        validate("reveal_clue", {"clue_id": "notice", "wrong": 7}, "keeper")
    monkeypatch.setitem(
        TOOLS, "mutating_empty", ToolDefinition(Empty, frozenset({"keeper"}), "test")
    )
    with pytest.raises(ValidationError):
        validate("mutating_empty", {"wrong": 7}, "keeper")
    facts, intent, _ = policy_case()
    plan = KeeperPlan(
        plan_id="p",
        cycle_id=facts.cycle_id,
        current_scene_id=facts.scene_id,
        parsed_intent=intent,
        focus=TurnFocus(question="你好吗？"),
    )
    verdict = ActionPolicyValidator().validate(
        intent,
        plan,
        facts,
        [
            PlannedTool(name="inspect_public_state", arguments={"wrong": 7}),
            PlannedTool(name="inspect_character", arguments={"wrong": 7}),
        ],
    )
    assert verdict.approved_actions[0].tool.arguments == {}
    assert verdict.rejected_actions[0].code == "invalid_arguments"


@pytest.mark.parametrize("basis", ["improvise", "unrecorded"])
def test_npc_improvisation_published_and_recalled_by_all_roles(client, game, basis):  # noqa: F811
    detail = "那晚我在擦窗沿的绿色瓷杯。"
    seen = []
    install(client, {"npc_speech": {"text": detail}, "incidental_details": [detail]}, seen, basis)
    ok(submit(client, game, "林先生，昨晚你在做什么？"))
    assert wait_cycle(client, game)["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    speech = next(e for e in events if e["type"] == "npc.spoke")
    assert speech["payload"]["incidental_details"] == [detail]
    assert speech["payload"]["entity_id"] == "caretaker"
    assert speech["payload"]["scene_id"] == "square"
    assert not any(e["type"] == "keeper.narration" for e in events)
    assert not any(schema == "ArgumentRepair" for schema, _ in seen)
    narrator = next(c for schema, c in seen if schema == "KeeperNarration")
    assert narrator["response_brief"]["answer_basis"] == "improvise"
    assert narrator["response_brief"]["responder"]["portrayal"]
    ok(submit(client, game, "林先生，绿色瓷杯是什么样？"))
    assert wait_cycle(client, game)["status"] == "completed"
    for schema in ("KeeperPlan", "KeeperNarration"):
        c = next(c for s, c in reversed(seen) if s == schema)
        rows = c.get("response_brief", c)["incidental_memories"]
        assert any(r["text"] == detail and r["speaker_id"] == "caretaker" for r in rows)
    ok(submit(client, game, "队友，你还记得绿色瓷杯吗？"))
    assert wait_cycle(client, game)["status"] == "completed"
    c = next(c for s, c in reversed(seen) if s == "TeammateDecision")
    assert any(r["text"] == detail for r in c["incidental_memories"])


def test_publication_keeps_speakers_and_excludes_unspoken_draft(client, game):  # noqa: F811
    narration, speech = "门边的小铃轻轻晃动。", "我习惯在午后擦拭窗框。"
    install(
        client,
        {
            "public_narration": narration,
            "npc_speech": {"text": speech},
            "incidental_details": [narration, speech],
        },
    )
    ok(submit(client, game, "林先生，平时你做些什么？"))
    assert wait_cycle(client, game)["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert next(e for e in events if e["type"] == "keeper.narration")["payload"][
        "incidental_details"
    ] == [narration]
    assert next(e for e in events if e["type"] == "npc.spoke")["payload"]["incidental_details"] == [
        speech
    ]
    install(
        client,
        {"npc_speech": {"text": "午后我通常有空。"}, "incidental_details": ["未发布的红色信封。"]},
    )
    ok(submit(client, game, "林先生，午后有空吗？"))
    assert wait_cycle(client, game)["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    rows = relevant_incidental_memories(events, "红色信封", "square")
    assert not any("红色信封" in r["text"] for r in rows)
    cycle = ok(client.get(game["prefix"] + "/agent-cycle"))
    client.portal.call(
        client.app.state.agent_service.summary_recovery.update,
        game["room"]["id"],
        cycle["id"],
        True,
    )
    summary = next(
        m for m in ok(client.get(game["prefix"] + "/memories")) if m["kind"] == "summary"
    )
    assert "KP即兴补充出处" in summary["content"]
    assert "square" in summary["content"] and speech in summary["content"]
    assert "红色信封" not in summary["content"]


def test_rejected_private_draft_never_enters_public_memory(client, game):  # noqa: F811
    from app.agents.modules import load_modules

    secret = load_modules()["stopped-clock"].clues[-1].content
    install(client, {"public_narration": "门外有微风。", "incidental_details": [secret[:160]]})
    ok(submit(client, game, "林先生，今天天气如何？"))
    assert wait_cycle(client, game)["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    public = [e for e in events if e["visibility"] == "public"]
    assert secret[:160] not in json.dumps(public, ensure_ascii=False)
    assert not relevant_incidental_memories(events, "门外", "square")
    assert any(e["type"] == "keeper.narration" and e["payload"]["safe_fallback"] for e in public)


def test_snapshot_reload_retains_only_effective_improvisation_branch(client, game):  # noqa: F811
    first, abandoned = "窗沿上摆着绿色瓷杯。", "桌边摆着紫色花瓶。"
    install(client, {"npc_speech": {"text": first}, "incidental_details": [first]})
    ok(submit(client, game, "林先生，窗边有什么？"))
    assert wait_cycle(client, game)["status"] == "completed"
    snapshot = save(client, game)
    install(client, {"npc_speech": {"text": abandoned}, "incidental_details": [abandoned]})
    ok(submit(client, game, "林先生，桌边有什么？"))
    assert wait_cycle(client, game)["status"] == "completed"
    restore(client, game, snapshot)
    seen = []
    install(client, {"npc_speech": {"text": "瓷杯还在那里。"}}, seen)
    ok(submit(client, game, "林先生，瓷杯和花瓶呢？"))
    assert wait_cycle(client, game)["status"] == "completed"
    c = next(c for schema, c in seen if schema == "KeeperNarration")
    rows = c["response_brief"]["incidental_memories"]
    assert first in [r["text"] for r in rows]
    assert abandoned not in [r["text"] for r in rows]
