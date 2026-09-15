"""Focused discovery and custom skill regressions; no external model calls."""

from uuid import uuid4

import pytest
from test_coc7_creation import create_seventh
from test_rooms import ok

from app.domain.character import CharacterDraft
from app.domain.specializations import CustomSpecialization
from app.preparation.search import complete_automatic_discovery
from app.rules.checks import check_value, prompt_skills
from app.rules.display import resolve_check_name
from app.rules.engine import recalculate
from app.rules.loader import archived_ruleset, load_rulesets


def custom(group="language", name="葡萄牙语"):
    return dict(id=f"custom_{group}_{uuid4().hex}", group=group, name=name)


def test_custom_card_roundtrip_and_same_identity_rename(client):
    card = create_seventh(client)
    specs = [custom(), custom("art_craft", "陶艺"), custom("science", "地球物理学")]
    language, art, science = [s["id"] for s in specs]
    endpoint = "/api/characters/" + card["id"]
    card = ok(
        client.patch(
            endpoint,
            json=dict(
                version=card["version"],
                occupation="professor",
                occupation_group_choices={
                    "language": [language],
                    "academic": ["history", "biology", "chemistry", "occult"],
                },
                custom_specializations=specs,
                selected_specializations=[art, science],
                occupation_skills={"credit_rating": {"points": 20}, language: {"points": 30}},
                interest_skills={art: {"points": 20}, science: {"points": 10}},
            ),
        )
    )
    assert card["validation"]["valid"], card["validation"]
    assert [card["skill_values"][k] for k in [language, art, science]] == [31, 25, 11]
    assert card["skill_half_values"][language] == 15
    assert card["skill_fifth_values"][art] == 5
    assert check_value(card, "skill", science) == 11
    assert all(k in prompt_skills(card) for k in [language, art, science])
    assert resolve_check_name(art, snapshot=card)["display_name"] == "艺术与手艺（陶艺）"
    specs[0]["name"] = "冰岛语"
    renamed = ok(
        client.patch(endpoint, json=dict(version=card["version"], custom_specializations=specs))
    )
    assert renamed["skill_values"] == card["skill_values"]
    assert renamed["roll_records"] == card["roll_records"]
    final = ok(client.post(endpoint + "/finalize", json={"version": renamed["version"]}))
    exported = ok(client.get(endpoint + "/export"))
    exported["character"]["skill_values"][science] = 999
    imported = ok(client.post("/api/characters/import", json=exported), 201)
    for field in [
        "custom_specializations",
        "skill_values",
        "skill_base_values",
        "occupation_group_choices",
    ]:
        assert imported[field] == final[field]
    assert [r["dice"] for r in imported["roll_records"]] == [
        r["dice"] for r in final["roll_records"]
    ]
    endpoint = "/api/characters/" + imported["id"]
    # Removing definitions alone cannot leave purchasable/checkable ghost keys.
    assert (
        client.patch(
            endpoint, json={"version": imported["version"], "custom_specializations": []}
        ).status_code
        == 422
    )
    deleted = ok(
        client.patch(
            endpoint,
            json=dict(
                version=imported["version"],
                custom_specializations=[],
                selected_specializations=[],
                occupation_group_choices={},
                occupation_skills={},
                interest_skills={},
            ),
        )
    )
    assert all(k not in deleted["skill_values"] for k in [language, art, science])
    with pytest.raises(ValueError):
        check_value(deleted, "skill", language)


@pytest.mark.parametrize(
    "group,name", [("language", "拉丁语"), ("science", "数学"), ("art_craft", "表演")]
)
def test_catalog_duplicates_cannot_change_base_values(client, group, name):
    card = CharacterDraft.model_validate(create_seventh(client))
    card.custom_specializations = [CustomSpecialization(**custom(group, name))]
    recalculate(card, load_rulesets()[card.ruleset_id])
    assert any(i.code == "duplicate" for i in card.validation.issues)


