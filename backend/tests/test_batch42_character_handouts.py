"""Native HO calculation, consent and trusted-source regressions."""

from copy import deepcopy

import pytest

from app.domain.character import CharacterDraft, CharacterSheet
from app.domain.handouts import CharacterHandout, HandoutAdjustments, PreparedHandout
from app.persistence.preparation_models import ModulePreparation
from app.preparation.packages import content_digest
from app.rules.engine import recalculate
from app.rules.handouts import approve_module_handout

RULESET = "coc7-character-creation"
ATTRIBUTES = {"str": 50, "con": 60, "siz": 55, "dex": 60,
              "app": 55, "int": 65, "pow": 60, "edu": 55}


def handout(number):
    adjustments = HandoutAdjustments(
        required_age=19, required_occupation="student", required_era="modern",
        attribute_points=30 if number == 2 else 0,
        attribute_choices=["str", "pow", "dex"] if number == 2 else [],
        attribute_maxima={"str": 99, "dex": 99} if number == 2 else {},
        credit_maximum=5 if number == 2 else None,
        skill_bonuses={} if number == 2 else {
            "credit_rating": 30, "psychology" if number == 1 else "stealth": 30,
        },
    )
    return PreparedHandout(
        id=f"HO{number}", title=f"HO {number}", text=f"Private handout {number}",
        source_hash="a" * 64, source_pages=[3 + 2 * number],
        source_block_ids=[f"test-block-{number}"], adjustments=adjustments,
    )


@pytest.fixture
def trusted_preparation(client, monkeypatch):
    definitions = [handout(n) for n in (1, 2, 3)]
    registry = {"source_handouts": {"a" * 64: {
        item.id: content_digest(item.model_dump(mode="json")) for item in definitions
    }}}
    monkeypatch.setattr("app.preparation.packages.evidence_registry", lambda: registry)

    async def insert():
        async with client.app.state.database.sessions.begin() as session:
            session.add(ModulePreparation(
                id="batch42-prep", source_id="batch42-source", source_hash="a" * 64,
                status="approved", document={
                    "handouts": [item.model_dump(mode="json") for item in definitions],
                },
            ))

    client.portal.call(insert)
    return definitions


def base_card(client):
    response = client.post("/api/characters/point-buy", json={
        "ruleset_id": RULESET, "name": "Batch 42", "age": 19,
        "attributes": {key: {"value": value} for key, value in ATTRIBUTES.items()},
    })
    assert response.status_code == 201, response.text
    card = response.json()
    response = client.patch(f"/api/characters/{card['id']}", json={
        "version": card["version"], "age_deductions": {"str": 5}, "era": "modern",
        "occupation": "student", "selected_specializations": ["language_english"],
        "occupation_group_choices": {
            "language": ["language_english"],
            "academic": ["history", "psychology", "persuade"],
            "personal": ["spot_hidden", "stealth"],
        },
        "occupation_skills": {key: {"points": value} for key, value in {
            "credit_rating": 5, "psychology": 40, "persuade": 40,
            "listen": 30, "history": 45, "language_english": 40,
        }.items()},
        "interest_skills": {"dodge": {"points": 20}, "first_aid": {"points": 20}},
    })
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["valid"], response.text
    return response.json()


def select(client, card, number, allocations=None, approve=False):
    return client.patch(f"/api/characters/{card['id']}", json={
        "version": card["version"], "module_handout": {
            "preparation_id": "batch42-prep", "handout_id": f"HO{number}",
            "attribute_allocations": allocations or {},
        }, "approve_module_handout": approve,
    })


