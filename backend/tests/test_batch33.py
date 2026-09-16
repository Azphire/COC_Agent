"""Mythos P1: isolated creation, approval, real SAN and persistent knowledge."""

# Imported pytest fixtures are intentionally redeclared as test arguments.
# ruff: noqa: F811

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_batch10 import FixedRandom, current, effect, manage, restore, roll, save, source_event
from test_batch10 import request as encounter
from test_batch28 import patch
from test_batch30 import document, finalize, normal_check, special_card
from test_coc7_creation import create_seventh
from test_module_preparation import approve_opening, preparation  # noqa: F401
from test_rooms import headers, lobby, ok, prepare  # noqa: F401

from app.dice.service import DiceService
from app.domain.character import CharacterDraft
from app.rooms.sanity_schemas import SanityEffect
from app.rules.engine import recalculate
from app.rules.loader import archived_ruleset, load_rulesets
from app.rules.mythos import initial_belief


def selection(belief="believer", value=7, method="manual", spells=False):
    return dict(
        package="mythos",
        history="与KP讨论过的读书研究经历",
        mythos=dict(
            knowledge="reading",
            belief=belief,
            method=method,
            value=value if method == "manual" else None,
            backgrounds=[
                dict(kind="phobia", detail="神话研究造成的幽闭恐惧"),
                dict(kind="encounter", detail="梦中反复出现的怪异轮廓"),
            ],
            spells=[dict(id=str(uuid4()), name="KP许可的隔离法术记录", source="隔离KP记录来源")]
            if spells
            else [],
        ),
    )


def mythos_card(client, belief="believer", value=7, method="manual", spells=False, approve=False):
    return patch(
        client,
        create_seventh(client),
        occupation="doctor",
        occupation_group_choices={"academic": ["history", "law"]},
        occupation_skills={"credit_rating": {"points": 30}},
        experience=selection(belief, value, method, spells),
        **({"approve_experience": ["mythos"]} if approve else {}),
    )


@pytest.mark.parametrize("belief", ["believer", "unbeliever"])
@pytest.mark.parametrize("value", [0, 7, 25, 50, 99])
def test_creation_san_sources_repeat_and_freeze(client, belief, value):
    c = mythos_card(client, belief, value)
    assert {i["code"] for i in c["validation"]["issues"]} == {"keeper_approval"}
    assert not c["experience_rolls"] and c["remaining_points"]["experience"] == 0
    assert c["initial_mythos"] == c["skill_values"]["cthulhu_mythos"] == value
    expected = min(99 - value, max(0, 50 - (value if belief == "believer" else 0)))
    assert c["derived_values"]["san"] == expected
    assert c["derived_values"]["san_max"] == 99 - value
    assert c["initial_mythos_sources"][0]["source"] == "experience:mythos"
    assert finalize(client, c).status_code == 422
    c = patch(client, c, approve_experience=["mythos"])
    assert c["validation"]["valid"]
    for _ in range(2):
        c = patch(client, c, name="普通姓名修改")
        assert c["derived_values"]["san"] == expected and c["initial_mythos"] == value
        assert c["experience_approvals"] and not c["known_spells"]
    sheet = ok(finalize(client, c))
    assert initial_belief(sheet) == belief


def test_random_once_manual_switch_revoke_import(client):
    from app.api.characters import character_service

    c = mythos_card(client)
    svc = character_service(SimpleNamespace(app=client.app))
    rng = FixedRandom(4)
    svc.dice = DiceService(rng)
    client.app.dependency_overrides[character_service] = lambda: svc
    random = selection(method="suggested_roll")
    c = patch(client, c, experience=random)
    original = deepcopy(c)
    assert c["initial_mythos"] == 9
    assert c["experience_rolls"]["mythos"]["purpose"] == "initial_mythos"
    for choice in [selection(value=25), random, None, random, random]:
        c = patch(client, c, experience=choice)
        assert c["experience_rolls"] == original["experience_rolls"]
    assert rng.used == [4]
    c = patch(client, c, approve_experience=["mythos"])
    imported = ok(client.post("/api/characters/import", json=document(client, c)), 201)
    assert imported["initial_mythos"] == 9 and not imported["experience_approvals"]
    imported_roll = imported["experience_rolls"]["mythos"]
    assert imported_roll == {**original["experience_rolls"]["mythos"], "source": "imported"}
    imported = patch(client, imported, name="导入后改名")
    assert imported["initial_mythos"] == 9 and rng.used == [4]


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "experience_san"),
        ("attribute", "experience_war"),
        ("formula", "1d10"),
        ("total", 20),
        ("dice", [11]),
        ("multiplier", 2),
        ("modifier", 4),
        ("source", "client"),
    ],
)
def test_import_checks_original_mythos_roll(client, field, value):
    c = mythos_card(client, method="suggested_roll")
    doc = document(client, c)
    doc["character"]["experience_rolls"]["mythos"][field] = value
    r = client.post("/api/characters/import", json=doc)
    if field == "source":
        assert r.status_code == 422
    else:
        imported = ok(r, 201)
        assert any(i["code"] == "inconsistent" for i in imported["validation"]["issues"])
        assert finalize(client, imported).status_code == 422


