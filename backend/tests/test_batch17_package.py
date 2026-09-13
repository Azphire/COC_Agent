"""Actual batch17 package rules in explicit state fixtures, never a model playthrough."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_batch16 import module_battle  # noqa: F401
from test_batch17 import interactions  # noqa: F401
from test_module_navigation import structure_data  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import lobby  # noqa: F401

from app.persistence.agent_models import AgentCycle, CheckRecord
from app.rooms.combat_service import load_state, store_state
from app.rooms.service import RoomError


@pytest.fixture
def package_rules(client, interactions):  # noqa: F811
    path = (
        Path(__file__).resolve().parents[2]
        / "data/prepared/changan/batch-17/package-supplement-test.json"
    )
    if not path.exists():
        pytest.skip("Generate the independently loadable batch17 package first")
    entities = {
        e["key"]: e["fields"] for e in json.loads(path.read_text(encoding="utf8"))["entities"]
    }
    d, action = interactions
    svc = client.app.state.agent_service
    ids = {key: d["item"] for key in ("keys", "ending_a", "station_staff", "ending_b", "ending_c")}
    ids["staff"] = d["guard"]

    def remap(value):
        if isinstance(value, dict):
            return {k: remap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        return ids.get(value, value) if isinstance(value, str) else value

    async def configure(flags=None, holder=None, dead=False):
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, d["room"]["id"])
            entity = await svc.entities.entity(session, room.id, d["item"])
            entity.snapshot = {
                **entity.snapshot,
                "interactions": remap(
                    [
                        r
                        for key in ("controls", "ending_b", "ending_c")
                        for r in entities[key]["interactions"]
                    ]
                ),
                "sanity_effects": remap(entities["ending_b"]["sanity_effects"]),
            }
            state = load_state(room)
            state.module_runtime.flags.update(flags or {})
            if holder:
                state.module_runtime.inventory[d["item"]] = holder
            if dead:
                for c in state.characters.values():
                    c.hp, c.injury.dead = 0, True
            store_state(room, state)

    async def state():
        async with svc.rooms.transaction() as session:
            return load_state(await svc.rooms.room(session, d["room"]["id"]))

    return d, action, configure, state, entities


def test_actual_package_a_needs_unlocked_panel_actual_key_holder_and_replays_rewards(
    client, package_rules
):
    d, action, configure, state, _ = package_rules
    client.portal.call(configure)
    with pytest.raises(RoomError, match="所用物品实例"):
        client.portal.call(action, "accelerate", d["player"], "我下推油门加速。")
    client.portal.call(lambda: configure(holder=d["player"]))
    with pytest.raises(RoomError, match="条件未满足"):
        client.portal.call(action, "accelerate", d["player"], "我下推油门加速。")
    client.portal.call(lambda: configure(flags={"panel_open": True}))
    receipt = client.portal.call(action, "accelerate", d["player"], "我下推右侧油门加速。")
    before = client.portal.call(state).model_dump(mode="json")
    assert before["module_runtime"]["outcome"] == "A"
    assert len(receipt["rewards"]) == 4  # Two real investigators, basic and all-survive awards.
    assert (
        client.portal.call(
            lambda: action("accelerate", d["player"], "我下推右侧油门加速。", reuse=receipt)
        )
        == receipt
    )
    assert client.portal.call(state).model_dump(mode="json") == before


def test_actual_package_b_waits_for_all_party_san_and_awards_mythos_once(client, package_rules):
    d, action, configure, state, entities = package_rules
    svc = client.app.state.agent_service
    client.portal.call(lambda: configure(flags={"panel_open": True}, holder=d["player"]))
    begin = client.portal.call(action, "decelerate", d["player"], "我上推油门减速停车。")
    assert client.portal.call(state).module_runtime.pending_outcome == "B"
    effect = entities["ending_b"]["sanity_effects"][0]
    assert (effect["success_loss"], effect["failure_loss"], effect["audience"]) == (
        "1d4",
        "1d10",
        "party",
    )

    async def settled_san(member):
        # Terminal-state fixture; real SAN rolls have a separate service regression.
        async with svc.rooms.transaction() as session:
            assert await session.get(AgentCycle, begin["cycle_id"])
            session.add(
                CheckRecord(
                    id=str(uuid4()),
                    room_id=d["room"]["id"],
                    cycle_id=begin["cycle_id"],
                    target_member_id=member,
                    agent_run_id="explicit terminal state fixture",
                    status="resolved",
                    document={
                        "sanity": {"entity_id": d["item"], "effect": effect, "stage": "done"}
                    },
                )
            )

    for member in (d["player"], d["agent"]):
        with pytest.raises(RoomError, match="每位调查员"):
            client.portal.call(action, "settle_bad_end", d["player"], "我面对已经经历的噩梦。")
        client.portal.call(settled_san, member)
    receipt = client.portal.call(action, "settle_bad_end", d["player"], "我面对已经经历的噩梦。")
    final = client.portal.call(state)
    assert final.module_runtime.outcome == "B" and final.module_runtime.pending_outcome is None
    assert all(c.sanity.mythos_gain == 3 for c in final.characters.values())
    client.portal.call(
        lambda: action("settle_bad_end", d["player"], "我面对已经经历的噩梦。", reuse=receipt)
    )
    assert client.portal.call(state) == final


def test_actual_package_c_rejects_threat_and_accepts_actual_party_death_fixture(
    client, package_rules
):
    d, action, configure, state, _ = package_rules
    client.portal.call(configure)
    with pytest.raises(RoomError, match="实际全员死亡"):
        client.portal.call(action, "crazy_end", d["player"], "我受到死亡威胁。")
    client.portal.call(lambda: configure(dead=True))
    receipt = client.portal.call(action, "crazy_end", d["player"], "全员死亡已经结算。")
    final = client.portal.call(state)
    assert final.module_runtime.outcome == "C"
    assert all(c.san == 0 for c in final.characters.values())
    client.portal.call(
        lambda: action("crazy_end", d["player"], "全员死亡已经结算。", reuse=receipt)
    )
    assert client.portal.call(state) == final
