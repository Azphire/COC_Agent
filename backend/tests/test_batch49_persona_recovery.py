"""Batch 49: preserve rolled investigators while repairing bounded prose failures."""
# ruff: noqa: F811

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_batch48_party import create, ho, next_member, party  # noqa: F401

from app.party.persona import ability_issues
from app.persistence.preparation_models import ModuleEntity, ModulePreparation
from app.preparation.packages import content_digest


def test_exhausted_persona_can_repair_without_reroll(client, party):
    svc, model = party
    initial = create(client)
    first = next_member(client, initial["id"])
    model.fail = True
    for _ in range(4):
        failed = next_member(client, initial["id"])
    before = client.portal.call(svc.get, initial["id"])
    assert len(model.messages) == 5
    assert client.post(f"/api/party-batches/{initial['id']}/next", json={
        "request_id": str(uuid4()),
    }).status_code == 422
    model.fail = False
    request = {"request_id": str(uuid4())}
    response = client.post(f"/api/party-batches/{initial['id']}/members/1/repair-persona",
                           json=request)
    assert response.status_code == 200, response.text
    repaired = response.json()
    assert repaired["status"] == "ready"
    assert repaired["members"][0] == first["members"][0]
    assert repaired["members"][1]["model_calls"] == 5
    after = client.portal.call(svc.get, initial["id"])
    assert after["members"][1]["attempts"][:4] == before["members"][1]["attempts"]
    card_before = copy.deepcopy(failed["members"][1]["character"])
    card_after = copy.deepcopy(repaired["members"][1]["character"])
    for card in (card_before, card_after):
        card.pop("name")
        card.pop("background")
    assert card_after == card_before
    assert after["seed"] == before["seed"]
    assert after["members"][1]["stream_seed"] == before["members"][1]["stream_seed"]
    repeated = client.post(f"/api/party-batches/{initial['id']}/members/1/repair-persona",
                           json=request)
    assert repeated.json() == repaired and len(model.messages) == 6


def test_explicit_repair_is_one_call_and_preserves_name_other_member_and_audit(client, party):
    svc, model = party
    batch = create(client)
    next_member(client, batch["id"])
    ready = next_member(client, batch["id"])
    name = ready["members"][0]["character"]["name"]
    before = client.portal.call(svc.get, batch["id"])
    model.fail = True
    response = client.post(f"/api/party-batches/{batch['id']}/members/0/repair-persona", json={
        "request_id": str(uuid4()),
    })
    assert response.status_code == 200 and response.json()["status"] == "failed"
    failed = client.portal.call(svc.get, batch["id"])
    assert failed["members"][0]["model_calls"] == 2 and len(model.messages) == 3
    assert failed["members"][0]["character"] == before["members"][0]["character"]
    assert failed["members"][1] == before["members"][1]
    model.fail = False
    model.forced_name = "模型想换的新姓名"
    response = client.post(f"/api/party-batches/{batch['id']}/members/0/repair-persona", json={
        "request_id": str(uuid4()),
    })
    repaired = response.json()
    assert repaired["members"][0]["character"]["name"] == name
    assert repaired["members"][0]["recovery"]["repair_calls"] == 2
    assert repaired["members"][0]["model_calls"] == 3
    assert client.portal.call(svc.get, batch["id"])["members"][1] == before["members"][1]