def test_import_missing_roll_never_refilled_or_mixed_with_attribute_rolls(client):
    c = mythos_card(client, method="suggested_roll")
    doc = document(client, c)
    doc["character"]["roll_records"].append({
        **doc["character"]["experience_rolls"]["mythos"], "id": str(uuid4())
    })
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    assert any(i["field"] == "roll_records" for i in imported["validation"]["issues"])
    doc = document(client, c)
    doc["character"]["experience_rolls"] = {}
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    for choice in [None, selection(method="suggested_roll")]:
        imported = patch(client, imported, experience=choice)
        assert not imported["experience_rolls"]
    assert any(i["code"] == "incomplete" for i in imported["validation"]["issues"])


@pytest.mark.parametrize("pool", ["occupation_skills", "interest_skills", "experience_skills"])
def test_mythos_cannot_be_purchased(client, pool):
    c = mythos_card(client)
    c = patch(client, c, **{pool: {"cthulhu_mythos": {"points": 1}}}, approve_experience=["mythos"])
    assert any(i["code"] == "forbidden" for i in c["validation"]["issues"])
    assert finalize(client, c).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        "background",
        "history",
        "direct",
        "manual_missing",
        "random_manual",
        "points",
        "spells_unbeliever",
        "duplicate",
    ],
)
def test_invalid_choice_cannot_be_approved_around(client, change):
    c = mythos_card(client)
    exp = deepcopy(c["experience"])
    p = exp["mythos"]
    if change == "background":
        p["backgrounds"] = p["backgrounds"][:1]
    elif change == "history":
        exp["history"] = " "
    elif change == "direct":
        p.update(knowledge="direct", belief="unbeliever")
    elif change == "manual_missing":
        p["value"] = None
    elif change == "random_manual":
        p["method"] = "suggested_roll"
    elif change == "spells_unbeliever":
        p.update(belief="unbeliever", spells=selection(spells=True)["mythos"]["spells"])
    elif change == "duplicate":
        p["backgrounds"][1] = p["backgrounds"][0]
    c = patch(
        client,
        c,
        experience=exp,
        approve_experience=["mythos"],
        **({"experience_skills": {"listen": {"points": 1}}} if change == "points" else {}),
    )
    assert any(i["code"] != "keeper_approval" for i in c["validation"]["issues"])
    assert finalize(client, c).status_code == 422


@pytest.mark.parametrize("value", [-1, 100, True, 1.5])
def test_invalid_mythos_numbers(client, value):
    c = mythos_card(client)
    assert (
        client.patch(
            f"/api/characters/{c['id']}",
            json={
                "version": c["version"],
                "experience": selection(value=value),
                "approve_experience": ["mythos"],
            },
        ).status_code
        == 422
    )


def test_combination_is_explicitly_unsupported_without_changing_occultist(client):
    c = special_card(client, "occultist", value=25, approve=True)
    assert c["initial_mythos"] == 25 and c["derived_values"]["san"] == 50
    c = patch(client, c, experience=selection(), approve_experience=["mythos"])
    assert any(i["code"] == "unsupported_combination" for i in c["validation"]["issues"])
    assert c["initial_mythos"] == 25 and finalize(client, c).status_code == 422


