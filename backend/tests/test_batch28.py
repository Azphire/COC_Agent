"""Source-backed occupation shapes, restricted specialties and original service flow.

All model responses here are deterministic fixtures; external HTTP is forbidden.
"""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_batch26 import custom
from test_coc7_creation import create_seventh
from test_rooms import create_room, headers, join, lobby, ok  # noqa: F401

from app.domain.character import CharacterDraft
from app.domain.specializations import CustomSpecialization
from app.rules.character_options import group_options
from app.rules.checks import available_skills, check_value, prompt_skills
from app.rules.engine import recalculate
from app.rules.loader import archived_ruleset, load_rulesets


def patch(client, card, **changes):
    return ok(
        client.patch(f"/api/characters/{card['id']}", json={"version": card["version"], **changes})
    )


def academic_card(client, occupation="doctor", specs=None):
    specs = specs or [custom("science", "地球物理学"), custom("science", "海洋化学")]
    keys = [s["id"] for s in specs]
    choices = {"academic": keys[:2]}
    if occupation != "doctor":
        choices["academic"] += ["history", "occult"]
        choices["language"] = ["language_latin"]
    card = patch(
        client,
        create_seventh(client),
        occupation=occupation,
        custom_specializations=specs,
        occupation_group_choices=choices,
        occupation_skills={
            "credit_rating": {"points": 30},
            **{k: {"points": 20} for k in keys[:2]},
        },
    )
    return card, specs


@pytest.mark.parametrize("occupation", ["professor", "librarian", "doctor"])
def test_two_different_custom_sciences_are_occupational(client, occupation):
    card, specs = academic_card(client, occupation)
    assert card["validation"]["valid"], card["validation"]
    assert all(card["skill_values"][s["id"]] == 21 for s in specs)
    assert all(card["skill_half_values"][s["id"]] == 10 for s in specs)
    # Both categories already allowed by the original whitelist also expand.
    for group, names in [("art_craft", ["陶艺", "玻璃吹制"]), ("language", ["葡萄牙语", "冰岛语"])]:
        changed, _ = academic_card(client, occupation, [custom(group, n) for n in names])
        assert changed["validation"]["valid"], changed["validation"]


@pytest.mark.parametrize("group,name", [("pilot", "飞艇"), ("survival", "湿地")])
def test_academic_still_rejects_unrelated_categories(client, group, name):
    card, specs = academic_card(client, specs=[custom(group, name), custom("science", "海洋化学")])
    assert any(i["code"] == "selection" for i in card["validation"]["issues"])
    assert any(i["code"] == "occupation" for i in card["validation"]["issues"])


@pytest.mark.parametrize(
    "choices",
    [
        {"academic": ["biology", "science_physics"]},
        {"academic": ["science_physics", "science_physics"]},
    ],
)
def test_fixed_or_same_skill_cannot_take_two_places(client, choices):
    card = patch(
        client,
        create_seventh(client),
        occupation="doctor",
        occupation_group_choices=choices,
        occupation_skills={"credit_rating": {"points": 30}},
    )
    assert any(i["code"] == "duplicate" for i in card["validation"]["issues"])


def test_duplicate_across_groups_and_legacy_direction_limits(client):
    card = patch(
        client,
        create_seventh(client),
        occupation="professor",
        occupation_group_choices={
            "language": ["language_latin"],
            "academic": ["language_latin", "history", "chemistry", "occult"],
        },
    )
    assert any(i["code"] == "duplicate" for i in card["validation"]["issues"])
    rules = load_rulesets()[card["ruleset_id"]]
    for occupation, family in [("criminal", "fighting"), ("soldier", "language")]:
        o = next(o for o in rules.occupations if o.key == occupation)
        g = next(g for g in o.skill_groups if g.count > 1 and family in g.specialization_groups)
        model = CharacterDraft.model_validate(card)
        model.occupation = occupation
        keys = [s.key for s in rules.skills if s.specialization_group == family][:2]
        others = list(group_options(g, rules, "1920s") - set(keys))[: g.count - 2]
        model.occupation_group_choices = {g.key: keys + others}
        recalculate(model, rules)
        assert any(i.code == "duplicate_direction" for i in model.validation.issues)


