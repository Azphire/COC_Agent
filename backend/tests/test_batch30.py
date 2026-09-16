"""Occupation exceptions through normal creation/submission/check/SAN services.

Model responses and the runtime SAN encounter are deterministic test fixtures.
No external HTTP, original documents, approved packages or saved games are used.
"""

import json
import random
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_action_adjudication import modern_response
from test_agent_runtime import accept_original, submit, wait_cycle
from test_batch28 import patch
from test_coc7_creation import create_seventh
from test_module_preparation import preparation  # noqa: F401
from test_rooms import create_room, headers, join, lobby, ok  # noqa: F401

from app.agents.model import FakeModelAdapter
from app.dice.service import DiceService
from app.domain.character import CharacterDraft
from app.rules.engine import recalculate
from app.rules.loader import archived_ruleset, load_rulesets


def replacement(original="history", target="hypnosis", reason="经训练用于帮助离教者"):
    return dict(original_skill=original, replacement_skill=target, reason=reason)


def special_card(client, kind="deprogrammer", value=10, approve=False):
    changes = dict(occupation=kind, occupation_skills={"credit_rating": {"points": 20}})
    if kind == "deprogrammer":
        changes.update(
            era="modern",
            occupation_group_choices={"social": ["charm", "persuade"], "combat": ["brawl"]},
            occupation_skill_replacement=replacement(),
        )
        changes["occupation_skills"]["hypnosis"] = {"points": 54}
    else:
        changes.update(
            occupation_group_choices={
                "social": ["persuade"],
                "language": ["language_latin"],
                "personal": ["cthulhu_mythos"],
            },
            initial_mythos_proposal=dict(source="occultist", value=value, reason="KP确认研究所得"),
        )
    if approve:
        changes["approve_occupation_exceptions"] = [
            "skill_replacement" if kind == "deprogrammer" else "initial_mythos"
        ]
    return patch(client, create_seventh(client), **changes)


def finalize(client, card):
    return client.post(f"/api/characters/{card['id']}/finalize", json={"version": card["version"]})


def document(client, card):
    return ok(client.get(f"/api/characters/{card['id']}/export"))


@pytest.mark.parametrize("original", ["history", "charm", "brawl"])
def test_replacement_fixed_or_selected_consumes_one_slot(client, original):
    card = special_card(client)
    card = patch(client, card, occupation_skill_replacement=replacement(original))
    assert {i["code"] for i in card["validation"]["issues"]} == {"keeper_approval"}
    assert finalize(client, card).status_code == 422
    card = patch(client, card, approve_occupation_exceptions=["skill_replacement"])
    assert card["validation"]["valid"]
    assert len(card["effective_occupation_skills"]) == 9  # eight slots + credit
    assert original not in card["effective_occupation_skills"]
    assert card["skill_values"]["hypnosis"] == 55
    assert card["remaining_points"]["occupation"] == card["effective_attributes"]["edu"] * 4 - 74
    sheet = ok(finalize(client, card))
    assert sheet["skill_half_values"]["hypnosis"] == 27
    assert sheet["skill_fifth_values"]["hypnosis"] == 11


@pytest.mark.parametrize(
    "original,target",
    [
        ("credit_rating", "hypnosis"),
        ("spot_hidden", "hypnosis"),
        ("history", "cthulhu_mythos"),
        ("history", "psychology"),
        ("hypnosis", "hypnosis"),
    ],
)
def test_illegal_replacement_stays_invalid(client, original, target):
    card = special_card(client)
    card = patch(client, card, occupation_skill_replacement=replacement(original, target))
    assert any(i["code"] == "selection" for i in card["validation"]["issues"])
    assert finalize(client, card).status_code == 422