def test_known_spells_permission_revocation_import_and_names(client):
    c = mythos_card(client, spells=True)
    assert c["experience"]["mythos"]["spells"] and not c["known_spells"]
    c = patch(client, c, approve_experience=["mythos"])
    assert len(c["known_spells"]) == 1
    known = deepcopy(c["known_spells"])
    assert known[0]["casting_status"] == "unsupported" and known[0]["permission"]
    c = patch(client, c, name="改名保持法术许可")
    assert c["known_spells"] == known
    doc = document(client, c)
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    assert not imported["known_spells"] and not imported["experience_approvals"]
    assert imported["experience"]["mythos"]["spells"][0]["id"] == known[0]["id"]
    exp = deepcopy(c["experience"])
    exp["mythos"]["spells"][0]["source"] = "更换来源"
    c = patch(client, c, experience=exp)
    assert not c["known_spells"] and not c["experience_approvals"]
    exp["mythos"]["spells"].append({**exp["mythos"]["spells"][0], "id": str(uuid4())})
    c = patch(client, c, experience=exp, approve_experience=["mythos"])
    assert any(i["code"] == "duplicate" for i in c["validation"]["issues"])


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"])
def test_legacy_forged_fields_get_no_benefit(client, version):
    c = mythos_card(client, spells=True, approve=True)
    doc = document(client, c)
    doc["ruleset"]["version"] = doc["character"]["ruleset_version"] = version
    # The original 1.0 catalogue does not contain doctor; use its known occupation.
    if version == "1.0.0":
        doc["character"]["occupation"] = "professor"
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    assert imported["initial_mythos"] == 0 and not imported["initial_belief"]
    assert not imported["experience_effects"] and not imported["known_spells"]
    assert not initial_belief({**c, "status": "finalized", "ruleset_version": version})
    assert finalize(client, imported).status_code == 422


def test_recompute_policy_changes_invalidate_permission(client):
    c = CharacterDraft.model_validate(mythos_card(client, approve=True, spells=True))
    rules = load_rulesets()[c.ruleset_id]
    for _ in range(3):
        recalculate(c, rules)
        assert c.initial_mythos == 7 and c.known_spells
    next(p for p in rules.experience_packages if p.key == "mythos").source += " altered"
    recalculate(c, rules)
    assert not c.known_spells and not c.experience_approvals
    assert archived_ruleset(c.ruleset_id, "1.4.0").version == "1.4.0"


@pytest.mark.parametrize("package", ["war", "police", "criminal", "medical"])
def test_legacy_14_package_still_freezes_and_matches_immunity(client, package):
    from test_batch32 import package_card

    from app.rules.experiences import matching_immunity

    c = package_card(client, package, approve=True)
    doc = document(client, c)
    doc["ruleset"]["version"] = doc["character"]["ruleset_version"] = "1.4.0"
    doc["character"]["experience"].pop("mythos")
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    imported = patch(client, imported, approve_experience=[package])
    sheet = ok(finalize(client, imported))
    assert sheet["skill_values"] == c["skill_values"]
    assert sheet["derived_values"] == c["derived_values"]
    assert sheet["initial_belief"] is None and not sheet["known_spells"]
    proof = SanityEffect.model_validate({
        **effect("corpse", "0", "1").model_dump(), "perception": "visual",
        "experience_category": "witness_corpse", "experience_basis": "仅为普通人类尸体",
    })
    assert matching_immunity(sheet, proof)["package"] == package


