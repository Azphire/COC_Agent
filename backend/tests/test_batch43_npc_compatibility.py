"""Keep authorized NPC disclosure separate from the public KP streaming boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_runtime import submit, wait_cycle
from test_batch36_collaboration import responder, team_game  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.action_runtime import ActionRuntimeMixin
from app.agents.adjudication_schemas import KeeperNarration, NPCSpeech
from app.agents.model import FakeModelAdapter
from app.agents.narration_stream import NarrationStream, private_fragments
from app.rooms.realtime import RoomHub
from app.rooms.service import RoomError


@pytest.fixture
def npc_authority(monkeypatch):
    testimony = "钟楼每晚九点会响一次。"
    npc = {"id": "witness", "type": "npc", "title": "目击者",
           "public_summary": "一位仍愿意交谈的目击者。", "fact_scope": "current_scene"}
    scene = {"id": "scene", "title": "钟楼外", "public_description": "夜雨落在石阶上。"}
    module = SimpleNamespace(
        document={
            "id": "test", "title": "证词边界", "version": "1",
            "public_introduction": "夜雨中的钟楼。", "keeper_brief": "不公开的地下室安排。",
            "initial_scene": "scene", "scenes": [scene], "npcs": [], "clues": [],
            "suggested_checks": [], "completion_conditions": None,
        },
        state={"scene_id": "scene", "revealed_clues": []},
    )
    # This source is not yet publicly revealed. Existing NPC authorization adds
    # it only when the approved witness actually speaks in this response.
    rows = [SimpleNamespace(
        source_entity_id="testimony_fact", entity_type="clue", state="hidden",
        snapshot={"title": "钟声证词", "public_summary": testimony},
    )]
    service = SimpleNamespace(
        entities=SimpleNamespace(public=AsyncMock(return_value=[npc]),
                                 rows=AsyncMock(return_value=rows)),
        navigation=SimpleNamespace(state=AsyncMock(return_value=None)),
        module=AsyncMock(return_value=module),
    )
    monkeypatch.setattr("app.preparation.inventory.inventory_context",
                        AsyncMock(return_value={}))
    context = {
        "prepared_module": True, "intent_type": "converse", "module": {"scene": scene},
        "public_entities": [npc], "conversation_target": npc["id"],
        "triggering_action": {"actor_member_id": "actor", "payload": {"text": "钟声何时响？"}},
        "response_brief": {
            "responder": {"kind": "npc", "id": npc["id"], "name": npc["title"]},
            "question": "钟声何时响？", "current_scene": scene,
        },
    }
    run = SimpleNamespace(id="run", context=context)
    cycle = SimpleNamespace(id="cycle", state={"dialogue_available_facts": [{
        "entity_id": "testimony_fact", "npc_id": npc["id"],
        "title": "钟声证词", "text": testimony,
    }]})
    runtime = SimpleNamespace(
        service=service, rooms=SimpleNamespace(hub=RoomHub(None)),
        public_results=AsyncMock(return_value={"events": [], "result_facts": [],
                                              "current_result_facts": []}),
    )
    snapshot = {
        "run": run, "private": private_fragments([testimony, "黑莲"], scene["public_description"]),
        "handout_private": private_fragments(["黑莲"], ""), "internal_ids": set(),
    }
    stream = NarrationStream(runtime, {"room_id": "room", "cycle_id": "cycle"},
                             "run", KeeperNarration, snapshot)
    runtime.narration_streams = {run.id: stream}
    return SimpleNamespace(runtime=runtime, run=run, cycle=cycle, stream=stream,
                           room=SimpleNamespace(id="room", session_state={}), testimony=testimony)


@pytest.mark.parametrize("partial", [False, True])
async def test_unrevealed_authorized_npc_testimony_keeps_existing_full_publication(
    npc_authority, partial,
):
    env = npc_authority
    output = KeeperNarration(
        npc_speech=NPCSpeech(entity_id="witness", text=env.testimony),
        incidental_details=[env.testimony],
    )
    result = await ActionRuntimeMixin.validate_narration_output(
        env.runtime, None, env.room, env.cycle, env.run, output, partial=partial,
    )
    assert result["valid"]
    assert not env.stream.eligible  # NPC prose continues to wait for full validation.


@pytest.mark.parametrize("partial", [False, True])
async def test_npc_authorization_cannot_license_same_fact_in_kp_body(npc_authority, partial):
    env = npc_authority
    output = KeeperNarration(
        public_narration=env.testimony,
        npc_speech=NPCSpeech(entity_id="witness", text=env.testimony),
    )
    with pytest.raises(RoomError, match="私密"):
        await ActionRuntimeMixin.validate_narration_output(
            env.runtime, None, env.room, env.cycle, env.run, output, partial=partial,
        )


@pytest.mark.parametrize("partial", [False, True])
async def test_npc_testimony_authority_never_discloses_private_handout(npc_authority, partial):
    env = npc_authority
    output = KeeperNarration(npc_speech=NPCSpeech(entity_id="witness", text="黑莲。"))
    with pytest.raises(RoomError, match="私人HO"):
        await ActionRuntimeMixin.validate_narration_output(
            env.runtime, None, env.room, env.cycle, env.run, output, partial=partial,
        )


def test_npc_only_reply_ends_waiting_bubble_without_synthetic_kp_event(
    client, team_game,  # noqa: F811
):
    def answer(messages, kwargs):
        schema = kwargs["response_schema"].__name__
        if schema == "KeeperPlan":
            return {"parsed_intent": {"type": "converse"}, "focus": {
                "question_clause_ids": ["u1"], "addressee_id": "caretaker",
            }}
        if schema == "KeeperNarration":
            return {"npc_speech": {"answers": [{
                "question_index": 0, "certainty": "social", "text": "听得清，你慢慢说。",
            }]}}
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=answer)
    ok(submit(client, team_game, "能听清我说话吗？"))
    cycle = wait_cycle(client, team_game)
    assert cycle["status"] == "completed", cycle
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any(event["type"] == "npc.spoke" for event in events)
    assert not any(event["type"] == "keeper.narration" for event in events)
    stream = client.app.state.room_hub.stream_snapshot(team_game["room"]["id"])["data"]
    assert stream["status"] == "interrupted"
    assert stream["reason"] == "complete_without_narration"
    assert not stream["text"]
    assert not client.app.state.agent_service.runtime.narration_streams
