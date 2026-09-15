"""Rollback-only replay of encounter discovery on an isolated batch21 DB."""

import asyncio
import sys

from module_package import ROOT, settings_for

from app.agents.model import AgentModelClient
from app.agents.service import AgentService
from app.persistence.agent_models import AgentCycle
from app.persistence.database import Database
from app.rooms.service import RoomService


async def main():
    directory = ROOT / "data/prepared/changan/batch-21/short-c2"
    settings = settings_for(directory)
    database = Database(settings.database_url)
    rooms = RoomService(database, settings)
    agents = AgentService(rooms, AgentModelClient(settings))
    async with database.sessions() as session:
        cycle = await session.get(AgentCycle, sys.argv[1])
        room = await rooms.room(session, cycle.room_id)
        try:
            await agents.sanity.encounters.discover(session, room, cycle)
        finally:
            await session.rollback()
    await database.close()


if __name__ == "__main__":
    asyncio.run(main())