def test_duplicate_names_ids_and_legacy_are_rejected(client):
    card = CharacterDraft.model_validate(create_seventh(client))
    first = custom(name=" TEST ")
    card.custom_specializations = [
        CustomSpecialization(**first),
        CustomSpecialization(**custom(name="Ｔｅｓｔ")),
    ]
    rules = load_rulesets()[card.ruleset_id]
    recalculate(card, rules)
    assert any(i.code == "duplicate" for i in card.validation.issues)
    card.custom_specializations = [CustomSpecialization(**first)] * 2
    recalculate(card, rules)
    assert any(i.code == "duplicate" for i in card.validation.issues)
    recalculate(card, archived_ruleset(card.ruleset_id, "1.0.0"))
    assert any(i.code == "unsupported" for i in card.validation.issues)
    assert first["id"] not in card.skill_values


@pytest.mark.parametrize(
    "change",
    [
        {"name": " "},
        {"name": "a\n"},
        {"name": "科学（新项）"},
        {"name": "x" * 41},
        {"group": "fighting"},
        {"id": "spot_hidden"},
        {"base_value": 80},
        {"maximum": 999},
        {"group": "science"},
    ],
)
def test_custom_schema_rejects_injected_rules(client, change):
    card = create_seventh(client)
    response = client.patch(
        "/api/characters/" + card["id"],
        json={
            "version": card["version"],
            "custom_specializations": [{**custom(), **change}],
        },
    )
    assert response.status_code == 422


def test_allocation_bounds_and_occupation_groups_use_custom_definitions(client):
    card = CharacterDraft.model_validate(create_seventh(client))
    card.occupation = "professor"
    spec = CustomSpecialization(**custom("science", "地球物理学"))
    card.custom_specializations = [spec]
    from app.domain.character import SkillAllocation

    card.interest_skills = {spec.id: SkillAllocation(points=99)}
    rules = load_rulesets()[card.ruleset_id]
    recalculate(card, rules)
    assert {i.code for i in card.validation.issues} >= {"specialization", "maximum"}
    card.selected_specializations = [spec.id]
    card.interest_skills[spec.id].points = -1
    card.occupation_group_choices = {
        "language": [spec.id],
        "academic": ["history", "biology", "chemistry"],
    }
    recalculate(card, rules)
    assert {i.code for i in card.validation.issues} >= {"negative", "selection"}


@pytest.mark.parametrize(
    "action,eligible,expected",
    [
        ("我仔细查看墙上的图示。", True, True),
        ("我检查墙上的图示，看看有没有标注。", True, True),
        ("我仔细查看墙上的图示。", False, False),
        ("不要查看墙上的图示。", True, False),
        ("如果查看墙上的图示会怎样？", True, False),
        ("我拿起墙上的图示。", True, False),
    ],
)
def test_selected_automatic_discovery_completes_only_actual_observation(action, eligible, expected):
    plan = dict(
        parsed_intent={"type": "observe"},
        focus={"action": action, "action_target_id": "map"},
        proposed_reveal_entity_ids=[],
    )
    context = {"automatic_discoveries": [{"entity_id": "map"}] if eligible else []}
    complete_automatic_discovery(plan, context)
    assert plan["proposed_reveal_entity_ids"] == (["map"] if expected else [])
    complete_automatic_discovery(plan, context)
    assert len(plan["proposed_reveal_entity_ids"]) <= 1


def test_formal_retest_status_tracks_actual_provider_calls_and_scenario():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from check_batch25_evidence import formal_retest_status

    config = dict(provider="openai", model="model", base_url="https://api.openai.com/v1/")
    calls = [
        dict(provider="openai", model="model", response_model="actual", token_usage={"input": 10})
    ]
    assert formal_retest_status(config, [], "not_run") == "not_run"
    assert formal_retest_status(config, [], "not_run", True) == "blocked"
    assert formal_retest_status({**config, "provider": "ollama"}, calls, "passed") == "not_run"
    assert formal_retest_status(config, calls, "passed") == "passed"
    assert formal_retest_status(config, calls, "failed") == "failed"
    assert formal_retest_status(config, calls, "not_run") == "not_run"
    assert (
        formal_retest_status({**config, "base_url": "https://example.com/v1"}, calls, "passed")
        == "failed"
    )


