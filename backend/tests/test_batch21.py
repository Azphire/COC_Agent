"""Batch 21 fixtures: rules, card lifecycle and instance invariants, not live play."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_action_adjudication import plan_for
from test_coc7_creation import create_seventh
from test_rooms import ok

from app.agents.adjudication_schemas import TurnFocus
from app.domain.character import CharacterDraft
from app.preparation.action_authority import freeze_action
from app.preparation.adjudication import decision_contract, routine_inventory_decision
from app.preparation.inventory import acquire_instance, acquisition_error, apply_inventory
from app.preparation.runtime_schemas import ModuleActionArgs, ModuleInteraction, ModuleRuntimeState
from app.preparation.search import repair_local_interaction_target
from app.rooms.schemas import CharacterRuntimeV1, SessionStateV1
from app.rooms.service import RoomError
from app.rules.character_options import credit_finances
from app.rules.checks import check_value, prompt_skills
from app.rules.display import resolve_check_name
from app.rules.engine import recalculate
from app.rules.loader import archived_ruleset, load_rulesets


def rule(**kwargs):
    return ModuleInteraction(
        id="take",
        instruction="拿起已发现物品",
        public_result="已拿起物品",
        source_block_ids=["source"],
        kp_enabled=True,
        **kwargs,
    )


@pytest.mark.parametrize(
    "name,words", [("报纸", "拿起报纸"), ("木匣", "我把木匣收进背包"), ("信封", "捡起信封")]
)
def test_failed_detail_does_not_block_current_physical_take(name, words):
    text = words + "。"
    plan = plan_for(text, "investigate", "hidden-detail")
    plan.focus = TurnFocus(action="调查日期", action_target_id="hidden-detail", obstacle="调查失败")
    plan.proposed_reveal_entity_ids = ["hidden-detail"]
    entity = dict(
        id="item",
        type="item",
        title=name,
        interactions=[rule(acquire_item_ids=["item"]).model_dump()],
    )
    facts = SimpleNamespace(
        raw_text=text,
        actor_member_id="actor",
        approved_entities={"item": entity},
        local_entity_ids={"item"},
        revealed_entity_ids={"item"},
        reveal_errors={},
        scene_id="scene",
    )
    repair_local_interaction_target(plan, facts, {"actor": "甲", "peer": "乙"})
    assert plan.focus.action == text and plan.focus.action_target_id == "item"
    assert (
        not plan.focus.obstacle and not plan.proposed_check and not plan.proposed_reveal_entity_ids
    )
    assert plan.parsed_intent.type == "interact"
    runtime = ModuleRuntimeState()
    authority = freeze_action(
        plan, text, "actor", 1, "scene", {"item": entity}, runtime, {"actor": "甲", "peer": "乙"}
    )
    candidates = [
        dict(
            entity_id="item",
            title=name,
            option="1",
            rule=rule(acquire_item_ids=["item"]).model_dump(),
        )
    ]
    context = dict(
        actor="actor",
        action_authority=authority,
        members={"actor": "甲", "peer": "乙"},
        items=[],
        npc_instances=[],
        clauses=[dict(id="u1", text=text)],
    )
    decision = routine_inventory_decision(
        context, candidates, decision_contract(context, candidates)
    )
    assert decision.applicable
    assert (
        routine_inventory_decision(
            context,
            [
                {
                    **candidates[0],
                    "rule": rule(acquire_item_ids=["item"], host_review=True).model_dump(),
                }
            ],
            decision_contract(context, candidates),
        )
        is None
    )


@pytest.mark.parametrize(
    "text",
    [
        "如果可以就拿起报纸。",
        "不要拿起报纸。",
        "之前谁拿起报纸？",
        "乙，请拿起报纸。",
        "我对乙说：请把报纸交给我。",
    ],
)
def test_questions_negation_and_teammate_requests_do_not_become_transfers(text):
    plan = plan_for(text, "wait")
    plan.focus = TurnFocus()
    facts = SimpleNamespace(
        raw_text=text,
        actor_member_id="actor",
        approved_entities={"item": dict(type="item", title="报纸")},
        local_entity_ids={"item"},
        revealed_entity_ids={"item"},
    )
    repair_local_interaction_target(plan, facts, {"actor": "甲", "peer": "乙"})
    assert plan.parsed_intent.type == "wait" and not plan.focus.action


@pytest.mark.asyncio
async def test_same_instance_acquire_transfer_drop_restore_pickup_and_consumed_guard():
    state = SessionStateV1()
    rt = state.module_runtime
    acquire_instance(rt, "item", "a")
    acquire_instance(rt, "item", "a")
    assert rt.inventory == {"item": "a"} and rt.item_instances == {"item": "item"}

    async def entity(*args):
        return SimpleNamespace(entity_type="item", state="revealed")

    async def slots(*args):
        return [SimpleNamespace(member_id=m) for m in ["a", "b"]]

    async def members(*args):
        return [SimpleNamespace(id=m, active=True) for m in ["a", "b"]]

    agents = SimpleNamespace(
        entities=SimpleNamespace(entity=entity), rooms=SimpleNamespace(slots=slots, members=members)
    )
    room = SimpleNamespace(id="room")

    async def transfer(op, actor, node="scene", recipient=None):
        await apply_inventory(
            agents,
            None,
            room,
            state,
            rule(inventory_operation=op, item_id="item"),
            ModuleActionArgs(
                entity_id="item",
                interaction_id=op,
                evidence_quote="actual",
                recipient_member_id=recipient,
            ),
            actor,
            node,
            1,
            [],
        )

    await transfer("give", "a", recipient="b")
    with pytest.raises(RoomError):
        acquire_instance(rt, "item", "a")
    await transfer("drop", "b", node="other")
    with pytest.raises(RoomError):
        await transfer("pickup", "a")
    state = SessionStateV1.model_validate_json(state.model_dump_json())
    await transfer("pickup", "b", node="other")
    assert state.module_runtime.inventory == {"item": "b"}
    await transfer("consume", "b")
    assert acquisition_error(state.module_runtime, "item", "a")
    with pytest.raises(RoomError):
        acquire_instance(state.module_runtime, "item", "a")


@pytest.mark.parametrize(
    "credit,cash,assets,spend",
    [
        (0, 0.5, 0, 0.5),
        (1, 1, 10, 2),
        (9, 9, 90, 2),
        (10, 20, 500, 10),
        (49, 98, 2450, 10),
        (50, 250, 25000, 50),
        (89, 445, 44500, 50),
        (90, 1800, 180000, 250),
        (98, 1960, 196000, 250),
        (99, 50000, 5000000, 5000),
    ],
)
def test_credit_bracket_boundaries_both_eras(credit, cash, assets, spend):
    for era, factor in [("1920s", 1), ("modern", 20)]:
        f = credit_finances(credit, era)
        assert (f.cash, f.assets, f.spending) == (cash * factor, assets * factor, spend * factor)
        assert f.assets_lower_bound == (credit == 99)


@pytest.mark.parametrize(
    "occupation,attribute,expected",
    [
        ("professor", None, 240),
        ("athlete", "str", 240),
        ("athlete", "dex", 240),
        ("artist", "pow", 220),
        ("artist", "dex", 240),
        ("dilettante", None, 220),
        ("drifter", "app", 220),
        ("drifter", "str", 240),
        ("pilot", None, 240),
        ("musician", "pow", 220),
        ("zealot", "app", 220),
        ("accountant", None, 240),
    ],
)
def test_occupation_uses_actual_formula_after_age_adjustment(
    client, occupation, attribute, expected
):
    card = CharacterDraft.model_validate(create_seventh(client))
    for r in card.roll_records:
        if r.purpose == "education_check":
            r.dice = [1]
            r.total = 1
    card.occupation = occupation
    card.occupation_attribute = attribute
    recalculate(card, load_rulesets()[card.ruleset_id])
    assert card.remaining_points.occupation == expected
    if occupation == "athlete":
        card.occupation_attribute = "pow"
        recalculate(card, load_rulesets()[card.ruleset_id])
        assert any(i.field == "occupation_attribute" for i in card.validation.issues)


@pytest.mark.parametrize(
    "mode,occupation,choices,attribute",
    [
        ("random", "doctor", {"academic": ["history", "psychoanalysis"]}, None),
        (
            "point-buy",
            "musician",
            {
                "instrument": ["art_violin"],
                "social": ["charm"],
                "personal": ["listen", "history", "photography", "spot_hidden"],
            },
            "pow",
        ),
    ],
)
def test_new_card_roundtrip_specializations_details_and_switch(
    client, mode, occupation, choices, attribute
):
    card = create_seventh(client, mode)
    # Fixed listen is deliberately duplicated first to exercise group validation.
    if occupation == "musician":
        choices["personal"][0] = "language_french"
    endpoint = "/api/characters/" + card["id"]
    card = ok(
        client.patch(
            endpoint,
            json=dict(
                version=card["version"],
                occupation=occupation,
                occupation_attribute=attribute,
                occupation_group_choices=choices,
                era="modern",
                selected_specializations=["language_japanese", "science_physics"],
                occupation_skills={"credit_rating": {"points": 30}},
                interest_skills={
                    "language_japanese": {"points": 20},
                    "science_physics": {"points": 10},
                },
                background={
                    "appearance": "旧外套",
                    "beliefs": "求实",
                    "people": "姐姐",
                    "places": "故乡",
                    "possessions": "怀表",
                    "traits": "谨慎",
                    "key_connection": "people",
                },
                asset_details=[{"description": "存款", "value": 1000}],
                equipment=[
                    {
                        "id": "kit",
                        "catalog_id": "knife",
                        "name": "小刀",
                        "quantity": 1,
                        "notes": "原有装备",
                    },
                    {"id": "other", "name": "家书", "quantity": 2, "notes": "没有数值效果"},
                ],
            ),
        )
    )
    assert card["validation"]["valid"], card["validation"]
    assert (
        card["skill_values"]["language_japanese"] == 21
        and card["skill_values"]["science_physics"] == 11
    )
    assert check_value(card, "skill", "science_physics") == 11
    assert resolve_check_name("science_physics")["display_name"] == "科学（物理学）"
    assert "science_physics" in prompt_skills(card)
    assert "fighting_chainsaw" not in prompt_skills(card)
    assert ok(client.get(endpoint)) == card
    finalized = ok(client.post(endpoint + "/finalize", json={"version": card["version"]}))
    exported = ok(client.get(endpoint + "/export"))
    imported = ok(client.post("/api/characters/import", json=exported), 201)
    for field in [
        "attributes",
        "effective_attributes",
        "skill_values",
        "background",
        "equipment",
        "asset_details",
        "finances",
    ]:
        assert imported[field] == finalized[field]
    assert [r["dice"] for r in imported["roll_records"]] == [
        r["dice"] for r in finalized["roll_records"]
    ]
    changed = ok(
        client.patch(
            "/api/characters/" + imported["id"], json={"version": 1, "occupation": "firefighter"}
        )
    )
    assert not changed["validation"]["valid"]
    assert any(i["field"] == "occupation_group_choices" for i in changed["validation"]["issues"])


def test_archived_card_edit_export_import_preserves_rolls_and_rule_values(client):
    legacy = archived_ruleset("coc7-character-creation", "1.0.0")
    card = CharacterDraft.model_validate(create_seventh(client))
    card.id = uuid4()
    for record in card.roll_records:
        record.id = uuid4()
    card.ruleset_version = "1.0.0"
    card.occupation = "professor"
    card.selected_occupation_skills = ["history", "biology", "chemistry", "occult"]
    from app.domain.character import SkillAllocation

    card.occupation_skills = {"credit_rating": SkillAllocation(points=20)}
    recalculate(card, legacy)

    async def save():
        from app.character.repository import CharacterRepository

        await CharacterRepository(client.app.state.database).save(card, [])

    client.portal.call(save)
    before = deepcopy(card.model_dump(mode="json"))
    endpoint = "/api/characters/" + str(card.id)
    updated = ok(client.patch(endpoint, json={"version": 1, "name": "旧草稿继续编辑"}))
    for field in [
        "roll_records",
        "attributes",
        "effective_attributes",
        "skill_values",
        "derived_values",
    ]:
        assert updated[field] == before[field]
    final = ok(client.post(endpoint + "/finalize", json={"version": 2}))
    assert final["ruleset_version"] == "1.0.0"
    exported = ok(client.get(endpoint + "/export"))
    imported = ok(client.post("/api/characters/import", json=exported), 201)
    assert imported["skill_values"] == before["skill_values"]
    assert (
        ok(client.get("/api/character-rulesets/coc7-character-creation?version=1.0.0"))["version"]
        == "1.0.0"
    )


def test_specialization_groups_and_catalog_sources():
    rules = archived_ruleset("coc7-character-creation", "1.1.0")
    assert len(rules.occupations) == 31 and len(rules.skills) == 103
    old = archived_ruleset(rules.id, "1.0.0")
    current = {s.key: s for s in rules.skills}
    assert all(current[s.key].base_value == s.base_value for s in old.skills)
    assert not current["cthulhu_mythos"].allocatable
    assert all(o.point_formula and "PDF" in o.source for o in rules.occupations)


def test_room_original_equipment_actual_instance_drop_pickup_and_save(client, monkeypatch):
    from test_rooms import create_room, headers, join

    from app.preparation.runtime import apply_interaction
    from app.preparation.runtime_schemas import HostModuleAction

    card = create_seventh(client)
    path = "/api/characters/" + card["id"]
    card = ok(
        client.patch(
            path,
            json={
                "version": 1,
                "occupation": "firefighter",
                "occupation_attribute": "str",
                "occupation_skills": {"credit_rating": {"points": 9}},
                "selected_specializations": ["science_physics"],
                "interest_skills": {"science_physics": {"points": 40}},
                "equipment": [
                    {
                        "id": "knife",
                        "catalog_id": "knife",
                        "name": "小刀",
                        "quantity": 1,
                        "notes": "私人备注",
                    }
                ],
            },
        )
    )
    card = ok(client.post(path + "/finalize", json={"version": card["version"]}))
    created = create_room(client)
    p = "/api/rooms/" + created["room"]["id"]
    remote = join(client, created["invite_code"])
    member = remote["room"]["self_member_id"]
    room = ok(client.post(p + "/character-slots", json={"character_id": card["id"]}))["room"]
    slot = room["character_slots"][0]["id"]
    ok(client.post(p + "/character-assignments", json={"slot_id": slot, "member_id": member}))
    ok(client.post(p + "/module", json={"module_id": "stopped-clock"}))
    ok(client.post(p + "/ready", json={"ready": True}, headers=headers(remote["member_token"])))
    room = ok(client.post(p + "/start"))["room"]
    assert room["character_slots"][0]["character_snapshot"]["equipment"] == card["equipment"]
    item = room["inventory"][0]
    iid, eid = item["instance_id"], item["item_id"]
    assert room["session_state"]["characters"][slot]["equipment_settlement"] == "retained"
    assert any(w["id"] == iid for w in room["session_state"]["characters"][slot]["weapons"])
    assert "私人备注" not in client.get(p + "/public-entities").text

    # Host fixture invokes the same interaction reducer from recorded player
    # events; real KP actions are checked separately in the local model run.
    async def transfer(op, text):
        agents = client.app.state.agent_service

        async def execute(session, room):
            event = agents.rooms.append(session, room, "action.submitted", member, {"text": text})
            await session.flush()
            return await apply_interaction(
                agents,
                session,
                room,
                HostModuleAction(
                    entity_id=eid,
                    interaction_id=op,
                    evidence_quote=text,
                    source_event_seq=event.seq,
                    reason="隔离装备回归",
                ),
                host=True,
            )

        return await agents.mutate(created["room"]["id"], execute)

    client.portal.call(transfer, "drop", "我放下小刀。")
    dropped = ok(client.get(p))
    assert not dropped["inventory"]
    assert all(w["id"] != iid for w in dropped["session_state"]["characters"][slot]["weapons"])
    saved = ok(client.post(p + "/snapshots", json={"name": "装备在地上"}))["snapshot"]
    client.portal.call(transfer, "pickup", "我捡起小刀。")
    picked = ok(client.get(p))
    assert picked["inventory"][0]["instance_id"] == iid
    ok(client.post(p + "/pause"))
    ok(client.post(p + "/snapshots/" + saved["id"] + "/load"))
    assert not ok(client.get(p))["inventory"]
    ok(client.post(p + "/resume"))
    client.portal.call(transfer, "pickup", "我拿起小刀。")
    restored = ok(client.get(p))
    assert restored["inventory"][0]["instance_id"] == iid
    assert restored["character_slots"][0]["character_snapshot"] == card


@pytest.mark.parametrize(
    "kind", ["unselected_specialty", "duplicate_groups", "wrong_era", "assets", "key_connection"]
)
def test_invalid_extended_card_choices_are_blocked(client, kind):
    card = create_seventh(client)
    body = {
        "version": 1,
        "occupation": "musician",
        "occupation_attribute": "pow",
        "occupation_group_choices": {
            "instrument": ["art_violin"],
            "social": ["charm"],
            "personal": ["history", "photography", "spot_hidden", "language_french"],
        },
        "occupation_skills": {"credit_rating": {"points": 9}},
    }
    if kind == "unselected_specialty":
        body["interest_skills"] = {"science_physics": {"points": 5}}
    elif kind == "duplicate_groups":
        body["occupation_group_choices"]["personal"][0] = "art_violin"
    elif kind == "wrong_era":
        body["interest_skills"] = {"computer_use": {"points": 5}}
    elif kind == "assets":
        body["asset_details"] = [{"description": "超额房产", "value": 1000000}]
    else:
        body["background"] = {"key_connection": "people"}
    card = ok(client.patch("/api/characters/" + card["id"], json=body))
    assert not card["validation"]["valid"], kind
    assert (
        client.post(
            "/api/characters/" + card["id"] + "/finalize", json={"version": card["version"]}
        ).status_code
        == 422
    )


def test_unavailable_item_feedback_keeps_old_hidden_check_closed():
    from test_action_adjudication import facts_for

    from app.agents.action_policy import ActionPolicyValidator
    from app.preparation.inventory import guard_unavailable_transfer

    plan = plan_for("我拿起报纸。", "interact", "paper")
    plan.focus = TurnFocus(action="我拿起报纸。", action_target_id="paper")
    plan.action_authority = {"target_id": "paper", "kinds": ["take"]}
    facts = SimpleNamespace(
        actor_member_id="a",
        scene_id="room",
        revealed_entity_ids={"paper"},
        approved_entities={"paper": {"type": "item", "title": "报纸"}},
    )
    runtime = ModuleRuntimeState(inventory={"paper": "b"})
    assert guard_unavailable_transfer(plan, facts, runtime, {"b": "队友"})
    assert "队友" in plan.parsed_intent.clarification_question
    assert not plan.proposed_check and not plan.proposed_reveal_entity_ids
    policy_facts = facts_for("我拿起报纸。")
    decision = ActionPolicyValidator().validate(plan.parsed_intent, plan, policy_facts, [])
    assert "队友" in decision.clarification_question


@pytest.mark.asyncio
async def test_equipment_initialization_loss_and_weapon_transfer_preserve_ammo():
    from app.preparation.character_equipment import initialize_equipment, sync_equipment_weapons

    sid, other = uuid4(), uuid4()
    initial = SessionStateV1(characters={sid: CharacterRuntimeV1(), other: CharacterRuntimeV1()})
    room = SimpleNamespace(id="room", session_state=initial.model_dump(mode="json"))
    snapshots = [
        SimpleNamespace(
            id=str(sid),
            member_id="a",
            source_character_id="card",
            character_snapshot={
                "equipment": [
                    dict(id="knife", catalog_id="knife", name="小刀", quantity=1, notes="private"),
                    dict(id="gun", catalog_id="revolver", name=".38 左轮", quantity=1, notes=""),
                    dict(id="note", catalog_id=None, name="纸条", quantity=2, notes="ordinary"),
                ]
            },
        ),
        SimpleNamespace(
            id=str(other), member_id="b", source_character_id="peer", character_snapshot={}
        ),
    ]
    rows = []

    async def entities(*a):
        return []

    async def slots(*a):
        return snapshots

    agents = SimpleNamespace(
        entities=SimpleNamespace(rows=entities),
        rooms=SimpleNamespace(slots=slots, append=lambda *a: None),
    )
    await initialize_equipment(agents, SimpleNamespace(add=rows.append), room)
    state = SessionStateV1.model_validate(room.session_state)
    assert len(state.module_runtime.inventory) == 4 and len(rows) == 3
    before = deepcopy(room.session_state)
    await initialize_equipment(agents, SimpleNamespace(add=rows.append), room)
    assert room.session_state == before
    gun = next(w for w in state.characters[sid].weapons if w.kind == "firearm")
    gun.ammo = 2
    gun.reserve = 4
    state.module_runtime.inventory[gun.id] = "b"
    sync_equipment_weapons(state)
    assert gun.id not in [w.id for w in state.characters[sid].weapons]
    received = next(w for w in state.characters[other].weapons if w.id == gun.id)
    assert (received.ammo, received.reserve) == (2, 4)
    state.module_runtime.inventory.pop(gun.id)
    state.module_runtime.dropped_items[gun.id] = "floor"
    sync_equipment_weapons(state)
    state = SessionStateV1.model_validate_json(state.model_dump_json())
    state.module_runtime.inventory[gun.id] = "a"
    state.module_runtime.dropped_items.pop(gun.id)
    sync_equipment_weapons(state)
    assert next(w for w in state.characters[sid].weapons if w.id == gun.id).ammo == 2

    async def loss_rules(*a):
        return [SimpleNamespace(snapshot={"interactions": [{"inventory_operation": "initial"}]})]

    agents.entities.rows = loss_rules
    room.session_state = initial.model_dump(mode="json")
    await initialize_equipment(agents, SimpleNamespace(add=rows.append), room)
    state = SessionStateV1.model_validate(room.session_state)
    assert not state.module_runtime.inventory and not state.characters[sid].weapons
    assert state.characters[sid].equipment_settlement == "module_pending"


def test_carried_paper_reading_uses_only_public_body_after_scene_change():
    from app.agents.adjudication_schemas import KeeperNarration, TurnFocus
    from app.agents.generation_contracts import restore_output
    from app.agents.narration import response_brief

    raw = "我读手里的报纸正文。"
    plan = plan_for(raw, "observe", "paper")
    plan.focus = TurnFocus(action=raw, action_target_id="paper")
    paper = dict(
        id="paper",
        type="item",
        title="报纸",
        public_summary="报纸正文：列车报道。",
        fact_scope="current_scene",
    )
    context = dict(triggering_action={"seq": 22, "payload": {"text": raw}}, public_entities=[paper])
    brief, _ = response_brief(plan, context, {"events": []})
    value = restore_output(
        KeeperNarration(public_narration="开始朗读隐藏日期。"),
        KeeperNarration,
        {"response_brief": brief},
    )
    assert value.public_narration == paper["public_summary"]
    paper["fact_scope"] = "historical"
    assert response_brief(plan, context, {"events": []})[0]["source_quotes"] == []
    context["public_entities"] = []
    assert response_brief(plan, context, {"events": []})[0]["source_quotes"] == []


@pytest.mark.asyncio
async def test_explicit_pickup_selects_one_of_multiple_dropped_instances():
    raw = "我拾回报纸，实例 paper-copy-2。"
    plan = plan_for(raw, "interact", "paper")
    plan.focus = TurnFocus(action=raw, action_target_id="paper")
    state = SessionStateV1()
    rt = state.module_runtime
    rt.item_instances = {"paper-copy-1": "paper", "paper-copy-2": "paper"}
    rt.dropped_items = {"paper-copy-1": "scene", "paper-copy-2": "scene"}
    authority = freeze_action(
        plan, raw, "actor", 1, "scene", {"paper": dict(type="item", title="报纸")}, rt, {}
    )
    assert authority["item_instances"] == {"paper": "paper-copy-2"}
    picked = rule(inventory_operation="pickup", item_id="paper")
    context = dict(
        actor="actor",
        items=[],
        members={},
        npc_instances=[],
        local_dropped_instances=list(rt.dropped_items),
        action_authority=authority,
        clauses=[dict(id="u1", text=raw)],
    )
    candidate = dict(option="pick", rule=picked.model_dump())
    schema = decision_contract(context, [candidate])
    decision = routine_inventory_decision(context, [candidate], schema)
    assert decision.item_instance_id == "paper-copy-2"

    async def entity(*args):
        return SimpleNamespace(entity_type="item", state="revealed")

    await apply_inventory(
        SimpleNamespace(entities=SimpleNamespace(entity=entity)),
        None,
        SimpleNamespace(id="room"),
        state,
        picked,
        ModuleActionArgs(
            entity_id="paper",
            interaction_id="pickup",
            evidence_quote=raw,
            item_instance_id=decision.item_instance_id,
        ),
        "actor",
        "scene",
        1,
        [],
    )
    assert rt.inventory == {"paper-copy-2": "actor"}
    assert rt.dropped_items == {"paper-copy-1": "scene"}
