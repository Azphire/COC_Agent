"""Declare a short-test starting scene before the first player action.

This is fixture preparation, never a natural route or an in-play repair. The
approved package is unchanged; no keys, check results or passage flags are set.
Run with the isolated server stopped, after check_batch18.py setup.
"""

import sys

from check_batch18 import DIRECTORY
from fastapi.testclient import TestClient
from module_package import read, settings_for, write
from sqlalchemy import select

from app.main import create_app
from app.persistence.room_models import RoomEvent
from app.rooms.combat_service import load_state, store_state


def seed(scene):
    info = read(DIRECTORY / "load-info.json")
    session_info = read(DIRECTORY / "session.json")
    package = read(DIRECTORY.parent / "package-approved.json")
    node = next(e["node_ids"][0] for e in package["entities"] if e["key"] == scene)
    room_id = session_info["prefix"].rsplit("/", 1)[-1]
    with TestClient(create_app(settings_for(DIRECTORY))) as client:
        service = client.app.state.agent_service

        async def operation():
            async with service.rooms.transaction() as session:
                room = await service.rooms.room(session, room_id)
                actions = await session.scalar(
                    select(RoomEvent).where(
                        RoomEvent.room_id == room_id,
                        RoomEvent.type.in_(
                            [
                                "action.submitted",
                                "agent.action_proposed",
                                "fixture.starting_state",
                            ]
                        ),
                    )
                )
                if actions:
                    raise ValueError("Only a never-played room can be seeded once")
                nav = await service.navigation.state(session, room_id)
                snapshot, _ = await service.navigation.snapshot(session, nav)
                nav.current_scene_node_id = node
                nav.visited_scene_node_ids = list(
                    dict.fromkeys([*nav.visited_scene_node_ids, node])
                )
                await service.navigation.project(session, room, nav, snapshot)
                await service.navigation.persist(session, nav)
                await service.entities.reveal(
                    session,
                    room,
                    info["entity_ids"]["phone"],
                    room.host_member_id,
                    host_override=True,
                )
                data = load_state(room)
                eid, actor = info["entity_ids"]["phone"], session_info["player_id"]
                instance = eid + ":" + actor
                data.module_runtime.item_instances[instance] = eid
                data.module_runtime.inventory[instance] = actor
                store_state(room, data)
                receipt = {
                    "fixture_only": True,
                    "scene": scene,
                    "phone_holder": actor,
                    "phone_instance": instance,
                    "keys_hidden": True,
                    "no_passage_flags_or_dice": True,
                }
                service.rooms.append(
                    session,
                    room,
                    "fixture.starting_state",
                    room.host_member_id,
                    receipt,
                    "host_only",
                )
                write(DIRECTORY / "short-start.json", receipt)

        client.portal.call(operation)
    print("Declared short fixture start", scene)


if __name__ == "__main__":
    if not (DIRECTORY / "session.json").exists():
        import check_batch18

        with TestClient(create_app(settings_for(DIRECTORY))) as setup_client:

            def request(method, path, body=None, *, player=False):
                token = (
                    read(DIRECTORY / "session.json")["token"]
                    if player
                    else check_batch18.driver.HOST
                )
                response = setup_client.request(
                    method, "/api" + path, json=body, headers={"Authorization": "Bearer " + token}
                )
                response.raise_for_status()
                return response.json()

            check_batch18.driver.request = request
            check_batch18.setup()
    seed(sys.argv[1] if len(sys.argv) > 1 else "s4")
