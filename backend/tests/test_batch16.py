"""Full-source preparation and runtime boundaries, using only original test fixtures."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_module_navigation import structure_data  # noqa: F401
from test_module_preparation import preparation, wait_preparation  # noqa: F401
from test_rooms import headers, lobby, ok  # noqa: F401

from app.agents.model import FakeModelAdapter
from app.preparation.runtime import freeze_adjustment
from app.rooms.combat_schemas import CombatTemplate
from app.rooms.combat_service import load_state, store_state
from app.rooms.service import RoomError
from app.rules.sanity import RULE_SOURCE


def test_segment_resume_cross_segment_identity_and_reviewed_fields(client):
    svc = client.app.state.agent_service
    folder = svc.settings.data_dir / "modules/segmented"
    folder.mkdir(parents=True)
    (folder / "source.md").write_text(
        "\n".join(f"# Chapter {i}\n" + "守卫甲在这里携带钥匙。" * 50 for i in range(8)),
        encoding="utf8",
    )
    svc.knowledge.indexer.index("modules")
    source = svc.knowledge.repository.sources()[0]
    prep = ok(
        client.post(
            "/api/module-preparations",
            json={
                "source_id": source.source_id,
                "source_hash": source.source_hash,
                "display_title": "Full source",
            },
        )
    )

    def response(messages, kwargs):
        c = json.loads(messages[-1]["content"])
        prior = c["existing_entities"]
        return {
            "entities": [
                {
                    "local_id": "guard",
                    "type": "npc",
                    "title": "守卫甲",
                    "public_summary": "一位守卫。",
                    "evidence_ids": [c["evidence"][0]["evidence_id"]],
                    "existing_entity_id": prior[0]["id"] if prior else None,
                }
            ]
        }

    svc.model.adapter = FakeModelAdapter(responder=response)
    ok(client.post(f"/api/module-preparations/{prep['id']}/generate"))
    ready = wait_preparation(client, prep["id"])
    assert ready["status"] == "review_ready" and ready["total_batches"] > 3
    assert ready["completed_batches"] == ready["total_batches"]
    assert ready["entity_count"] == 1
    entities = ok(client.get(f"/api/module-preparations/{prep['id']}/entities"))
    eid = entities[0]["id"]
    assert entities[0]["additional_evidence"]
    ok(client.patch(f"/api/module-entities/{eid}", json={"public_summary": "已校对的守卫介绍。"}))
    ok(client.post(f"/api/module-entities/{eid}/approve"))
    calls = ready["model_call_count"]
    ok(client.post(f"/api/module-preparations/{prep['id']}/generate"))
    resumed = wait_preparation(client, prep["id"])
    assert resumed["model_call_count"] == calls
    assert (
        ok(client.get(f"/api/module-preparations/{prep['id']}/entities"))[0]["public_summary"]
        == "已校对的守卫介绍。"
    )
    assert svc.model.adapter.max_active == 1


@pytest.fixture
def module_battle(client, structure_data, lobby):  # noqa: F811
    d, svc = structure_data, client.app.state.agent_service
    guard = d["entities"][2]["id"]
    ir = svc.structure.repository.get(d["snapshot"]["structure_version"])
    block = next(b.block_id for b in ir.blocks if b.node_id == d["nodes"]["Opening"])
    ok(client.post(f"/api/module-entities/{guard}/draft"))
    ok(
        client.patch(
            f"/api/module-entities/{guard}",
            json={
                "source_block_ids": [block],
                "reviewed_by": "Automated fixture (not user)",
                "combat_template": {
                    "source": "Original fixture",
                    "attributes": {"dex": 40, "con": 50},
                    "skills": {"brawl": 30, "dodge": 20},
                    "hp": 8,
                    "hp_max": 8,
                    "armor": 1,
                    "damage_bonus": "0",
                    "weapons": [
                        {
                            "id": "club",
                            "name": "Club",
                            "skill": "brawl",
                            "damage": "1d6",
                            "source": "Original fixture",
                        }
                    ],
                },
            },
        )
    )
    ok(client.post(f"/api/module-entities/{guard}/approve"))
    item = d["entities"][3]["id"]
    ok(client.post(f"/api/module-entities/{item}/draft"))
    ok(
        client.patch(
            f"/api/module-entities/{item}",
            json={
                "type": "item",
                "source_block_ids": [block],
                "reviewed_by": "Automated fixture",
                "sanity_effects": [
                    {
                        "id": "ending",
                        "encounter": "Fixture ending",
                        "trigger": "entity_revealed",
                        "success_loss": "0",
                        "failure_loss": "1",
                        "source": RULE_SOURCE,
                        "page": 130,
                        "basis": "Fixture only",
                    }
                ],
                "interactions": [
                    {
                        "id": "begin_bad",
                        "instruction": "Begin ending encounter",
                        "source_block_ids": [block],
                        "public_result": "Encounter pending",
                        "prepare_outcome": "B",
                        "set_flags": {"bad": True},
                    },
                    {
                        "id": "finish_bad",
                        "instruction": "Finish after everyone settles SAN",
                        "source_block_ids": [block],
                        "public_result": "Ending settled",
                        "outcome": "B",
                        "required_flags": {"bad": True},
                        "required_sanity": [{"entity_id": item, "effect_id": "ending"}],
                        "mythos_reward": 3,
                    },
                    {
                        "id": "take",
                        "instruction": "Pick up the fixture key",
                        "source_block_ids": [block],
                        "public_result": "Key taken",
                        "acquire_item_ids": [item],
                        "set_flags": {"key_taken": True},
                    },
                    {
                        "id": "finish",
                        "instruction": "Use key to finish",
                        "source_block_ids": [block],
                        "public_result": "Fixture completed",
                        "required_item_ids": [item],
                        "required_flags": {"key_taken": True},
                        "outcome": "A",
                        "san_rewards": [{"formula": "1d6+2"}],
                    },
                ],
            },
        )
    )
    item_state = next(
        e
        for e in ok(client.get(f"/api/module-preparations/{d['prep']['id']}/entities"))
        if e["id"] == item
    )
    assert not item_state["validation_errors"], item_state["validation_errors"]
    ok(client.post(f"/api/module-entities/{item}/approve"))
    ok(client.post(f"/api/module-preparations/{d['prep']['id']}/approve"))
    ok(client.post(d["prefix"] + "/approve", json={}))
    for slot, member in zip(lobby["slots"], [lobby["player"], lobby["agent"]]):
        ok(
            client.post(
                lobby["prefix"] + "/character-assignments",
                json={"slot_id": slot, "member_id": member},
            )
        )
    ok(
        client.patch(
            lobby["prefix"] + "/module-preparation", json={"preparation_id": d["prep"]["id"]}
        )
    )
    return {**d, **lobby, "guard": guard, "item": item}


def move(client, d, name):
    nav = ok(client.get(d["prefix"] + "/module-navigation"))
    return client.post(
        d["prefix"] + "/scene-transition",
        json={
            "target_scene_node_id": d["nodes"][name],
            "expected_revision": nav["navigation_revision"],
            "request_id": str(uuid4()),
        },
    )


def test_automatic_npc_mapping_visibility_location_and_revisit(client, module_battle):
    d, svc = module_battle, client.app.state.agent_service
    pid = f"module:{d['guard']}:1"
    room = ok(client.get(d["prefix"]))
    assert pid in room["combat"]["participants"]
    player = ok(client.get(d["prefix"], headers=headers(d["remote"]["member_token"])))
    assert pid not in player["combat"]["participants"]
    nav = ok(client.get(d["prefix"] + "/module-navigation"))
    assert d["guard"] not in nav["active_npc_entity_ids"]
    ok(
        client.post(
            d["prefix"] + f"/entities/{d['guard']}/correct",
            json={
                "public_summary": "You hear a guard nearby.",
                "reason": "Auditory disclosure only",
            },
        )
    )
    public = ok(
        client.get(d["prefix"] + "/public-entities", headers=headers(d["remote"]["member_token"]))
    )
    assert d["guard"] not in {e["id"] for e in public}
    ok(client.post(d["prefix"] + f"/entities/{d['guard']}/reveal"))
    public = ok(
        client.get(d["prefix"] + "/public-entities", headers=headers(d["remote"]["member_token"]))
    )
    assert (
        next(e for e in public if e["id"] == d["guard"])["public_summary"]
        == "You hear a guard nearby."
    )
    player = ok(client.get(d["prefix"], headers=headers(d["remote"]["member_token"])))
    assert pid in player["combat"]["participants"]
    assert "hp" not in player["combat"]["participants"][pid]
    assert d["guard"] in ok(client.get(d["prefix"] + "/module-navigation"))["active_npc_entity_ids"]

    async def injure():
        async with svc.rooms.transaction() as session:
            r = await svc.rooms.room(session, room["id"])
            data = load_state(r)
            data.combat.participants[pid].hp = 5
            data.combat.participants[d["player"]].hp = 10
            data.combat.participants[pid].weapons[0].quantity = 0
            store_state(r, data)

    client.portal.call(injure)
    ok(move(client, d, "Future"))
    room = ok(client.get(d["prefix"]))
    assert room["combat"]["participants"][d["player"]]["scene_node_id"] == d["nodes"]["Future"]
    assert room["combat"]["participants"][d["player"]]["hp"] == 10
    assert (
        pid
        not in ok(client.get(d["prefix"], headers=headers(d["remote"]["member_token"])))["combat"][
            "participants"
        ]
    )
    nav = ok(client.get(d["prefix"] + "/module-navigation"))
    assert d["guard"] not in nav["active_npc_entity_ids"]
    ok(move(client, d, "Opening"))
    p = ok(client.get(d["prefix"]))["combat"]["participants"][pid]
    assert p["hp"] == 5 and p["weapons"][0]["quantity"] == 0


@pytest.mark.parametrize(
    "pending,active,con", [(True, False, False), (False, True, False), (False, False, True)]
)
def test_navigation_cannot_bypass_combat_or_injury(client, module_battle, pending, active, con):
    d, svc = module_battle, client.app.state.agent_service

    async def stage():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            data = load_state(room)
            data.combat.pending_id = "pending-attack" if pending else None
            data.combat.active = active
            if con:
                data.combat.participants[d["player"]].injury.con_pending = "wound"
            store_state(room, data)

    client.portal.call(stage)

    # Direct reducer avoids asking the DTO to render a deliberately minimal pending fixture.
    async def attempt():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            nav = await svc.navigation.state(session, room.id)
            await svc.navigation.transition(
                session,
                room,
                {
                    "target_scene_node_id": d["nodes"]["Future"],
                    "expected_revision": nav.navigation_revision,
                    "request_id": str(uuid4()),
                },
                host=True,
            )

    with pytest.raises(RoomError):
        client.portal.call(attempt)


def test_treatment_after_navigation_keeps_location_and_resources(
    client, module_battle, monkeypatch
):
    from test_combat import FixedRandom

    from app.dice.service import DiceService

    d, svc = module_battle, client.app.state.agent_service
    monkeypatch.setattr(svc.runtime, "schedule", lambda *_: None)
    profile = ok(client.post("/api/agent-profiles", json={"role": "keeper", "name": "KP"}), 201)
    ok(
        client.post(
            d["prefix"] + "/agent-bindings",
            json={"member_id": d["room"]["host_member_id"], "profile_id": profile["id"]},
        )
    )
    profile = ok(
        client.post("/api/agent-profiles", json={"role": "investigator", "name": "Teammate"}), 201
    )
    ok(
        client.post(
            d["prefix"] + "/agent-bindings",
            json={"member_id": d["agent"], "profile_id": profile["id"]},
        )
    )
    ok(
        client.post(
            d["prefix"] + "/ready",
            json={"ready": True},
            headers=headers(d["remote"]["member_token"]),
        )
    )
    ok(client.post(d["prefix"] + "/ready", json={"ready": True, "member_id": d["agent"]}))
    ok(client.post(d["prefix"] + "/start"))
    ok(
        client.post(
            d["prefix"] + "/combat/damage",
            json={
                "target_id": d["player"],
                "amount": 2,
                "client_request_id": str(uuid4()),
                "reason": "Fixture minor injury",
            },
        )
    )
    ok(move(client, d, "Future"))
    client.app.state.room_service.dice = DiceService(FixedRandom(10, 2))
    result = ok(
        client.post(
            d["prefix"] + "/combat/action",
            json={
                "actor_id": d["agent"],
                "target_id": d["player"],
                "operation": "first_aid",
                "turn_key": 0,
                "client_request_id": str(uuid4()),
                "reason": "Treat after moving",
            },
        )
    )
    p = result["room"]["combat"]["participants"][d["player"]]
    assert p["hp"] == 11 and p["injury"]["first_aid_attempted"]
    assert p["scene_node_id"] == d["nodes"]["Future"]
    ok(client.post(d["prefix"] + "/agent-cycle/cancel"))
    ok(move(client, d, "Opening"))
    back = ok(client.get(d["prefix"]))["combat"]["participants"][d["player"]]
    assert back["hp"] == 11 and back["injury"]["first_aid_attempted"]


def test_incomplete_template_does_not_invent_stats():
    template = CombatTemplate(source="Original DEX/STR only", attributes={"dex": 40, "str": 100})
    assert {"hp", "hp_max", "con", "weapons", "armor", "damage_bonus"} <= set(template.missing())


def test_verified_module_skills_preserve_frozen_cards():
    from app.rules.checks import available_skills, check_value

    card = {"ruleset_id": "coc7-character-creation", "skill_values": {"stealth": 47}}
    assert check_value(card, "skill", "stealth") == 47
    assert check_value(card, "skill", "throw") == 20
    assert available_skills(card) == {"stealth": 47, "throw": 20}
    assert card["skill_values"] == {"stealth": 47}
    with pytest.raises(ValueError):
        check_value(card, "skill", "unknown")


def test_explicit_gated_detail_restores_required_roll_not_parent_object():
    from app.agents.adjudication_schemas import KeeperPlan
    from app.agents.generation_contracts import generation_contract, restore_output

    actor = str(uuid4())
    context = {
        "action_identifiers": dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id=actor,
            actor_character_slot_id="slot",
            current_scene_id="scene",
            expected_navigation_revision=1,
        ),
        "triggering_action": {"seq": 1, "payload": {"text": "我查阅报纸日期。"}},
        "current_targets": [
            {"id": "paper", "title": "报纸", "type": "item"},
            {"id": "date", "title": "报纸日期", "type": "clue"},
        ],
        "check_requirements": [
            {
                "entity_id": "date",
                "title": "报纸日期",
                "access_policy": "requires_check",
                "successful_check": {
                    "kind": "skill",
                    "name": "library_use",
                    "difficulty": "regular",
                },
            }
        ],
    }
    schema = generation_contract(KeeperPlan, context)
    result = restore_output(
        schema(
            parsed_intent={"type": "investigate"},
            focus={"action_clause_ids": ["u1"], "action_target_id": "paper"},
        ),
        KeeperPlan,
        context,
    )
    assert result.focus.action_target_id == "date" and result.focus.obstacle
    assert result.proposed_check.name == "library_use"
    assert str(result.proposed_check.target_member_id) == actor
    assert result.proposed_check.target_entity_id == "date"


def test_sourced_adjustment_uses_actual_flags_and_fixed_original_die(client, module_battle):
    svc, d = client.app.state.agent_service, module_battle

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            entity = {
                "id": d["guard"],
                "check_adjustments": [
                    {
                        "id": "half",
                        "kind": "attribute",
                        "name": "luck",
                        "source_block_ids": ["fixture"],
                        "denominator": 2,
                        "add": 10,
                        "subtract_die": "1d20",
                        "basis": "fixture",
                    }
                ],
            }
            first = SimpleNamespace(
                id="fixed-adjustment",
                kind="attribute",
                name="luck",
                value=65,
                opposed=None,
                module_adjustment=None,
            )
            second = SimpleNamespace(**vars(first))
            await freeze_adjustment(svc, session, room, first, entity)
            await session.flush()
            await freeze_adjustment(svc, session, room, second, entity)
            assert first.module_adjustment == second.module_adjustment
            assert first.value == 42 - first.module_adjustment["subtract_roll"]

    client.portal.call(verify)


@pytest.mark.parametrize("actor_type", ["human", "agent"])
def test_item_receipts_real_action_ending_and_snapshot_restore(
    client, module_battle, monkeypatch, actor_type
):
    from app.preparation.runtime import apply_interaction
    from app.preparation.runtime_schemas import HostModuleAction

    d, svc = module_battle, client.app.state.agent_service
    actor = d["player"] if actor_type == "human" else d["agent"]
    monkeypatch.setattr(svc.runtime, "schedule", lambda *_: None)
    ok(
        client.post(
            d["prefix"] + "/ready",
            json={"ready": True},
            headers=headers(d["remote"]["member_token"]),
        )
    )
    ok(client.post(d["prefix"] + "/ready", json={"ready": True, "member_id": d["agent"]}))
    ok(client.post(d["prefix"] + "/start"))

    async def receipt(operation, quote="Take the key and use it", seq=None, other_actor=None):
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            if seq is None:
                event = svc.rooms.append(
                    session,
                    room,
                    "action.submitted" if actor_type == "human" else "agent.action_proposed",
                    other_actor or actor,
                    {"text": "Take the key and use it"},
                )
                await session.flush()
                seq = event.seq
            args = HostModuleAction(
                entity_id=d["item"],
                interaction_id=operation,
                source_event_seq=seq,
                evidence_quote=quote,
                reason="fixture",
            )
            return await apply_interaction(svc, session, room, args, host=True)

    with pytest.raises(RoomError):
        client.portal.call(receipt, "take")  # Hidden item cannot become inventory.
    ok(client.post(d["prefix"] + f"/entities/{d['item']}/reveal"))
    with pytest.raises(RoomError):
        client.portal.call(receipt, "finish")  # Discovery alone does not satisfy item gate.
    with pytest.raises(RoomError):
        client.portal.call(receipt, "take", "Invented action")
    taken = client.portal.call(receipt, "take")
    with pytest.raises(RoomError, match="本次实际行动者持有"):
        client.portal.call(
            receipt,
            "finish",
            "Take the key and use it",
            None,
            d["agent"] if actor_type == "human" else d["player"],
        )
    again = client.portal.call(
        receipt, "take", "Take the key and use it", taken["source_event_seq"]
    )
    assert again == taken
    public = ok(client.get(d["prefix"], headers=headers(d["remote"]["member_token"])))
    assert public["session_state"]["module_runtime"] == {}
    found = next(
        e for e in ok(client.get(d["prefix"] + "/public-entities")) if e["id"] == d["item"]
    )
    assert found["held_by_member_id"] == actor
    ok(client.post(d["prefix"] + "/pause"))
    saved = ok(client.post(d["prefix"] + "/snapshots", json={"name": "Before ending"}))["snapshot"]
    ended = client.portal.call(receipt, "finish")
    assert ended["outcome"] == "A" and ended["rewards"]
    original = ended["rewards"]
    same = client.portal.call(
        receipt, "finish", "Take the key and use it", ended["source_event_seq"]
    )
    assert same["rewards"] == original
    ok(client.post(d["prefix"] + f"/snapshots/{saved['id']}/load"))
    restored = ok(client.get(d["prefix"]))["session_state"]["module_runtime"]
    assert restored["inventory"][d["item"]] == actor and restored["outcome"] is None


def test_luck_uses_room_resource_and_cannot_spend_or_push(client, module_battle):
    from app.rooms.sanity_service import current_check_value
    from app.rules.check_options import can_push, luck_options
    from app.rules.checks import judge

    d, svc = module_battle, client.app.state.agent_service

    async def verify():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            slot = next(
                s for s in await svc.rooms.slots(session, room) if s.member_id == d["player"]
            )
            assert (
                current_check_value(room, slot, "attribute", "luck")
                == room.session_state["characters"][slot.id]["luck"]
            )

    client.portal.call(verify)
    check = {
        "kind": "attribute",
        "name": "luck",
        "result": judge(65, "regular", 70),
        "value": 65,
        "difficulty": "regular",
    }
    assert luck_options(check, 65, True) == []
    assert not can_push(check)


def test_ending_waits_for_every_actual_sanity_settlement(client, module_battle, monkeypatch):
    d, svc = module_battle, client.app.state.agent_service
    monkeypatch.setattr(svc.runtime, "schedule", lambda *_: None)
    for member, role in [(d["room"]["host_member_id"], "keeper"), (d["agent"], "investigator")]:
        p = ok(client.post("/api/agent-profiles", json={"role": role, "name": role}), 201)
        ok(
            client.post(
                d["prefix"] + "/agent-bindings", json={"member_id": member, "profile_id": p["id"]}
            )
        )
    ok(
        client.post(
            d["prefix"] + "/ready",
            json={"ready": True},
            headers=headers(d["remote"]["member_token"]),
        )
    )
    ok(client.post(d["prefix"] + "/ready", json={"ready": True, "member_id": d["agent"]}))
    ok(client.post(d["prefix"] + "/start"))
    ok(client.post(d["prefix"] + f"/entities/{d['item']}/reveal"))

    async def action_event():
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            return svc.rooms.append(
                session, room, "action.submitted", d["player"], {"text": "Complete the encounter"}
            ).seq

    seq = client.portal.call(action_event)

    def confirm(rule):
        return client.post(
            d["prefix"] + "/module-action",
            json={
                "entity_id": d["item"],
                "interaction_id": rule,
                "source_event_seq": seq,
                "evidence_quote": "Complete the encounter",
                "reason": "Fixture",
            },
        )

    ok(confirm("begin_bad"))
    assert not ok(client.get(d["prefix"] + "/module-navigation"))["available_transition_ids"]
    assert move(client, d, "Future").status_code == 409
    assert confirm("finish_bad").status_code == 409
    source_seq = next(
        e["seq"]
        for e in ok(client.get(d["prefix"] + "/events"))["events"]
        if e["type"] == "entity.revealed" and e["payload"]["id"] == d["item"]
    )
    for member in (d["player"], d["agent"]):
        check = ok(
            client.post(
                d["prefix"] + "/sanity/encounters",
                json={
                    "target_member_id": member,
                    "entity_id": d["item"],
                    "effect_id": "ending",
                    "source_event_seq": source_seq,
                    "encounter_confirmed": True,
                    "reason": "Actually revealed fixture encounter",
                },
            )
        )["check"]
        for _ in range(2):
            if check["sanity"]["stage"] == "done":
                break
            if member == d["agent"]:

                async def auto_roll():
                    from app.persistence.agent_models import CheckRecord

                    async with svc.rooms.transaction() as session:
                        room = await svc.rooms.room(session, d["room"]["id"])
                        record = await session.get(CheckRecord, check["id"])
                        await svc.sanity.roll(
                            session, room, record, check["sanity"]["stage"], automatic=True
                        )
                        return record.document

                check = client.portal.call(auto_roll)
            else:
                check = ok(
                    client.post(
                        d["prefix"] + f"/sanity/checks/{check['id']}/roll",
                        json={"expected_stage": check["sanity"]["stage"]},
                        headers=headers(d["remote"]["member_token"]),
                    )
                )["check"]
        assert check["sanity"]["stage"] == "done"
        client.post(d["prefix"] + "/agent-cycle/cancel")
        if member == d["player"]:
            assert confirm("finish_bad").status_code == 409
    ok(confirm("finish_bad"))
    state = ok(client.get(d["prefix"]))["session_state"]
    assert state["module_runtime"]["outcome"] == "B"
    assert state["module_runtime"]["pending_outcome"] is None
    assert all(c["sanity"]["mythos_gain"] == 3 for c in state["characters"].values())
