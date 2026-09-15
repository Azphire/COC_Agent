"""Search intent, frozen consequences and recovery use the existing check pipeline."""

import json
import random
from copy import deepcopy

import pytest
from test_action_adjudication import modern_response, plan_for
from test_agent_runtime import accept_original, game, submit, wait_cycle  # noqa: F401
from test_batch24_packages import bundle, import_bundle  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
from app.agents.check_policy import CheckProposal
from app.agents.generation_contracts import generation_contract, restore_output
from app.agents.model import FakeModelAdapter
from app.dice.service import DiceService
from app.models.ollama import ModelFormatError
from app.preparation.search import validate_search_plan


def context_for():
    return {
        "action_identifiers": {
            "plan_id": "plan",
            "cycle_id": "cycle",
            "actor_member_id": "00000000-0000-0000-0000-000000000001",
            "actor_character_slot_id": "slot",
            "current_scene_id": "scene",
            "expected_navigation_revision": 0,
        },
        "triggering_action": {"seq": 1, "payload": {"text": "我翻找工作台背面和抽屉。"}},
        "current_targets": [{"id": "scene", "type": "scene", "title": "房间"}],
        "check_requirements": [
            {
                "entity_id": eid,
                "title": title,
                "access_policy": "requires_check",
                "successful_check": {
                    "kind": "skill",
                    "name": "spot_hidden",
                    "difficulty": "regular",
                },
            }
            for eid, title in [("selected", "布袋"), ("unrelated", "墙上的夹层")]
        ],
    }


@pytest.mark.parametrize(
    "effect,required", [("发现隐藏的布袋", True), ("平稳移动，避免弄伤手", False)]
)
def test_unbound_discovery_rejected_before_dice_but_generic_risk_allowed(effect, required):
    context = context_for()
    raw = context["triggering_action"]["payload"]["text"]
    plan = plan_for(raw, "investigate", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene", obstacle="存在风险")
    plan.proposed_check = CheckProposal(
        target_member_id=context["action_identifiers"]["actor_member_id"],
        name="spot_hidden",
        reason=raw,
        success_effect=effect,
        target_entity_id="scene",
    )
    if required:
        with pytest.raises(ModelFormatError):
            validate_search_plan(plan, context)
        assert plan.proposed_check.clue_id is None
    else:
        validate_search_plan(plan, context)


def test_hidden_choices_are_selectable_and_only_selected_discovery_gets_a_check():
    context = context_for()
    schema = generation_contract(KeeperPlan, context)
    focus = schema.model_fields["focus"].annotation
    assert "selected" in str(focus.model_fields["action_target_id"].annotation)
    raw = context["triggering_action"]["payload"]["text"]
    plan = plan_for(raw, "investigate", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="selected")
    restored = restore_output(plan, KeeperPlan, context)
    validate_search_plan(restored, context)
    assert restored.proposed_check.clue_id == "selected"
    assert restored.proposed_check.basis_entity_id == "selected"
    assert restored.proposed_reveal_entity_ids == []
    ambiguous = deepcopy(plan)
    ambiguous.focus.action_target_id = "scene"
    ambiguous.needs_clarification = True
    ambiguous.parsed_intent.requires_clarification = True
    validate_search_plan(ambiguous, context)
    assert ambiguous.proposed_check is None


@pytest.mark.parametrize("seed", [1, 5])
def test_legacy_plain_search_roll_reveal_and_snapshot_restore(client, game, seed):  # noqa: F811
    svc = client.app.state.agent_service

    async def workshop_fixture():
        # Only the starting scene and deterministic test RNG are fixtures.
        async with svc.rooms.transaction() as session:
            module = await svc.module(session, game["room"]["id"])
            module.state = {**module.state, "scene_id": "workshop"}

    client.portal.call(workshop_fixture)
    svc.rooms.dice = DiceService(random.Random(seed))

    def response(messages, kwargs):
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            c = json.loads(messages[-1]["content"])
            raw = c["triggering_action"]["payload"]["text"]
            result["parsed_intent"]["type"] = "investigate"
            result["focus"] = {"action": raw, "action_target_id": "pin"}
        return result

    svc.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "我翻找工作台背面和抽屉。"))
    waiting = wait_cycle(client, game)
    assert waiting["status"] == "waiting_for_roll", waiting
    check = ok(client.get(game["prefix"] + "/checks"))[0]
    assert check["clue_id"] == "pin" and check["policy_target_id"] == "pin"
    # Persist and reload the waiting check using the normal save route.
    save = ok(client.post(game["prefix"] + "/snapshots", json={"name": "waiting search"}))[
        "snapshot"
    ]
    ok(client.post(game["prefix"] + "/pause"))
    ok(client.post(game["prefix"] + f"/snapshots/{save['id']}/load"))
    ok(client.post(game["prefix"] + "/resume"))
    path = game["prefix"] + f"/checks/{check['id']}/roll"
    ok(client.post(path, json={}))
    accept_original(client, game, check["id"])
    assert wait_cycle(client, game)["status"] == "completed"
    final = ok(client.get(game["prefix"] + "/checks"))[0]
    ok(client.post(path, json={}))
    assert ok(client.get(game["prefix"] + "/checks"))[0] == final
    events = ok(client.get(game["prefix"] + "/events?limit=200"))["events"]
    reveals = [e for e in events if e["type"] == "clue.revealed"]
    assert len(reveals) == int(final["result"]["passed"])
    assert all(e["payload"]["clue_id"] == "pin" for e in reveals)
    assert sum(e["type"] == "check.dice_fixed" for e in events) == 1
    assert sum(e["type"] == "check.resolved" for e in events) == 1

    async def unchanged_other_clue():
        async with svc.rooms.database.sessions() as session:
            module = await svc.module(session, game["room"]["id"])
            assert "logbook" not in module.state["revealed_clues"]

    client.portal.call(unchanged_other_clue)
    assert final["result"]["passed"] is (seed == 1)


