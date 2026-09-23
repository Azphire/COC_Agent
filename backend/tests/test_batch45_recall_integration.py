"""Exercise actual build_context wiring with frozen entity and scene projections."""

import json

from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_module_navigation_runtime import act, running_navigation  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.memory.service import build_context
from app.persistence.agent_models import ProfileRecord


async def context_for(service, session, room, text):
    binding = next(b for b in await service.bindings(session, room.id)
                   if b.member_id == room.host_member_id)
    profile = await session.get(ProfileRecord, binding.profile_id)
    cycle = await service.cycle(session, room.id)
    trigger = service.rooms.append(session, room, "action.submitted", room.host_member_id,
                                   {"text": text, "cycle_id": cycle.id})
    cycle.state = {**cycle.state, "triggering_event_seq": trigger.seq}
    await session.flush()
    context, _, _ = await build_context(service, session, room, binding, profile, cycle,
                                         phase="plan_keeper_action")
    return context


def test_build_context_uses_approved_aliases_only_for_public_current_entities(
    client, running_navigation,  # noqa: F811
):
    data = running_navigation
    assert act(client, data, "Read Notice")["status"] == "completed"
    service = client.app.state.agent_service

    async def verify():
        async with service.rooms.database.sessions() as session:
            room = await service.rooms.room(session, data["room"]["id"])
            rows = await service.entities.rows(session, room.id)
            notice = next(row for row in rows if row.snapshot["title"] == "Notice")
            hidden = next(row for row in rows if row.snapshot["title"] == "Future")
            notice.snapshot = {**notice.snapshot, "aliases": ["墙边便条"],
                               "keeper_summary": "ALIAS_PRIVATE_PROSE"}
            hidden.snapshot = {**hidden.snapshot, "aliases": ["墙边便条"]}
            service.rooms.append(session, room, "clue.revealed", room.host_member_id,
                {"target_id": notice.source_entity_id, "content": "读数：003149。",
                 "scene_id": "previous-archive"})
            await session.flush()
            context = await context_for(service, session, room, "核对墙边便条")
            resolution = context["memory_selection_audit"]["reference_resolution"]
            assert resolution["target_ids"] == [notice.source_entity_id]
            assert hidden.source_entity_id not in resolution["target_ids"]
            selected = json.dumps(context["memory_evidence"], ensure_ascii=False)
            assert "003149" in selected and "previous-archive" in selected
            assert "ALIAS_PRIVATE_PROSE" not in selected

    client.portal.call(verify)


def test_unprepared_build_context_uses_module_scene_for_same_named_evidence(client, game):  # noqa: F811
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    service = client.app.state.agent_service

    async def verify():
        async with service.rooms.database.sessions() as session:
            room = await service.rooms.room(session, game["room"]["id"])
            module = await service.module(session, room.id)
            current_scene = module.state["scene_id"]
            for scene, code in [(current_scene, "003149"), ("another-cellar", "998877")]:
                service.rooms.append(session, room, "clue.revealed", room.host_member_id,
                    {"title": "铁门", "content": "铁门密码：" + code, "scene_id": scene})
            await session.flush()
            context = await context_for(service, session, room, "核对铁门密码")
            codes = [row for row in context["memory_evidence"]
                     if row.get("text", "").startswith("铁门密码")]
            assert codes[0]["source"]["scene"] == current_scene
            assert codes[0]["text"].endswith("003149")
            assert all(row["historical_only"] for row in codes)

    client.portal.call(verify)
