"""Mixed-file provenance, pickup prerequisite reachability and resumable batch stages."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_batch24_packages import bundle  # noqa: F401

from app.preparation.packages import audit, entity_source_references


def test_mixed_files_keep_the_selected_file_and_page_identity():
    source = SimpleNamespace(source_id="mixed", source_hash="hash", title="Mixed")
    blocks = [
        SimpleNamespace(
            block_id=key, source_position=SimpleNamespace(file_reference=file, physical_page=page)
        )
        for key, file, page in [
            ("pdf", "main.pdf", 3),
            ("pdf-again", "main.pdf", 3),
            ("docx", "alternative.docx", None),
            ("legacy", "appendix.doc", 3),
            ("unselected", "different.pdf", 3),
        ]
    ]
    refs = entity_source_references(
        source,
        SimpleNamespace(blocks=blocks),
        SimpleNamespace(source_block_ids=["pdf", "pdf-again", "docx", "legacy"]),
    )
    assert {(r["file_reference"], r["physical_page"], r["page_kind"]) for r in refs} == {
        ("main.pdf", 3, "pdf"),
        ("alternative.docx", None, "text"),
        ("appendix.doc", 3, "word"),
    }
    assert len(refs) == 3


@pytest.mark.parametrize(
    "operation,reachable",
    [("pickup", True), ("initial", True), ("drop", False), ("consume", False)],
)
def test_inventory_producer_opens_gate_but_consumption_does_not(
    client, bundle, operation, reachable  # noqa: F811
):
    package = deepcopy(bundle)
    root = package["initial_node_id"]
    end = next(n for n in package["ir"]["nodes"] if n["node_id"] != root)
    for node in package["nodes"]:
        if node["node_id"] == end["node_id"]:
            node["patch"]["approved_type"] = "scene"
    key = package["entities"][1]
    key["fields"]["type"] = "item"
    key["fields"]["interactions"] = [
        {
            "id": "operate",
            "instruction": "Synthetic item action",
            "source_block_ids": key["fields"]["source_block_ids"],
            "kp_enabled": True,
            "inventory_operation": operation,
            "item_id": "paper",
            "public_result": "Item action.",
        }
    ]
    package["transitions"] = [
        {
            "source_scene_node_id": root,
            "target_scene_node_id": end["node_id"],
            "required_item_ids": ["paper"],
            "approved": True,
            "condition_summary": "Requires the acquired key",
        }
    ]
    if reachable:
        assert audit(package, client.app.state.agent_service.settings.data_dir)[
            "static_validation_passed"
        ]
    else:
        with pytest.raises(ValueError, match="conditional scene prerequisite cycle"):
            audit(package, client.app.state.agent_service.settings.data_dir)


def test_completed_generation_is_not_rerun_and_unreviewed_source_cannot_be_approved(
    tmp_path, monkeypatch
):
    from scripts import prepare_batch41 as queue

    monkeypatch.setattr(queue, "ROOT", tmp_path)
    entry = {
        "module_id": "synthetic",
        "decision": "prepare",
        "source_hash": "unchanged",
        "output_directory": "data/prepared/synthetic/batch-41/unchanged",
    }
    out = tmp_path / entry["output_directory"]
    queue.write(out / "indexed-source.json", {})
    queue.write(
        out / "generation-progress.json",
        {"source_hash": "unchanged", "preparation": {"status": "review_ready"}},
    )

    async def unexpected(*args):
        pytest.fail("Completed generation must not be called again")

    monkeypatch.setattr(queue, "generate", unexpected)
    assert queue.process(entry, "resume")["stage"] == "awaiting_source_review"
    assert not (out / "package-reviewed.json").exists()


def test_build_rejects_review_flag_without_reviewed_file_manifest(tmp_path):
    from scripts.build_batch41 import build
    from scripts.prepare_batch41 import write

    write(tmp_path / "reviewed-spec.json", {"source_review_complete": True})
    write(tmp_path / "source-manifest.json", {"review_complete": False})
    with pytest.raises(ValueError, match="file manifest"):
        build(tmp_path)


def test_resume_does_not_reuse_generation_for_a_different_source(tmp_path, monkeypatch):
    from scripts import prepare_batch41 as queue

    monkeypatch.setattr(queue, "ROOT", tmp_path)
    entry = {"decision": "prepare", "source_hash": "new", "output_directory": "new-version"}
    out = tmp_path / "new-version"
    queue.write(out / "indexed-source.json", {})
    queue.write(out / "generation-progress.json", {
        "source_hash": "old", "preparation": {"status": "review_ready"},
    })
    seen = []

    async def generate(value):
        seen.append(value["source_hash"])
        return {"stage": "generated_unreviewed"}

    monkeypatch.setattr(queue, "generate", generate)
    assert queue.process(entry, "generate")["stage"] == "generated_unreviewed"
    assert seen == ["new"]


def test_inventory_refresh_preserves_delivery_identity_and_acceptance(tmp_path, monkeypatch):
    from scripts import prepare_batch41 as queue

    monkeypatch.setattr(queue, "ROOT", tmp_path)
    monkeypatch.setattr(queue, "QUEUE", tmp_path / "data/prepared/batch-41")
    queue.QUEUE.mkdir(parents=True)
    source = tmp_path / "data/modules/example"
    source.mkdir(parents=True)
    (source / "main.txt").write_text("A complete synthetic source.", encoding="utf-8")
    first = queue.inventory()
    first["modules"][0].update(
        status="delivered",
        source_scope={"independent_complete_module_count": 1, "physical_pages": 1},
        review={"source_review_complete": True},
        delivery={
            "preparation_id": "approved-id", "version": 2, "short_validation_status": "passed"
        },
    )
    queue.write(queue.QUEUE / "preparation-inventory.json", first)
    refreshed = queue.inventory()
    assert refreshed["modules"][0]["delivery"] == first["modules"][0]["delivery"]
    assert refreshed["modules"][0]["status"] == "delivered"
    assert refreshed["counts"]["full_source_character_short_validation_passed"] == 1
    assert refreshed["counts"]["pending_source_preparation"] == 0