@pytest.mark.parametrize("number,key,expected", [(1, "psychology", 80), (3, "stealth", 50)])
def test_native_skill_credit_preview_consent_and_repeat(
    client, trusted_preparation, number, key, expected,
):
    base = base_card(client)
    response = select(client, base, number)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["attributes"] == base["attributes"]
    assert preview["occupation_skills"] == base["occupation_skills"]
    assert preview["skill_values"][key] == expected
    assert preview["skill_values"]["credit_rating"] == 35
    assert preview["finances"]["assets"] > base["finances"]["assets"]
    assert {i["code"] for i in preview["validation"]["issues"]} == {"keeper_approval"}
    assert client.post(f"/api/characters/{base['id']}/finalize", json={
        "version": preview["version"],
    }).status_code == 422
    response = select(client, preview, number, approve=True)
    assert response.status_code == 200, response.text
    approved = response.json()
    assert approved["validation"]["valid"]
    again = select(client, approved, number, approve=True).json()
    assert again["module_handout_effects"] == approved["module_handout_effects"]
    assert again["skill_values"] == approved["skill_values"]
    frozen = client.post(f"/api/characters/{base['id']}/finalize", json={
        "version": again["version"],
    }).json()
    assert frozen["status"] == "finalized"
    assert client.patch(f"/api/characters/{base['id']}", json={
        "version": frozen["version"], "module_handout": None,
    }).status_code == 409
    assert client.get(f"/api/characters/{base['id']}").json() == frozen


def test_attribute_bonus_rebuilds_derived_skill_bases_and_roundtrip(client, trusted_preparation):
    base = base_card(client)
    response = select(client, base, 2, {"dex": 30}, approve=True)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["validation"]["valid"]
    assert card["attributes"]["dex"]["value"] == 60
    assert card["effective_attributes"]["dex"] == 90
    assert card["derived_values"]["dex_half"] == 45
    assert card["skill_base_values"]["dodge"] == 45
    assert card["skill_values"]["dodge"] == 65  # Batch 41 side table incorrectly kept 50.
    assert card["skill_half_values"]["dodge"] == 32
    assert card["skill_fifth_values"]["dodge"] == 13
    frozen_response = client.post(f"/api/characters/{card['id']}/finalize", json={
        "version": card["version"],
    })
    assert frozen_response.status_code == 200, frozen_response.text
    frozen = CharacterSheet.model_validate(frozen_response.json())
    rules = client.app.state.character_rulesets[RULESET]
    for _ in range(3):
        recalculate(frozen, rules)
        assert frozen.effective_attributes["dex"] == 90
        assert frozen.skill_values["dodge"] == 65
        assert frozen.validation.valid
    exported = client.get(f"/api/characters/{card['id']}/export").json()
    response = client.post("/api/characters/import", json=exported)
    assert response.status_code == 201, response.text
    restored = response.json()
    assert restored["module_handout_approval"] is None
    assert restored["effective_attributes"]["dex"] == 90
    assert restored["skill_values"]["dodge"] == 65
    assert {i["code"] for i in restored["validation"]["issues"]} == {"keeper_approval"}


def test_str_pow_split_uses_age_adjusted_base_and_recomputes_derived(client, trusted_preparation):
    base = base_card(client)
    response = select(client, base, 2, {"str": 15, "pow": 15}, approve=True)
    card = response.json()
    assert card["validation"]["valid"], response.text
    assert card["effective_attributes"]["str"] == 60  # 50 - age 5 + HO 15.
    assert card["effective_attributes"]["pow"] == 75
    assert card["derived_values"]["san"] == 75
    assert card["derived_values"]["mp"] == 15
    assert card["derived_values"]["mov"] == 9
    assert card["module_handout_effects"][0]["base_value"] == 45
    changed = CharacterDraft.model_validate(card)
    changed.background.appearance = "Changed source-required character description"
    recalculate(changed, client.app.state.character_rulesets[RULESET])
    assert changed.module_handout_approval is None
    assert changed.effective_attributes["pow"] == 75


@pytest.mark.parametrize(
    "allocation,code", [({"dex": 29}, "allocation"), ({"con": 30}, "selection")],
)
def test_incomplete_or_wrong_attribute_choices_cannot_finalize(
    client, trusted_preparation, allocation, code,
):
    card = select(client, base_card(client), 2, allocation, approve=True).json()
    assert code in {i["code"] for i in card["validation"]["issues"]}
    assert client.post(f"/api/characters/{card['id']}/finalize", json={
        "version": card["version"],
    }).status_code == 422


