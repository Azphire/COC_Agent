import json
from uuid import uuid4

import pytest
from test_rooms import headers, lobby, ok, prepare  # noqa: F401

from app.module_ir.schemas import ModuleSearchArgs, NodeArgs
from app.persistence.module_ir_models import ApprovedStructure
from app.rooms.service import RoomError


@pytest.fixture
def structure_data(client):
    svc = client.app.state.agent_service
    folder = svc.settings.data_dir / "modules/navigation"
    folder.mkdir(parents=True)
    (folder / "chapter.md").write_text(
        "# Opening\nOPENING_ONLY keeper body. Guard key notice.\n"
        "## Details\nA read-only local detail.\n"
        "# Future\nFUTURE_SECRET never inject before arrival.\n",
        encoding="utf-8",
    )
    svc.knowledge.indexer.index("modules")
    source = svc.knowledge.repository.sources()[0]
    prep = ok(
        client.post(
            "/api/module-preparations",
            json={
                "source_id": source.source_id,
                "source_hash": source.source_hash,
                "display_title": "Synthetic",
            },
        )
    )
    entities = []
    for kind, title in (
        ("scene", "Opening"),
        ("scene", "Future"),
        ("npc", "Guard"),
        ("clue", "Notice"),
    ):
        e = ok(
            client.post(
                "/api/module-entities",
                json={
                    "preparation_id": prep["id"],
                    "type": kind,
                    "title": title,
                    "public_summary": f"Public {title}.",
                    "keeper_summary": f"PRIVATE {title}",
                },
            )
        )
        ok(client.post(f"/api/module-entities/{e['id']}/approve"))
        entities.append(e)
    ok(
        client.patch(
            f"/api/module-preparations/{prep['id']}",
            json={
                "initial_scene_entity_id": entities[0]["id"],
                "required_entity_ids": [entities[2]["id"]],
            },
        )
    )
    ok(client.post(f"/api/module-preparations/{prep['id']}/approve"))
    prefix = f"/api/module-preparations/{prep['id']}/structure"
    built = ok(client.post(prefix + "/build"))
    nodes = {n["title"]: n["node_id"] for n in built["nodes"]}
    for name in ("Opening", "Future"):
        ok(
            client.patch(
                prefix + f"/nodes/{nodes[name]}",
                json={
                    "approved_type": "scene",
                    "public_title": name,
                    "public_summary": f"Public {name}.",
                    "initial_scene": name == "Opening",
                },
            )
        )
    for e in entities:
        ok(
            client.post(
                prefix + "/entity-bindings",
                json={
                    "entity_id": e["id"],
                    "node_id": nodes["Future"] if e["title"] == "Future" else nodes["Opening"],
                    "source_hash": source.source_hash,
                },
            )
        )
    ok(
        client.post(
            prefix + "/entity-bindings",
            json={
                "entity_id": entities[2]["id"],
                "node_id": nodes["Future"],
                "source_hash": source.source_hash,
            },
        )
    )
    forward = ok(
        client.post(
            prefix + "/transitions",
            json={
                "source_scene_node_id": nodes["Opening"],
                "target_scene_node_id": nodes["Future"],
                "transition_type": "conditional",
                "required_revealed_entity_ids": [entities[3]["id"]],
                "approved": True,
            },
        )
    )
    ok(
        client.post(
            prefix + "/transitions",
            json={
                "source_scene_node_id": nodes["Future"],
                "target_scene_node_id": nodes["Opening"],
                "approved": True,
            },
        )
    )
    snapshot = ok(client.post(prefix + "/approve", json={}))
    return {
        "prep": prep,
        "entities": entities,
        "nodes": nodes,
        "prefix": prefix,
        "snapshot": snapshot,
        "source": source,
        "forward": forward,
    }


@pytest.fixture
def navigation_game(client, structure_data, lobby):  # noqa: F811
    ok(
        client.patch(
            lobby["prefix"] + "/module-preparation",
            json={"preparation_id": structure_data["prep"]["id"]},
        )
    )
    return {**lobby, **structure_data, "room_prefix": lobby["prefix"]}


def test_host_tree_bindings_and_snapshot_immutable(client, structure_data):
    d = structure_data
    structure = ok(client.get(d["prefix"]))
    assert structure["approved_snapshot_id"]
    guard = d["entities"][2]
    assert sum(b["entity_id"] == guard["id"] for b in structure["entity_bindings"]) == 2
    ok(
        client.patch(
            d["prefix"] + f"/nodes/{d['nodes']['Details']}",
            json={"included": False, "approved_type": "appendix"},
        )
    )
    changed = ok(client.get(d["prefix"]))
    assert changed["approved_snapshot_id"] is None

    async def read_snapshot():
        async with client.app.state.database.sessions() as session:
            return (await session.get(ApprovedStructure, d["snapshot"]["snapshot_id"])).document

    assert client.portal.call(read_snapshot)["approved"] is True
    assert next(n for n in client.portal.call(read_snapshot)["nodes"] if n["title"] == "Details")[
        "included"
    ]


@pytest.mark.parametrize("bad", ["hash", "rejected", "node", "cycle"])
def test_invalid_binding_and_tree_rejected(client, structure_data, bad):
    d = structure_data
    if bad == "cycle":
        r = client.patch(
            d["prefix"] + f"/nodes/{d['nodes']['Opening']}",
            json={"parent_node_id": d["nodes"]["Details"]},
        )
    else:
        entity_id = d["entities"][2]["id"]
        if bad == "rejected":
            ok(client.post(f"/api/module-entities/{entity_id}/reject"))
        r = client.post(
            d["prefix"] + "/entity-bindings",
            json={
                "entity_id": entity_id,
                "node_id": "node_forged" if bad == "node" else d["nodes"]["Opening"],
                "source_hash": "0" * 64 if bad == "hash" else d["source"].source_hash,
            },
        )
    assert r.status_code == 422


