import json
import time
from uuid import uuid4

import pytest
from adjudication_helpers import ScenarioAdapter as FakeModelAdapter
from fastapi.testclient import TestClient
from test_agent_runtime import accept_original
from test_module_preparation import approve_opening, preparation  # noqa: F401
from test_rooms import headers, lobby, ok, prepare  # noqa: F401

from app.main import create_app


def wait(
    client, prefix, statuses=("waiting_for_review", "waiting_for_roll", "completed", "failed")
):
    for _ in range(400):
        cycle = ok(client.get(prefix + "/agent-cycle"))
        if cycle and cycle["status"] in statuses:
            return cycle
        time.sleep(0.01)
    raise AssertionError("cycle did not reach expected state")


def scenario(messages, kwargs):
    context = json.loads(messages[-1]["content"])
    if context.get("phase") == "summary":
        return {"content": "当前公开实体以调查板为准。"}
    if context.get("phase") == "generate_keeper_narration":
        entity = context["public_entities"][-1]
        return {
            "claims": [
                {
                    "claim_id": "visible",
                    "category": "module_fact",
                    "statement": entity["public_summary"],
                    "entity_ids": [entity["id"]],
                }
            ]
        }
    if context["role"] == "investigator":
        return {
            "tools": [
                {"name": "inspect_public_entities", "arguments": {}},
                # Keep this fixture focused on the original host exception.
                # Batch 12 tests the extra adjudication for actual AI actions.
                {"name": "speak", "arguments": {"text": "我留意公告附近的动静。"}},
            ]
        }
    if context["phase"] == "execute_state_tools":
        return {"tools": []}
    evidence = context.get("MODULE_EVIDENCE", [])
    action = context["triggering_action"]
    if "无证据" in action["payload"]["text"]:
        evidence = []
    tools = [
        {
            "name": "propose_module_fact",
            "arguments": {
                "proposed_title": "时刻表",
                "proposed_public_summary": "站台边放着一本时刻表。",
                "keeper_reason": "私密审阅原因紫月",
                "evidence_ids": [e["evidence_id"] for e in evidence],
                "entity_type": "item",
            },
        }
    ]
    if "检定" in action["payload"]["text"]:
        tools.insert(
            0,
            {
                "name": "request_skill_check",
                "arguments": {
                    "target_member_id": action["actor_member_id"],
                    "name": "spot_hidden",
                    "reason": "寻找细节",
                },
            },
        )
    return {"tools": tools}


@pytest.fixture
def prepared_game(client, preparation, lobby):  # noqa: F811
    approved = approve_opening(client, preparation)
    prefix = lobby["prefix"]
    ok(client.patch(prefix + "/module-preparation", json={"preparation_id": approved["id"]}))
    prepare(client, lobby)
    ok(client.post(prefix + "/pause"))
    for role, member in [
        ("keeper", lobby["room"]["host_member_id"]),
        ("investigator", lobby["agent"]),
    ]:
        profile = ok(client.post("/api/agent-profiles", json={"role": role, "name": role}), 201)
        ok(
            client.post(
                prefix + "/agent-bindings", json={"member_id": member, "profile_id": profile["id"]}
            )
        )
    ok(client.post(prefix + "/resume"))
    adapter = FakeModelAdapter(responder=scenario)
    client.app.state.agent_service.model.adapter = adapter
    return {**lobby, **preparation, "approved": approved, "adapter": adapter}


def submit(client, game, text="查看时刻表"):
    return ok(
        client.post(
            game["prefix"] + "/actions",
            json={"text": text, "client_request_id": str(uuid4())},
            headers=headers(game["remote"]["member_token"]),
        )
    )