def game_with_mythos(
    client,
    lobby,
    preparation,
    evidence="direct",
    loss="2",
    value=7,
    belief="unbeliever",
    extra_effects=None,
):
    c = mythos_card(client, belief, value, approve=True)
    sheet = ok(finalize(client, c))
    room = ok(
        client.post(lobby["prefix"] + "/character-slots", json={"character_id": sheet["id"]})
    )["room"]
    lobby["slots"][0] = room["character_slots"][-1]["id"]
    entity = next(e for e in preparation["entities"] if e["title"] == "公告")
    e = effect("mythos-proof", "0", loss).model_dump()
    e.update(
        mythos=True,
        mythos_evidence=evidence,
        mythos_evidence_basis="隔离批准事实",
        perception="visual",
        automation="automatic",
    )
    ok(
        client.patch(
            f"/api/module-entities/{entity['id']}",
            json={"sanity_effects": [e] + (extra_effects or []), "initial_visibility": "revealed"},
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


@pytest.mark.parametrize(
    "evidence,die,convert",
    [
        ("direct", 70, True),
        ("direct", 20, False),
        (None, 70, False),
        ("otherworldly", 20, True),
        ("deity_avatar", 20, True),
    ],
)
def test_runtime_conversion_once_combined_loss_and_zero_exception(
    client, lobby, preparation, evidence, die, convert
):
    g = game_with_mythos(client, lobby, preparation, evidence=evidence)
    client.app.state.room_service.dice = DiceService(FixedRandom(die, 90))
    seq = source_event(client, g)
    check = encounter(client, g, "mythos-proof", seq)
    check = roll(client, g, check, "san")
    loss = (2 if die == 70 else 0) + (7 if convert else 0)
    assert check["sanity"]["loss"] == loss
    if loss >= 5:
        assert check["sanity"]["stage"] == "int"
        check = roll(client, g, check, "int")
    c = current(client, g)
    assert c["san"] == 50 - loss and c["sanity"]["day_loss"] == loss
    assert c["sanity"]["belief"] == ("believer" if convert else "unbeliever")
    assert c["sanity"]["mythos_gain"] == 0
    assert bool(c["sanity"]["belief_conversion"]) == convert
    assert encounter(client, g, "mythos-proof", seq)["id"] == check["id"]
    assert current(client, g)["sanity"] == c["sanity"]


@pytest.mark.parametrize("value,kind", [(4, "temporary"), (8, "indefinite"), (50, "permanent")])
def test_conversion_insanity_daily_limit_and_permanent(client, lobby, preparation, value, kind):
    g = game_with_mythos(client, lobby, preparation, value=value)
    dice = [70, 20, 2, 4] if kind == "temporary" else [70, 4] if kind == "indefinite" else [70]
    client.app.state.room_service.dice = DiceService(FixedRandom(*dice))
    check = roll(client, g, encounter(client, g, "mythos-proof"), "san")
    if kind == "temporary":
        check = roll(client, g, check, "int")
        check = roll(client, g, check, "duration")
    c = current(client, g)
    assert c["sanity"]["kind"] == kind
    assert c["sanity"]["day_loss"] == min(99 - value, 50, value + 2)
    assert c["sanity"]["belief_conversion"]["cost"] == value
    assert c["sanity"]["mythos_gain"] == (0 if kind == "permanent" else 5)
    assert c["san_max"] == max(0, 99 - value - c["sanity"]["mythos_gain"])
    if kind != "permanent":
        ok(manage(client, g, "symptom", symptom="隔离症状"))
    snapshot = save(client, g)
    for _ in range(2):
        restore(client, g, snapshot)
        assert (
            current(client, g)["sanity"] == c["sanity"]
            if kind == "permanent"
            else current(client, g)["sanity"]["belief_conversion"]
            == c["sanity"]["belief_conversion"]
        )


def test_evidence_schema_requires_approved_facts_and_no_client_boolean():
    base = effect("proof", "0", "2").model_dump()
    for changes in [
        dict(mythos_evidence="direct"),
        dict(mythos=True, mythos_evidence="direct"),
        dict(mythos=True, mythos_evidence="deity_avatar", mythos_evidence_basis="fact"),
    ]:
        with pytest.raises(ValidationError):
            SanityEffect.model_validate({**base, **changes})


@pytest.mark.parametrize("value", [0, 4, 7])
def test_voluntary_choice_owned_once_and_restore(client, lobby, preparation, value):
    g = game_with_mythos(client, lobby, preparation, value=value)
    p = g["prefix"]
    auth = headers(g["remote"]["member_token"])
    body = {"expected_revision": ok(client.get(p))["revision"], "reason": "玩家自愿相信所读内容"}
    assert client.post(p + "/sanity/voluntary-belief", json=body).status_code == 404
    client.app.state.room_service.dice = DiceService(FixedRandom(90))
    check = ok(client.post(p + "/sanity/voluntary-belief", headers=auth, json=body))["check"]
    assert not check["sanity"]["rolls"]
    assert check["sanity"]["loss"] == value
    if value >= 5:
        check = roll(client, g, check, "int")
    c = current(client, g)
    assert c["sanity"]["belief"] == "believer" and c["sanity"]["day_loss"] == value
    assert c["sanity"]["belief_conversion"]["evidence"] == "voluntary"
    assert (
        ok(client.post(p + "/sanity/voluntary-belief", headers=auth, json=body))["check"]["id"]
        == check["id"]
    )
    checkpoint = save(client, g)
    for _ in range(2):
        restore(client, g, checkpoint)
        ok(client.post(p + "/sanity/voluntary-belief", headers=auth, json=body))
        assert current(client, g)["sanity"] == c["sanity"]


def bind_fixture(client, g, responder):
    from app.agents.model import FakeModelAdapter

    ok(client.post(g["prefix"] + "/pause"))
    for role, member in [("keeper", g["room"]["host_member_id"]), ("investigator", g["agent"])]:
        profile = ok(
            client.post("/api/agent-profiles", json={"role": role, "name": "隔离确定性档案"}), 201
        )
        ok(
            client.post(
                g["prefix"] + "/agent-bindings",
                json={"member_id": member, "profile_id": profile["id"]},
            )
        )
    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=responder)
    ok(client.post(g["prefix"] + "/resume"))


def test_normal_action_automatic_conversion_and_private_receipt(client, lobby, preparation):
    import json

    from test_action_adjudication import modern_response
    from test_agent_runtime import wait_cycle
    from test_rooms import join

    g = game_with_mythos(client, lobby, preparation, value=2)

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
                "target_id": g["entity"],
                "confidence": 1,
                "evidence_quote": ctx["triggering_action"]["payload"]["text"],
            },
        }

    bind_fixture(client, g, respond)
    rng = FixedRandom(70)
    client.app.state.room_service.dice = DiceService(rng)
    auth = headers(g["remote"]["member_token"])
    ok(
        client.post(
            g["prefix"] + "/actions",
            headers=auth,
            json={
                "text": "我查看公告",
                "target_entity_id": g["entity"],
                "client_request_id": str(uuid4()),
            },
        )
    )
    assert wait_cycle(client, g)["status"] == "waiting_for_roll"
    check = next(c for c in ok(client.get(g["prefix"] + "/checks")) if c.get("sanity"))
    assert check["sanity"]["origin"] == "automatic"
    roll(client, g, check, "san")
    assert wait_cycle(client, g)["status"] == "completed"
    assert current(client, g)["sanity"]["belief"] == "believer"
    assert current(client, g)["sanity"]["day_loss"] == 4 and rng.used == [70]
    ok(
        client.post(
            g["prefix"] + "/actions",
            headers=auth,
            json={
                "text": "我再查看公告",
                "target_entity_id": g["entity"],
                "client_request_id": str(uuid4()),
            },
        )
    )
    assert wait_cycle(client, g)["status"] == "completed"
    assert len([c for c in ok(client.get(g["prefix"] + "/checks")) if c.get("sanity")]) == 1
    ok(client.post(g["prefix"] + "/pause"))
    stranger = join(client, g["created"]["invite_code"], "旁观者")
    assert not ok(client.get(g["prefix"] + "/checks", headers=headers(stranger["member_token"])))
    assert (
        client.post(
            g["prefix"] + "/actions",
            headers=auth,
            json={"text": "我相信了", "client_request_id": str(uuid4()), "belief": "believer"},
        ).status_code
        == 422
    )


