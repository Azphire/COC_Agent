from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, websocket
from app.config import Settings
from app.persistence.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings.database_url)
        application.state.database = database
        try:
            await database.initialize()
            yield
        finally:
            await database.close()

    application = FastAPI(title="CoC Agent", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )
    application.include_router(health.router)
    application.include_router(websocket.router)
    return application


app = create_app()


if __name__ == "__main__":
    settings = app.state.settings
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)