def test_ho_exhaustion_repair_keeps_original_private_scope(client, party, monkeypatch):
    svc, model = party
    definitions = [ho(1), ho(2)]
    registry = {"source_handouts": {"a" * 64: {
        item.id: content_digest(item.model_dump(mode="json")) for item in definitions
    }}}
    monkeypatch.setattr("app.preparation.packages.evidence_registry", lambda: registry)

    async def setup():
        async with svc.database.sessions.begin() as session:
            session.add(ModulePreparation(
                id="repair-ho", source_id="source", source_hash="a" * 64, status="approved",
                document={"handouts": [d.model_dump(mode="json") for d in definitions],
                          "initial_scene_entity_id": "repair-ho-scene"},
            ))
            session.add(ModuleEntity(
                id="repair-ho-scene", preparation_id="repair-ho", type="scene",
                status="approved", document={"public_summary": "公开导入"},
            ))

    client.portal.call(setup)
    batch = create(client, preparation_id="repair-ho", handout_ids=["HO1", "HO2"], era="modern")
    first = next_member(client, batch["id"])
    model.fail = True
    for _ in range(4):
        exhausted = next_member(client, batch["id"])
    recovery = exhausted["members"][1]["recovery"]
    assert not recovery["can_continue"] and recovery["can_repair"]
    assert not recovery["can_edit_text"]
    before = client.portal.call(svc.get, batch["id"])
    model.fail = False
    result = client.post(f"/api/party-batches/{batch['id']}/members/1/repair-persona", json={
        "request_id": str(uuid4()),
    })
    assert result.status_code == 200 and result.json()["status"] == "ready"
    after = client.portal.call(svc.get, batch["id"])
    assert after["members"][0] == before["members"][0]
    assert result.json()["members"][0] == first["members"][0]
    for key in ("public_name", "stream_seed", "reroll_count"):
        assert after["members"][1][key] == before["members"][1][key]
    for key in ("module_handout", "attributes", "roll_records", "skill_values"):
        assert after["members"][1]["character"][key] == before["members"][1]["character"][key]
    context = json.loads(model.messages[-1][-1]["content"])
    assert context["own_handout"] == "private-only-2"
    assert "private-only-1" not in str(model.messages[-1])
    assert "private-only" not in json.dumps(result.json())


@pytest.mark.parametrize("text", [
    "他双目失明，靠记忆行动。", "他因旧伤无法行走。", "他随身携带一把手枪。",
    "他拥有驾驶执照。", "他已经取得医学博士学位。", "他对极地生存有深厚造诣。",
])
def test_current_effects_gear_qualifications_and_moderate_expertise_need_support(rulesets, text):
    card = SimpleNamespace(skill_values={"survival_arctic": 41}, equipment=[])
    assert ability_issues({"background": text}, card, rulesets["coc7-character-creation"])


@pytest.mark.parametrize("text", [
    "他希望取得驾驶执照。", "他没有驾驶执照。", "他并不失明。",
    "他曾经失明，现已恢复视力。", "他曾经瘫痪，如今已经康复。",
    "他照顾失明的朋友。", "他的朋友失聪，他负责沟通。",
    "他急躁且不善于与陌生人相处。", "他想拥有手枪，但没有购买。",
])
def test_wishes_flaws_recovered_history_and_other_people_remain_valid(rulesets, text):
    card = SimpleNamespace(skill_values={}, equipment=[])
    assert not ability_issues({"background": text}, card, rulesets["coc7-character-creation"])


def test_actual_equipment_and_own_approved_background_support_claims(rulesets):
    card = SimpleNamespace(
        skill_values={"survival_arctic": 75}, equipment=[SimpleNamespace(name="手枪")],
        module_handout=SimpleNamespace(definition=SimpleNamespace(text="他失明，持有驾驶执照。")),
    )
    text = "他失明。他随身携带一把手枪。他持有驾驶执照。他对极地生存有深厚造诣。"
    assert not ability_issues({"background": text}, card, rulesets["coc7-character-creation"])


def test_frozen_batch48_final_blindness_and_survival_claims(rulesets):
    # Exact final live hunter prose, real-01/after-start.json, profile.background.
    text = ("曾是机械工程师，因意外失去视力后转行成为猎人。"
            "对机械与生存技能有深厚造诣，擅长在极端环境下独自行动。")
    card = SimpleNamespace(skill_values={"mechanical_repair": 79, "survival_arctic": 41},
                           equipment=[])
    issues = ability_issues({"background": text}, card, rulesets["coc7-character-creation"])
    assert any("失去视力" in issue["message"] for issue in issues)
    assert any(issue.get("skill") == "survival_arctic" for issue in issues)
    recovered = "曾因意外失去视力，但现已恢复视力。他熟悉机械维修。"
    assert not ability_issues({"background": recovered}, card, rulesets["coc7-character-creation"])


