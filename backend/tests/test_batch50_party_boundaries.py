"""Narrow batch 50 checks: coordinated equipment and sourced party expansion."""
# ruff: noqa: F811

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_batch48_party import create, ho, next_member, party  # noqa: F401

from app.domain.character_details import EquipmentEntry
from app.party.persona import ability_issues
from app.persistence.launch_models import LaunchDraft
from app.persistence.preparation_models import ModuleEntity, ModulePreparation
from app.preparation.packages import content_digest


@pytest.mark.parametrize("text", [
    "他携带手电筒和绳索。", "他携带一支手电筒与一根绳索。",
    "他随身携带手电、绳子以及相机用于记录。", "他携带手电筒并携带绳索。",
    "他携带手电筒和绳索，并未持有手枪。",
])
def test_gear_conjunctions_and_exact_aliases(rulesets, text):
    card = SimpleNamespace(skill_values={}, equipment=[SimpleNamespace(name=n)
                           for n in ("手电筒", "绳索", "照相机")])
    assert not ability_issues({"background": text}, card, rulesets["coc7-character-creation"])


@pytest.mark.parametrize("text", [
    "他携带手电筒和手枪。", "他携带手电筒、绳索以及一把手枪。",
    "他携带手电筒并携带手枪。", "他携带手电筒和枪。",
])
def test_one_legal_item_never_authorizes_unheld_weapon(rulesets, text):
    card = SimpleNamespace(skill_values={}, equipment=[SimpleNamespace(name=n)
                           for n in ("手电筒", "绳索", "枪油")])
    issues = ability_issues({"background": text}, card, rulesets["coc7-character-creation"])
    assert issues and "枪" in issues[0]["message"]


@pytest.mark.parametrize("text", [
    "他携带轻型剑（花剑、剑杖）和绳索。", "他携带花剑与绳索。",
    "他携带矛、骑士长枪和绳索。",
])
def test_catalogued_name_variants_are_not_mistaken_for_extra_gear(rulesets, text):
    card = SimpleNamespace(skill_values={}, equipment=[SimpleNamespace(name=n)
                           for n in ("轻型剑（花剑、剑杖）", "矛、骑士长枪", "绳索")])
    assert not ability_issues({"background": text}, card, rulesets["coc7-character-creation"])


@pytest.mark.parametrize("entry", ["generate", "edit", "adopt"])
@pytest.mark.parametrize("unsupported", [False, True])
def test_all_persona_entrypoints_share_item_validation(client, party, entry, unsupported):
    svc, model = party
    batch = create(client, count=1)
    path = f"/api/party-batches/{batch['id']}"

    async def equipment():
        document = await svc.get(batch["id"])
        document["members"][0]["character"]["equipment"] = [
            EquipmentEntry(id=key, catalog_id=key, name=name).model_dump(mode="json")
            for key, name in (("torch", "手电筒"), ("rope", "绳索"))
        ]
        await svc._save(document)
    client.portal.call(equipment)
    text = "他携带手电筒和绳索" + ("以及手枪。" if unsupported else "。")
    if entry == "generate":
        model.background_override = text
        result = next_member(client, batch["id"])
        assert result["status"] == ("failed" if unsupported else "ready")
    else:
        ready = next_member(client, batch["id"])
        if entry == "edit":
            result = client.patch(path + "/members/0", json={
                "request_id": str(uuid4()),
                "profile": {**ready["members"][0]["profile"], "background": text},
            })
            assert result.status_code == 200
            assert result.json()["status"] == ("failed" if unsupported else "ready")
        else:
            async def legacy():
                document = await svc.get(batch["id"])
                document["members"][0]["profile"]["background"] = text
                await svc._save(document)
            client.portal.call(legacy)
    result = client.post(path + "/adopt", json={"request_id": str(uuid4())})
    assert result.status_code == (422 if unsupported else 200), result.text
    assert len(model.messages) == 1


def preparation(client, svc, monkeypatch, definitions=None):
    definitions = definitions or [ho(1), ho(2), ho(3)]
    registry = {"source_handouts": {"a" * 64: {
        item.id: content_digest(item.model_dump(mode="json")) for item in definitions
    }}}
    monkeypatch.setattr("app.preparation.packages.evidence_registry", lambda: registry)

    async def setup():
        async with svc.database.sessions.begin() as session:
            session.add(ModulePreparation(
                id="expand-ho", source_id="source", source_hash="a" * 64, status="approved",
                document={"handouts": [d.model_dump(mode="json") for d in definitions],
                          "initial_scene_entity_id": "expand-ho-scene"},
            ))
            session.add(ModuleEntity(
                id="expand-ho-scene", preparation_id="expand-ho", type="scene",
                status="approved", document={"public_summary": "公开导入"},
            ))
    client.portal.call(setup)


def resize(client, batch_id, count, **extra):
    return client.post(f"/api/party-batches/{batch_id}/resize", json={
        "request_id": str(uuid4()), "count": count, **extra,
    })