def test_restore_between_san_and_loss_keeps_one_conversion(client, lobby, preparation):
    g = game_with_mythos(client, lobby, preparation, loss="1d4")
    rng = FixedRandom(70, 2, 90)
    client.app.state.room_service.dice = DiceService(rng)
    seq = source_event(client, g)
    check = roll(client, g, encounter(client, g, "mythos-proof", seq), "san")
    assert check["sanity"]["stage"] == "loss"
    checkpoint = save(client, g)
    for _ in range(2):
        restore(client, g, checkpoint)
        check = roll(client, g, check, "loss")
        check = roll(client, g, check, "int")
        c = current(client, g)
        assert c["san"] == 41 and c["sanity"]["day_loss"] == 9
        assert c["sanity"]["belief_conversion"]["cost"] == 7
    assert rng.used == [70, 2, 90]


def test_conversion_uses_current_mythos_before_new_insanity_gain(client, lobby, preparation):
    prior = {**effect("prior-insanity", "5", "5").model_dump(), "mythos": True}
    g = game_with_mythos(client, lobby, preparation, extra_effects=[prior])
    client.app.state.room_service.dice = DiceService(FixedRandom(20, 20, 2, 4, 70, 4))
    check = roll(client, g, encounter(client, g, "prior-insanity"), "san")
    check = roll(client, g, check, "int")
    roll(client, g, check, "duration")
    ok(manage(client, g, "symptom", symptom="短暂恐惧"))
    ok(manage(client, g, "recover", recovery_basis="safe_sleep"))
    ok(manage(client, g, "new_day"))
    c = current(client, g)
    assert c["sanity"]["mythos_gain"] == 5 and c["sanity"]["belief"] == "unbeliever"
    check = roll(client, g, encounter(client, g, "mythos-proof"), "san")
    c = current(client, g)
    assert c["sanity"]["belief_conversion"]["cost"] == 12
    assert c["sanity"]["day_loss"] == 14 and c["sanity"]["mythos_gain"] == 6
    assert c["san_max"] == 86
    ok(manage(client, g, "symptom", symptom="新的神话恐惧"))
    ok(manage(client, g, "recover", recovery_basis="chapter_end"))
    from test_action_adjudication import modern_response

    bind_fixture(client, g, modern_response)
    client.app.state.room_service.dice = DiceService(FixedRandom(1, 8))
    actor = normal_check(client, g, "cthulhu_mythos", 13)
    assert actor["runtime"]["belief"] == "believer"