@pytest.mark.parametrize("decision", ["approve", "edit-and-approve", "reject"])
def test_review_interrupt_resume_permissions_idempotency_and_public_context(
    client, prepared_game, decision
):
    game = prepared_game
    prefix = game["prefix"]
    submit(client, game, "查看时刻表并检定；我冒着失去平衡的风险尝试。")
    cycle = wait(client, prefix)
    assert cycle["status"] == "waiting_for_review", cycle
    assert cycle["wait_reason"] == "host_review"
    assert ok(client.get(prefix + "/checks")) == []
    reviews = ok(client.get(prefix + "/review-requests"))
    assert len(reviews) == 1 and reviews[0]["status"] == "pending"
    review = reviews[0]
    assert review["evidence"]
    player_headers = headers(game["remote"]["member_token"])
    public = ok(client.get(prefix + "/public-entities", headers=player_headers))
    assert len(public) == 1
    for path in ("review-requests", "host-entities"):
        assert client.get(prefix + "/" + path, headers=player_headers).status_code == 403
    path = prefix + f"/review-requests/{review['id']}/{decision}"
    assert client.post(path, json={}, headers=player_headers).status_code == 403
    body = {"host_response": "经过主机确认"}
    if decision == "edit-and-approve":
        body.update(public_summary="站台旁摆着一本列车时刻表。", entity_type="clue")
    ok(client.post(path, json=body))
    again = ok(client.post(path, json=body))
    assert again["result"]["status"] != "pending"
    next_cycle = wait(client, prefix, ("waiting_for_roll", "completed", "failed"))
    if decision != "reject":
        assert next_cycle["status"] == "waiting_for_roll", next_cycle
        assert next_cycle["wait_reason"] == "human_roll"
        check = ok(client.get(prefix + "/checks"))[-1]
        ok(client.post(prefix + f"/checks/{check['id']}/roll", json={}, headers=player_headers))
        accept_original(client, game, check["id"])
        next_cycle = wait(client, prefix, ("completed", "failed"))
    assert next_cycle["status"] == "completed" and next_cycle["id"] == cycle["id"], next_cycle
    public = ok(client.get(prefix + "/public-entities", headers=player_headers))
    assert len(public) == (1 if decision == "reject" else 2)
    if decision == "edit-and-approve":
        assert any(
            e["type"] == "clue" and e["public_summary"] == body["public_summary"] for e in public
        )
    runs = ok(client.get(prefix + "/agent-runs"))
    if decision == "reject":
        assert len(game["adapter"].prompts) <= 2
        assert not any(r["graph_node"] == "decide_teammates" for r in runs)
    for run in runs:
        if run["graph_node"] in {"generate_keeper_narration", "decide_teammates"}:
            text = json.dumps(run["context"], ensure_ascii=False)
            assert (
                "keeper_summary" not in text
                and "私密审阅原因" not in text
                and "私密标记" not in text
            )
            assert {e["id"] for e in run["context"]["public_entities"]} == {e["id"] for e in public}
    events = ok(client.get(prefix + "/events", headers=player_headers))["events"]
    assert "私密审阅原因" not in json.dumps(events, ensure_ascii=False)
    assert not any(e["type"] in {"review.requested", "review.resolved"} for e in events)
    assert len(ok(client.get(prefix + "/review-requests"))) == 1


def test_no_evidence_returns_ruling_without_review_or_canonical_write(client, prepared_game):
    game = prepared_game
    submit(client, game, "无证据的新事实")
    cycle = wait(client, game["prefix"])
    assert cycle["status"] == "completed", cycle
    assert ok(client.get(game["prefix"] + "/review-requests")) == []
    assert len(ok(client.get(game["prefix"] + "/public-entities"))) == 1


def test_cancel_review_and_repeated_resolution_is_terminal(client, prepared_game):
    game = prepared_game
    submit(client, game)
    assert wait(client, game["prefix"])["status"] == "waiting_for_review"
    review = ok(client.get(game["prefix"] + "/review-requests"))[0]
    ok(client.post(game["prefix"] + "/agent-cycle/cancel", json={}))
    assert ok(client.get(game["prefix"] + "/review-requests"))[0]["status"] == "cancelled"
    result = ok(client.post(game["prefix"] + f"/review-requests/{review['id']}/approve", json={}))
    assert result["result"]["status"] == "cancelled"
    assert len(ok(client.get(game["prefix"] + "/public-entities"))) == 1


def test_review_restart_save_load_and_resume_same_cycle(client, prepared_game, character_settings):
    game = prepared_game
    prefix = game["prefix"]
    submit(client, game)
    cycle = wait(client, prefix)
    assert cycle["status"] == "waiting_for_review", cycle
    review = ok(client.get(prefix + "/review-requests"))[0]
    save = ok(client.post(prefix + "/snapshots", json={"name": "等待审阅存档"}))["snapshot"]
    ok(client.post(prefix + "/pause"))
    client.portal.call(client.app.state.agent_service.runtime.close)
    restarted = create_app(character_settings)
    restarted.state.agent_model_adapter = FakeModelAdapter(responder=scenario)
    with TestClient(
        restarted, headers=headers(character_settings.host_admin_token.get_secret_value())
    ) as other:
        assert ok(other.get(prefix + "/agent-cycle"))["id"] == cycle["id"]
        ok(other.post(prefix + f"/snapshots/{save['id']}/load", json={}))
        ok(other.post(prefix + "/resume"))
        ok(
            other.post(
                prefix + f"/review-requests/{review['id']}/edit-and-approve",
                json={"public_summary": "一本公开确认的时刻表。"},
            )
        )
        completed = wait(other, prefix, ("completed", "failed"))
        assert completed["status"] == "completed" and completed["id"] == cycle["id"], completed
        public = ok(other.get(prefix + "/public-entities"))
        ok(other.post(prefix + "/pause"))
        ok(other.post(prefix + f"/snapshots/{save['id']}/load", json={}))
        assert ok(other.get(prefix + "/public-entities")) == public
        assert ok(other.get(prefix + "/review-requests"))[0]["status"] == "edited"