def test_replacement_revoke_points_and_approval_invalidation(client):
    card = special_card(client, approve=True)
    rolls = card["roll_records"]
    # A changed source removes its qualification; neither allocation silently changes pool.
    card = patch(
        client,
        card,
        occupation_skills={
            "credit_rating": {"points": 20},
            "history": {"points": 12},
            "hypnosis": {"points": 54},
        },
    )
    assert any(i["field"] == "occupation_skills.history" for i in card["validation"]["issues"])
    card = patch(client, card, occupation_skill_replacement=None)
    assert not card["occupation_exception_approvals"]
    assert card["occupation_skills"]["hypnosis"]["points"] == 54 and not card["interest_skills"]
    assert any(i["field"] == "occupation_skills.hypnosis" for i in card["validation"]["issues"])
    card = patch(client, card, occupation_skills={"credit_rating": {"points": 20}})
    assert card["validation"]["valid"] and card["initial_mythos"] == 0
    # The original interest purchase remains available without a replacement.
    card = patch(client, card, interest_skills={"hypnosis": {"points": 10}})
    assert card["validation"]["valid"] and card["skill_values"]["hypnosis"] == 11
    card = patch(
        client,
        card,
        occupation_skill_replacement=replacement(),
        approve_occupation_exceptions=["skill_replacement"],
    )
    card = patch(client, card, occupation_skill_replacement=replacement("occult"))
    assert not card["occupation_exception_approvals"]
    card = patch(client, card, approve_occupation_exceptions=["skill_replacement"])
    card = patch(client, card, occupation="occultist")
    assert not card["occupation_exception_approvals"]
    assert any(i["code"] == "unsupported" for i in card["validation"]["issues"])
    card = patch(client, card, occupation="deprogrammer")
    assert not card["occupation_exception_approvals"] and card["roll_records"] == rolls


def test_replacement_duplicates_era_and_pool_remain_enforced(client):
    card = special_card(client)
    for choices in [
        {"social": ["charm", "charm"], "combat": ["brawl"]},
        {"social": ["charm", "hypnosis"], "combat": ["brawl"]},
        {"social": ["charm", "persuade"], "combat": ["history"]},
    ]:
        invalid = patch(client, card, occupation_group_choices=choices)
        assert any(i["code"] in {"duplicate", "selection"} for i in invalid["validation"]["issues"])
        card = invalid
    card = patch(client, card, era="1920s")
    assert not card["occupation_exception_approvals"]
    assert any(i["code"] == "era" for i in card["validation"]["issues"])
    card = special_card(client)
    card = patch(
        client,
        card,
        occupation_skills={
            "credit_rating": {"points": 20},
            "hypnosis": {"points": 999},
        },
    )
    assert any(i["code"] == "overspent" for i in card["validation"]["issues"])


@pytest.mark.parametrize("value,pow_value,expected", [(7, 50, 50), (15, 90, 84), (10, 90, 89)])
def test_mythos_advisory_and_san_cap_not_experience_package(client, value, pow_value, expected):
    card = special_card(client, "occultist", value)
    attrs = deepcopy(card["attributes"])
    attrs["pow"]["value"] = pow_value
    attrs["str"]["value"] -= pow_value - 50
    card = patch(client, card, attributes=attrs)
    assert {i["code"] for i in card["validation"]["issues"]} == {"keeper_approval"}
    assert finalize(client, card).status_code == 422
    assert (card["derived_values"]["san"], card["derived_values"]["san_max"]) == (
        expected,
        99 - value,
    )
    assert card["skill_base_values"]["cthulhu_mythos"] == 0
    assert card["skill_values"]["cthulhu_mythos"] == value
    assert card["skill_half_values"]["cthulhu_mythos"] == value // 2
    assert card["skill_fifth_values"]["cthulhu_mythos"] == value // 5
    assert card["remaining_points"]["occupation"] == card["effective_attributes"]["edu"] * 4 - 20
    assert card["remaining_points"]["interest"] == 120
    assert len(card["effective_occupation_skills"]) == 9
    card = patch(client, card, approve_occupation_exceptions=["initial_mythos"])
    for _ in range(2):
        card = patch(client, card, name=card["name"])
        assert card["validation"]["valid"] and card["derived_values"]["san"] == expected
    frozen = ok(finalize(client, card))
    assert frozen["initial_mythos"] == value
    imported = ok(client.post("/api/characters/import", json=document(client, card)), 201)
    assert imported["initial_mythos"] == value and imported["derived_values"]["san"] == expected
    assert not imported["occupation_exception_approvals"]