def test_automatic_plan_omitted_reveal_uses_real_context(client, lobby, bundle):  # noqa: F811
    import json

    from test_action_adjudication import modern_response
    from test_agent_runtime import submit, wait_cycle
    from test_batch24_packages import import_bundle
    from test_rooms import prepare

    from app.agents.model import FakeModelAdapter

    imported = ok(import_bundle(client, bundle))
    prefix = lobby["prefix"]
    ok(
        client.patch(
            prefix + "/module-preparation", json={"preparation_id": imported["preparation_id"]}
        )
    )
    prepare(client, lobby)
    ok(client.post(prefix + "/pause"))
    for role, member in [
        ("keeper", lobby["room"]["host_member_id"]),
        ("investigator", lobby["agent"]),
    ]:
        p = ok(client.post("/api/agent-profiles", json={"role": role, "name": role}), 201)
        ok(
            client.post(
                prefix + "/agent-bindings", json={"member_id": member, "profile_id": p["id"]}
            )
        )
    ok(client.post(prefix + "/resume"))
    eid = imported["entity_ids"]["paper"]

    def response(messages, kwargs):
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            context = json.loads(messages[-1]["content"])
            assert eid in {e["entity_id"] for e in context["automatic_discoveries"]}
            result["parsed_intent"]["type"] = "observe"
            result["focus"] = {
                "action": context["triggering_action"]["payload"]["text"],
                "action_target_id": eid,
            }
            result["proposed_reveal_entity_ids"] = []
            # Some models request a roll despite an empty obstacle. Its later
            # removal must still leave a real ordinary discovery to execute.
            result["proposed_check"] = {
                "target_member_id": context["action_identifiers"]["actor_member_id"],
                "name": "spot_hidden",
                "reason": "查看图示",
            }
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    before = ok(client.get(prefix))["inventory"]
    ok(submit(client, lobby, "我仔细查看墙上的图示。"))
    assert wait_cycle(client, lobby)["status"] == "completed"
    assert eid in {e["id"] for e in ok(client.get(prefix + "/public-entities"))}
    assert not ok(client.get(prefix + "/checks"))
    assert ok(client.get(prefix))["inventory"] == before


from test_batch24_packages import bundle  # noqa: E402,F401
from test_rooms import lobby  # noqa: E402,F401


def test_custom_skill_frozen_room_check_and_roll_replay(client, lobby):  # noqa: F811
    import json

    from test_action_adjudication import modern_response
    from test_agent_runtime import accept_original, submit, wait_cycle
    from test_agent_runtime import game as make_game

    from app.agents.model import FakeModelAdapter

    card = create_seventh(client)
    spec = custom()
    key = spec["id"]
    endpoint = "/api/characters/" + card["id"]
    card = ok(
        client.patch(
            endpoint,
            json=dict(
                version=card["version"],
                occupation="firefighter",
                occupation_attribute="str",
                occupation_skills={"credit_rating": {"points": 9}},
                custom_specializations=[spec],
                selected_specializations=[key],
                interest_skills={key: {"points": 60}},
            ),
        )
    )
    card = ok(client.post(endpoint + "/finalize", json={"version": card["version"]}))
    prefix = lobby["prefix"]
    room = ok(client.post(prefix + "/character-slots", json={"character_id": card["id"]}))["room"]
    slot = next(s for s in room["character_slots"] if s["source_character_id"] == card["id"])
    assert slot["character_snapshot"]["custom_specializations"] == [spec]
    lobby["slots"][0] = slot["id"]
    game = make_game.__wrapped__(client, lobby)

    def response(messages, kwargs):
        value = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            context = json.loads(messages[-1]["content"])
            actor = next(c for c in context["characters"] if c["member_id"] == game["player"])
            assert actor["skill_values"][key] == 61
            assert actor["custom_skill_names"][key] == "外语（葡萄牙语）"
            value["proposed_check"]["name"] = key
        return value

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "我冒着误读导致危险的风险翻译这段文字，进行葡萄牙语检定。"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "waiting_for_roll"
    check = ok(client.get(prefix + "/checks"))[0]
    assert (check["name"], check["value"], check["display_name"]) == (
        "外语（葡萄牙语）",
        61,
        "外语（葡萄牙语）",
    )
    plan = ok(client.get(prefix + f"/cycles/{cycle['id']}/plan"))
    assert plan["proposed_check"]["name"] == key
    path = prefix + f"/checks/{check['id']}/roll"
    first = ok(client.post(path, json={}))
    accept_original(client, game, check["id"])
    assert wait_cycle(client, game)["status"] == "completed"
    again = ok(client.post(path, json={}))
    assert first["check"]["dice"] == again["check"]["dice"]
    assert check_value(slot["character_snapshot"], "skill", key) == 61