@pytest.mark.parametrize("seed", [1, 5])
def test_imported_search_board_inventory_memory_and_already_revealed(
    client,
    lobby,  # noqa: F811
    bundle,  # noqa: F811
    seed,
):
    from test_rooms import prepare

    fields = bundle["entities"][1]["fields"]
    fields.update(
        type="item",
        search_aliases=["工作台"],
        reveal_conditions={
            "access_policy": "requires_check",
            "successful_check": {"kind": "skill", "name": "spot_hidden", "difficulty": "regular"},
        },
    )
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
        profile = ok(client.post("/api/agent-profiles", json={"role": role, "name": role}), 201)
        ok(
            client.post(
                prefix + "/agent-bindings", json={"member_id": member, "profile_id": profile["id"]}
            )
        )
    ok(client.post(prefix + "/resume"))
    svc = client.app.state.agent_service
    svc.rooms.dice = DiceService(random.Random(seed))
    eid = imported["entity_ids"]["paper"]

    def response(messages, kwargs):
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            c = json.loads(messages[-1]["content"])
            result["parsed_intent"]["type"] = "investigate"
            result["focus"] = {
                "action": c["triggering_action"]["payload"]["text"],
                "action_target_id": eid,
            }
        return result

    svc.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, lobby, "我翻找工作台背面和抽屉。"))
    assert wait_cycle(client, lobby)["status"] == "waiting_for_roll"
    check = ok(client.get(prefix + "/checks"))[0]
    assert check["clue_id"] == eid
    ok(client.post(prefix + f"/checks/{check['id']}/roll", json={}))
    accept_original(client, lobby, check["id"])
    assert wait_cycle(client, lobby)["status"] == "completed"
    final = ok(client.get(prefix + "/checks"))[0]
    assert final["result"]["passed"] is (seed == 1)
    board = ok(client.get(prefix + "/public-entities"))
    assert any(e["id"] == eid for e in board) is (seed == 1)
    room = ok(client.get(prefix))
    assert not room["session_state"]["module_runtime"]["inventory"]
    if seed == 1:
        memories = ok(client.get(prefix + "/memories"))
        assert "Synthetic paper" in json.dumps(memories, ensure_ascii=False)
        ok(submit(client, lobby, "我再看看工作台中已经找到的纸。"))
        assert wait_cycle(client, lobby)["status"] == "completed"
        assert ok(client.get(prefix + "/checks")) == [final]
    events = ok(client.get(prefix + "/events?limit=200"))["events"]
    assert sum(
        e["type"] == "entity.revealed" and e["payload"].get("id") == eid for e in events
    ) == int(seed == 1)