def test_unapproved_structure_cannot_bind_new_room(client, structure_data, lobby):  # noqa: F811
    d = structure_data
    ok(
        client.patch(
            d["prefix"] + f"/nodes/{d['nodes']['Opening']}", json={"title": "Host corrected"}
        )
    )
    assert (
        client.patch(
            lobby["prefix"] + "/module-preparation", json={"preparation_id": d["prep"]["id"]}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("endpoint", ["module-navigation", "module-context-debug"])
def test_player_cannot_get_debug_and_public_scene_has_no_nodes(client, navigation_game, endpoint):
    d = navigation_game
    player = headers(d["remote"]["member_token"])
    assert client.get(d["room_prefix"] + "/" + endpoint, headers=player).status_code == 403
    public = ok(client.get(d["room_prefix"] + "/current-scene", headers=player))
    raw = json.dumps(public)
    assert "Future" not in raw and "keeper" not in raw and "node_" not in raw
    assert public["title"] == "Opening"


def test_exact_scene_context_ancestors_and_scoped_search(client, navigation_game):
    d = navigation_game
    svc = client.app.state.agent_service

    async def resolve():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            keeper = await svc.module_context.resolve(session, room, "keeper", budget=5000)
            player = await svc.module_context.resolve(session, room, "investigator")
            ancestor = await svc.module_context.open_node(
                session, room, None, NodeArgs(node_id=d["snapshot"]["root_node_id"])
            )
            result = await svc.module_context.search(
                session, room, None, ModuleSearchArgs(query="FUTURE_SECRET")
            )
            with pytest.raises(RoomError):
                await svc.module_context.open_node(
                    session, room, None, NodeArgs(node_id=d["nodes"]["Future"])
                )
            with pytest.raises(RoomError):
                await svc.module_context.search(
                    session, room, None, ModuleSearchArgs(query="secret", scope="global")
                )
            return keeper, player, ancestor, result

    keeper, player, ancestor, result = client.portal.call(resolve)
    assert "OPENING_ONLY" in json.dumps(keeper)
    assert "FUTURE_SECRET" not in json.dumps(keeper)
    assert "PRIVATE" not in json.dumps(player) and "blocks" not in json.dumps(player)
    assert ancestor["blocks"] == [] and result["blocks"] == []


def test_transition_revision_idempotency_return_and_save_load(client, navigation_game):
    d = navigation_game
    prefix = d["room_prefix"]
    prepare(client, {**d, "prefix": prefix})
    first = ok(client.get(prefix + "/module-navigation"))
    save = ok(client.post(prefix + "/snapshots", json={"name": "navigation"}))["snapshot"]
    body = {
        "target_scene_node_id": d["nodes"]["Future"],
        "expected_revision": first["navigation_revision"],
        "request_id": str(uuid4()),
    }
    moved = ok(client.post(prefix + "/scene-transition", json=body))
    again = ok(client.post(prefix + "/scene-transition", json=body))
    assert moved["result"] == again["result"]
    stale = {**body, "target_scene_node_id": d["nodes"]["Opening"], "request_id": str(uuid4())}
    assert client.post(prefix + "/scene-transition", json=stale).status_code == 409
    nav = ok(client.get(prefix + "/module-navigation"))
    assert nav["previous_scene_node_id"] == d["nodes"]["Opening"]
    assert len(nav["visited_scene_node_ids"]) == 2
    assert "FUTURE_SECRET" in json.dumps(nav["context"]) and "OPENING_ONLY" not in json.dumps(
        nav["context"]
    )
    ok(client.post(prefix + "/scene-transition", json={**stale, "expected_revision": 1}))
    restored = ok(client.get(prefix + "/module-navigation"))
    assert restored["current_scene_node_id"] == d["nodes"]["Opening"]
    ok(client.post(prefix + "/pause"))
    ok(client.post(prefix + f"/snapshots/{save['id']}/load", json={}))
    loaded = ok(client.get(prefix + "/module-navigation"))
    assert loaded["current_scene_node_id"] == first["current_scene_node_id"]
    assert loaded["visited_scene_node_ids"] == first["visited_scene_node_ids"]


def test_missing_structure_preserves_public_and_blocks_context(client, navigation_game):
    d = navigation_game
    svc = client.app.state.agent_service
    with svc.knowledge.repository.connect() as db:
        db.execute(
            "DELETE FROM module_blocks WHERE structure_version=?",
            (d["snapshot"]["structure_version"],),
        )
    debug = ok(client.get(d["room_prefix"] + "/module-navigation"))
    assert debug["module_structure_missing"]
    assert ok(client.get(d["room_prefix"] + "/current-scene"))["title"] == "Opening"


def test_host_global_fallback_and_local_budget(client, navigation_game):
    d = navigation_game
    result = ok(
        client.post(
            d["room_prefix"] + "/module-search", json={"query": "FUTURE_SECRET", "scope": "global"}
        )
    )
    assert result["result"]["context_mode"] == "global_fallback"
    svc = client.app.state.agent_service

    async def bounded():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            return await svc.module_context.resolve(session, room, "keeper", budget=900)

    context = client.portal.call(bounded)
    assert context["module_context_audit"]["budget_used"] <= 900
    assert "FUTURE_SECRET" not in json.dumps(context)
