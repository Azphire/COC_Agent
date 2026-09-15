"""Room-scoped character preparation; never writes a library draft on submission."""

import json
from uuid import uuid4

from sqlalchemy import select

from app.auth import digest
from app.character.service import CharacterError
from app.domain.character import CharacterDraft, utc_now
from app.persistence.room_models import RoomCharacterSubmission, RoomMember
from app.rooms.schemas import AssignCharacter
from app.rooms.service import event_view, iso_utc, require


class SubmissionService:
    def __init__(self, rooms, characters):
        self.rooms, self.characters = rooms, characters

    @staticmethod
    def view(row, identity):
        summary = {
            "id": row.id,
            "member_id": row.member_id,
            "version": row.version,
            "status": row.status,
            "created_at": iso_utc(row.created_at),
            "updated_at": iso_utc(row.updated_at),
        }
        if identity.is_host or row.member_id == identity.member_id:
            summary.update(
                character=row.character,
                reason=row.reason,
                slot_id=row.slot_id,
                source={
                    "kind": "imported",
                    "original_id": row.document["character"]["id"],
                    "exported_at": row.document["exported_at"],
                    "ruleset": row.document["ruleset"],
                },
            )
        return summary

    async def latest(self, session, room, member_id):
        return await session.scalar(
            select(RoomCharacterSubmission)
            .where(
                RoomCharacterSubmission.room_id == room.id,
                RoomCharacterSubmission.member_id == member_id,
            )
            .order_by(RoomCharacterSubmission.version.desc())
            .limit(1)
        )

    async def list(self, session, room, identity):
        # Only each member's current version travels in snapshots. History remains durable.
        rows = await session.scalars(
            select(RoomCharacterSubmission)
            .where(RoomCharacterSubmission.room_id == room.id)
            .order_by(RoomCharacterSubmission.version.desc())
        )
        latest = {}
        for row in rows:
            latest.setdefault(row.member_id, row)
        return [self.view(row, identity) for row in latest.values()]

    async def detail(self, session, room, identity, target):
        row = await session.get(RoomCharacterSubmission, str(target))
        require(row is not None and row.room_id == room.id, "提交记录不存在", 404)
        require(
            identity.is_host or row.member_id == identity.member_id,
            "仅主机和提交者可以查看角色提交详情",
            403,
        )
        return row

    def preview(self, room, identity, document):
        require(not identity.is_host, "请使用玩家的房间成员身份提交角色", 403)
        require(room.status == "lobby", "只能在游戏开始前的大厅提交角色")
        character = self.characters.prepare_import(document)
        if not character.validation.valid:
            raise CharacterError(
                422, "角色校验未通过，请在原车卡工具修正后重新导出", character.validation.issues
            )
        return character

    async def command(self, session, room, identity, action, body, target):
        fingerprint = digest(
            action
            + json.dumps({"target": target, "body": body.model_dump(mode="json")}, sort_keys=True)
        )
        previous = await self.rooms.receipt(
            session, room, identity.member_id, body.client_request_id, fingerprint
        )
        if previous:
            return {"event": event_view(previous)}
        require(room.status == "lobby", "只能在游戏开始前的大厅提交或处理角色")
        if action == "submission.submit":
            character = self.preview(room, identity, body.document)
            current = await self.latest(session, room, identity.member_id)
            version = current.version if current else 0
            require(body.expected_version == version, "提交版本已更新，请重新查看后提交")
            if current and current.status == "pending":
                current.status, current.reason = "rejected", "已被提交者的新版本替换"
                current.updated_at = utc_now()
                await session.flush()  # Release the single-pending-version constraint first.
            row = RoomCharacterSubmission(
                id=str(uuid4()),
                room_id=room.id,
                member_id=identity.member_id,
                version=version + 1,
                status="pending",
                reason="",
                slot_id=None,
                document=body.document.model_dump(mode="json"),
                character=character.model_dump(mode="json"),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(row)
        else:
            require(action == "submission.review", "未知提交操作", 422)
            require(identity.is_host, "仅主机可以接受或拒绝角色提交", 403)
            row = await self.detail(session, room, identity, target)
            current = await self.latest(session, room, row.member_id)
            require(
                current.id == row.id and body.expected_version == row.version,
                "提交版本已更新，不能批准旧版本，请重新预览",
            )
            require(row.status == "pending", "该版本已处理，请查看当前状态")
            if body.decision == "accept":
                member = await session.get(RoomMember, row.member_id)
                require(member is not None and member.active, "提交者已离开房间")
                slots = await self.rooms.slots(session, room)
                occupied = next((s for s in slots if s.member_id == member.id), None)
                require(occupied is None, "提交者已有角色，请先在房间调查员中取消原角色分配")
                require(len(slots) < 100, "房间最多发布 100 个角色")
                draft = CharacterDraft.model_validate(row.character)
                sheet = self.characters.finalize_candidate(draft, draft.version)
                await self.characters.repository.save_in_session(
                    session,
                    sheet,
                    [
                        (
                            "imported",
                            {
                                "original_id": str(sheet.original_id),
                                "room_id": room.id,
                                "submission_id": row.id,
                                "member_id": member.id,
                                "submission_version": row.version,
                            },
                        ),
                        ("finalized", {}),
                    ],
                )
                slot = self.rooms.publish_character(session, room, identity.member_id, sheet)
                await session.flush()
                await self.rooms.apply(
                    session,
                    room,
                    identity,
                    "assign",
                    AssignCharacter(slot_id=slot.id, member_id=member.id),
                    None,
                )
                row.character, row.slot_id = sheet.model_dump(mode="json"), slot.id
                row.status = "accepted"
            else:
                row.status = "rejected"
            row.reason, row.updated_at = body.reason, utc_now()
        # Receipts contain only the same status fields visible to other members.
        event = self.rooms.append(
            session,
            room,
            "character.submission." + row.status,
            identity.member_id,
            {
                "submission_id": row.id,
                "member_id": row.member_id,
                "version": row.version,
                "status": row.status,
            },
            request_id=str(body.client_request_id),
            request_hash=fingerprint,
        )
        return {"event": event_view(event)}
