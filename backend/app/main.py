from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api import characters, health, model, websocket
from app.character.repository import VersionConflict
from app.character.service import CharacterError
from app.config import Settings
from app.persistence.database import Database
from app.rules.loader import load_rulesets


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings.database_url)
        application.state.database = database
        try:
            application.state.character_rulesets = load_rulesets()
            await database.initialize()
            yield
        finally:
            await database.close()

    application = FastAPI(title="CoC Agent", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )
    application.include_router(health.router)
    application.include_router(model.router)
    application.include_router(websocket.router)
    application.include_router(characters.router)

    @application.exception_handler(CharacterError)
    async def character_error_handler(request, error: CharacterError):
        return JSONResponse(
            status_code=error.status,
            content={
                "detail": {
                    "message": error.message,
                    "issues": [issue.model_dump() for issue in error.issues],
                }
            },
        )

    @application.exception_handler(VersionConflict)
    async def version_error_handler(request, error):
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "message": "角色已被更新，请重新加载后编辑",
                    "issues": [],
                }
            },
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(request, error):
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "message": "数据库暂时不可用，请稍后重试",
                    "issues": [],
                }
            },
        )

    return application


app = create_app()


if __name__ == "__main__":
    settings = app.state.settings
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)