def test_credit_limit_caps_and_approval_invalidation(client, trusted_preparation):
    base = base_card(client)
    card = select(client, base, 2, {"dex": 30}, approve=True).json()
    response = client.patch(f"/api/characters/{card['id']}", json={
        "version": card["version"], "interest_skills": {"credit_rating": {"points": 1}},
    })
    changed = response.json()
    assert changed["module_handout_approval"] is None
    assert any(
        i["field"] == "module_handout.credit_rating" for i in changed["validation"]["issues"]
    )
    draft = CharacterDraft.model_validate(base)
    draft.module_handout = CharacterHandout(
        preparation_id="batch42-prep", handout_id="HO2", attribute_allocations={"dex": 30},
        definition=handout(2),
    )
    draft.attributes["dex"].value = 80
    draft.attributes["int"].value = 45
    rules = client.app.state.character_rulesets[RULESET]
    approve_module_handout(draft, rules)
    recalculate(draft, rules)
    assert draft.effective_attributes["dex"] == 110  # No silent truncation of the source grant.
    assert any(
        i.field == "module_handout.attribute_allocations.dex" for i in draft.validation.issues
    )


def test_pow_above_100_follows_rulebook_exception_and_san_ceiling(client, trusted_preparation):
    base = base_card(client)
    attributes = deepcopy(base["attributes"])
    attributes["pow"]["value"] = 90
    attributes["str"]["value"] = 20
    response = client.patch(f"/api/characters/{base['id']}", json={
        "version": base["version"], "attributes": attributes,
    })
    assert response.status_code == 200, response.text
    response = select(client, response.json(), 2, {"pow": 30}, approve=True)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["validation"]["valid"], response.text
    assert card["attributes"]["pow"]["value"] == 90
    assert card["effective_attributes"]["pow"] == 120
    assert card["derived_values"]["san"] == card["derived_values"]["san_max"] == 99
    assert card["derived_values"]["mp"] == 24
    response = client.post(f"/api/characters/{card['id']}/finalize", json={
        "version": card["version"],
    })
    assert response.status_code == 200, response.text
    frozen = CharacterSheet.model_validate(response.json())
    for _ in range(3):
        recalculate(frozen, client.app.state.character_rulesets[RULESET])
        assert frozen.effective_attributes["pow"] == 120
        assert frozen.derived_values["san"] == 99 and frozen.derived_values["mp"] == 24
        assert frozen.validation.valid


def test_claimed_source_forgery_and_client_defined_effects_rejected(client, trusted_preparation):
    base = base_card(client)
    response = client.patch(f"/api/characters/{base['id']}", json={
        "version": base["version"], "module_handout": {
            "preparation_id": "batch42-prep", "handout_id": "HO1",
            "definition": handout(1).model_dump(mode="json"),
        },
    })
    assert response.status_code == 422
    card = select(client, base, 1, approve=True).json()
    exported = client.get(f"/api/characters/{card['id']}/export").json()
    forged = deepcopy(exported)
    forged["character"]["module_handout"]["definition"]["adjustments"]["skill_bonuses"][
        "credit_rating"
    ] = 90
    assert client.post("/api/characters/import", json=forged).status_code == 422
    forged = deepcopy(exported)
    forged["character"]["module_handout"]["definition"]["text"] = "Forged secret"
    assert client.post("/api/characters/import", json=forged).status_code == 422


def test_skill_overflow_and_removal_preserve_base_build(client, trusted_preparation):
    card = select(client, base_card(client), 1, approve=True).json()
    response = client.patch(f"/api/characters/{card['id']}", json={
        "version": card["version"], "interest_skills": {"psychology": {"points": 30}},
    })
    overflow = response.json()
    assert overflow["skill_values"]["psychology"] == 110
    assert any(i["field"] == "module_handout.psychology" for i in overflow["validation"]["issues"])
    removed = client.patch(f"/api/characters/{card['id']}", json={
        "version": overflow["version"], "module_handout": None,
    }).json()
    assert removed["validation"]["valid"]
    assert removed["module_handout_effects"] == []
    assert removed["module_handout_approval"] is None
    assert removed["skill_values"]["credit_rating"] == 5
    assert removed["skill_values"]["psychology"] == 80
