import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import create_app

DEV = "development-character-creation"


def create(client, mode="point-buy", **extra):
    response = client.post(
        f"/api/characters/{mode}",
        json={
            "ruleset_id": DEV,
            "name": "测试调查员",
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def patch(client, character, **changes):
    response = client.patch(
        f"/api/characters/{character['id']}",
        json={
            "version": character["version"],
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def complete(client, character):
    return patch(
        client,
        character,
        occupation="researcher",
        selected_occupation_skills=["observe"],
        occupation_skills={"research": {"points": 3}},
        interest_skills={"observe": {"points": 2}},
    )


def test_ruleset_endpoints(client):
    rules = client.get("/api/character-rulesets").json()
    assert {rule["id"] for rule in rules} == {DEV, "coc7-character-creation"}
    assert (
        client.get(f"/api/character-rulesets/{DEV}").json()["verification_status"] == "unverified"
    )
    assert client.get("/api/character-rulesets/missing").status_code == 422


def test_random_is_auditable_and_cannot_be_modified(client):
    character = create(client, "random")
    assert len(character["roll_records"]) == 3
    for roll in character["roll_records"]:
        assert len(roll["dice"]) == 2
        assert sum(roll["dice"]) + roll["modifier"] == roll["total"]
        assert character["attributes"][roll["attribute"]]["value"] == roll["total"]
    assert character["derived_values"]["focus"] == character["attributes"]["insight"]["value"] * 2
    response = client.patch(
        f"/api/characters/{character['id']}",
        json={
            "version": 1,
            "attributes": character["attributes"],
        },
    )
    assert response.status_code == 422
    assert client.get(f"/api/characters/{character['id']}").json() == character


def test_point_buy_balances_derived_and_separate_skills(client):
    character = create(
        client,
        attributes={
            key: {"value": value}
            for key, value in {
                "vigor": 7,
                "insight": 7,
                "learning": 7,
            }.items()
        },
        derived_values={"focus": 900},
    )
    assert character["remaining_points"] == {"attributes": 0, "occupation": 14, "interest": 7}
    assert character["derived_values"] == {"endurance": 7, "focus": 14, "capacity": 2}
    character = patch(
        client,
        character,
        occupation="researcher",
        selected_occupation_skills=["observe"],
        occupation_skills={"research": {"points": 14}},
        interest_skills={"research": {"points": 7}},
        derived_values={"focus": -999},
    )
    assert character["skill_values"]["research"] == 26
    assert character["remaining_points"] == {"attributes": 0, "occupation": 0, "interest": 0}
    assert character["validation"]["valid"]
    assert character["derived_values"]["focus"] == 14


@pytest.mark.parametrize("value,expected", [(1, "range"), (13, "range"), (12, "overspent")])
def test_invalid_allocations_remain_drafts(client, value, expected):
    character = create(
        client, attributes={key: {"value": value} for key in ["vigor", "insight", "learning"]}
    )
    assert expected in {issue["code"] for issue in character["validation"]["issues"]}
    response = client.post(f"/api/characters/{character['id']}/finalize", json={"version": 1})
    assert response.status_code == 422
    assert client.get(f"/api/characters/{character['id']}").json()["status"] == "draft"


@pytest.mark.parametrize(
    "changes",
    [
        {"occupation": "unknown"},
        {"interest_skills": {"unknown": {"points": 1}}},
        {"occupation_skills": {"unknown": {"points": 1}}},
        {"attributes": {"unknown": {"value": 1}}},
        {"selected_occupation_skills": ["unknown"]},
    ],
)
def test_unknown_choices_rejected_without_saving(client, changes):
    character = create(client)
    response = client.patch(f"/api/characters/{character['id']}", json={"version": 1, **changes})
    assert response.status_code == 422
    assert client.get(f"/api/characters/{character['id']}").json() == character


def test_invalid_occupation_membership_and_skill_caps(client):
    character = create(client)
    character = patch(
        client,
        character,
        occupation="researcher",
        selected_occupation_skills=["observe"],
        occupation_skills={"craft": {"points": 50}},
        interest_skills={"craft": {"points": 20}, "observe": {"points": -1}},
    )
    assert {"occupation", "maximum", "negative", "overspent"} <= {
        issue["code"] for issue in character["validation"]["issues"]
    }


@pytest.mark.parametrize("mode", ["random", "point-buy"])
def test_disabled_ruleset(client, character_app, mode):
    character_app.state.character_rulesets[DEV].enabled = False
    response = client.post(f"/api/characters/{mode}", json={"ruleset_id": DEV})
    assert response.status_code == 422
    assert "未启用" in response.text
    assert client.get("/api/characters").json() == []


def test_finalize_revalidates_and_locks_all_updates(client, character_app):
    character = complete(client, create(client))
    assert character["validation"]["valid"]
    # A cached valid flag cannot bypass a subsequent complete validation.
    rules = character_app.state.character_rulesets[DEV]
    rules.points.allow_unspent_points = False
    endpoint = f"/api/characters/{character['id']}"
    assert client.post(endpoint + "/finalize", json={"version": 2}).status_code == 422
    rules.points.allow_unspent_points = True
    response = client.post(endpoint + "/finalize", json={"version": 2})
    assert response.status_code == 200
    assert response.json()["status"] == "finalized"
    for changes in [{"name": "new"}, {"attributes": {}}, {"occupation_skills": {}}]:
        assert client.patch(endpoint, json={"version": 3, **changes}).status_code == 409
    assert client.post(endpoint + "/finalize", json={"version": 3}).status_code == 409
    assert client.delete(endpoint).status_code == 405


def test_stale_version_cannot_overwrite(client):
    character = create(client)
    endpoint = f"/api/characters/{character['id']}"
    current = patch(client, character, name="new")
    assert client.patch(endpoint, json={"version": 1, "name": "stale"}).status_code == 409
    assert client.get(endpoint).json() == current


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("GET", "", None),
        ("GET", "/export", None),
        ("PATCH", "", {"version": 1}),
        ("POST", "/finalize", {"version": 1}),
    ],
)
def test_missing_character_404(client, method, suffix, body):
    assert (
        client.request(method, f"/api/characters/{uuid4()}{suffix}", json=body).status_code == 404
    )


@pytest.mark.parametrize(
    "body",
    [
        {"seed": 42},
        {"age": True},
        {"age": "25"},
        {"age": 0},
        {"name": None},
        {"attributes": {"vigor": {"value": 2.5}}},
        {"attributes": {"vigor": {"value": True}}},
        {"extra": "bad"},
        {"ruleset_id": "missing"},
    ],
)
def test_invalid_create_fields_422(client, body):
    response = client.post("/api/characters/point-buy", json={"ruleset_id": DEV, **body})
    assert response.status_code == 422
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version": True},
        {"version": 1, "attributes": None},
        {"version": 1, "name": None},
        {"version": 1, "status": "finalized"},
        {"version": 1, "seed": 42},
        {"version": 1, "remaining_points": {}},
    ],
)
def test_invalid_patch_fields_422(client, body):
    character = create(client)
    assert client.patch(f"/api/characters/{character['id']}", json=body).status_code == 422


