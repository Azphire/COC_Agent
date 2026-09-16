"""Isolated source-backed packages through creation, approval and SAN settlement.

No real models/network, original PDF/package/save edits or random reroll hunting.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_batch10 import FixedRandom, current, effect, restore, roll, save, source_event
from test_batch10 import request as encounter
from test_batch28 import patch
from test_batch30 import document, finalize, normal_check, special_card
from test_coc7_creation import create_seventh
from test_module_preparation import approve_opening, preparation  # noqa: F401
from test_rooms import create_room, headers, join, lobby, ok, prepare  # noqa: F401

from app.dice.service import DiceService
from app.domain.character import CharacterDraft
from app.rooms.sanity_schemas import SanityEffect
from app.rules.engine import recalculate
from app.rules.experiences import matching_immunity
from app.rules.loader import archived_ruleset, load_rulesets

PACKAGES = ["war", "police", "criminal", "medical"]


def selection(kind="war", age=30, variant=None):
    return dict(
        package=kind,
        variant=variant or ("soldier" if kind == "war" else "default"),
        history={
            "war": "曾作为军队士兵参加战争",
            "police": "从警八年后离职",
            "criminal": "从小在犯罪组织生活，已二十年",
            "medical": "资深医生从业八年",
        }[kind],
        background_kind="scar",
        background_detail="与上述经历相关的旧伤疤",
        choices={
            "war": {},
            "police": {"firearm": ["handgun"], "social": ["charm", "persuade"]},
            "criminal": {"fighting": ["brawl"], "firearm": ["handgun"], "social": ["intimidate"]},
            "medical": {"science": ["biology", "chemistry"]},
        }[kind],
        **(dict(war_year=1918, scenario_year=1925, age_at_war=age - 7) if kind == "war" else {}),
    )


def package_card(client, kind="war", age=30, approve=False):
    card = patch(
        client,
        create_seventh(client, age=age),
        occupation="doctor",
        occupation_group_choices={"academic": ["history", "law"]},
        occupation_skills={"credit_rating": {"points": 30}},
        experience=selection(kind, age),
        experience_skills={"listen": {"points": 30}},
    )
    if approve:
        card = patch(client, card, approve_experience=[kind])
    return card


@pytest.mark.parametrize("kind", PACKAGES)
def test_package_creation_pool_original_roll_revoke_reselect(client, kind):
    card = package_card(client, kind)
    assert {i["code"] for i in card["validation"]["issues"]} == {"keeper_approval"}
    assert card["remaining_points"]["experience"] == (40 if kind == "war" else 30)
    assert card["skill_values"]["listen"] == 50
    assert card["occupation_skills"] == {"credit_rating": {"points": 30}}
    assert card["remaining_points"]["interest"] == 120 and not card["equipment"]
    original = deepcopy(card)
    assert finalize(client, card).status_code == 422
    card = patch(client, card, approve_experience=[kind])
    assert card["validation"]["valid"], card["validation"]
    for _ in range(2):
        card = patch(client, card, name="只改角色姓名")
        assert card["experience_rolls"] == original["experience_rolls"]
        assert card["derived_values"]["san"] == original["derived_values"]["san"]
        assert card["experience_approvals"]
    card = patch(client, card, experience=None)
    assert not card["experience_effects"] and not card["experience_approvals"]
    assert card["skill_values"]["listen"] == 20 and card["derived_values"]["san"] == 50
    assert card["experience_skills"] == original["experience_skills"]
    assert any(i["code"] == "experience" for i in card["validation"]["issues"])
    card = patch(client, card, experience=original["experience"], approve_experience=[kind])
    assert card["validation"]["valid"] and card["roll_records"] == original["roll_records"]
    assert card["experience_rolls"] == original["experience_rolls"]
    assert ok(finalize(client, card))["experience_effects"]["approved"]


@pytest.mark.parametrize("kind", PACKAGES)
def test_package_qualification_and_background(client, kind):
    age = {"war": 30, "police": 24, "criminal": 19, "medical": 29}[kind]
    card = package_card(client, kind, age)
    if kind == "war":
        card = patch(client, card, experience={**card["experience"], "scenario_year": 1926})
    assert any(i["code"] in {"age", "chronology"} for i in card["validation"]["issues"])
    assert finalize(client, card).status_code == 422
    card = patch(
        client, card, experience={**card["experience"], "history": " ", "background_detail": " "}
    )
    assert any(i["code"] == "background" for i in card["validation"]["issues"])


@pytest.mark.parametrize("kind", PACKAGES)
@pytest.mark.parametrize(
    "allocation,code",
    [
        ({"listen": {"points": 71}}, "overspent"),
        ({"cthulhu_mythos": {"points": 1}}, "forbidden"),
        ({"credit_rating": {"points": 1}}, "experience"),
        ({"library_use": {"points": 1}}, "experience"),
        ({"listen": {"points": -1}}, "negative"),
    ],
)
def test_package_pool_whitelist_and_mythos(client, kind, allocation, code):
    card = patch(
        client, package_card(client, kind), experience_skills=allocation, approve_experience=[kind]
    )
    assert any(i["code"] == code for i in card["validation"]["issues"])
    assert finalize(client, card).status_code == 422


@pytest.mark.parametrize(
    "field", ["experience_effects", "experience_rolls", "experience_approvals", "skill_values"]
)
def test_computed_fields_not_patchable(client, field):
    card = package_card(client)
    response = client.patch(
        f"/api/characters/{card['id']}", json={"version": card["version"], field: {}}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("kind", ["police", "criminal", "medical"])
def test_group_count_duplicates_and_wrong_specialty(client, kind):
    card = package_card(client, kind)
    key = "science" if kind == "medical" else "social"
    for chosen in ([], ["charm", "charm"], ["cthulhu_mythos"], ["lore_folklore"]):
        card = patch(
            client,
            card,
            experience={
                **card["experience"],
                "choices": {**card["experience"]["choices"], key: chosen},
            },
        )
        assert any(
            i["code"] in {"selection_count", "duplicate", "selection"}
            for i in card["validation"]["issues"]
        )
        assert finalize(client, card).status_code == 422


def test_officer_variant_reuses_war_roll_and_limits_four_way_choice(client):
    card = package_card(client, approve=True)
    before = deepcopy(card)
    card = patch(
        client,
        card,
        experience={
            **card["experience"],
            "variant": "officer",
            "choices": {"officer_choice": ["navigate"]},
        },
        experience_skills={"navigate": {"points": 70}},
    )
    assert card["skill_values"]["navigate"] == 80
    assert card["experience_rolls"] == before["experience_rolls"]
    assert not card["experience_approvals"]
    card = patch(client, card, experience_skills={"sleight_of_hand": {"points": 1}})
    assert any(i["code"] == "experience" for i in card["validation"]["issues"])


@pytest.mark.parametrize(
    "change", ["attributes", "age_deductions", "experience_skills", "era", "experience"]
)
def test_consent_invalidates_relevant_changes(client, change):
    card = package_card(client, approve=True)
    value = {
        "attributes": {**card["attributes"], "pow": {"value": 45}, "app": {"value": 55}},
        "age_deductions": {"str": 1},
        "experience_skills": {"listen": {"points": 31}},
        "era": "modern",
        "experience": {**card["experience"], "background_kind": "phobia"},
    }[change]
    changed = patch(client, card, **{change: value})
    assert not changed["experience_approvals"]
    assert changed["experience_rolls"] == card["experience_rolls"]


def test_age_locked_actual_age_and_old_roll_validation(client):
    card = package_card(client, age=40)
    card = patch(client, card, age_deductions={"str": 5})
    assert sum(r["purpose"] == "education_check" for r in card["roll_records"]) == 2
    assert card["experience"]["age_at_war"] == 33
    assert (
        client.patch(
            f"/api/characters/{card['id']}", json={"version": card["version"], "age": 50}
        ).status_code
        == 422
    )
    doc = document(client, card)
    doc["character"]["roll_records"].append(doc["character"]["experience_rolls"]["war"])
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    assert any(i["field"] == "roll_records" for i in imported["validation"]["issues"])


def test_custom_specialties_stable_names_duplicates_and_no_lore_alias(client):
    from test_batch26 import custom

    card = package_card(client, "medical", approve=True)
    spec = custom("science", "海洋化学")
    card = patch(
        client,
        card,
        custom_specializations=[spec],
        experience={**card["experience"], "choices": {"science": [spec["id"], "biology"]}},
        experience_skills={spec["id"]: {"points": 60}},
        approve_experience=["medical"],
    )
    assert card["validation"]["valid"] and card["skill_values"][spec["id"]] == 61
    card = patch(client, card, custom_specializations=[{**spec, "name": "海洋地质"}])
    assert card["experience_skills"][spec["id"]]["points"] == 60
    assert not card["experience_approvals"]
    for specs in ([spec, spec], [custom("lore", "克苏鲁神话")]):
        bad = patch(
            client,
            card,
            custom_specializations=specs,
            experience_skills={},
            experience={**card["experience"], "choices": {"science": ["biology", "chemistry"]}},
        )
        assert any(
            i["code"] in {"duplicate", "forbidden_name"} for i in bad["validation"]["issues"]
        )
        card = bad


@pytest.mark.parametrize(
    "mutation", ["total", "purpose", "formula", "multiplier", "missing", "unknown"]
)
def test_import_roll_validation_and_provenance(client, mutation):
    card = package_card(client, approve=True)
    doc = document(client, card)
    record = doc["character"]["experience_rolls"]["war"]
    if mutation == "missing":
        doc["character"]["experience_rolls"] = {}
    elif mutation == "unknown":
        doc["character"]["experience_rolls"]["mythos"] = record
    else:
        record[mutation] = {
            "total": 999,
            "purpose": "attribute",
            "formula": "1d10",
            "multiplier": 2,
        }[mutation]
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    assert not imported["experience_approvals"]
    assert any(i["field"] == "experience_rolls" for i in imported["validation"]["issues"])
    assert finalize(client, imported).status_code == 422
    if mutation == "missing":
        imported = patch(client, imported, name="导入不得补骰")
        assert not imported["experience_rolls"]


def test_one_package_schema_and_unsupported_version(client):
    card = package_card(client, approve=True)
    assert (
        client.patch(
            f"/api/characters/{card['id']}",
            json={"version": card["version"], "experience": [selection(), selection("police")]},
        ).status_code
        == 422
    )
    doc = document(client, card)
    doc["ruleset"]["version"] = doc["character"]["ruleset_version"] = "1.3.0"
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    assert not imported["experience_effects"] and not imported["experience_approvals"]
    assert imported["derived_values"]["san"] == 50
    assert imported["skill_values"]["listen"] == 20
    assert finalize(client, imported).status_code == 422
    doc["character"].update(experience=None, experience_skills={}, experience_rolls={})
    old = ok(client.post("/api/characters/import", json=doc), 201)
    assert old["validation"]["valid"] and ok(finalize(client, old))["ruleset_version"] == "1.3.0"


def test_three_pools_share_skill_maximum(client):
    card = patch(
        client, package_card(client),
        interest_skills={"listen": {"points": 20}},
        experience_skills={"listen": {"points": 60}}, approve_experience=["war"],
    )
    assert card["remaining_points"]["interest"] == 100
    assert card["remaining_points"]["experience"] == 10
    assert card["skill_values"]["listen"] == 100
    assert any(i["code"] == "maximum" for i in card["validation"]["issues"])
    assert finalize(client, card).status_code == 422


def test_mythos_package_combination_and_san_zero(client, monkeypatch):
    from app.api.characters import character_service

    card = special_card(client, "occultist", value=15, approve=True)
    original = deepcopy(card)
    svc = character_service(SimpleNamespace(app=client.app))
    svc.dice = DiceService(FixedRandom(10, 5))
    client.app.dependency_overrides[character_service] = lambda: svc
    card = patch(
        client,
        card,
        attributes={**card["attributes"], "pow": {"value": 90}, "str": {"value": 20}},
        experience=selection("war", 25),
        approve_experience=["war"],
        approve_occupation_exceptions=["initial_mythos"],
    )
    assert card["validation"]["valid"], card["validation"]
    assert (card["derived_values"]["san"], card["derived_values"]["san_max"]) == (75, 84)
    assert card["roll_records"] == original["roll_records"]
    card = patch(
        client,
        card,
        attributes={
            **card["attributes"],
            "pow": {"value": 15},
            "str": {"value": 90},
            "app": {"value": 55},
        },
        approve_experience=["war"],
        approve_occupation_exceptions=["initial_mythos"],
    )
    assert card["validation"]["valid"] and card["derived_values"]["san"] == 0
    sheet = ok(finalize(client, card))
    created = create_room(client)
    p = f"/api/rooms/{created['room']['id']}"
    room = ok(client.post(p + "/character-slots", json={"character_id": sheet["id"]}))["room"]
    runtime = next(iter(room["session_state"]["characters"].values()))
    assert (runtime["sanity"]["kind"], runtime["sanity"]["phase"]) == ("permanent", "bout")
    assert runtime["sanity"]["day_loss"] == runtime["sanity"]["mythos_gain"] == 0
    imported = ok(client.post("/api/characters/import", json=document(client, card)), 201)
    assert imported["derived_values"]["san"] == 0
    # A current occultist is not one of the medical occupations required by this source.
    bad = patch(client, imported, experience=selection("medical", 25))
    assert any(i["code"] == "occupation" for i in bad["validation"]["issues"])


def accept_package(client, card):
    created = create_room(client)
    remote = join(client, created["invite_code"])
    prefix = f"/api/rooms/{created['room']['id']}"
    path = prefix + "/character-submissions"
    doc = document(client, card)
    doc["character"]["experience_effects"] = {"san_loss": 0, "pool": 999, "approved": True}
    doc["character"]["skill_values"]["listen"] = 99
    auth = headers(remote["member_token"])
    preview = ok(client.post(path + "/preview", headers=auth, json={"document": doc}))
    assert not preview["experience_approvals"]
    assert preview["skill_values"]["listen"] == 50
    assert preview["derived_values"]["san"] == card["derived_values"]["san"]
    assert preview["experience_rolls"][card["experience"]["package"]]["source"] == "imported"
    bad = deepcopy(doc)
    bad["character"]["experience_skills"]["cthulhu_mythos"] = {"points": 1}
    assert client.post(path + "/preview", headers=auth, json={"document": bad}).status_code == 422
    submitted = ok(
        client.post(
            path,
            headers=auth,
            json={"document": doc, "expected_version": 0, "client_request_id": str(uuid4())},
        )
    )["room"]
    row = submitted["character_submissions"][0]
    url = path + f"/{row['id']}/review"
    body = dict(expected_version=1, decision="accept", client_request_id=str(uuid4()))
    assert client.post(url, json=body).status_code == 422
    body["approve_experience"] = [card["experience"]["package"]]
    assert client.post(url, headers=auth, json=body).status_code == 403
    room = ok(client.post(url, json=body))["room"]
    slot = room["character_slots"][0]
    assert ok(client.post(url, json=body))["room"]["session_state"] == room["session_state"]
    assert slot["character_snapshot"]["experience_effects"]["approved"]
    return dict(
        prefix=prefix,
        remote=remote,
        player=remote["room"]["self_member_id"],
        slots=[slot["id"]],
        room=room,
        snapshot=slot["character_snapshot"],
    )


@pytest.mark.parametrize("kind", PACKAGES)
def test_submission_freeze_check_and_repeat_restore(client, kind):
    card = package_card(client, kind, approve=True)
    ok(finalize(client, card))
    game = accept_package(client, card)
    p = game["prefix"]
    ok(client.post(p + "/module", json={"module_id": "stopped-clock"}))
    profile = ok(client.post("/api/agent-profiles", json={"role": "keeper", "name": "隔离KP"}), 201)
    ok(
        client.post(
            p + "/agent-bindings",
            json={"member_id": game["room"]["host_member_id"], "profile_id": profile["id"]},
        )
    )
    ok(
        client.post(
            p + "/ready", headers=headers(game["remote"]["member_token"]), json={"ready": True}
        )
    )
    ok(client.post(p + "/start"))
    actor = normal_check(client, game, "listen", 50)
    assert actor["experience"]["package"] == kind
    assert actor["experience_effects"]["approved"]
    state = current(client, game)
    assert state["san"] == card["derived_values"]["san"]
    assert state["sanity"]["day_loss"] == state["sanity"]["mythos_gain"] == 0
    checkpoint = save(client, game)
    for _ in range(2):
        restore(client, game, checkpoint)
        assert current(client, game) == state
        assert ok(client.get(p))["character_slots"][0]["character_snapshot"] == game["snapshot"]


@pytest.mark.parametrize(
    "kind,category,immune",
    [
        ("war", "witness_corpse", True),
        ("war", "witness_severe_injury", True),
        ("war", "witness_murder", False),
        ("war", None, False),
        ("police", "witness_corpse", True),
        ("police", "witness_severe_injury", False),
        ("criminal", "witness_corpse", True),
        ("criminal", "witness_murder", True),
        ("criminal", "commit_murder", True),
        ("criminal", "witness_human_mutilation", True),
        ("criminal", "witness_severe_injury", False),
        ("medical", "witness_corpse", True),
        ("medical", "witness_severe_injury", True),
        ("medical", "witness_murder", False),
        (None, "witness_corpse", False),
    ],
)
def test_approved_encounter_immunity_matrix(client, lobby, preparation, kind, category, immune):  # noqa: F811
    game = prepared_game(client, lobby, preparation, kind, category)
    before = current(client, game)
    rng = FixedRandom(*([] if immune else [20, 1]))
    client.app.state.room_service.dice = DiceService(rng)
    seq = source_event(client, game)
    check = encounter(client, game, "classified", seq)
    if immune:
        assert check["status"] == "resolved"
        assert check["sanity"]["immunity"]["package"] == kind
        assert check["sanity"]["immunity"]["kind"] == "experience"
        assert check["sanity"]["immunity"]["source"]
        assert check["sanity"]["loss"] == 0 and not check["sanity"]["rolls"]
        assert not rng.used
    else:
        assert check["status"] == "pending" and not check["sanity"]["immune"]
        roll(client, game, check, "san")
        check = roll(client, game, check, "loss")
        assert rng.used == [20, 1]
    after = current(client, game)
    assert after["san"] == before["san"] - (0 if immune else 1)
    assert after["sanity"]["day_loss"] == (0 if immune else 1)
    assert after["sanity"]["mythos_gain"] == 0
    again = encounter(client, game, "classified", seq)
    assert again["id"] == check["id"]
    assert {k: again["sanity"][k] for k in check["sanity"]} == check["sanity"]
    if immune and kind == "war" and category == "witness_corpse":
        checkpoint = save(client, game)
        for _ in range(2):
            restore(client, game, checkpoint)
            assert {k: current(client, game)[k] for k in ("san", "san_max", "sanity")} == {
                k: after[k] for k in ("san", "san_max", "sanity")
            }
            assert encounter(client, game, "classified", seq)["sanity"] == check["sanity"]


def test_classification_requires_explicit_source_and_visual_cause():
    base = effect("unclassified", "0", "1d3").model_dump()
    for update in (
        {"experience_category": "witness_corpse"},
        {"experience_category": "witness_corpse", "experience_basis": "source"},
        {"experience_category": "any_non_mythos", "experience_basis": "source"},
    ):
        with pytest.raises(ValidationError):
            SanityEffect.model_validate({**base, **update})
    assert SanityEffect.model_validate(base).experience_category is None


def test_old_rules_and_forged_consent_cannot_match(client):
    sheet = ok(finalize(client, package_card(client, approve=True)))
    e = SanityEffect.model_validate(
        {
            **effect("a", "0", "1").model_dump(),
            "perception": "visual",
            "experience_category": "witness_corpse",
            "experience_basis": "isolated cause",
        }
    )
    assert matching_immunity(sheet, e)
    for update in (
        {"ruleset_version": "1.3.0"},
        {"experience_approvals": {"war": "forged"}},
        {"experience": None},
    ):
        assert matching_immunity({**sheet, **update}, e) is None
    assert not archived_ruleset("coc7-character-creation", "1.3.0").experience_packages


def test_source_templates_unchanged_by_recalculation(client):
    rules = load_rulesets()["coc7-character-creation"]
    original = rules.model_dump()
    card = CharacterDraft.model_validate(package_card(client, approve=True))
    for _ in range(3):
        recalculate(card, rules)
    assert rules.model_dump() == original
    assert card.validation.valid
    rules.version = "1.4.1"
    recalculate(card, rules)
    assert not card.experience_approvals


@pytest.mark.parametrize(
    "kind,group,name", [("war", "survival", "湿地"), ("police", "language", "葡萄牙语")]
)
def test_package_open_specialties_use_server_templates(client, kind, group, name):
    from test_batch26 import custom

    spec = custom(group, name)
    card = patch(
        client,
        package_card(client, kind),
        custom_specializations=[spec],
        selected_specializations=[spec["id"]],
        experience_skills={spec["id"]: {"points": 60}},
        approve_experience=[kind],
    )
    assert card["validation"]["valid"]
    assert card["skill_values"][spec["id"]] == (70 if group == "survival" else 61)
    card = patch(client, card, selected_specializations=[])
    assert any(i["code"] == "experience" for i in card["validation"]["issues"])
    assert card["experience_skills"][spec["id"]]["points"] == 60


def test_automatic_immunity_pipeline_receipt_permissions_and_repeat(client, lobby, preparation):  # noqa: F811
    import json

    from test_action_adjudication import modern_response
    from test_agent_runtime import wait_cycle

    from app.agents.model import FakeModelAdapter

    game = prepared_game(client, lobby, preparation)
    p = game["prefix"]
    ok(client.post(p + "/pause"))
    profile = ok(
        client.post("/api/agent-profiles", json={"role": "keeper", "name": "免疫夹具"}), 201
    )
    ok(
        client.post(
            p + "/agent-bindings",
            json={"member_id": game["room"]["host_member_id"], "profile_id": profile["id"]},
        )
    )
    teammate = ok(
        client.post("/api/agent-profiles", json={"role": "investigator", "name": "沉默夹具"}), 201
    )
    ok(
        client.post(
            p + "/agent-bindings", json={"member_id": game["agent"], "profile_id": teammate["id"]}
        )
    )

    def respond(messages, kwargs):
        if kwargs["response_schema"].__name__ != "KeeperPlan":
            return modern_response(messages, kwargs)
        ctx = json.loads(messages[-1]["content"])
        ids = ctx["action_identifiers"]
        return {
            **{
                k: ids[k]
                for k in ("plan_id", "cycle_id", "current_scene_id", "expected_navigation_revision")
            },
            "parsed_intent": {
                "type": "observe",
                "actor_member_id": ids["actor_member_id"],
                "actor_character_slot_id": ids["actor_character_slot_id"],
                "target_id": game["entity"],
                "evidence_quote": ctx["triggering_action"]["payload"]["text"],
                "confidence": 1,
            },
        }

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=respond)
    rng = FixedRandom()
    client.app.state.room_service.dice = DiceService(rng)
    ok(client.post(p + "/resume"))
    for _ in range(2):
        ok(
            client.post(
                p + "/actions",
                headers=headers(game["remote"]["member_token"]),
                json={
                    "text": "我查看公告",
                    "target_entity_id": game["entity"],
                    "client_request_id": str(uuid4()),
                },
            )
        )
        assert wait_cycle(client, game)["status"] == "completed"
    checks = [c for c in ok(client.get(p + "/checks")) if c.get("sanity")]
    assert len(checks) == 1 and checks[0]["sanity"]["immune"]
    assert checks[0]["sanity"]["origin"] == "automatic"
    assert not rng.used
    ok(client.post(p + "/pause"))
    stranger = join(client, game["created"]["invite_code"], "旁观玩家")
    hidden = ok(client.get(p + "/checks", headers=headers(stranger["member_token"])))
    assert checks[0]["id"] not in {c["id"] for c in hidden}
    # Ordinary action/encounter callers cannot assert an immunity or classification.
    assert (
        client.post(
            p + "/sanity/encounters",
            json={
                "target_member_id": game["player"],
                "entity_id": game["entity"],
                "effect_id": "classified",
                "source_event_seq": 1,
                "experience_category": "witness_corpse",
                "immune": True,
            },
        ).status_code
        == 422
    )


def test_bout_protection_stays_distinct_from_package_immunity(client, lobby, preparation):  # noqa: F811
    from uuid import UUID

    from app.rooms.combat_service import load_state, store_state

    game = prepared_game(client, lobby, preparation)
    svc = client.app.state.agent_service

    async def configure(session, room):
        state = load_state(room)
        sanity = state.characters[UUID(game["slots"][0])].sanity
        sanity.kind, sanity.phase = "temporary", "bout"
        store_state(room, state)

    client.portal.call(svc.mutate, game["room"]["id"], configure)
    client.app.state.room_service.dice = DiceService(FixedRandom())
    check = encounter(client, game, "classified")
    assert check["status"] == "resolved"
    assert check["sanity"]["immunity"]["kind"] == "insanity_bout"


def prepared_game(client, lobby, preparation, kind="war", category="witness_corpse"):  # noqa: F811
    if kind:
        card = package_card(client, kind, approve=True)
        sheet = ok(finalize(client, card))
        room = ok(
            client.post(lobby["prefix"] + "/character-slots", json={"character_id": sheet["id"]})
        )["room"]
        lobby["slots"][0] = room["character_slots"][-1]["id"]
    entity = next(e for e in preparation["entities"] if e["title"] == "公告")
    sanity = effect("classified", "1d3", "1d3").model_dump()
    sanity.update(
        experience_category=category,
        experience_basis="隔离来源仅有该独立原因" if category else "",
        perception="visual",
        mythos=True,
        automation="automatic",
    )
    ok(
        client.patch(
            f"/api/module-entities/{entity['id']}",
            json={"sanity_effects": [sanity], "initial_visibility": "revealed"},
        )
    )
    approved = approve_opening(client, preparation)
    ok(
        client.patch(
            lobby["prefix"] + "/module-preparation", json={"preparation_id": approved["id"]}
        )
    )
    prepare(client, lobby)
    return {**lobby, "entity": entity["id"]}
