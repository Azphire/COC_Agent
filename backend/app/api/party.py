from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.auth import require_host
from app.party import schemas as s

router = APIRouter(prefix="/api/party-batches", dependencies=[Depends(require_host)])


def service(request: Request):
    return request.app.state.party_service


Service = Annotated[object, Depends(service)]


@router.post("")
async def create(body: s.BatchInput, svc: Service):
    return svc.view(await svc.create(body))


@router.get("/{batch_id}")
async def get(batch_id: UUID, svc: Service):
    return svc.view(await svc.get(batch_id))


@router.post("/{batch_id}/next")
async def next_member(batch_id: UUID, body: s.OperationInput, svc: Service):
    return svc.view(await svc.next(batch_id, body.request_id))


@router.post("/{batch_id}/reroll")
async def reroll(batch_id: UUID, body: s.RerollInput, svc: Service):
    return svc.view(await svc.reroll(batch_id, body))


@router.post("/{batch_id}/resize")
async def resize(batch_id: UUID, body: s.ResizeInput, svc: Service):
    return svc.view(await svc.resize(batch_id, body))


@router.post("/{batch_id}/members/{index}/repair-persona")
async def repair_persona(batch_id: UUID, index: int, body: s.OperationInput, svc: Service):
    return svc.view(await svc.next(batch_id, body.request_id, repair_index=index))


@router.patch("/{batch_id}/members/{index}")
async def edit(batch_id: UUID, index: int, body: s.MemberEdit, svc: Service):
    return svc.view(await svc.edit(batch_id, index, body))


@router.post("/{batch_id}/adopt")
async def adopt(batch_id: UUID, body: s.AdoptInput, svc: Service):
    return svc.view(await svc.adopt(batch_id, body.request_id, body.approve_handouts))
