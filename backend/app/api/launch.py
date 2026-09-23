import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.auth import require_host
from app.launch.schemas import LaunchInput, LaunchPatch, LaunchStart, PreparationRequirements
from app.launch.service import LaunchService

router = APIRouter(prefix="/api", tags=["launch"], dependencies=[Depends(require_host)])


def service(request: Request):
    return request.app.state.launch_service


Service = Annotated[LaunchService, Depends(service)]


@router.get("/launch/options")
async def options(svc: Service):
    return await svc.options()


@router.post("/launch/rules/refresh")
async def refresh_rules(svc: Service):
    from app.rooms.service import require

    knowledge = svc.agents.knowledge
    require(not knowledge.index_lock.locked(), "规则来源正在读取，请稍候", 409)
    async with knowledge.index_lock:
        await asyncio.to_thread(knowledge.indexer.index, "rules")
    return await svc.options()


@router.post("/launch/preparations/{preparation_id}/requirements")
async def requirements(preparation_id: UUID, body: PreparationRequirements, svc: Service):
    return await svc.configure_requirements(preparation_id, body)


@router.get("/launch-drafts")
async def drafts(svc: Service):
    return await svc.get()


@router.post("/launch-drafts")
async def create(body: LaunchInput, svc: Service):
    return await svc.create(body)


@router.get("/launch-drafts/{draft_id}")
async def get(draft_id: UUID, svc: Service):
    return await svc.get(draft_id)


@router.patch("/launch-drafts/{draft_id}")
async def patch(draft_id: UUID, body: LaunchPatch, svc: Service):
    return await svc.patch(draft_id, body)


@router.post("/launch-drafts/{draft_id}/preflight")
async def preflight(draft_id: UUID, svc: Service):
    return await svc.preflight(draft_id)


@router.post("/launch-drafts/{draft_id}/assemble")
async def assemble(draft_id: UUID, body: LaunchStart, svc: Service):
    return await svc.assemble(draft_id, body.expected_version)


@router.post("/launch-drafts/{draft_id}/start")
async def start(draft_id: UUID, body: LaunchStart, svc: Service):
    return await svc.start(draft_id, body)


@router.post("/launch-drafts/{draft_id}/repair")
async def repair(draft_id: UUID, svc: Service):
    return await svc.repair(draft_id)
