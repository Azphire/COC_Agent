"""Portable package publication and provenance; all prose is a synthetic fixture."""

from copy import deepcopy

import pytest
from test_rooms import lobby, ok  # noqa: F401

from app.module_ir.parser import parse_module
from app.preparation.packages import content_digest


@pytest.fixture
def bundle(client):
    svc = client.app.state.agent_service
    folder = svc.settings.data_dir / "modules/package-test"
    folder.mkdir(parents=True)
    (folder / "source.md").write_text(
        "# Room\nA desk hides a paper. A guard waits.\n", encoding="utf-8"
    )
    svc.knowledge.indexer.index("modules")
    source = svc.knowledge.repository.sources()[0]
    ir = parse_module(source, svc.settings.data_dir)
    with svc.knowledge.repository.connect() as db:
        chunks = [dict(r) for r in db.execute("select * from knowledge_chunks")]
    root = ir.root_node_id
    blocks = [b.block_id for b in ir.blocks]
    entities = [
        {
            "key": key,
            "node_ids": [root],
            "fields": {
                "type": kind,
                "title": key,
                "public_summary": f"Synthetic {key}",
                "reviewed_by": "fixture",
                "source_block_ids": blocks,
                "source_pages": [1],
            },
        }
        for key, kind in [("room", "scene"), ("paper", "clue"), ("guard", "npc")]
    ]
    return {
        "format_version": 1,
        "title": "Package fixture",
        "reviewer": "fixture",
        "knowledge": {"source": source.model_dump(mode="json"), "chunks": chunks},
        "ir": ir.model_dump(mode="json"),
        "entities": entities,
        "nodes": [
            {
                "node_id": n.node_id,
                "patch": {
                    "approved_type": "scene" if n.node_id == root else "chapter",
                    "public_title": n.title,
                    "public_summary": "Synthetic room",
                    "initial_scene": n.node_id == root,
                },
            }
            for n in ir.nodes
        ],
        "transitions": [],
        "initial_node_id": root,
        "initial_entity_key": "room",
        "required_entity_keys": ["paper"],
        "coverage": [
            {
                "id": "all",
                "status": "prepared",
                "gaps": [],
                "entity_keys": ["room", "paper", "guard"],
                "node_ids": [root],
                "source_orders": [b.source_order for b in ir.blocks],
            }
        ],
    }


def import_bundle(client, bundle):
    return client.post("/api/module-preparations/import", json=bundle)


def test_import_reuse_new_content_and_frozen_room(client, bundle, lobby):  # noqa: F811
    svc = client.app.state.agent_service
    before = ok(client.get("/api/model/config"))
    imported = ok(import_bundle(client, bundle))
    assert imported["preparation"]["status"] == "approved"
    assert imported["snapshot_id"]
    assert imported["coverage"]["required_operations_ready"]
    assert imported["coverage"]["runtime_acceptance"]["status"] == "not_run_by_import"
    assert imported["preparation"]["model_call_count"] == 0
    rows = ok(client.get(f"/api/module-preparations/{imported['preparation_id']}/entities"))
    assert next(e for e in rows if e["id"] == imported["entity_ids"]["paper"])["title"] == "paper"
    prefix = "/api/rooms/" + lobby["room"]["id"]
    frozen = ok(
        client.patch(
            prefix + "/module-preparation", json={"preparation_id": imported["preparation_id"]}
        )
    )["room"]
    again = ok(import_bundle(client, deepcopy(bundle)))
    assert again["reused"] and again["preparation_id"] == imported["preparation_id"]
    changed = deepcopy(bundle)
    changed["entities"][1]["fields"]["public_summary"] = "Another paper"
    newer = ok(import_bundle(client, changed))
    assert newer["preparation_id"] != imported["preparation_id"]
    assert newer["preparation"]["version"] > imported["preparation"]["version"]
    assert ok(client.get(prefix))["game"] == frozen["game"]
    assert ok(client.get("/api/model/config")) == before
    assert not svc.model.calls


@pytest.mark.parametrize(
    "case",
    ["hash", "source_path", "entity_ref", "node", "initial", "sanity", "transition", "interaction"],
)
def test_invalid_package_has_no_bindable_product(client, bundle, case):
    if case == "hash":
        bundle["knowledge"]["source"]["files"][0]["sha256"] = "0" * 64
    elif case == "source_path":
        bundle["knowledge"]["source"]["relative_reference"] = "../outside"
    elif case == "entity_ref":
        bundle["entities"][1]["fields"]["reveal_conditions"] = {"required_entity_ids": ["absent"]}
    elif case == "node":
        bundle["entities"][1]["node_ids"] = ["absent"]
    elif case == "initial":
        bundle["initial_entity_key"] = "paper"
    elif case == "sanity":
        bundle["entities"][1]["fields"]["sanity_effects"] = [{"id": "bad"}]
    elif case == "transition":
        bundle["transitions"] = [
            {
                "source_scene_node_id": bundle["initial_node_id"],
                "target_scene_node_id": "absent",
                "approved": True,
            }
        ]
    else:
        bundle["entities"][1]["fields"]["interactions"] = [
            {"id": "bad", "reveal_entity_ids": ["absent"]}
        ]
    assert import_bundle(client, bundle).status_code == 422
    assert ok(client.get("/api/module-preparations")) == []