def test_frozen_batch49_first_real_repair_is_rejected_and_guides_next_repair(client, party):
    # real-persona-01/calls.json generated_output.background, rejected with no card change.
    text = ("曾是机械工程师，因事故失去视力后转行成为猎人。"
            "擅长修理机械与近身搏斗，对未知事物充满好奇。")
    svc, model = party
    batch = create(client, count=1)
    model.background_override = text
    before = copy.deepcopy(batch["members"][0]["character"])
    result = client.post(f"/api/party-batches/{batch['id']}/members/0/repair-persona", json={
        "request_id": str(uuid4()),
    })
    assert result.json()["status"] == "failed"
    assert result.json()["members"][0]["character"] == before
    model.background_override = "他从事普通工作，谨慎且不善表达。"
    result = client.post(f"/api/party-batches/{batch['id']}/members/0/repair-persona", json={
        "request_id": str(uuid4()),
    })
    assert result.json()["status"] == "ready"
    context = json.loads(model.messages[-1][-1]["content"])
    assert "逐项消除" in context["text_task"]
    assert any("失去视力" in issue for issue in context["previous_output_issues"])
    assert "信用评级" not in context["actual_strengths"]
    assert len(model.messages) == 2


def test_gear_name_substring_does_not_grant_a_different_item(rulesets):
    card = SimpleNamespace(skill_values={}, equipment=[SimpleNamespace(name="枪油")])
    assert ability_issues({"background": "他携带一把枪。"}, card,
                          rulesets["coc7-character-creation"])


@pytest.mark.parametrize("source,claim", [
    ("他曾经失明，现已恢复视力。", "他现在失明。"),
    ("他没有驾驶执照。", "他拥有驾驶执照。"),
    ("他不得携带手枪。", "他携带一把手枪。"),
    ("他持有驾驶执照。", "他持有医师执照。"),
])
def test_negative_recovered_or_different_approved_details_do_not_supply_support(
    rulesets, source, claim,
):
    card = SimpleNamespace(skill_values={}, equipment=[], module_handout=SimpleNamespace(
        definition=SimpleNamespace(text=source),
    ))
    assert ability_issues({"background": claim}, card, rulesets["coc7-character-creation"])


@pytest.mark.parametrize("entry", ["generate", "edit", "adopt"])
def test_same_consistency_check_at_generation_edit_and_adoption(client, party, entry):
    svc, model = party
    batch = create(client, count=1)
    prefix = f"/api/party-batches/{batch['id']}"
    unsupported = "他双目失明，靠记忆行动。"
    if entry == "generate":
        model.background_override = unsupported
        result = next_member(client, batch["id"])
        assert result["status"] == "failed"
    else:
        ready = next_member(client, batch["id"])
        if entry == "edit":
            profile = {**ready["members"][0]["profile"], "background": unsupported}
            response = client.patch(prefix + "/members/0", json={
                "request_id": str(uuid4()), "profile": profile,
            })
            assert response.status_code == 200 and response.json()["status"] == "failed"
        else:
            async def seed_legacy_output():
                document = await svc.get(batch["id"])
                document["members"][0]["profile"]["background"] = unsupported
                await svc._save(document)
            client.portal.call(seed_legacy_output)
    adoption = client.post(prefix + "/adopt", json={"request_id": str(uuid4())})
    assert adoption.status_code == 422
    rejected = client.portal.call(svc.get, batch["id"])
    assert rejected["members"][0]["_persona_rejections"]
    assert rejected["members"][0]["character"]["skill_values"] == (
        batch["members"][0]["character"]["skill_values"]
    )
    assert len(model.messages) == 1


def test_edit_and_whole_party_adoption_reject_overlapping_new_names(client, party):
    svc, model = party
    batch = create(client)
    next_member(client, batch["id"])
    ready = next_member(client, batch["id"])
    prefix = f"/api/party-batches/{batch['id']}"
    duplicate = ready["members"][1]["character"]["name"] + "·猎人"
    response = client.patch(prefix + "/members/0", json={
        "request_id": str(uuid4()), "character": {"name": duplicate},
    })
    assert response.status_code == 422
    assert client.portal.call(svc.get, batch["id"])["members"][0]["character"]["name"] == (
        ready["members"][0]["character"]["name"]
    )

    async def old_unadopted_draft():
        document = await svc.get(batch["id"])
        first = document["members"][0]
        first["profile"]["name"] = first["character"]["name"] = duplicate
        await svc._save(document)
    client.portal.call(old_unadopted_draft)
    assert client.post(prefix + "/adopt", json={"request_id": str(uuid4())}).status_code == 422
    after = client.portal.call(svc.get, batch["id"])
    assert after["members"][0]["character"]["name"] == duplicate
    assert len(model.messages) == 2
