from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.persistence import (  # noqa: F401
    agent_models,
    knowledge_models,
    module_ir_models,
    preparation_models,  # noqa: F401
    room_models,
)
from app.persistence.character_models import Base


class Database:
    def __init__(self, database_url: str) -> None:
        url = make_url(database_url)
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(url, connect_args={"timeout": 30})
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def check_connection(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self.engine.dispose()