@pytest.mark.parametrize(
    "occupation,attribute,choices",
    [
        ("acrobat", None, {"personal": ["history", "photography"]}),
        ("boxer_wrestler", None, {"personal": ["history", "photography"]}),
        ("animal_trainer", "pow", {"personal": ["history"]}),
        (
            "explorer",
            "app",
            {
                "physical": ["climb"],
                "firearms": ["handgun"],
                "language": ["language_latin"],
                "survival": ["survival_forest"],
            },
        ),
        (
            "laboratory_assistant",
            None,
            {
                "research": ["library_use"],
                "language": ["language_latin"],
                "science": ["biology", "science_physics"],
                "personal": ["history"],
            },
        ),
        (
            "scientist",
            None,
            {
                "science": ["biology", "chemistry", "science_physics"],
                "research": ["library_use"],
                "language": ["language_latin"],
                "social": ["persuade"],
            },
        ),
        (
            "craftsperson",
            None,
            {"art_craft": ["art_cooking", "art_carpentry"], "personal": ["history", "biology"]},
        ),
        (
            "secretary",
            "dex",
            {
                "office": ["art_typing"],
                "social": ["charm", "persuade"],
                "research": ["library_use"],
                "personal": ["history"],
            },
        ),
        (
            "student",
            None,
            {
                "language": ["own_language"],
                "academic": ["history", "biology", "chemistry"],
                "personal": ["photography", "navigate"],
            },
        ),
    ],
)
def test_handbook_formula_and_group_shapes(client, occupation, attribute, choices):
    rules = load_rulesets()["coc7-character-creation"]
    o = next(o for o in rules.occupations if o.key == occupation)
    credit = o.credit_rating_minimum
    card = patch(
        client,
        create_seventh(client),
        occupation=occupation,
        occupation_attribute=attribute,
        occupation_group_choices=choices,
        occupation_skills={"credit_rating": {"points": credit}},
    )
    assert card["validation"]["valid"], card["validation"]
    attrs = card["effective_attributes"]
    pool = sum(attrs[k] * v for k, v in o.point_formula.fixed.items())
    if attribute:
        pool += attrs[attribute] * 2
    assert card["remaining_points"]["occupation"] == pool - credit


def test_catalog_coverage_and_archive_are_distinct():
    r = load_rulesets()["coc7-character-creation"]
    assert (r.version, len(r.occupations), len(r.skills)) == ("1.5.0", 115, 106)
    old = archived_ruleset(r.id, "1.1.0")
    assert (len(old.occupations), len(old.skills), len(old.custom_specialization_templates)) == (
        31,
        103,
        3,
    )
    coverage = (Path(__file__).resolve().parents[2] / "docs/occupation-coverage.md").read_text(
        encoding="utf-8"
    )
    for o in r.occupations:
        assert f"`{o.key}`" in coverage and o.source and o.point_formula
        # One data-wide invariant, rather than a duplicate test per YAML entry.
        assert len(o.fixed_skills) + sum(g.count for g in o.skill_groups) == 8
    assert set(r.custom_specialization_templates) == {
        "language",
        "art_craft",
        "science",
        "pilot",
        "survival",
        "lore",
    }
    assert not next(s for s in r.skills if s.key == "cthulhu_mythos").allocatable


