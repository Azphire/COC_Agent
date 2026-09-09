import random
from uuid import UUID

import pytest

from app.character.repository import CharacterRepository, VersionConflict
from app.character.schemas import PointBuyRequest
from app.character.service import CharacterService
from app.dice.service import DiceService
from app.domain.character import CharacterDraft
from app.persistence.database import Database
from app.rules.age import age_band
from app.rules.engine import calculate, recalculate

COC7 = "coc7-character-creation"
ATTRIBUTES = {
    "str": 60,
    "con": 60,
    "siz": 60,
    "dex": 60,
    "app": 50,
    "int": 60,
    "pow": 50,
    "edu": 60,
}


def create_seventh(client, mode="point-buy", age=25):
    body = {"ruleset_id": COC7, "name": "第七版调查员", "age": age}
    if mode == "point-buy":
        body["attributes"] = {key: {"value": value} for key, value in ATTRIBUTES.items()}
    response = client.post(f"/api/characters/{mode}", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_coc7_source_and_point_buy(client):
    rules = client.get(f"/api/character-rulesets/{COC7}").json()
    assert rules["enabled"] and rules["verification_status"] == "verified"
    assert rules["edition"] == "coc7" and len(rules["source_reference"]) == 3
    character = create_seventh(client)
    assert sum(item["value"] for item in character["attributes"].values()) == 460
    assert character["remaining_points"]["attributes"] == 0
    assert character["remaining_points"]["interest"] == 120
    assert (
        character["remaining_points"]["occupation"] == character["effective_attributes"]["edu"] * 4
    )
    assert (
        character["skill_base_values"]["own_language"] == character["effective_attributes"]["edu"]
    )
    assert character["skill_base_values"]["dodge"] == 30
    assert character["skill_half_values"]["spot_hidden"] == 12
    assert character["skill_fifth_values"]["spot_hidden"] == 5
    derived = character["derived_values"]
    assert (derived["hp"], derived["mp"], derived["san"], derived["mov"]) == (12, 10, 50, 8)
    assert derived["damage_bonus"] == "0" and derived["build"] == 0
    assert derived["str_half"] == 30 and derived["str_fifth"] == 12


@pytest.mark.parametrize(
    "age,checks,luck,deduction,penalty",
    [
        (15, 0, 2, 5, 0),
        (19, 0, 2, 5, 0),
        (20, 1, 1, 0, 0),
        (39, 1, 1, 0, 0),
        (40, 2, 1, 5, 1),
        (49, 2, 1, 5, 1),
        (50, 3, 1, 10, 2),
        (59, 3, 1, 10, 2),
        (60, 4, 1, 20, 3),
        (69, 4, 1, 20, 3),
        (70, 4, 1, 40, 4),
        (79, 4, 1, 40, 4),
        (80, 4, 1, 80, 5),
        (89, 4, 1, 80, 5),
    ],
)
def test_age_bands_and_audited_random_rolls(client, age, checks, luck, deduction, penalty):
    character = create_seventh(client, "random", age)
    records = character["roll_records"]
    assert len(records) == 8 + checks * 2 + luck
    assert len([roll for roll in records if roll["purpose"] == "luck"]) == luck
    for roll in records:
        assert roll["total"] == sum(roll["dice"]) + roll["modifier"]
        if roll["purpose"] == "attribute":
            assert character["attributes"][roll["attribute"]]["value"] == roll["total"] * 5
    assert character["derived_values"]["luck"] == max(
        roll["total"] * 5 for roll in records if roll["purpose"] == "luck"
    )
    endpoint = f"/api/characters/{character['id']}"
    assert client.get(endpoint).json()["roll_records"] == records
    assert client.patch(endpoint, json={"version": 1, "age": age + 1}).status_code == 422
    rules = client.app.state.character_rulesets[COC7]
    band = age_band(CharacterDraft.model_validate(character), rules)
    assert band.deduction_pool == deduction and band.movement_penalty == penalty


@pytest.mark.parametrize("age", [None, 14, 90, 120])
def test_unsupported_creation_age_rejected(client, age):
    response = client.post("/api/characters/random", json={"ruleset_id": COC7, "age": age})
    assert response.status_code == 422


def test_age_adjustment_and_education_are_deterministic(client):
    raw = create_seventh(client, age=42)
    character = CharacterDraft.model_validate(raw)
    rules = client.app.state.character_rulesets[COC7]
    character.attributes["edu"].value = 80
    character.age_deductions = {"dex": 5}
    records = {roll.attribute: roll for roll in character.roll_records}
    for name, value in {
        "education_check_1": 86,
        "education_gain_1": 4,
        "education_check_2": 82,
        "education_gain_2": 10,
    }.items():
        records[name].dice = [value]
        records[name].total = value
    recalculate(character, rules)
    assert character.effective_attributes["edu"] == 84
    assert character.effective_attributes["dex"] == 55
    assert character.effective_attributes["app"] == 45
    assert character.derived_values["mov"] == 7
    assert character.skill_base_values["own_language"] == 84
    assert character.remaining_points.occupation == 336
    before = character.model_dump()
    recalculate(character, rules)
    assert character.model_dump() == before
    character.attributes["edu"].value = 90
    records["education_check_1"].dice = [100]
    records["education_check_1"].total = 100
    records["education_gain_1"].dice = [10]
    records["education_gain_1"].total = 10
    recalculate(character, rules)
    assert character.effective_attributes["edu"] == 99


def test_minor_and_invalid_age_deductions(client):
    character = create_seventh(client, age=18)
    endpoint = f"/api/characters/{character['id']}"
    response = client.patch(endpoint, json={"version": 1, "age_deductions": {"str": 3, "siz": 2}})
    assert response.status_code == 200
    updated = response.json()
    assert updated["effective_attributes"]["str"] == 57
    assert updated["effective_attributes"]["siz"] == 58
    assert updated["effective_attributes"]["edu"] == 55
    assert updated["remaining_points"]["attributes"] == 0
    assert updated["roll_records"] == character["roll_records"]
    response = client.patch(endpoint, json={"version": 2, "age_deductions": {"pow": 5}})
    assert not response.json()["validation"]["valid"]
    assert any(
        issue["field"] == "age_deductions" for issue in response.json()["validation"]["issues"]
    )


@pytest.mark.parametrize(
    "total,build,damage",
    [
        (64, -2, "-2"),
        (65, -1, "-1"),
        (84, -1, "-1"),
        (85, 0, "0"),
        (124, 0, "0"),
        (125, 1, "+1d4"),
        (164, 1, "+1d4"),
        (165, 2, "+1d6"),
        (180, 2, "+1d6"),
    ],
)
def test_damage_and_build_boundaries(rulesets, total, build, damage):
    rules = {rule.key: rule for rule in rulesets[COC7].derived_values}
    assert calculate(rules["build"], {"str_siz": total}) == build
    assert calculate(rules["damage_bonus"], {"str_siz": total}) == damage


@pytest.mark.parametrize(
    "strength,dexterity,size,expected",
    [
        (50, 50, 60, 7),
        (60, 50, 60, 8),
        (60, 60, 60, 8),
        (70, 60, 60, 8),
        (70, 70, 60, 9),
    ],
)
def test_movement_comparisons(rulesets, strength, dexterity, size, expected):
    rule = next(item for item in rulesets[COC7].derived_values if item.key == "mov")
    assert calculate(rule, {"str": strength, "dex": dexterity, "siz": size}) == expected


def test_credit_mythos_and_finalization(client):
    character = create_seventh(client)
    endpoint = f"/api/characters/{character['id']}"
    selection = ["history", "biology", "chemistry", "occult"]
    response = client.patch(
        endpoint,
        json={
            "version": 1,
            "occupation": "professor",
            "selected_occupation_skills": selection,
            "occupation_skills": {"credit_rating": {"points": 19}},
            "interest_skills": {"cthulhu_mythos": {"points": 1}},
        },
    )
    assert response.status_code == 200
    assert {"credit_rating", "forbidden"} <= {
        issue["code"] for issue in response.json()["validation"]["issues"]
    }
    response = client.patch(
        endpoint,
        json={
            "version": 2,
            "occupation_skills": {"credit_rating": {"points": 20}, "history": {"points": 40}},
            "interest_skills": {"spot_hidden": {"points": 30}},
        },
    )
    assert response.json()["validation"]["valid"], response.text
    assert client.post(endpoint + "/finalize", json={"version": 3}).status_code == 200


def test_point_buy_edu_can_be_15_and_records_cannot_be_forged(client):
    character = create_seventh(client)
    endpoint = f"/api/characters/{character['id']}"
    attributes = character["attributes"]
    attributes["edu"]["value"] = 15
    response = client.patch(endpoint, json={"version": 1, "attributes": attributes})
    assert not any(
        issue["field"] == "attributes.edu" for issue in response.json()["validation"]["issues"]
    )
    assert response.json()["roll_records"] == character["roll_records"]
    document = client.get(endpoint + "/export").json()
    document["character"]["roll_records"][0]["dice"][0] = 0
    response = client.post("/api/characters/import", json=document)
    # Structural or business validation must reject/flag an inconsistent audit trail.
    assert response.status_code == 422 or not response.json()["validation"]["valid"]


async def test_repository_atomic_version_guard_and_seeded_creation(character_settings, rulesets):
    database = Database(character_settings.database_url)
    await database.initialize()
    try:
        repository = CharacterRepository(database)
        service = CharacterService(repository, rulesets, DiceService(random.Random(12)))
        character = await service.create_point_buy(
            PointBuyRequest(
                ruleset_id=COC7,
                age=25,
                name="版本测试",
            )
        )
        copy = await repository.get(UUID(str(character.id)))
        character.version = 2
        await repository.save(character, [("allocation_updated", {})], expected_version=1)
        copy.name = "stale"
        copy.version = 2
        with pytest.raises(VersionConflict):
            await repository.save(copy, [("allocation_updated", {})], expected_version=1)
        persisted = await repository.get(character.id)
        assert persisted.name == "版本测试" and persisted.version == 2
    finally:
        await database.close()
