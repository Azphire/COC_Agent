"""Portable HO source validation and backward compatibility, with synthetic prose."""

from copy import deepcopy

import pytest
from test_batch24_packages import bundle, import_bundle  # noqa: F401
from test_rooms import ok

from app.domain.handouts import PreparedHandout
from app.preparation import packages


@pytest.fixture
def handout_bundle(bundle, monkeypatch):  # noqa: F811
    for block in bundle["ir"]["blocks"]:
        block["source_position"]["physical_page"] = 1
    handout = PreparedHandout(
        id="HO1",
        title="Synthetic personal dossier",
        text="Only this recipient knows AMBER.",
        source_hash=bundle["ir"]["source_hash"],
        source_pages=[1],
        source_block_ids=[b["block_id"] for b in bundle["ir"]["blocks"]],
        adjustments={"skill_bonuses": {"psychology": 30}},
    ).model_dump(mode="json")
    registry = deepcopy(packages.evidence_registry())
    registry.setdefault("source_handouts", {})[handout["source_hash"]] = {
        handout["id"]: packages.content_digest(handout)
    }
    monkeypatch.setattr(packages, "evidence_registry", lambda: registry)
    bundle["handouts"] = [handout]
    return bundle


def test_import_retains_verified_ho_and_reuses_identical_content(client, handout_bundle):
    first = ok(import_bundle(client, handout_bundle))
    assert first["preparation"]["handouts"] == handout_bundle["handouts"]
    assert first["coverage"]["handouts"]["count"] == 1
    second = ok(import_bundle(client, handout_bundle))
    assert second["reused"] and first["preparation_id"] == second["preparation_id"]


@pytest.mark.parametrize("mutation", ["text", "bonus", "hash", "pages", "blocks", "duplicate"])
def test_unreviewed_text_bonus_and_source_cannot_be_smuggled(client, handout_bundle, mutation):
    value = deepcopy(handout_bundle)
    handout = value["handouts"][0]
    if mutation == "text":
        handout["text"] += " Invented secret"
    elif mutation == "bonus":
        handout["adjustments"]["skill_bonuses"]["psychology"] = 99
    elif mutation == "hash":
        handout["source_hash"] = "0" * 64
    elif mutation == "pages":
        handout["source_pages"] = [99]
    elif mutation == "blocks":
        handout["source_block_ids"] = ["not-a-source-block"]
    else:
        value["handouts"].append(deepcopy(handout))
    assert import_bundle(client, value).status_code == 422


def test_old_package_without_handouts_still_imports(client, bundle):  # noqa: F811
    result = ok(import_bundle(client, bundle))
    assert result["preparation"]["handouts"] == []
    assert result["preparation"]["package_bindable"]