def test_expansion_binds_ho_and_restores_exact_members_without_reroll(client, party, monkeypatch):
    svc, model = party
    preparation(client, svc, monkeypatch)
    batch = create(client, count=1, era="modern", preparation_id="expand-ho", handout_ids=["HO1"])
    next_member(client, batch["id"])
    first = client.portal.call(svc.get, batch["id"])["members"][0]
    response = resize(client, batch["id"], 2, handout_ids=["HO2"])
    assert response.status_code == 200, response.text
    second = next_member(client, batch["id"])
    assert second["members"][1]["character"]["module_handout"]["handout_id"] == "HO2"
    original = client.portal.call(svc.get, batch["id"])["members"]
    assert original[0] == first
    assert original[1]["character"]["age"] == 19
    assert original[1]["character"]["occupation"] == "student"
    assert sum(original[1]["character"]["module_handout"]["attribute_allocations"].values()) == 30
    assert resize(client, batch["id"], 1, handout_ids=[]).status_code == 200
    # Recomputed UI plans never overwrite an archived member's HO or card.
    response = resize(client, batch["id"], 3, handout_ids=["HO1", "HO3"])
    assert response.status_code == 200, response.text
    restored = client.portal.call(svc.get, batch["id"])
    assert restored["members"][:2] == original
    assert restored["members"][2]["character"]["module_handout"]["handout_id"] == "HO3"
    assert len(model.messages) == 2


@pytest.mark.parametrize("selection", [None, [], [None], ["HO1"], ["unknown"], ["HO2", "HO2"]])
def test_invalid_expansion_plan_is_rejected_before_any_card(client, party, monkeypatch, selection):
    svc, model = party
    preparation(client, svc, monkeypatch)
    batch = create(client, count=1, era="modern", preparation_id="expand-ho", handout_ids=["HO1"])
    before = client.portal.call(svc.get, batch["id"])

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid HO plan must be rejected before generating any card")
    monkeypatch.setattr(svc, "_member", forbidden)
    response = resize(client, batch["id"], 3 if selection == ["HO2", "HO2"] else 2,
                      **({} if selection is None else {"handout_ids": selection}))
    assert response.status_code == 422, response.text
    assert client.portal.call(svc.get, batch["id"]) == before and not model.messages


@pytest.mark.parametrize("changes", [
    {"required_era": "1920s"}, {"required_occupation": "not-real"},
    {"required_age": 150}, {"ruleset_id": "other"}, {"credit_maximum": 0},
])
def test_impossible_new_ho_is_blocked_before_generation(client, party, monkeypatch, changes):
    svc, model = party
    impossible = ho(2)
    impossible.adjustments = impossible.adjustments.model_copy(update=changes)
    preparation(client, svc, monkeypatch, [ho(1), impossible])
    batch = create(client, count=1, era="modern", preparation_id="expand-ho", handout_ids=["HO1"])
    before = client.portal.call(svc.get, batch["id"])

    def forbidden(*args, **kwargs):
        raise AssertionError("impossible build must be rejected before generating a card")
    monkeypatch.setattr(svc, "_member", forbidden)
    response = resize(client, batch["id"], 2, handout_ids=["HO2"])
    assert response.status_code == 422, response.text
    assert client.portal.call(svc.get, batch["id"]) == before and not model.messages


def test_own_ho_is_reserved_and_source_change_blocks_resize(client, party, monkeypatch):
    svc, _ = party
    preparation(client, svc, monkeypatch)
    batch = create(client, count=1, era="modern", preparation_id="expand-ho", handout_ids=["HO1"])

    async def link():
        document = await svc.get(batch["id"])
        document["launch"] = {"draft_id": "expand-draft", "role": "party"}
        async with svc.database.sessions.begin() as session:
            session.add(LaunchDraft(
                id="expand-draft", request_id=str(uuid4()), request_hash="test",
                document={"preparation_id": "expand-ho", "preparation_version": 1,
                          "source_hash": "a" * 64, "era": "modern", "own_handout": "HO2",
                          "party_batch_id": batch["id"], "ai_count": 1},
            ))
        await svc._save(document)
    client.portal.call(link)
    assert resize(client, batch["id"], 2, handout_ids=["HO2"]).status_code == 422

    async def alter_source():
        async with svc.database.sessions.begin() as session:
            prep = await session.get(ModulePreparation, "expand-ho")
            document = copy.deepcopy(prep.document)
            document["handouts"][2]["text"] = "changed without source approval"
            prep.document = document
    client.portal.call(alter_source)
    assert resize(client, batch["id"], 2, handout_ids=["HO3"]).status_code == 422
    assert client.portal.call(svc.get, batch["id"])["count"] == 1


def test_plain_party_expansion_and_resize_request_idempotency(client, party):
    svc, _ = party
    batch = create(client, count=1)
    original = client.portal.call(svc.get, batch["id"])["members"][0]
    body = {"request_id": str(uuid4()), "count": 2, "handout_ids": [None]}
    path = f"/api/party-batches/{batch['id']}/resize"
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert client.post(path, json=body).json() == response.json()
    assert client.post(path, json={**body, "handout_ids": ["other"]}).status_code == 409
    assert client.portal.call(svc.get, batch["id"])["members"][0] == original