def test_pow_order_and_direct_believer(client):
    for belief, expected in [("believer", 75), ("unbeliever", 84)]:
        c = mythos_card(client, belief, value=15)
        exp = deepcopy(c["experience"])
        if belief == "believer":
            exp["mythos"]["knowledge"] = "direct"
        c = patch(
            client,
            c,
            experience=exp,
            attributes={**c["attributes"], "pow": {"value": 90}, "str": {"value": 20}},
            approve_experience=["mythos"],
        )
        assert c["validation"]["valid"] and c["derived_values"]["san"] == expected


@pytest.mark.parametrize("bout", [False, True])
def test_believer_no_second_initial_cost_and_bout_protection(client, lobby, preparation, bout):
    from uuid import UUID

    from app.rooms.combat_service import load_state, store_state

    g = game_with_mythos(client, lobby, preparation,
                         belief="unbeliever" if bout else "believer")
    if bout:
        async def configure(session, room):
            state = load_state(room)
            c = state.characters[UUID(g["slots"][0])]
            c.sanity.kind, c.sanity.phase = "temporary", "bout"
            store_state(room, state)

        client.portal.call(client.app.state.agent_service.mutate, g["room"]["id"], configure)
    rng = FixedRandom(*([] if bout else [70]))
    client.app.state.room_service.dice = DiceService(rng)
    check = encounter(client, g, "mythos-proof")
    if bout:
        assert check["sanity"]["immunity"]["kind"] == "insanity_bout"
        assert not rng.used
    else:
        check = roll(client, g, check, "san")
    c = current(client, g)
    assert c["san"] == (50 if bout else 41)
    assert c["sanity"]["day_loss"] == (0 if bout else 2)
    assert c["sanity"]["belief_conversion"] is None
    assert c["sanity"]["belief"] == ("unbeliever" if bout else "believer")


def test_import_cross_source_roll_ids_cannot_be_laundered(client):
    c = mythos_card(client, method="suggested_roll")
    doc = document(client, c)
    doc["character"]["experience_rolls"]["mythos"]["id"] = c["roll_records"][0]["id"]
    assert client.post("/api/characters/import", json=doc).status_code == 422


def test_new_computed_fields_cannot_be_patched(client):
    c = mythos_card(client)
    for field, value in [
        ("initial_mythos_sources", []),
        ("initial_belief", "believer"),
        ("known_spells", []),
        ("experience_rolls", {}),
    ]:
        assert (
            client.patch(
                f"/api/characters/{c['id']}", json={"version": c["version"], field: value}
            ).status_code
            == 422
        )
