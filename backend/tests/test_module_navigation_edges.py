# ruff: noqa: F811
import json

from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_module_navigation_runtime import act, running_navigation  # noqa: F401
from test_rooms import headers, lobby, ok, prepare  # noqa: F401

from app.module_ir.schemas import NodeArgs
from app.persistence.module_ir_models import ApprovedStructure


def test_missing_structure_load_and_reload_exact_version(client, navigation_game):
    d = navigation_game
    prefix = d["room_prefix"]
    svc = client.app.state.agent_service
    prepare(client, {**d, "prefix": prefix})
    saved = ok(client.post(prefix + "/snapshots", json={"name": "exact version"}))["snapshot"]
    ir = svc.structure.repository.get(d["snapshot"]["structure_version"])
    public = ok(client.get(prefix + "/public-entities"))
    with svc.knowledge.repository.connect() as db:
        db.execute("DELETE FROM module_blocks WHERE structure_version=?", (ir.structure_version,))
    ok(client.post(prefix + "/pause"))
    ok(client.post(prefix + f"/snapshots/{saved['id']}/load", json={}))
    assert ok(client.get(prefix + "/module-navigation"))["module_structure_missing"]
    assert client.post(prefix + "/resume").status_code == 409
    assert ok(client.get(prefix + "/public-entities")) == public
    assert client.post(prefix + "/module-navigation/reload").status_code == 409
    svc.structure.repository.store(ir)
    ok(client.post(prefix + "/module-navigation/reload"))
    ok(client.post(prefix + "/resume"))
    restored = ok(client.get(prefix + "/module-navigation"))
    assert not restored["module_structure_missing"]
    assert restored["structure_snapshot_id"] == d["snapshot"]["snapshot_id"]


def test_snapshot_source_mismatch_does_not_change_public_scene(client, navigation_game):
    d = navigation_game
    svc = client.app.state.agent_service

    async def corrupt():
        async with svc.rooms.transaction() as session:
            row = await session.get(ApprovedStructure, d["snapshot"]["snapshot_id"])
            row.document = {**row.document, "source_hash": "f" * 64}

    client.portal.call(corrupt)
    assert ok(client.get(d["room_prefix"] + "/module-navigation"))["module_structure_missing"]
    public = ok(
        client.get(
            d["room_prefix"] + "/current-scene", headers=headers(d["remote"]["member_token"])
        )
    )
    assert public["title"] == "Opening" and "PRIVATE" not in json.dumps(public)


def test_source_change_prevents_new_binding(client, structure_data, lobby):  # noqa: F811
    d = structure_data
    svc = client.app.state.agent_service
    (svc.settings.data_dir / "modules/navigation/chapter.md").write_text("# New\nBody.")
    svc.knowledge.indexer.index("modules")
    assert (
        client.patch(
            lobby["prefix"] + "/module-preparation", json={"preparation_id": d["prep"]["id"]}
        ).status_code
        == 422
    )


def test_entity_paths_and_linked_node_scope(client, navigation_game):
    d = navigation_game
    svc = client.app.state.agent_service

    async def lookup():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            result = await svc.module_context.lookup(session, room, "Guard")
            assert all("Future" not in path for path in result["candidates"][0]["node_paths"])
            row = await session.get(ApprovedStructure, d["snapshot"]["snapshot_id"])
            document = json.loads(json.dumps(row.document))
            next(n for n in document["nodes"] if n["title"] == "Opening")["linked_node_ids"] = [
                d["nodes"]["Future"]
            ]
            row.document = document
            result = await svc.module_context.open_node(
                session, room, None, NodeArgs(node_id=d["nodes"]["Future"])
            )
            assert "FUTURE_SECRET" in json.dumps(result)

    client.portal.call(lookup)


def test_session_text_cannot_override_authoritative_position(client, navigation_game):
    d = navigation_game
    prefix = d["room_prefix"]
    prepare(client, {**d, "prefix": prefix})
    room = ok(client.get(prefix))
    body = {
        "expected_revision": room["revision"],
        "state": {**room["session_state"], "scene_title": "Future", "scene_summary": "Forged"},
    }
    changed = ok(client.patch(prefix + "/session-state", json=body))["room"]
    assert changed["session_state"]["scene_title"] == "Opening"
    assert ok(client.get(prefix + "/module-navigation"))["navigation_revision"] == 0


def test_pending_navigation_review_save_load_resumes_same_cycle(client, running_navigation):
    d = running_navigation
    prefix = d["room_prefix"]
    cycle = act(client, d, "move")
    assert cycle["status"] == "waiting_for_review"
    save = ok(client.post(prefix + "/snapshots", json={"name": "pending transition"}))["snapshot"]
    before = ok(client.get(prefix + "/module-navigation"))
    ok(client.post(prefix + "/pause"))
    ok(client.post(prefix + f"/snapshots/{save['id']}/load", json={}))
    restored = ok(client.get(prefix + "/module-navigation"))
    assert restored["pending_review_id"] == before["pending_review_id"]
    assert restored["pending_transition"] == before["pending_transition"]
    assert ok(client.get(prefix + "/agent-cycle"))["id"] == cycle["id"]
    ok(client.post(prefix + "/resume"))
    ok(client.post(prefix + f"/review-requests/{before['pending_review_id']}/approve", json={}))
    from test_host_review import wait

    assert wait(client, prefix, ("completed", "failed"))["status"] == "completed"
    assert (
        ok(client.get(prefix + "/module-navigation"))["current_scene_node_id"]
        == d["nodes"]["Future"]
    )
