"""Equipment, submission, inventory and combat acceptance; no external model calls."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_batch10 import FixedRandom
from test_coc7_creation import create_seventh
from test_rooms import create_room, headers, join, ok

from app.dice.service import DiceService
from app.domain.character_details import EquipmentEntry
from app.preparation.character_equipment import initialize_equipment
from app.preparation.runtime import apply_interaction
from app.preparation.runtime_schemas import HostModuleAction
from app.rooms.combat_schemas import Combatant, Weapon, unarmed
from app.rooms.combat_service import CombatService, weapon_for
from app.rooms.schemas import CharacterRuntimeV1, SessionStateV1
from app.rooms.service import RoomError
from app.rules.checks import judge
from app.rules.combat import damage_plan
from app.rules.equipment import CATALOG, WEAPONS, equipment_weapon, validate_equipment


def entry(key="rifle_22", **extra):
    return (
        dict(id="carried", catalog_id=key, name=WEAPONS[key]["name"], quantity=1, notes="") | extra
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_ammo", -1),
        ("initial_ammo", 1.5),
        ("initial_ammo", True),
        ("initial_reserve", "4"),
        ("initial_reserve", -1),
        ("initial_reserve", 1001),
    ],
)
def test_ammunition_requires_bounded_integers(field, value):
    with pytest.raises(ValidationError):
        EquipmentEntry(**entry(**{field: value}))


@pytest.mark.parametrize("field,value", [("damage", "100"), ("capacity", 999), ("skill", "brawl")])
def test_card_cannot_supply_weapon_rules(field, value):
    with pytest.raises(ValidationError):
        EquipmentEntry(**entry(**{field: value}))


@pytest.mark.parametrize(
    "gear,skills,code",
    [
        (entry(initial_ammo=7), {"firearms_rifle_shotgun": 25}, "ammo_capacity"),
        (entry("sword_medium", initial_reserve=1), {"fighting_sword": 20}, "ammo_kind"),
        (dict(id="note", name="纸条", initial_ammo=1), {}, "ammo_kind"),
        (entry(), {}, "weapon_skill"),
    ],
)
def test_equipment_semantic_validation(gear, skills, code):
    issues = []
    validate_equipment(
        SimpleNamespace(equipment=[EquipmentEntry(**gear)], era="1920s", skill_values=skills),
        lambda *args: issues.append(args),
    )
    assert code in {i[1] for i in issues}


def test_catalog_sources_legacy_defaults_and_spear_db():
    assert len(CATALOG) == 33 and len(WEAPONS) == 14
    assert all(c["source"] and c["description"] and c["category"] and c["eras"] for c in CATALOG)
    assert EquipmentEntry(**entry("revolver")).initial_ammo == 0
    assert equipment_weapon("revolver", "old").ammo == 0
    old = Weapon(id="old", name="旧剑", skill="brawl", damage="1d6", source="旧快照")
    assert old.template_id is None and old.base_range is None
    normal = judge(60, "regular", 40)
    assert damage_plan(old, "1d4", normal) == (0, ["1d6", "1d4"])
    spear = equipment_weapon("spear", "new")
    assert damage_plan(spear, "1d4", normal) == (0, ["1d8+1"])
    assert damage_plan(spear, "1d4", judge(60, "regular", 5)) == (9, ["1d8+1"])


def test_old_frozen_skill_set_can_carry_but_cannot_use_received_rifle():
    rifle = equipment_weapon("rifle_22", "actual-instance", ammo=2, reserve=7, ready=True)
    actor = Combatant(
        id="old-member",
        slot_id=str(uuid4()),
        label="旧卡",
        scene_id="square",
        attributes={"dex": 50, "con": 50},
        skills={"brawl": 25},
        hp=12,
        hp_max=12,
        weapons=[unarmed(), rifle],
        source="1.0.0冻结快照",
    )
    state = SessionStateV1.model_validate_json(
        SessionStateV1(
            combat={"participants": {actor.id: actor}, "order": [actor.id]}
        ).model_dump_json()
    )
    actor = state.combat.participants[actor.id]
    CombatService(SimpleNamespace(rooms=None)).order(state.combat)
    assert state.combat.firearm_priority == []
    assert weapon_for(actor, "unarmed").skill == "brawl"
    with pytest.raises(RoomError, match="缺少武器对应技能"):
        weapon_for(actor, rifle.id)
    assert actor.weapons[1].ammo == 2 and "firearms_rifle_shotgun" not in actor.skills


@pytest.mark.parametrize("text", ["我用步枪开一枪。", "我用剑刺向目标。", "我挥斧砍向目标。"])
def test_weapon_phrasing_routes_to_existing_combat_classifier(text):
    assert CombatService.route(SimpleNamespace(session_state={}), text)


@pytest.mark.asyncio
async def test_each_copy_initializes_once_and_module_loss_wins(monkeypatch):
    sid = uuid4()
    original = SessionStateV1(characters={sid: CharacterRuntimeV1()})
    room = SimpleNamespace(id="room", session_state=original.model_dump(mode="json"))
    slot = SimpleNamespace(
        id=str(sid),
        member_id="player",
        source_character_id="card",
        character_snapshot={"equipment": [entry(quantity=2, initial_ammo=3, initial_reserve=8)]},
    )
    rows = []

    async def slots(*args):
        return [slot]

    async def entities(*args):
        return []

    agents = SimpleNamespace(
        entities=SimpleNamespace(rows=entities),
        rooms=SimpleNamespace(slots=slots, append=lambda *args: None),
    )
    await initialize_equipment(agents, SimpleNamespace(add=rows.append), room)
    state = SessionStateV1.model_validate(room.session_state)
    guns = [w for w in state.characters[sid].weapons if w.kind == "firearm"]
    assert len(guns) == 2 and guns[0].id != guns[1].id
    assert all((w.ammo, w.reserve, w.capacity) == (3, 8, 6) for w in guns)
    guns[0].ammo = 1
    monkeypatch.setitem(WEAPONS["rifle_22"], "damage", "100")
    room.session_state = state.model_dump(mode="json")
    await initialize_equipment(agents, SimpleNamespace(add=rows.append), room)
    assert len(rows) == 1
    assert all(
        w["damage"] == "1d6+1"
        for w in room.session_state["module_runtime"]["equipment_weapons"].values()
    )
    assert (
        next(
            w
            for w in room.session_state["characters"][str(sid)]["weapons"]
            if w["id"] == guns[0].id
        )["ammo"]
        == 1
    )

    async def loss_rules(*args):
        return [SimpleNamespace(snapshot={"interactions": [{"inventory_operation": "initial"}]})]

    agents.entities.rows = loss_rules
    room.session_state = original.model_dump(mode="json")
    await initialize_equipment(agents, SimpleNamespace(add=rows.append), room)
    await initialize_equipment(agents, SimpleNamespace(add=rows.append), room)
    assert not room.session_state["module_runtime"]["inventory"]
    assert not room.session_state["module_runtime"]["equipment_weapons"]
    assert room.session_state["characters"][str(sid)]["equipment_settlement"] == "module_pending"


def card_with_gear(client, key):
    card = create_seventh(client)
    skill = WEAPONS[key]["skill"]
    card = ok(
        client.patch(
            f"/api/characters/{card['id']}",
            json={
                "version": card["version"],
                "occupation": "firefighter",
                "occupation_attribute": "str",
                "occupation_skills": {"credit_rating": {"points": 9}},
                "selected_specializations": [skill],
                "interest_skills": {skill: {"points": 40}},
                "equipment": [
                    entry(
                        key,
                        initial_ammo=3 if WEAPONS[key]["kind"] == "firearm" else 0,
                        initial_reserve=8 if WEAPONS[key]["kind"] == "firearm" else 0,
                    )
                ],
            },
        )
    )
    assert card["validation"]["valid"], card["validation"]
    return ok(
        client.post(f"/api/characters/{card['id']}/finalize", json={"version": card["version"]})
    )


def equipped_room(client, monkeypatch, key):
    monkeypatch.setattr(client.app.state.agent_service.runtime, "schedule", lambda *_: None)
    card = card_with_gear(client, key)
    document = ok(client.get(f"/api/characters/{card['id']}/export"))
    created = create_room(client)
    remote = join(client, created["invite_code"])
    peer = join(client, created["invite_code"], "交接队友")
    p = f"/api/rooms/{created['room']['id']}"
    auth = headers(remote["member_token"])
    for injection in [
        {"capacity": 100},
        {"initial_ammo": 100},
        {"catalog_id": "notebook", "name": "笔记本", "initial_reserve": 1},
    ]:
        forged = deepcopy(document)
        forged["character"]["equipment"][0].update(injection)
        assert (
            client.post(
                p + "/character-submissions/preview", headers=auth, json={"document": forged}
            ).status_code
            == 422
        )
    preview = ok(
        client.post(p + "/character-submissions/preview", headers=auth, json={"document": document})
    )
    assert preview["equipment"] == card["equipment"]
    ok(
        client.post(
            p + "/character-submissions",
            headers=auth,
            json={
                "document": document,
                "expected_version": 0,
                "client_request_id": str(uuid4()),
            },
        )
    )
    submission = ok(client.get(p + "/character-submissions"))[0]
    accepted = ok(
        client.post(
            p + f"/character-submissions/{submission['id']}/review",
            json={
                "expected_version": submission["version"],
                "decision": "accept",
                "client_request_id": str(uuid4()),
            },
        )
    )["room"]
    slot = accepted["character_slots"][0]
    assert slot["character_snapshot"]["equipment"] == card["equipment"]
    # A second real frozen card for possession transfer, with no extra equipment.
    peer_doc = deepcopy(document)
    peer_doc["character"]["equipment"] = []
    peer_card = ok(client.post("/api/characters/import", json=peer_doc), 201)
    peer_card = ok(
        client.post(
            f"/api/characters/{peer_card['id']}/finalize", json={"version": peer_card["version"]}
        )
    )
    added = ok(client.post(p + "/character-slots", json={"character_id": peer_card["id"]}))["room"]
    peer_slot = next(s for s in added["character_slots"] if not s["member_id"])
    ok(
        client.post(
            p + "/character-assignments",
            json={"slot_id": peer_slot["id"], "member_id": peer["room"]["self_member_id"]},
        )
    )
    ok(client.post(p + "/module", json={"module_id": "stopped-clock"}))
    profile = ok(client.post("/api/agent-profiles", json={"role": "keeper", "name": "离线KP"}), 201)
    ok(
        client.post(
            p + "/agent-bindings",
            json={"member_id": created["room"]["host_member_id"], "profile_id": profile["id"]},
        )
    )
    for player in [remote, peer]:
        ok(client.post(p + "/ready", json={"ready": True}, headers=headers(player["member_token"])))
    room = ok(client.post(p + "/start"))["room"]
    weapon = next(
        w
        for w in room["session_state"]["characters"][slot["id"]]["weapons"]
        if w.get("template_id") == key
    )
    return dict(
        prefix=p,
        room=room,
        slot=slot["id"],
        peer_slot=peer_slot["id"],
        member=remote["room"]["self_member_id"],
        peer=peer["room"]["self_member_id"],
        auth=auth,
        weapon=weapon,
        card=card,
    )


@pytest.mark.parametrize("key", ["sword_medium", "rifle_22"])
def test_submission_start_combat_reload_transfer_and_restore(client, monkeypatch, key):
    g = equipped_room(client, monkeypatch, key)
    p, iid = g["prefix"], g["weapon"]["id"]
    assert iid != key and g["weapon"]["skill"] == WEAPONS[key]["skill"]
    room = ok(client.get(p))
    # Clear missing-skill error; no fallback to brawl/handgun and no partial setup.
    body = dict(
        expected_revision=room["revision"],
        reason="原创隔离训练对象",
        npc=dict(
            id="target",
            label="训练目标",
            team="training",
            scene_id="square",
            attributes={"dex": 1, "con": 50},
            skills={"brawl": 0, "dodge": 0},
            hp=100,
            hp_max=100,
            source="本测试原创",
        ),
        weapon_loadout=[dict(template_id=key)],
    )
    assert client.post(p + "/combat/setup", json=body).status_code == 422
    body["npc"]["skills"][WEAPONS[key]["skill"]] = 20
    target = ok(client.post(p + "/combat/setup", json=body))["room"]["combat"]["participants"][
        "target"
    ]
    target_weapon = next(w for w in target["weapons"] if w["id"] != "unarmed")
    assert target_weapon["id"] not in {iid, key}
    assert target_weapon["damage"] == g["weapon"]["damage"]

    # Scene positioning is fixture-only, to keep the noncombat recipient out of initiative.
    async def position():
        agents = client.app.state.agent_service

        async def mutate(session, room):
            state = SessionStateV1.model_validate(room.session_state)
            await agents.combat.ensure_members(session, room, state, "square")
            state.combat.participants[g["peer"]].scene_id = "elsewhere"
            room.session_state = state.model_dump(mode="json")

        await agents.mutate(g["room"]["id"], mutate)

    client.portal.call(position)
    firearm = WEAPONS[key]["kind"] == "firearm"
    client.app.state.room_service.dice = DiceService(
        FixedRandom(10, 2 if firearm else 4, *([] if firearm else [10, 8]), 4)
    )
    command = dict(
        actor_id=g["member"],
        target_id="target",
        operation="attack",
        weapon_id=iid,
        reason="实际使用车卡武器",
        client_request_id=str(uuid4()),
        turn_key=0,
        range_band="long" if firearm else "base",
    )
    ok(client.post(p + "/combat/action", json=command, headers=g["auth"]))
    pending = ok(client.get(p))["combat"]["pending"]
    roll = dict(action_id=pending["id"], stage=pending["stage"], operation="roll")
    ok(client.post(p + "/combat/step", json=roll, headers=g["auth"]))
    room = ok(client.get(p))
    action = room["session_state"]["combat"]["actions"][pending["id"]]
    assert action["bases"]["attack"]["difficulty"] == ("hard" if firearm else "regular")
    assert action["bases"]["attack"]["name"] == WEAPONS[key]["skill"]
    assert action["bases"]["attack"]["value"] == g["card"]["skill_values"][WEAPONS[key]["skill"]]
    assert room["combat"]["participants"]["target"]["hp"] == 95
    for path, payload in [("/combat/action", command), ("/combat/step", roll)]:
        ok(client.post(p + path, json=payload, headers=g["auth"]))
    assert ok(client.get(p))["combat"]["participants"]["target"]["hp"] == 95
    ok(client.post(p + "/agent-cycle/cancel"))
    room = ok(client.get(p))
    ok(
        client.post(
            p + "/combat/control",
            json=dict(operation="end", expected_revision=room["revision"], reason="结束单次训练"),
        )
    )

    def weapon_in(room, sid):
        return next(
            w for w in room["session_state"]["characters"][sid]["weapons"] if w["id"] == iid
        )

    if firearm:
        assert weapon_in(ok(client.get(p)), g["slot"])["ammo"] == 2
        reload = dict(
            actor_id=g["member"],
            operation="reload",
            weapon_id=iid,
            reason="移用备弹",
            client_request_id=str(uuid4()),
            turn_key=ok(client.get(p))["combat"]["turn_key"],
        )
        ok(client.post(p + "/combat/action", json=reload, headers=g["auth"]))
        ok(client.post(p + "/combat/action", json=reload, headers=g["auth"]))
        loaded = weapon_in(ok(client.get(p)), g["slot"])
        assert (loaded["ammo"], loaded["reserve"]) == (4, 6)
        ok(client.post(p + "/agent-cycle/cancel"))

    eid = next(i["item_id"] for i in ok(client.get(p))["inventory"] if i["instance_id"] == iid)

    async def transfer(op, actor, recipient=None):
        agents = client.app.state.agent_service
        text = {"give": "我把武器交给交接队友。", "drop": "我放下武器。", "pickup": "我捡起武器。"}[
            op
        ]

        async def mutate(session, room):
            event = agents.rooms.append(session, room, "action.submitted", actor, {"text": text})
            await session.flush()
            await apply_interaction(
                agents,
                session,
                room,
                HostModuleAction(
                    entity_id=eid,
                    interaction_id=op,
                    item_instance_id=iid,
                    recipient_member_id=recipient,
                    evidence_quote=text,
                    source_event_seq=event.seq,
                    reason="定向库存验收",
                ),
                host=True,
            )

        await agents.mutate(g["room"]["id"], mutate)

    before = deepcopy(weapon_in(ok(client.get(p)), g["slot"]))
    client.portal.call(transfer, "give", g["member"], g["peer"])
    assert weapon_in(ok(client.get(p)), g["peer_slot"]) == before
    client.portal.call(transfer, "drop", g["peer"])
    saved = ok(client.post(p + "/snapshots", json={"name": "武器已消耗弹药后放在地上"}))["snapshot"]
    client.portal.call(transfer, "pickup", g["member"])
    assert weapon_in(ok(client.get(p)), g["slot"]) == before
    ok(client.post(p + "/pause"))
    ok(client.post(p + f"/snapshots/{saved['id']}/load"))
    ok(client.post(p + "/resume"))
    client.portal.call(transfer, "pickup", g["member"])
    assert weapon_in(ok(client.get(p)), g["slot"]) == before
    assert client.post(p + "/start").status_code == 409

    async def reinitialize():
        agents = client.app.state.agent_service

        async def mutate(session, room):
            await initialize_equipment(agents, session, room)

        await agents.mutate(g["room"]["id"], mutate)

    client.portal.call(reinitialize)
    assert weapon_in(ok(client.get(p)), g["slot"]) == before
    assert before["id"] == iid
