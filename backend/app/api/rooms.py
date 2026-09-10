import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, WebSocket
from fastapi.responses import Response

from app.auth import bearer, require_host
from app.rooms import schemas as s
from app.rooms.sanity_schemas import (
    EncounterReview,
    HostSanityRequest,
    ResourceCorrection,
    SanityManagement,
    SanityRoll,
)
from app.rooms.service import RoomService

router = APIRouter(prefix="/api/rooms", tags=["rooms"])
ws_router = APIRouter()


def service(request: Request) -> RoomService:
    return request.app.state.room_service


Service = Annotated[RoomService, Depends(service)]
Token = Annotated[str, Depends(bearer)]


@router.post("/{room_id}/sanity/encounters")
async def sanity_request(room_id: UUID, body: HostSanityRequest, svc: Service, token: Token):
    return await svc.command(room_id, token, "agent.sanity.request", body)


@router.post("/{room_id}/sanity/encounter-review")
async def encounter_review(room_id: UUID, body: EncounterReview, svc: Service, token: Token):
    return await svc.command(room_id, token, "agent.sanity.review", body)


@router.post("/{room_id}/sanity/checks/{check_id}/roll")
async def sanity_roll(room_id: UUID, check_id: UUID, body: SanityRoll, svc: Service, token: Token):
    return await svc.command(room_id, token, "agent.sanity.roll", body, check_id)


@router.post("/{room_id}/sanity/manage")
async def sanity_manage(room_id: UUID, body: SanityManagement, svc: Service, token: Token):
    return await svc.command(room_id, token, "agent.sanity.manage", body)


@router.post("/{room_id}/resources/correct")
async def resource_correct(room_id: UUID, body: ResourceCorrection, svc: Service, token: Token):
    return await svc.command(room_id, token, "agent.resource.correct", body)


@router.post("", dependencies=[Depends(require_host)], status_code=201)
async def create_room(body: s.CreateRoom, svc: Service):
    return await svc.create(body)


@router.get("", dependencies=[Depends(require_host)])
async def list_rooms(svc: Service):
    return await svc.list_rooms()


@router.post("/join", status_code=201)
async def join_room(body: s.JoinRoom, svc: Service):
    return await svc.join(body)


@router.get("/{room_id}")
async def get_room(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token)


@router.post("/{room_id}/invite/rotate")
async def rotate_invite(room_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "invite.rotate")


@router.post("/{room_id}/members")
async def add_member(room_id: UUID, body: s.AddMember, svc: Service, token: Token):
    return await svc.command(room_id, token, "member.add", body)


@router.patch("/{room_id}/members/{member_id}")
async def patch_member(
    room_id: UUID, member_id: UUID, body: s.PatchMember, svc: Service, token: Token
):
    return await svc.command(room_id, token, "member.patch", body, member_id)


@router.post("/{room_id}/leave")
async def leave_room(room_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "member.leave")


@router.post("/{room_id}/ready")
async def ready(room_id: UUID, body: s.ReadyRequest, svc: Service, token: Token):
    return await svc.command(room_id, token, "ready", body)


@router.post("/{room_id}/character-slots")
async def publish(room_id: UUID, body: s.PublishCharacter, svc: Service, token: Token):
    return await svc.command(room_id, token, "slot.publish", body)


@router.delete("/{room_id}/character-slots/{slot_id}")
async def remove_slot(room_id: UUID, slot_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "slot.remove", target=slot_id)


@router.post("/{room_id}/character-assignments")
async def assign(room_id: UUID, body: s.AssignCharacter, svc: Service, token: Token):
    return await svc.command(room_id, token, "assign", body)


@router.delete("/{room_id}/character-assignments/{slot_id}")
async def unassign(room_id: UUID, slot_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "unassign", target=slot_id)


@router.post("/{room_id}/start")
async def start(room_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "start")


@router.post("/{room_id}/pause")
async def pause(room_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "pause")


@router.post("/{room_id}/resume")
async def resume(room_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "resume")


@router.post("/{room_id}/end")
async def end(room_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "end")


@router.patch("/{room_id}/session-state")
async def patch_state(room_id: UUID, body: s.PatchSession, svc: Service, token: Token):
    return await svc.command(room_id, token, "state.patch", body)


@router.post("/{room_id}/messages")
async def message(room_id: UUID, body: s.MessageRequest, svc: Service, token: Token):
    return await svc.command(room_id, token, "message", body)


@router.post("/{room_id}/rolls")
async def roll(room_id: UUID, body: s.RollRequest, svc: Service, token: Token):
    return await svc.command(room_id, token, "roll", body)


@router.get("/{room_id}/events")
async def events(
    room_id: UUID,
    svc: Service,
    token: Token,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    return await svc.get(room_id, token, "events", after_seq, limit)


@router.post("/{room_id}/snapshots")
async def save(room_id: UUID, body: s.CreateSnapshot, svc: Service, token: Token):
    return await svc.command(room_id, token, "snapshot.create", body)


@router.get("/{room_id}/snapshots")
async def saves(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "snapshots")


@router.post("/{room_id}/snapshots/{snapshot_id}/load")
async def load(room_id: UUID, snapshot_id: UUID, svc: Service, token: Token):
    return await svc.command(room_id, token, "snapshot.load", target=snapshot_id)


@router.get("/{room_id}/logs")
async def logs(
    room_id: UUID, svc: Service, token: Token, format: Literal["jsonl", "markdown"] = "jsonl"
):
    events = await svc.get(room_id, token, "logs")
    if format == "jsonl":
        content = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
        media_type, extension = "application/x-ndjson", "jsonl"
    else:
        content = f"# 房间 {room_id} 游戏日志\n\n"
        for event in events:
            content += f"## {event['seq']} · {event['type']}\n\n"
            content += (
                "\n".join(
                    "    " + line
                    for line in json.dumps(event, ensure_ascii=False, indent=2).splitlines()
                )
                + "\n\n"
            )
        media_type, extension = "text/markdown", "md"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="room-{room_id}.{extension}"',
            "Cache-Control": "no-store",
        },
    )


@ws_router.websocket("/ws/rooms/{room_id}")
async def room_socket(websocket: WebSocket, room_id: UUID):
    await websocket.app.state.room_hub.connect(websocket, str(room_id))