@pytest.mark.parametrize("mode", ["point-buy", "random"])
def test_export_import_recalculates_and_records_origin(client, mode):
    original = create(client, mode)
    document = client.get(f"/api/characters/{original['id']}/export").json()
    assert document["schema_version"] == 1
    serialized = json.dumps(document)
    assert all(value not in serialized for value in ["sqlite", "database_url", "Traceback", "D:\\"])
    document["character"]["derived_values"] = {"focus": 9999}
    document["character"]["effective_attributes"] = {"insight": 9999}
    document["character"]["skill_values"] = {"observe": 9999}
    document["character"]["remaining_points"] = {
        "attributes": 999,
        "occupation": 999,
        "interest": 999,
    }
    document["character"]["validation"] = {"valid": True, "issues": []}
    document["character"]["status"] = "finalized"
    response = client.post("/api/characters/import", json=document)
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["id"] != original["id"] and imported["original_id"] == original["id"]
    assert imported["status"] == "draft"
    for field in [
        "attributes",
        "derived_values",
        "effective_attributes",
        "skill_values",
        "remaining_points",
        "validation",
    ]:
        assert imported[field] == original[field]
    for record, previous in zip(imported["roll_records"], original["roll_records"], strict=True):
        assert record["source"] == "imported" and record["id"] != previous["id"]
        assert record["dice"] == previous["dice"] and record["total"] == previous["total"]
    assert len(client.get("/api/characters").json()) == 2
    assert client.get(f"/api/characters/{original['id']}").json() == original


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc.update(schema_version=2),
        lambda doc: doc.update(schema_version=True),
        lambda doc: doc.update(unexpected=1),
        lambda doc: doc["ruleset"].update(version="unknown"),
        lambda doc: doc["character"].update(ruleset_version="unknown"),
        lambda doc: doc["character"]["attributes"].update(vigor={"value": "10"}),
    ],
)
def test_bad_import_schema_and_ruleset(client, mutation):
    character = create(client)
    document = client.get(f"/api/characters/{character['id']}/export").json()
    mutation(document)
    assert client.post("/api/characters/import", json=document).status_code == 422
    assert len(client.get("/api/characters").json()) == 1


def test_restart_preserves_drafts_rolls_and_events(character_settings):
    app = create_app(character_settings)
    headers = {"Authorization": f"Bearer {character_settings.host_admin_token.get_secret_value()}"}
    with TestClient(app, headers=headers) as client:
        random = create(client, "random")
        point = complete(client, create(client))
        endpoint = f"/api/characters/{point['id']}"
        client.post(endpoint + "/finalize", json={"version": point["version"]})
        document = client.get(endpoint + "/export").json()
        client.post("/api/characters/import", json=document)
    with TestClient(create_app(character_settings), headers=headers) as restarted:
        assert restarted.get(f"/api/characters/{random['id']}").json() == random
        assert restarted.get(endpoint).json()["status"] == "finalized"
        assert len(restarted.get("/api/characters").json()) == 3
    database = character_settings.data_dir / "characters.db"
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"character_drafts", "character_roll_records", "character_events"} <= tables
        events = {row[0] for row in connection.execute("SELECT type FROM character_events")}
        assert {
            "created",
            "attributes_rolled",
            "allocation_updated",
            "occupation_selected",
            "skills_updated",
            "finalized",
            "imported",
        } <= events
        assert connection.execute("SELECT count(*) FROM character_roll_records").fetchone()[0] == 3


def test_database_errors_are_sanitized(client, monkeypatch):
    async def fail(*args):
        raise OperationalError("secret SQL", {}, RuntimeError("secret filesystem path"))

    monkeypatch.setattr("app.character.repository.CharacterRepository.list_all", fail)
    response = client.get("/api/characters")
    assert response.status_code == 503
    assert "secret" not in response.text


def test_cors_allows_character_writes(client):
    for method in ["POST", "PATCH"]:
        response = client.options(
            "/api/characters/point-buy",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