@pytest.mark.parametrize("approved_relation", [True, False])
def test_scene_transition_uses_approved_relation_or_interrupt(
    client, prepared_game, approved_relation
):
    from app.persistence.preparation_models import RoomEntityState
    from app.preparation.schemas import EntityFields

    game = prepared_game
    entity_id = str(uuid4())

    async def seed_scene():
        svc = client.app.state.agent_service
        async with svc.rooms.transaction() as session:
            binding = await svc.entities.binding(session, game["room"]["id"])
            fields = EntityFields(
                type="scene", title="通道", public_summary="通道里很安静。"
            ).model_dump()
            session.add(
                RoomEntityState(
                    room_id=binding.room_id,
                    source_entity_id=entity_id,
                    entity_type="scene",
                    snapshot={
                        **fields,
                        "id": entity_id,
                        "status": "approved",
                        "generated_by": "host",
                        "version": 1,
                        "source_references": [],
                    },
                    state="hidden",
                    frozen_public_summary=fields["public_summary"],
                    frozen_source_references=[],
                )
            )
            if approved_relation:
                binding.relations = [
                    {
                        "source_entity_id": binding.current_scene,
                        "target_entity_id": entity_id,
                        "relation_type": "leads_to",
                        "keeper_note": "隐藏转换关系",
                        "status": "approved",
                    }
                ]
            # The synthetic runtime entity must also exist in the frozen structure.
            # Seed a new approved snapshot, as a host would before starting this room.
            from app.module_ir.schemas import EntityNodeBinding, SceneTransition
            from app.persistence.module_ir_models import ApprovedStructure

            nav = await svc.navigation.state(session, binding.room_id)
            snapshot, _ = await svc.navigation.snapshot(session, nav)
            target = next(n for n in snapshot.nodes if n.title == "通道")
            target.approved_type = "scene"
            target.public_title = fields["title"]
            target.public_summary = fields["public_summary"]
            snapshot.entity_bindings.append(
                EntityNodeBinding(
                    binding_id=str(uuid4()),
                    entity_id=entity_id,
                    node_id=target.node_id,
                    source_hash=snapshot.source_hash,
                )
            )
            if approved_relation:
                snapshot.transitions.append(
                    SceneTransition(
                        transition_id=str(uuid4()),
                        source_scene_node_id=nav.current_scene_node_id,
                        target_scene_node_id=target.node_id,
                        approved=True,
                    )
                )
            snapshot.snapshot_id = str(uuid4())
            session.add(
                ApprovedStructure(
                    id=snapshot.snapshot_id,
                    preparation_id=snapshot.preparation_id,
                    document=snapshot.model_dump(mode="json"),
                )
            )
            nav.structure_snapshot_id = snapshot.snapshot_id
            await svc.navigation.persist(session, nav)

    client.portal.call(seed_scene)

    def transition(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if context.get("phase") == "plan_keeper_action":
            return {"tools": [{"name": "transition_scene", "arguments": {"scene_id": entity_id}}]}
        return scenario(messages, kwargs)

    game["adapter"].responder = transition
    submit(client, game, "进入通道")
    cycle = wait(client, game["prefix"])
    if not approved_relation:
        assert cycle["status"] == "completed" and cycle["state"]["requires_clarification"], cycle
        assert ok(client.get(game["prefix"] + "/review-requests")) == []
        assert ok(client.get(game["prefix"]))["game"]["module"]["scene"]["id"] != entity_id
        return
    assert cycle["status"] == "completed", cycle
    assert ok(client.get(game["prefix"]))["game"]["module"]["scene"]["id"] == entity_id
    assert ok(client.get(game["prefix"] + "/review-requests")) == []


def test_rejection_format_failure_ends_without_second_rewrite(client, prepared_game):
    game = prepared_game
    submit(client, game)
    cycle = wait(client, game["prefix"])
    assert cycle["status"] == "waiting_for_review"
    review = ok(client.get(game["prefix"] + "/review-requests"))[0]
    game["adapter"].responder = lambda messages, kwargs: {"invalid": "format"}
    ok(client.post(game["prefix"] + f"/review-requests/{review['id']}/reject", json={}))
    cycle = wait(client, game["prefix"], ("completed", "failed"))
    assert cycle["status"] == "completed", cycle
    assert len(game["adapter"].prompts) == 2
    assert len(ok(client.get(game["prefix"] + "/public-entities"))) == 1


def test_grounded_raw_fact_becomes_review_before_canonical_memory(client, prepared_game):
    game = prepared_game

    def claim(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if context.get("phase") == "plan_keeper_action":
            evidence = context["MODULE_EVIDENCE"][0]
            return {
                "tools": [],
                "claims": [
                    {
                        "claim_id": "new_fact",
                        "category": "module_fact",
                        "statement": "站台边有一本尚未批准的时刻表。",
                        "evidence_ids": [evidence["evidence_id"]],
                        "visibility": "keeper_only",
                    }
                ],
            }
        return scenario(messages, kwargs)

    game["adapter"].responder = claim
    submit(client, game)
    assert wait(client, game["prefix"])["status"] == "waiting_for_review"
    memories = ok(client.get(game["prefix"] + "/memories"))
    assert not any("尚未批准的时刻表" in m["content"] for m in memories)
    ok(client.post(game["prefix"] + "/agent-cycle/cancel", json={}))
