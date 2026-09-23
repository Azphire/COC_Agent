from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.agents.model import AgentModelClient
from app.agents.runtime import AgentRuntime
from app.agents.service import AgentService
from app.api import (
    agents,
    characters,
    health,
    knowledge,
    launch,
    model,
    module_ir,
    party,
    preparation,
    room_submissions,
    rooms,
    websocket,
)
from app.auth import require_host
from app.character.repository import CharacterRepository, VersionConflict
from app.character.service import CharacterError, CharacterService
from app.config import Settings
from app.launch.service import LaunchService
from app.models.settings import ModelSettings
from app.party.service import PartyService
from app.persistence.database import Database
from app.rooms.realtime import RoomHub
from app.rooms.service import RoomError, RoomService
from app.rooms.submissions import SubmissionService
from app.rules.loader import load_rulesets


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    model_settings = ModelSettings(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings.database_url)
        application.state.database = database
        application.state.room_service = RoomService(database, settings)
        application.state.room_hub = RoomHub(application.state.room_service)
        application.state.room_service.hub = application.state.room_hub
        try:
            application.state.character_rulesets = load_rulesets()
            application.state.room_service.submissions = SubmissionService(
                application.state.room_service,
                CharacterService(
                    CharacterRepository(database), application.state.character_rulesets
                ),
            )
            await database.initialize()
            agent_service = AgentService(
                application.state.room_service,
                AgentModelClient(settings, getattr(application.state, "agent_model_adapter", None)),
            )
            application.state.agent_service = agent_service
            model_settings.service = agent_service
            agent_service.model.configuration = model_settings
            application.state.room_service.model_configuration = model_settings
            application.state.room_service.agent_service = agent_service
            character_service = application.state.room_service.submissions.characters
            application.state.party_service = PartyService(agent_service, character_service)
            application.state.launch_service = LaunchService(
                agent_service, character_service, application.state.party_service, model_settings,
            )
            application.state.room_service.launch_service = application.state.launch_service
            await agent_service.preparation.initialize()
            agent_service.runtime = AgentRuntime(agent_service)
            await agent_service.runtime.initialize()
            yield
        finally:
            if hasattr(application.state, "agent_service"):
                await application.state.agent_service.preparation.close()
                await application.state.agent_service.runtime.close()
            await database.close()

    application = FastAPI(title="CoC Agent", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.model_settings = model_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    application.include_router(health.router)
    application.include_router(model.router)
    application.include_router(websocket.router)
    application.include_router(characters.router)
    application.include_router(rooms.router)
    application.include_router(room_submissions.router)
    application.include_router(rooms.ws_router)
    application.include_router(agents.router)
    application.include_router(knowledge.router)
    application.include_router(preparation.router)
    application.include_router(module_ir.router)
    application.include_router(party.router)
    application.include_router(launch.router)

    @application.middleware("http")
    async def host_boundary(request, call_next):
        path = request.url.path
        if request.method != "OPTIONS" and (
            path == "/api/characters"
            or path.startswith("/api/characters/")
            or path.startswith("/api/model/")
            or path in ("/docs", "/redoc", "/openapi.json", "/api/host/unlock")
        ):
            try:
                require_host(request)
            except HTTPException as error:
                return JSONResponse(status_code=error.status_code, content={"detail": error.detail})
        if request.method in {"POST", "PATCH", "DELETE"} and path != "/api/model/config":
            try:
                with model_settings.operation("主机/玩家请求处理中"):
                    response = await call_next(request)
            except RoomError as error:
                return JSONResponse(status_code=error.status, content={"detail": error.message})
        else:
            response = await call_next(request)
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.post("/api/host/unlock")
    async def unlock():
        return {"unlocked": True}

    @application.exception_handler(RoomError)
    async def room_error_handler(request, error):
        return JSONResponse(
            status_code=error.status, content={"detail": {"message": error.message, "issues": []}}
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request, error):
        # Pydantic's default error contains input values, including failed credentials.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": issue["loc"], "type": issue["type"], "msg": "请求字段不合法"}
                    for issue in error.errors()
                ]
            },
        )

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