def test_new_categories_ids_duplicates_bases_limits_and_era(client):
    card = create_seventh(client)
    specs = [custom("pilot", "飞艇"), custom("survival", "湿地"), custom("lore", "梦境史")]
    ids = [s["id"] for s in specs]
    card = patch(
        client,
        card,
        occupation="acrobat",
        custom_specializations=specs,
        selected_specializations=ids,
        occupation_group_choices={"personal": ids[:2]},
        approve_specializations=[ids[0], ids[2]],
        occupation_skills={
            "credit_rating": {"points": 9},
            ids[0]: {"points": 30},
            ids[1]: {"points": 20},
        },
        interest_skills={ids[2]: {"points": 40}},
    )
    assert card["validation"]["valid"], card["validation"]
    assert [card["skill_values"][k] for k in ids] == [31, 30, 41]
    assert all(check_value(card, "skill", k) == card["skill_values"][k] for k in ids)
    assert set(ids) <= prompt_skills(card).keys()
    bad = patch(client, card, interest_skills={k: {"points": 80} for k in ids})
    assert {i["code"] for i in bad["validation"]["issues"]} >= {"overspent", "maximum"}
    specs[0]["name"] = "直升飞机"
    bad = patch(client, bad, custom_specializations=specs, interest_skills={})
    assert "era" in {i["code"] for i in bad["validation"]["issues"]}
    for group, name in [("pilot", "飞行器"), ("survival", "沙漠"), ("lore", "民俗")]:
        model = CharacterDraft.model_validate(card)
        model.custom_specializations = [CustomSpecialization(**custom(group, name))]
        recalculate(model, load_rulesets()[card["ruleset_id"]])
        assert any(i.code == "duplicate" for i in model.validation.issues)
    for change in [{"group": "firearms"}, {"base_value": 60}, {"id": "custom_pilot_bad"}]:
        assert (
            client.patch(
                f"/api/characters/{card['id']}",
                json={
                    "version": bad["version"],
                    "custom_specializations": [{**custom("pilot", "飞艇"), **change}],
                },
            ).status_code
            == 422
        )


def test_keeper_introduction_cannot_be_forged_and_rename_preserves_points(client):
    spec = custom("lore", "梦境史")
    key = spec["id"]
    card = patch(
        client,
        create_seventh(client),
        occupation="firefighter",
        occupation_attribute="str",
        custom_specializations=[spec],
        selected_specializations=[key],
        occupation_skills={"credit_rating": {"points": 9}},
        interest_skills={key: {"points": 30}},
    )
    assert [i["code"] for i in card["validation"]["issues"]] == ["keeper_approval"]
    assert key not in available_skills(card) and "lore_folklore" not in available_skills(card)
    endpoint = f"/api/characters/{card['id']}"
    assert client.post(endpoint + "/finalize", json={"version": card["version"]}).status_code == 422
    assert (
        client.patch(
            endpoint, json={"version": card["version"], "specialization_approvals": {key: "forged"}}
        ).status_code
        == 422
    )
    approved = patch(client, card, approve_specializations=[key])
    assert approved["validation"]["valid"] and key in available_skills(approved)
    spec["name"] = "梦境地理"
    renamed = patch(client, approved, custom_specializations=[spec])
    assert renamed["skill_values"][key] == 31 and renamed["roll_records"] == card["roll_records"]
    assert not renamed["specialization_approvals"] and not renamed["validation"]["valid"]
    approved = patch(client, renamed, approve_specializations=[key])
    document = ok(client.get(endpoint + "/export"))
    imported = ok(client.post("/api/characters/import", json=document), 201)
    assert not imported["specialization_approvals"] and imported["skill_values"][key] == 31
    # Deletion withdraws allocation and leaves no runtime ghost or cached approval.
    deleted = patch(
        client, approved, custom_specializations=[], selected_specializations=[], interest_skills={}
    )
    assert key not in deleted["skill_values"] and not deleted["specialization_approvals"]
    assert deleted["remaining_points"]["interest"] == approved["remaining_points"]["interest"] + 30
    forbidden = patch(client, deleted, custom_specializations=[custom("lore", "克苏鲁神话")])
    assert any(i["code"] == "forbidden_name" for i in forbidden["validation"]["issues"])


