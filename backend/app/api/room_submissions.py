"""Bounded JSON parsing before Pydantic; credentials always come from the room token."""

import json
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.api.rooms import Service, Token
from app.character.service import CharacterError
from app.domain.character import ValidationIssue
from app.rooms import schemas as s
from app.rooms.service import RoomError

router = APIRouter(prefix="/api/rooms/{room_id}/character-submissions", tags=["rooms"])
MAX_SUBMISSION_BYTES = 256 * 1024


async def read_body(request: Request, schema):
    size = request.headers.get("content-length")
    if size and size.isdecimal() and int(size) > MAX_SUBMISSION_BYTES:
        raise RoomError("角色提交请求不能超过 256 KiB", 413)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_SUBMISSION_BYTES:
            raise RoomError("角色提交请求不能超过 256 KiB", 413)
        raw.extend(chunk)
    try:
        return schema.model_validate(json.loads(raw))
    except (ValueError, RecursionError) as error:
        issues = []
        if isinstance(error, ValidationError):
            issues = [
                ValidationIssue(
                    field=".".join(str(part) for part in item["loc"]),
                    code=item["type"],
                    message="字段格式或取值不符合 CharacterExport JSON，请检查导出文件",
                )
                for item in error.errors()[:30]
            ]
        raise CharacterError(
            422, "角色文件格式无效；需要现有 CharacterExport JSON（schema_version=1）", issues
        ) from None


@router.get("")
async def list_submissions(room_id: UUID, svc: Service, token: Token):
    async with svc.lock(room_id), svc.database.sessions() as session:
        room = await svc.room(session, room_id)
        identity = await svc.identity(session, room, token)
        return await svc.submissions.list(session, room, identity)


@router.get("/{submission_id}")
async def get_submission(room_id: UUID, submission_id: UUID, svc: Service, token: Token):
    async with svc.lock(room_id), svc.database.sessions() as session:
        room = await svc.room(session, room_id)
        identity = await svc.identity(session, room, token)
        row = await svc.submissions.detail(session, room, identity, submission_id)
        return svc.submissions.view(row, identity)


@router.post("/preview")
async def preview(room_id: UUID, request: Request, svc: Service, token: Token):
    async with svc.lock(room_id), svc.database.sessions() as session:
        room = await svc.room(session, room_id)
        identity = await svc.identity(session, room, token)
        body = await read_body(request, s.PreviewCharacterSubmission)
        return svc.submissions.preview(room, identity, body.document)


@router.post("")
async def submit(room_id: UUID, request: Request, svc: Service, token: Token):
    body = await read_body(request, s.SubmitCharacter)
    return await svc.command(room_id, token, "submission.submit", body)


@router.post("/{submission_id}/review")
async def review(room_id: UUID, submission_id: UUID, request: Request, svc: Service, token: Token):
    body = await read_body(request, s.ReviewCharacterSubmission)
    return await svc.command(room_id, token, "submission.review", body, submission_id)
