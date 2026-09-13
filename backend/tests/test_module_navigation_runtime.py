import json
from uuid import uuid4

import pytest
from adjudication_helpers import ScenarioAdapter as FakeModelAdapter
from test_host_review import wait
from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_rooms import headers, lobby, ok, prepare  # noqa: F401

from app.agents.tools import definitions as registered_tools
from app.knowledge.schemas import GroundedClaim
from app.module_ir.schemas import NodeArgs
from app.persistence.agent_models import AgentRun
from app.rooms.service import RoomError


def responder(messages, kwargs):
    context = json.loads(messages[-1]["content"])
    if context.get("phase") == "summary":
        return {"content": "Public scene and revealed entities only."}
    if context.get("phase") == "generate_keeper_narration":
        scene = context["module"]["scene"]
        return {
            "claims": [
                {
                    "claim_id": "public",
                    "category": "module_fact",
                    "statement": scene["public_description"],
                    "entity_ids": [scene["id"]],
                }
            ]
        }
    if context["role"] == "investigator":
        return {
            "tools": [
                {"name": "get_public_scene", "arguments": {}},
                # These cases isolate navigation; actual teammate actions are
                # adjudicated (and exercised) by the conversation regressions.
                {"name": "speak", "arguments": {"text": "我跟上，先看看现场。"}},
            ]
        }
    # The compact prompt delegates movement through the validated plan field.
    assert "proposed_transition_id" in messages[0]["content"]
    transition_schema = next(
        t["function"]["parameters"]
        for t in registered_tools("keeper", structure_navigation=True)
        if t["function"]["name"] == "transition_scene"
    )
    assert "scene_id" not in transition_schema["properties"]
    assert set(transition_schema["required"]) == {
        "target_scene_node_id",
        "expected_revision",
        "request_id",
    }
    if context["phase"] == "execute_state_tools":
        return {"tools": []}
    text = context["triggering_action"]["payload"]["text"]
    if "move" in text:
        transition = context["approved_exits"][0]
        return {
            "tools": [
                {
                    "name": "transition_scene",
                    "arguments": {
                        "target_scene_node_id": transition["target_scene_node_id"],
                        "expected_revision": context["module_context_audit"]["navigation_revision"],
                        "request_id": "move-request",
                    },
                }
            ]
        }
    entity = next(e for e in context["module"]["approved_entities"] if e["type"] == "clue")
    return {"tools": [{"name": "reveal_entity", "arguments": {"entity_id": entity["id"]}}]}


@pytest.fixture
def running_navigation(client, navigation_game):  # noqa: F811
    d = navigation_game
    prefix = d["room_prefix"]
    prepare(client, {**d, "prefix": prefix})
    ok(client.post(prefix + "/pause"))
    for role, member in (("keeper", d["room"]["host_member_id"]), ("investigator", d["agent"])):
        profile = ok(client.post("/api/agent-profiles", json={"role": role, "name": role}), 201)
        ok(
            client.post(
                prefix + "/agent-bindings", json={"member_id": member, "profile_id": profile["id"]}
            )
        )
    ok(client.post(prefix + "/resume"))
    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=responder)
    return d


def act(client, d, text):
    ok(
        client.post(
            d["room_prefix"] + "/actions",
            json={"text": text, "client_request_id": str(uuid4())},
            headers=headers(d["remote"]["member_token"]),
        )
    )
    return wait(client, d["room_prefix"])


def test_normal_cycles_never_global_search_and_transition_context_switches(
    client, running_navigation, monkeypatch
):
    d = running_navigation
    svc = client.app.state.agent_service
    original = svc.knowledge.retriever.search
    calls = []

    def audited(*args, **kwargs):
        calls.append(kwargs["kind"])
        return original(*args, **kwargs)

    monkeypatch.setattr(svc.knowledge.retriever, "search", audited)
    assert act(client, d, "Read Notice")["status"] == "completed"
    assert act(client, d, "move")["status"] == "completed"
    nav = ok(client.get(d["room_prefix"] + "/module-navigation"))
    assert nav["current_scene_node_id"] == d["nodes"]["Future"]
    assert calls == []
    runs = ok(client.get(d["room_prefix"] + "/agent-runs"))
    kp = [r for r in runs if r["graph_node"] == "plan_keeper_action"]
    assert kp and "OPENING_ONLY" in json.dumps(kp[0]["context"])
    assert all(
        "FUTURE_SECRET" not in json.dumps(r["context"])
        for r in runs
        if r["graph_node"] in {"decide_teammates", "generate_keeper_narration"}
    )
    events = ok(
        client.get(d["room_prefix"] + "/events", headers=headers(d["remote"]["member_token"]))
    )["events"]
    assert "node_" not in json.dumps(events)
    assert sum(e["type"] == "keeper.narration" for e in events) == 2


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_missing_transition_condition_waits_for_host_and_resumes_same_cycle(
    client, running_navigation, decision
):
    d = running_navigation
    cycle = act(client, d, "move without revealing prerequisite")
    assert cycle["status"] == "waiting_for_review"
    review = ok(client.get(d["room_prefix"] + "/review-requests"))[0]
    assert review["navigation_request"]["expected_revision"] == 0
    assert ok(client.get(d["room_prefix"] + "/checks")) == []
    ok(client.post(d["room_prefix"] + f"/review-requests/{review['id']}/{decision}", json={}))
    completed = wait(client, d["room_prefix"], ("completed", "failed"))
    assert completed["id"] == cycle["id"] and completed["status"] == "completed"
    nav = ok(client.get(d["room_prefix"] + "/module-navigation"))
    assert (
        nav["current_scene_node_id"] == d["nodes"]["Future" if decision == "approve" else "Opening"]
    )
    assert nav["pending_review_id"] is None