def test_archive_draft_import_and_frozen_room_are_not_migrated(client, monkeypatch):
    service = client.app.state.room_service.submissions.characters
    old = archived_ruleset("coc7-character-creation", "1.1.0")
    with monkeypatch.context() as m:
        m.setitem(service.rulesets, old.id, old)
        card = create_seventh(client)
        card = patch(
            client,
            card,
            occupation="missionary",
            occupation_group_choices={
                "art": ["art_cooking"],
                "social": ["persuade"],
                "personal": ["history", "biology"],
            },
            occupation_skills={"credit_rating": {"points": 10}},
        )
    assert card["validation"]["valid"], card["validation"]
    before = deepcopy(card)
    card = patch(client, card, name="旧规则草稿编辑")
    for field in ["ruleset_version", "skill_values", "remaining_points", "roll_records"]:
        assert card[field] == before[field]
    endpoint = f"/api/characters/{card['id']}"
    card = ok(client.post(endpoint + "/finalize", json={"version": card["version"]}))
    room = create_room(client)
    prefix = f"/api/rooms/{room['room']['id']}"
    published = ok(client.post(prefix + "/character-slots", json={"character_id": card["id"]}))[
        "room"
    ]
    snap = published["character_slots"][0]["character_snapshot"]
    document = ok(client.get(endpoint + "/export"))
    imported = ok(client.post("/api/characters/import", json=document), 201)
    assert (
        imported["ruleset_version"] == "1.1.0" and imported["skill_values"] == card["skill_values"]
    )
    assert ok(client.get(prefix))["character_slots"][0]["character_snapshot"] == snap
    assert (
        ok(client.get("/api/character-rulesets/coc7-character-creation?version=1.1.0"))["version"]
        == "1.1.0"
    )


def test_submission_rechecks_restricted_specialties_and_freezes(client):
    card = create_seventh(client)
    specs = [custom("pilot", "飞艇"), custom("survival", "湿地"), custom("lore", "梦境史")]
    p, s, lore_id = [x["id"] for x in specs]
    card = patch(
        client,
        card,
        occupation="acrobat",
        custom_specializations=specs,
        selected_specializations=[p, s, lore_id],
        occupation_group_choices={"personal": [p, s]},
        occupation_skills={"credit_rating": {"points": 9}, p: {"points": 30}, s: {"points": 20}},
        interest_skills={lore_id: {"points": 40}},
        approve_specializations=[p, lore_id],
    )
    assert card["validation"]["valid"]
    endpoint = f"/api/characters/{card['id']}"
    ok(client.post(endpoint + "/finalize", json={"version": card["version"]}))
    doc = ok(client.get(endpoint + "/export"))
    doc["character"]["skill_values"][lore_id] = 999
    doc["character"]["derived_values"]["hp"] = 999
    room = create_room(client)
    player = join(client, room["invite_code"])
    prefix = f"/api/rooms/{room['room']['id']}"
    path = prefix + "/character-submissions"
    auth = headers(player["member_token"])
    preview = ok(client.post(path + "/preview", headers=auth, json={"document": doc}))
    assert preview["skill_values"][lore_id] == 41 and preview["derived_values"]["hp"] == 12
    assert not preview["specialization_approvals"]
    assert {i["code"] for i in preview["validation"]["issues"]} == {"keeper_approval"}
    tampered = deepcopy(doc)
    tampered["character"]["interest_skills"][lore_id]["points"] = 999
    assert (
        client.post(path + "/preview", headers=auth, json={"document": tampered}).status_code == 422
    )
    submitted = ok(
        client.post(
            path,
            headers=auth,
            json={"document": doc, "expected_version": 0, "client_request_id": str(uuid4())},
        )
    )["room"]
    row = submitted["character_submissions"][0]
    review = dict(expected_version=1, decision="accept", client_request_id=str(uuid4()))
    assert client.post(path + f"/{row['id']}/review", json=review).status_code == 422
    review.update(approve_specializations=[p, lore_id], client_request_id=str(uuid4()))
    assert client.post(path + f"/{row['id']}/review", json=review, headers=auth).status_code == 403
    accepted = ok(client.post(path + f"/{row['id']}/review", json=review))["room"]
    slot = accepted["character_slots"][0]
    frozen = slot["character_snapshot"]
    assert slot["member_id"] == player["room"]["self_member_id"]
    assert frozen["status"] == "finalized" and frozen["skill_values"][lore_id] == 41
    assert check_value(frozen, "skill", lore_id) == 41
    assert accepted["session_state"]["characters"][slot["id"]]["hp"] == 12
    assert [r["dice"] for r in frozen["roll_records"]] == [r["dice"] for r in card["roll_records"]]