@pytest.mark.parametrize("value", [-1, 0, 100, 1.5, True, "10"])
def test_mythos_strict_numbers(client, value):
    card = special_card(client, "occultist")
    proposal = {**card["initial_mythos_proposal"], "value": value}
    assert (
        client.patch(
            f"/api/characters/{card['id']}",
            json={
                "version": card["version"],
                "initial_mythos_proposal": proposal,
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize("field", ["occupation_skills", "interest_skills"])
def test_mythos_cannot_be_purchased_or_hidden_as_lore(client, field):
    card = special_card(client, "occultist", approve=True)
    card = patch(client, card, **{field: {**card[field], "cthulhu_mythos": {"points": 1}}})
    assert any(i["code"] == "forbidden" for i in card["validation"]["issues"])
    assert finalize(client, card).status_code == 422
    from test_batch26 import custom

    card = patch(client, card, custom_specializations=[custom("lore", "克苏鲁神话")])
    assert any(i["code"] == "forbidden_name" for i in card["validation"]["issues"])


def test_mythos_scope_slot_change_and_revoke(client):
    card = special_card(client, "occultist", approve=True)
    card = patch(
        client, card, initial_mythos_proposal={**card["initial_mythos_proposal"], "value": 11}
    )
    assert not card["occupation_exception_approvals"]
    card = patch(
        client,
        card,
        occupation_group_choices={**card["occupation_group_choices"], "personal": ["spot_hidden"]},
    )
    assert card["initial_mythos"] == 0
    assert any(i["code"] == "selection" for i in card["validation"]["issues"])
    card = patch(client, card, initial_mythos_proposal=None)
    assert card["validation"]["valid"] and card["skill_values"]["cthulhu_mythos"] == 0
    assert card["derived_values"]["san"] == 50 and "san_max" not in card["derived_values"]
    card = patch(
        client,
        card,
        initial_mythos_proposal=dict(value=10, reason="跨职业注入"),
        occupation="professor",
    )
    assert card["initial_mythos"] == 0
    assert any(i["code"] == "unsupported" for i in card["validation"]["issues"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("occupation_exception_approvals", {"initial_mythos": "approved"}),
        ("initial_mythos", 10),
        ("skill_values", {"cthulhu_mythos": 10}),
    ],
)
def test_patch_cannot_supply_computed_values_or_approval_flags(client, field, value):
    card = special_card(client, "occultist")
    assert (
        client.patch(
            f"/api/characters/{card['id']}",
            json={
                "version": card["version"],
                field: value,
            },
        ).status_code
        == 422
    )


def test_version_semantics_and_legacy_are_immutable(client):
    rules = load_rulesets()["coc7-character-creation"]
    old = archived_ruleset(rules.id, "1.2.0")
    assert (rules.version, len(rules.skills), len(rules.occupations)) == ("1.5.0", 106, 115)
    assert all(not o.skill_replacement and not o.initial_mythos for o in old.occupations)
    card = special_card(client, "occultist", approve=True)
    c = CharacterDraft.model_validate(card)
    changed = rules.model_copy(deep=True)
    next(o for o in changed.occupations if o.key == "occultist").initial_mythos.note += " 更新"
    recalculate(c, changed)
    assert not c.occupation_exception_approvals
    c = CharacterDraft.model_validate(card)
    changed.version = "1.3.1"
    recalculate(c, changed)
    assert not c.occupation_exception_approvals
    c = CharacterDraft.model_validate(card)
    c.ruleset_version = "1.2.0"
    recalculate(c, old)
    assert c.initial_mythos == 0 and c.skill_values["cthulhu_mythos"] == 0
    assert not c.occupation_exception_approvals
    assert any(i.code == "unsupported" for i in c.validation.issues)
    # Simulate an ordinary pre-feature exported card; preserve every old numeric field/roll.
    card = patch(
        client,
        card,
        initial_mythos_proposal=None,
        occupation_group_choices={**card["occupation_group_choices"], "personal": ["spot_hidden"]},
    )
    doc = document(client, card)
    doc["ruleset"]["version"] = doc["character"]["ruleset_version"] = "1.2.0"
    for key in [
        "occupation_skill_replacement",
        "initial_mythos_proposal",
        "initial_mythos",
        "occupation_exception_approvals",
        "effective_occupation_skills",
    ]:
        doc["character"].pop(key)
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    for field in ["derived_values", "skill_values", "remaining_points"]:
        assert imported[field] == card[field]
    assert [r["dice"] for r in imported["roll_records"]] == [
        r["dice"] for r in card["roll_records"]
    ]
    frozen = ok(finalize(client, imported))
    room = create_room(client)
    prefix = f"/api/rooms/{room['room']['id']}"
    published = ok(client.post(prefix + "/character-slots", json={"character_id": frozen["id"]}))[
        "room"
    ]
    assert (
        published["character_slots"][0]["character_snapshot"]["derived_values"]
        == card["derived_values"]
    )


@pytest.mark.parametrize("kind", ["deprogrammer", "occultist"])
def test_old_version_import_rejects_new_proposals_and_foreign_consent(client, kind):
    card = special_card(client, kind, approve=True)
    doc = document(client, card)
    doc["ruleset"]["version"] = doc["character"]["ruleset_version"] = "1.2.0"
    imported = ok(client.post("/api/characters/import", json=doc), 201)
    assert not imported["occupation_exception_approvals"] and imported["initial_mythos"] == 0
    assert any(i["code"] == "unsupported" for i in imported["validation"]["issues"])
    assert finalize(client, imported).status_code == 422
    created = create_room(client)
    player = join(client, created["invite_code"])
    path = f"/api/rooms/{created['room']['id']}/character-submissions/preview"
    assert (
        client.post(
            path, headers=headers(player["member_token"]), json={"document": doc}
        ).status_code
        == 422
    )


def test_san_attribute_edit_invalidates_consent_and_templates_stay_shared(client):
    rules = client.app.state.character_rulesets["coc7-character-creation"]
    original = rules.model_dump()
    card = special_card(client, "occultist", value=15, approve=True)
    attrs = deepcopy(card["attributes"])
    attrs["pow"]["value"] = 90
    attrs["str"]["value"] = 20
    changed = patch(client, card, attributes=attrs)
    assert not changed["occupation_exception_approvals"]
    assert changed["derived_values"]["san"] == 84
    first = special_card(client, approve=True)
    second = special_card(client)
    assert first["occupation_exception_approvals"] and not second["occupation_exception_approvals"]
    assert rules.model_dump() == original


def submit_accept(client, card, created=None, player=None):
    created = created or create_room(client)
    player = player or join(client, created["invite_code"])
    prefix = f"/api/rooms/{created['room']['id']}"
    path = prefix + "/character-submissions"
    auth = headers(player["member_token"])
    doc = document(client, card)
    doc["character"]["initial_mythos"] = 99
    doc["character"]["skill_values"]["cthulhu_mythos"] = 99
    doc["character"]["derived_values"]["san"] = 1
    preview = ok(client.post(path + "/preview", headers=auth, json={"document": doc}))
    assert preview["initial_mythos"] == card["initial_mythos"]
    assert preview["derived_values"]["san"] == card["derived_values"]["san"]
    assert not preview["occupation_exception_approvals"]
    bad = deepcopy(doc)
    bad["character"]["interest_skills"]["cthulhu_mythos"] = {"points": 1}
    assert client.post(path + "/preview", headers=auth, json={"document": bad}).status_code == 422
    submitted = ok(
        client.post(
            path,
            headers=auth,
            json={
                "document": doc,
                "expected_version": 0,
                "client_request_id": str(uuid4()),
            },
        )
    )["room"]
    row = submitted["character_submissions"][0]
    review = dict(expected_version=1, decision="accept", client_request_id=str(uuid4()))
    url = path + f"/{row['id']}/review"
    assert client.post(url, json=review).status_code == 422
    assert not ok(client.get(prefix))["character_submissions"][0]["slot_id"]
    review["approve_occupation_exceptions"] = list(card["occupation_exception_approvals"])
    assert client.post(url, json=review, headers=auth).status_code == 403
    accepted = ok(client.post(url, json=review))["room"]
    slot = next(
        s for s in accepted["character_slots"] if s["member_id"] == player["room"]["self_member_id"]
    )
    snapshot = slot["character_snapshot"]
    assert snapshot["status"] == "finalized" and snapshot["skill_values"] == card["skill_values"]
    repeated = ok(client.post(url, json=review))["room"]
    assert repeated["character_slots"] == accepted["character_slots"]
    assert repeated["session_state"] == accepted["session_state"]
    return dict(
        prefix=prefix,
        remote=player,
        player=player["room"]["self_member_id"],
        slots=[slot["id"]],
        room=accepted,
        snapshot=snapshot,
    )


def normal_check(client, game, key, expected):
    contexts = []

    def respond(messages, kwargs):
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            ctx = json.loads(messages[-1]["content"])
            actor = next(c for c in ctx["characters"] if c["member_id"] == game["player"])
            assert actor["skill_values"][key] == expected
            contexts.append(actor)
            result["proposed_check"]["name"] = key
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=respond)
    ok(submit(client, game, f"我冒着误判而错失机会的风险辨认现场线索，请进行 {key} 检定。"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "waiting_for_roll", cycle
    from app.rules.display import resolve_check_name

    check = next(
        c
        for c in ok(client.get(game["prefix"] + "/checks"))
        if c.get("display_name") == resolve_check_name(key)["display_name"]
        and c["status"] == "pending"
    )
    assert check["value"] == expected and contexts
    result = ok(client.post(game["prefix"] + f"/checks/{check['id']}/roll", json={}))
    accept_original(client, game, check["id"])
    assert wait_cycle(client, game)["status"] == "completed"
    again = ok(client.post(game["prefix"] + f"/checks/{check['id']}/roll", json={}))
    assert result["check"]["dice"] == again["check"]["dice"]
    return contexts[-1]


@pytest.mark.parametrize(
    "kind,key,expected", [("deprogrammer", "hypnosis", 55), ("occultist", "cthulhu_mythos", 10)]
)
def test_export_submit_accept_freeze_actual_check_and_restore(client, kind, key, expected):
    card = special_card(client, kind, approve=True)
    ok(finalize(client, card))
    game = submit_accept(client, card)
    p = game["prefix"]
    ok(client.post(p + "/module", json={"module_id": "stopped-clock"}))
    profile = ok(
        client.post("/api/agent-profiles", json={"role": "keeper", "name": "确定性KP"}), 201
    )
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
    room = ok(client.post(p + "/start"))["room"]
    state = room["session_state"]["characters"][game["slots"][0]]
    assert (state["san"], state["san_max"], state["sanity"]["mythos_gain"]) == (
        50,
        99 - card["initial_mythos"],
        0,
    )
    actor = normal_check(client, game, key, expected)
    assert actor["runtime"]["san"] == 50
    from test_batch10 import restore, save

    saved = save(client, game)
    for _ in range(2):
        restore(client, game, saved)
        current = ok(client.get(p))
        assert current["character_slots"][0]["character_snapshot"] == game["snapshot"]
        assert (
            current["session_state"]["characters"][game["slots"][0]]["sanity"]["mythos_gain"] == 0
        )


def test_initial_mythos_plus_runtime_gain_once(client, lobby, preparation, request):  # noqa: F811
    from test_batch10 import FixedRandom, current, manage, restore, roll, san_game, save
    from test_batch10 import request as encounter

    card = special_card(client, "occultist", value=7, approve=True)
    accepted = submit_accept(client, card, lobby["created"], lobby["remote"])
    slot = accepted["slots"][0]
    # san_game starts the normal preparation/assignment path; release the accepted seat first.
    ok(client.delete(lobby["prefix"] + f"/character-assignments/{slot}"))
    lobby["slots"][0] = slot
    game = san_game.__wrapped__(client, lobby, preparation, request)
    initial = current(client, game)
    assert (initial["san"], initial["san_max"], initial["sanity"]["mythos_gain"]) == (50, 92, 0)
    client.app.state.room_service.dice = DiceService(FixedRandom(20, 30, 1, 2))
    check = encounter(client, game, "five")
    for stage in ("san", "int", "duration"):
        roll(client, game, check, stage)
    ok(manage(client, game, "symptom", symptom="偏执"))
    state = current(client, game)
    assert (state["san"], state["san_max"], state["sanity"]["mythos_gain"]) == (45, 87, 5)
    assert (
        ok(client.get(game["prefix"]))["character_slots"][-1]["character_snapshot"][
            "initial_mythos"
        ]
        == 7
    )
    checkpoint = save(client, game)
    for _ in range(2):
        restore(client, game, checkpoint)
        restored = current(client, game)
        for field in ("san", "san_max", "sanity"):
            assert restored[field] == state[field]
    ok(manage(client, game, "recover", recovery_basis="safe_sleep"))
    client.app.state.room_service.dice = DiceService(random.Random(30))
    actor = normal_check(client, game, "cthulhu_mythos", 12)
    assert actor["runtime"]["mythos_gain"] == 5 and actor["runtime"]["san_max"] == 87

    # The inspect tool must expose the same current total and fractions as actual checks.
    async def inspect():
        from app.agents.tools import AgentTools

        svc = client.app.state.agent_service
        async with svc.rooms.database.sessions() as session:
            from app.persistence.room_models import GameRoom

            room = await session.get(GameRoom, game["room"]["id"])
            cycle = await svc.cycle(session, room.id)
            return await AgentTools(svc).dispatch(
                session,
                room,
                SimpleNamespace(cycle_id=cycle.id),
                None,
                None,
                "inspect_character",
                SimpleNamespace(member_id=game["player"]),
            )

    details = client.portal.call(inspect)
    assert (
        details["skill_values"]["cthulhu_mythos"],
        details["skill_half_values"]["cthulhu_mythos"],
        details["skill_fifth_values"]["cthulhu_mythos"],
    ) == (12, 6, 2)