def test_revision_change_during_review_stops_old_cycle(client, running_navigation):
    d = running_navigation
    cycle = act(client, d, "move")
    assert cycle["status"] == "waiting_for_review"
    ok(
        client.post(
            d["room_prefix"] + "/scene-transition",
            json={
                "target_scene_node_id": d["nodes"]["Future"],
                "expected_revision": 0,
                "request_id": "host-other",
            },
        )
    )
    review = ok(client.get(d["room_prefix"] + "/review-requests"))[0]
    ok(client.post(d["room_prefix"] + f"/review-requests/{review['id']}/approve", json={}))
    stopped = wait(client, d["room_prefix"], ("failed", "completed"))
    assert stopped["status"] == "failed" and "navigation_revision_conflict" in stopped["safe_error"]


def test_node_grounding_and_arbitrary_node_rejected(client, running_navigation):
    d = running_navigation
    assert act(client, d, "Read Notice")["status"] == "completed"
    svc = client.app.state.agent_service

    async def validate():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            from sqlalchemy import select

            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.room_id == room.id, AgentRun.graph_node == "plan_keeper_action"
                )
            )
            block = run.context["module"]["blocks"][0]
            claim = GroundedClaim(
                claim_id="node-reference",
                category="module_fact",
                statement=block["text"],
                node_ids=[block["node_id"]],
                visibility="keeper_only",
                basis_type="module_node",
            )
            result = await svc.knowledge.validate_claim(session, room, run, claim)
            assert result["basis_type"] == "module_node"
            with pytest.raises(RoomError):
                await svc.knowledge.validate_claim(
                    session, room, run, claim.model_copy(update={"visibility": "public"})
                )
            with pytest.raises(RoomError):
                await svc.knowledge.validate_claim(
                    session,
                    room,
                    run,
                    claim.model_copy(update={"node_ids": [d["nodes"]["Future"]]}),
                )
            with pytest.raises(RoomError):
                await svc.module_context.open_node(
                    session, room, run, NodeArgs(node_id="node_forged")
                )

    client.portal.call(validate)


def test_rejected_navigation_tool_rolls_back_without_expired_orm_crash(client, running_navigation):
    d = running_navigation

    def rejected(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if context.get("phase") == "plan_keeper_action":
            return {
                "tools": [
                    {"name": "open_module_node", "arguments": {"node_id": "node_forged"}},
                    {
                        "name": "transition_scene",
                        "arguments": {
                            "scene_id": d["entities"][0]["id"],
                            "target_scene_node_id": d["nodes"]["Future"],
                            "expected_revision": 0,
                            "request_id": "invalid-mixed-arguments",
                        },
                    },
                ]
            }
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=rejected)
    cycle = act(client, d, "Invalid reference must not change position")
    assert cycle["status"] == "completed"
    nav = ok(client.get(d["room_prefix"] + "/module-navigation"))
    assert nav["current_scene_node_id"] == d["nodes"]["Opening"]
    runs = ok(client.get(d["room_prefix"] + "/agent-runs"))
    tools = next(r["tool_results"] for r in runs if r["graph_node"] == "plan_keeper_action")
    assert tools == []  # Invalid requests are now rejected before tool dispatch.
    validation = ok(client.get(d["room_prefix"] + f"/cycles/{cycle['id']}/validation"))
    assert len(validation["validation"]["rejected_actions"]) == 2


def test_reveal_future_scene_entity_requires_current_binding(client, running_navigation):
    d = running_navigation

    def future(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if context.get("phase") == "plan_keeper_action":
            return {
                "tools": [
                    {
                        "name": "reveal_entity",
                        "arguments": {
                            "entity_id": d["entities"][1]["id"],
                        },
                    }
                ]
            }
        return responder(messages, kwargs)

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=future)
    assert act(client, d, "Reveal an unauthorized future entity")["status"] == "completed"
    public = ok(client.get(d["room_prefix"] + "/public-entities"))
    assert d["entities"][1]["id"] not in [e["id"] for e in public]