def test_new_specialty_normal_service_check_and_keeper_context(client, lobby):  # noqa: F811
    from test_action_adjudication import modern_response
    from test_agent_runtime import accept_original, submit, wait_cycle
    from test_agent_runtime import game as make_game

    from app.agents.model import FakeModelAdapter

    card = create_seventh(client)
    spec = custom("survival", "湿地")
    key = spec["id"]
    card = patch(
        client,
        card,
        occupation="outdoorsman",
        occupation_attribute="str",
        custom_specializations=[spec],
        occupation_group_choices={"firearms": ["handgun"], "survival": [key]},
        occupation_skills={"credit_rating": {"points": 9}, key: {"points": 50}},
    )
    endpoint = f"/api/characters/{card['id']}"
    card = ok(client.post(endpoint + "/finalize", json={"version": card["version"]}))
    prefix = lobby["prefix"]
    room = ok(client.post(prefix + "/character-slots", json={"character_id": card["id"]}))["room"]
    slot = next(s for s in room["character_slots"] if s["source_character_id"] == card["id"])
    lobby["slots"][0] = slot["id"]
    game = make_game.__wrapped__(client, lobby)
    contexts = []

    def response(messages, kwargs):
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            context = json.loads(messages[-1]["content"])
            actor = next(c for c in context["characters"] if c["member_id"] == game["player"])
            assert actor["skill_values"][key] == 60
            assert actor["custom_skill_names"][key] == "生存（湿地）"
            assert "lore_folklore" not in actor["skill_values"]
            contexts.append(actor)
            result["proposed_check"]["name"] = key
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "我冒着误判地面而陷入泥潭的危险辨认湿地落脚点，进行生存湿地检定。"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "waiting_for_roll"
    check = ok(client.get(prefix + "/checks"))[0]
    assert (check["display_name"], check["value"]) == ("生存（湿地）", 60)
    plan = ok(client.get(prefix + f"/cycles/{cycle['id']}/plan"))
    assert plan["proposed_check"]["name"] == key and contexts
    result = ok(client.post(prefix + f"/checks/{check['id']}/roll", json={}))
    accept_original(client, game, check["id"])
    assert wait_cycle(client, game)["status"] == "completed"
    again = ok(client.post(prefix + f"/checks/{check['id']}/roll", json={}))
    assert result["check"]["dice"] == again["check"]["dice"]


@pytest.mark.asyncio
async def test_inspect_character_returns_new_specialty_names_and_values():
    from app.agents.tools import AgentTools

    spec = custom("survival", "湿地")
    snapshot = dict(
        ruleset_id="coc7-character-creation",
        ruleset_version="1.2.0",
        custom_specializations=[spec],
        skill_values={spec["id"]: 60, "lore_folklore": 1},
    )

    async def slots(*_):
        return [SimpleNamespace(member_id="player", id="slot", character_snapshot=snapshot)]

    async def nothing(*_):
        return None

    async def module(*_):
        return SimpleNamespace(enabled=True)

    service = SimpleNamespace(
        rooms=SimpleNamespace(slots=slots),
        navigation=SimpleNamespace(state=nothing),
        module=module,
        entities=SimpleNamespace(binding=nothing),
    )
    executor = AgentTools(service)
    room = SimpleNamespace(id="room", session_state={"characters": {"slot": {"hp": 12}}})
    result = await executor.dispatch(
        SimpleNamespace(get=nothing),
        room,
        SimpleNamespace(cycle_id="cycle"),
        None,
        None,
        "inspect_character",
        SimpleNamespace(member_id="player"),
    )
    assert result["custom_skill_names"][spec["id"]] == "生存（湿地）"
    assert result["skill_values"][spec["id"]] == 60 and result["runtime"]["hp"] == 12
    assert "lore_folklore" not in result["skill_values"]