def test_late_validation_rolls_back_every_row(client, bundle, monkeypatch):
    from app.rooms.service import RoomError

    svc = client.app.state.agent_service

    async def fail(*args):
        raise RoomError("synthetic failure after staging", 422)

    monkeypatch.setattr(svc.structure, "approve", fail)
    assert import_bundle(client, bundle).status_code == 422
    assert ok(client.get("/api/module-preparations")) == []


def supplement(bundle):
    extra = {"attributes": {"con": 50}, "hp": 4, "hp_max": 10, "armor": 0}
    bundle["entities"][2]["fields"]["combat_template"] = {"source": "fixture-design", **extra}
    bundle["required_npc_operations"] = {"guard": ["treatment"]}
    bundle["numeric_supplement"] = {
        "id": "test",
        "status": "approved",
        "user_approved": True,
        "guard": extra,
        "approval": {
            "id": "test-approval",
            "status": "approved",
            "user_approved": True,
            "approved_proposal_id": "test",
        },
    }


def test_unregistered_supplement_uses_existing_host_review(client, bundle, lobby):  # noqa: F811
    supplement(bundle)
    imported = ok(import_bundle(client, bundle))
    assert imported["preparation"]["status"] == "review_ready"
    assert not imported["snapshot_id"]
    assert imported["coverage"]["numeric_review"]["status"] == "requires_host_review"
    pid = imported["preparation_id"]
    assert (
        client.patch(
            "/api/rooms/" + lobby["room"]["id"] + "/module-preparation",
            json={"preparation_id": pid},
        ).status_code
        == 422
    )
    assert client.post(f"/api/module-preparations/{pid}/approve").status_code == 422
    ok(client.post(f"/api/module-entities/{imported['entity_ids']['guard']}/approve"))
    ok(client.post(f"/api/module-preparations/{pid}/approve"))
    assert not ok(client.get(f"/api/module-preparations/{pid}"))["package_bindable"]
    assert (
        client.patch(
            "/api/rooms/" + lobby["room"]["id"] + "/module-preparation",
            json={"preparation_id": pid},
        ).status_code
        == 422
    )
    ok(client.post(f"/api/module-preparations/{pid}/structure/approve", json={"incomplete": False}))
    assert ok(client.get(f"/api/module-preparations/{pid}"))["package_bindable"]
    ok(
        client.patch(
            "/api/rooms/" + lobby["room"]["id"] + "/module-preparation",
            json={"preparation_id": pid},
        )
    )


def test_approved_numbers_reused_but_modified_values_rejected(client, bundle, monkeypatch):
    supplement(bundle)
    monkeypatch.setattr(
        "app.preparation.packages.evidence_registry",
        lambda: {
            "numeric_approvals": {"test-approval": content_digest(bundle["numeric_supplement"])},
            "packages": {},
        },
    )
    assert ok(import_bundle(client, bundle))["preparation"]["status"] == "approved"
    bundle["entities"][2]["fields"]["combat_template"]["hp"] = 7
    assert import_bundle(client, bundle).status_code == 422
    assert len(ok(client.get("/api/module-preparations"))) == 1


def test_missing_required_operation_cannot_be_manually_approved(client, bundle):
    bundle["required_npc_operations"] = {"guard": ["combat"]}
    imported = ok(import_bundle(client, bundle))
    assert imported["preparation"]["status"] == "review_ready"
    assert imported["coverage"]["operation_gaps"] == {"guard": {"combat": ["combat_template"]}}
    assert (
        client.post(f"/api/module-preparations/{imported['preparation_id']}/approve").status_code
        == 422
    )


def test_host_only_and_source_disappeared(client, bundle):
    assert (
        client.post(
            "/api/module-preparations/import",
            json=bundle,
            headers={"Authorization": "Bearer unrelated"},
        ).status_code
        == 401
    )
    folder = client.app.state.settings.data_dir / "modules/package-test"
    (folder / "source.md").unlink()
    assert import_bundle(client, bundle).status_code == 422


def test_unrecorded_numeric_addition_stays_draft(client, bundle):
    supplement(bundle)
    del bundle["numeric_supplement"]
    imported = ok(import_bundle(client, bundle))
    assert imported["preparation"]["status"] == "review_ready"
    assert imported["coverage"]["numeric_review"]["pending_entity_keys"] == ["guard"]


def test_changed_source_blocks_binding_and_reimport(client, bundle, lobby):  # noqa: F811
    imported = ok(import_bundle(client, bundle))
    folder = client.app.state.settings.data_dir / "modules/package-test"
    (folder / "source.md").write_text("Changed original", encoding="utf-8")
    response = client.patch(
        "/api/rooms/" + lobby["room"]["id"] + "/module-preparation",
        json={"preparation_id": imported["preparation_id"]},
    )
    assert response.status_code == 422 and "source.md" in response.text
    assert import_bundle(client, bundle).status_code == 422
    assert len(ok(client.get("/api/module-preparations"))) == 1


def test_reapproved_structure_edit_does_not_reuse_original_content(client, bundle):
    imported = ok(import_bundle(client, bundle))
    prefix = f"/api/module-preparations/{imported['preparation_id']}/structure"
    ok(
        client.patch(
            prefix + "/nodes/" + bundle["initial_node_id"], json={"public_title": "Changed"}
        )
    )
    ok(client.post(prefix + "/approve", json={"incomplete": False}))
    restored = ok(import_bundle(client, bundle))
    assert not restored["reused"]
    assert restored["preparation_id"] != imported["preparation_id"]
