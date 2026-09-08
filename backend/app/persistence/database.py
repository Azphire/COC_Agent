from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


class Database:
    def __init__(self, database_url: str) -> None:
        url = make_url(database_url)
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(url)

    async def initialize(self) -> None:
        # Opening SQLite creates the local file; no business tables are needed yet.
        await self.check_connection()

    async def check_connection(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self.engine.dispose()
