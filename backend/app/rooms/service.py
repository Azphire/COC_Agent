"""Authoritative room commands. SQLite commits precede every broadcast.

BEGIN IMMEDIATE serializes read/modify/write across database connections; the
per-room lock also orders initial WebSocket sync against committed broadcasts.
The locks and presence contain no durable game state.
"""

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.auth import digest, host_matches
from app.character.repository import CharacterRepository
from app.dice.service import DiceService
from app.domain.character import utc_now
from app.persistence.room_models import (
    GameRoom,
    RoomCharacterSlot,
    RoomEvent,
    RoomMember,
    RoomSnapshot,
)
from app.rooms.schemas import CharacterRuntimeV1, SessionStateV1, SnapshotDataV1


class RoomError(Exception):
    def __init__(self, message: str, status: int = 409):
        self.message, self.status = message, status


def require(condition, message: str, status: int = 409):
    if not condition:
        raise RoomError(message, status)


@dataclass(frozen=True)
class Identity:
    member_id: str
    is_host: bool


def visible(event: RoomEvent, identity: Identity) -> bool:
    return (
        identity.is_host
        or event.visibility == "public"
        or (event.visibility == "actor_and_host" and event.actor_member_id == identity.member_id)
    )


def iso_utc(value):
    # SQLite returns naive datetimes even for DateTime(timezone=True).
    return value.replace(tzinfo=timezone.utc).isoformat()


def event_view(event: RoomEvent) -> dict:
    return {
        "seq": event.seq,
        "type": event.type,
        "actor_member_id": event.actor_member_id,
        "visibility": event.visibility,
        "payload": event.payload,
        "occurred_at": iso_utc(event.occurred_at),
        "client_request_id": event.client_request_id,
    }


def snapshot_view(snapshot: RoomSnapshot) -> dict:
    return {
        key: getattr(snapshot, key)
        for key in ("id", "name", "creator_id", "event_seq", "format_version")
    } | {"created_at": iso_utc(snapshot.created_at)}


class RoomService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self.locks: dict[str, asyncio.Lock] = {}
        self.dice = DiceService()
        self.hub = None
        self.agent_service = None

    def lock(self, room_id):
        return self.locks.setdefault(str(room_id), asyncio.Lock())

    @asynccontextmanager
    async def transaction(self):
        async with self.database.sessions() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def room(self, session, room_id):
        room = await session.get(GameRoom, str(room_id))
        require(room is not None, "房间不存在", 404)
        return room

    async def identity(self, session, room, token, credential_type=None):
        if credential_type != "member" and host_matches(self.settings, token):
            return Identity(room.host_member_id, True)
        require(credential_type != "host", "身份凭据无效", 401)
        member = await session.scalar(
            select(RoomMember).where(
                RoomMember.room_id == room.id,
                RoomMember.token_hash == digest(token),
                RoomMember.active.is_(True),
                RoomMember.access_type == "remote",
            )
        )
        require(member is not None, "身份凭据无效或成员已离开", 401)
        return Identity(member.id, False)

    async def members(self, session, room):
        return list(
            await session.scalars(
                select(RoomMember)
                .where(RoomMember.room_id == room.id)
                .order_by(RoomMember.joined_at, RoomMember.id)
            )
        )

    async def slots(self, session, room):
        return list(
            await session.scalars(
                select(RoomCharacterSlot)
                .where(RoomCharacterSlot.room_id == room.id)
                .order_by(RoomCharacterSlot.published_at)
            )
        )

    async def view(self, session, room, identity):
        members, slots = await self.members(session, room), await self.slots(session, room)
        full_slots = {
            slot.id for slot in slots if identity.is_host or slot.member_id == identity.member_id
        }
        state = SessionStateV1.model_validate(room.session_state).model_dump(mode="json")
        state["characters"] = {
            key: value for key, value in state["characters"].items() if key in full_slots
        }
        return {
            "id": room.id,
            "name": room.name,
            "status": room.status,
            "host_member_id": room.host_member_id,
            "revision": room.revision,
            "latest_seq": room.revision,
            "state_version": room.state_version,
            "created_at": iso_utc(room.created_at),
            "updated_at": iso_utc(room.updated_at),
            "self_member_id": identity.member_id,
            "is_host": identity.is_host,
            "session_state": state,
            "members": [
                {
                    "id": m.id,
                    "display_name": m.display_name,
                    "role": m.role,
                    "controller_type": m.controller_type,
                    "access_type": m.access_type,
                    "ready": m.ready,
                    "active": m.active,
                    "joined_at": iso_utc(m.joined_at),
                    "last_seen_at": iso_utc(m.last_seen_at) if m.last_seen_at else None,
                    "slot_id": next((s.id for s in slots if s.member_id == m.id), None),
                }
                for m in members
            ],
            "character_slots": [
                {
                    "id": s.id,
                    "member_id": s.member_id,
                    "public_summary": s.public_summary,
                    **(
                        {
                            "source_character_id": s.source_character_id,
                            "character_snapshot": s.character_snapshot,
                        }
                        if s.id in full_slots
                        else {}
                    ),
                }
                for s in slots
            ],
            **(
                {"game": await self.agent_service.view(session, room, identity)}
                if self.agent_service
                else {}
            ),
        }

    async def events(self, session, room, identity, after_seq=0, limit=None):
        query = select(RoomEvent).where(RoomEvent.room_id == room.id, RoomEvent.seq > after_seq)
        if not identity.is_host:
            query = query.where(
                (RoomEvent.visibility == "public")
                | (
                    (RoomEvent.visibility == "actor_and_host")
                    & (RoomEvent.actor_member_id == identity.member_id)
                )
            )
        query = query.order_by(RoomEvent.seq)
        if limit is not None:
            query = query.limit(limit)
        return [event_view(event) for event in await session.scalars(query)]

    def append(
        self,
        session,
        room,
        event_type,
        actor,
        payload,
        visibility="public",
        request_id=None,
        request_hash=None,
    ):
        room.revision += 1
        room.updated_at = utc_now()
        event = RoomEvent(
            room_id=room.id,
            seq=room.revision,
            type=event_type,
            actor_member_id=actor,
            visibility=visibility,
            payload=payload,
            occurred_at=room.updated_at,
            client_request_id=request_id,
            request_hash=request_hash,
        )
        session.add(event)
        return event

    async def new_member(
        self,
        session,
        room,
        name,
        controller="human",
        access="host_managed",
        role="player",
        token=None,
    ):
        members = await self.members(session, room)
        require(len([m for m in members if m.active]) < 100, "房间已达到 100 个活动成员上限")
        require(
            all(not m.active or m.name_key != name.casefold() for m in members),
            "活动成员显示名已存在",
        )
        member = RoomMember(
            id=str(uuid4()) if role != "host" else room.host_member_id,
            room_id=room.id,
            display_name=name,
            name_key=name.casefold(),
            role=role,
            controller_type=controller,
            access_type=access,
            ready=False,
            active=True,
            token_hash=digest(token) if token else None,
            joined_at=utc_now(),
            last_seen_at=None,
        )
        session.add(member)
        await session.flush()
        return member

    async def create(self, body):
        invite = secrets.token_urlsafe(24)
        async with self.transaction() as session:
            room = GameRoom(
                id=str(uuid4()),
                name=body.name,
                host_member_id=str(uuid4()),
                invite_hash=digest(invite),
                status="lobby",
                revision=0,
                state_version=1,
                session_state=SessionStateV1().model_dump(mode="json"),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(room)
            await session.flush()
            await self.new_member(session, room, body.host_name, role="host")
            self.append(session, room, "room.created", room.host_member_id, {"name": room.name})
            await session.flush()
            view = await self.view(session, room, Identity(room.host_member_id, True))
        return {"room": view, "invite_code": invite}

    async def list_rooms(self):
        async with self.database.sessions() as session:
            rooms = await session.scalars(select(GameRoom).order_by(GameRoom.updated_at.desc()))
            return [
                {"id": room.id, "name": room.name, "status": room.status, "revision": room.revision}
                for room in rooms
            ]

    async def join(self, body):
        invite_hash = digest(body.invite_code.get_secret_value().strip())
        async with self.database.sessions() as session:
            room_id = await session.scalar(
                select(GameRoom.id).where(GameRoom.invite_hash == invite_hash)
            )
        require(room_id is not None, "邀请码无效", 404)
        async with self.lock(room_id):
            async with self.transaction() as session:
                room = await self.room(session, room_id)
                require(room.invite_hash == invite_hash, "邀请码无效", 404)
                require(room.status in ("lobby", "paused"), "仅大厅或暂停状态可以加入")
                token = secrets.token_urlsafe(32)
                member = await self.new_member(
                    session, room, body.display_name, access="remote", token=token
                )
                event = self.append(
                    session,
                    room,
                    "member.joined",
                    member.id,
                    {"member_id": member.id, "display_name": member.display_name},
                )
                await session.flush()
                view = await self.view(session, room, Identity(member.id, False))
            await self.broadcast(room_id, [event])
        return {"room": view, "member_token": token}

    async def get(self, room_id, token, kind="room", after_seq=0, limit=500):
        async with self.lock(room_id), self.database.sessions() as session:
            room = await self.room(session, room_id)
            identity = await self.identity(session, room, token)
            if kind == "events":
                events = await self.events(session, room, identity, after_seq, limit)
                # Advance across invisible gaps only when the filtered page is exhausted.
                cursor = events[-1]["seq"] if len(events) == limit else room.revision
                return {
                    "events": events,
                    "next_seq": max(after_seq, cursor),
                    "latest_seq": room.revision,
                }
            if kind == "logs":
                return await self.events(session, room, identity)
            if kind == "snapshots":
                require(identity.is_host, "仅主机可以管理存档", 403)
                rows = await session.scalars(
                    select(RoomSnapshot)
                    .where(RoomSnapshot.room_id == room.id)
                    .order_by(RoomSnapshot.created_at)
                )
                return [snapshot_view(row) for row in rows]
            return await self.view(session, room, identity)

    async def broadcast(self, room_id, events):
        if self.hub:
            await self.hub.broadcast(str(room_id), events)

    async def command(self, room_id, token, action, body=None, target=None):
        async with self.lock(room_id):
            async with self.transaction() as session:
                room = await self.room(session, room_id)
                identity = await self.identity(session, room, token)
                require(room.status != "ended", "房间已结束，只能读取")
                before = room.revision
                result = await self.apply(
                    session, room, identity, action, body, str(target) if target else None
                )
                await session.flush()
                events = list(
                    await session.scalars(
                        select(RoomEvent)
                        .where(RoomEvent.room_id == room.id, RoomEvent.seq > before)
                        .order_by(RoomEvent.seq)
                    )
                )
                view = await self.view(session, room, identity)
            await self.broadcast(room_id, events)
            if self.agent_service:
                runtime = self.agent_service.runtime
                if action in {
                    "agent.action",
                    "agent.check.roll",
                    "agent.retry",
                    "resume",
                    "agent.sanity.request",
                    "agent.sanity.roll",
                    "agent.sanity.manage",
                }:
                    runtime.schedule(room_id)
                elif action in {"agent.cancel", "agent.check.cancel"}:
                    runtime.cancel_task(room_id)
            return {"room": view, **(result or {})}

    async def apply(self, session, room, identity, action, body, target):
        if self.agent_service and action.startswith("agent."):
            return await self.agent_service.apply(session, room, identity, action, body, target)
        if self.agent_service and action in {
            "pause",
            "end",
            "state.patch",
            "assign",
            "unassign",
            "member.patch",
            "member.leave",
        }:
            cycle = await self.agent_service.cycle(session, room.id, active=True)
            require(
                not cycle or (cycle.status != "running" and action == "pause"),
                "请先等待或取消活动 Agent 回合",
            )
        actor = identity.member_id
        host_actions = {
            "invite.rotate",
            "member.add",
            "slot.publish",
            "slot.remove",
            "start",
            "pause",
            "resume",
            "end",
            "state.patch",
            "snapshot.create",
            "snapshot.load",
        }
        if action in host_actions:
            require(identity.is_host, "仅主机可以执行此操作", 403)
        members = await self.members(session, room)
        slots = await self.slots(session, room)
        member_by_id = {m.id: m for m in members}
        slot_by_id = {s.id: s for s in slots}

        def player(member_id):
            member = member_by_id.get(member_id)
            require(
                member is not None and member.active and member.role == "player",
                "活动玩家不存在",
                404,
            )
            return member

        def editable_lobby():
            require(room.status in ("lobby", "paused"), "请在大厅或暂停后操作")

        if action == "invite.rotate":
            invite = secrets.token_urlsafe(24)
            room.invite_hash = digest(invite)
            self.append(session, room, "invite.rotated", actor, {}, "host_only")
            return {"invite_code": invite}
        if action == "member.add":
            editable_lobby()
            member = await self.new_member(session, room, body.display_name, body.controller_type)
            self.append(
                session,
                room,
                "member.joined",
                actor,
                {
                    "member_id": member.id,
                    "display_name": member.display_name,
                    "controller_type": member.controller_type,
                },
            )
        elif action in ("member.patch", "member.leave"):
            member = player(target if action == "member.patch" else actor)
            require(identity.is_host or member.id == actor, "不能修改其他成员", 403)
            if action == "member.leave" or body.active is False:
                member.active, member.ready = False, False
                for slot in slots:
                    if slot.member_id == member.id:
                        slot.member_id = None
                        self.append(
                            session,
                            room,
                            "character.unassigned",
                            actor,
                            {"slot_id": slot.id, "member_id": member.id},
                        )
                self.append(
                    session,
                    room,
                    "member.deactivated" if identity.is_host else "member.left",
                    actor,
                    {"member_id": member.id},
                )
            elif body.display_name is not None:
                require(
                    all(
                        not m.active
                        or m.id == member.id
                        or m.name_key != body.display_name.casefold()
                        for m in members
                    ),
                    "活动成员显示名已存在",
                )
                member.display_name, member.name_key = (
                    body.display_name,
                    body.display_name.casefold(),
                )
                self.append(
                    session,
                    room,
                    "member.updated",
                    actor,
                    {"member_id": member.id, "display_name": member.display_name},
                )
        elif action == "ready":
            editable_lobby()
            member = player(str(body.member_id) if body.member_id else actor)
            require(
                member.id == actor or (identity.is_host and member.access_type == "host_managed"),
                "只能设置自己或主机管理席位的 ready",
                403,
            )
            require(not body.ready or any(s.member_id == member.id for s in slots), "请先选择角色")
            member.ready = body.ready
            self.append(
                session,
                room,
                "member.ready",
                actor,
                {"member_id": member.id, "ready": member.ready},
            )
        elif action == "slot.publish":
            require(room.status == "lobby", "只能在大厅发布角色")
            require(len(slots) < 100, "房间最多发布 100 个角色")
            require(
                all(s.source_character_id != str(body.character_id) for s in slots), "角色已发布"
            )
            character = await CharacterRepository(self.database).get(body.character_id)
            require(character is not None, "角色不存在", 404)
            require(character.status == "finalized", "只能发布最终确认的角色", 422)
            slot = RoomCharacterSlot(
                id=str(uuid4()),
                room_id=room.id,
                source_character_id=str(character.id),
                character_snapshot=character.model_dump(mode="json"),
                public_summary={
                    "name": character.name,
                    "age": character.age,
                    "occupation": character.occupation,
                    "ruleset_id": character.ruleset_id,
                },
                member_id=None,
                published_at=utc_now(),
            )
            session.add(slot)
            state = SessionStateV1.model_validate(room.session_state)
            state.characters[UUID(slot.id)] = CharacterRuntimeV1(
                san_max=max(0, 99 - character.skill_values.get("cthulhu_mythos", 0))
                if character.ruleset_id == "coc7-character-creation"
                else None,
                **{key: character.derived_values.get(key) for key in ("hp", "mp", "san", "luck")},
            )
            room.session_state = state.model_dump(mode="json")
            self.append(
                session,
                room,
                "character.published",
                actor,
                {"slot_id": slot.id, "public_summary": slot.public_summary},
            )
        elif action == "slot.remove":
            require(room.status == "lobby", "只能在大厅撤下角色")
            slot = slot_by_id.get(target)
            require(slot is not None, "角色席位不存在", 404)
            require(slot.member_id is None, "请先取消角色分配")
            await session.delete(slot)
            state = SessionStateV1.model_validate(room.session_state)
            state.characters.pop(UUID(slot.id))
            room.session_state = state.model_dump(mode="json")
            self.append(session, room, "character.removed", actor, {"slot_id": slot.id})
        elif action in ("assign", "unassign"):
            editable_lobby()
            slot = slot_by_id.get(str(body.slot_id) if action == "assign" else target)
            require(slot is not None, "角色席位不存在", 404)
            if action == "assign":
                member = player(str(body.member_id) if body.member_id else actor)
                require(identity.is_host or member.id == actor, "不能替其他成员选择角色", 403)
                require(slot.member_id is None, "角色已被占用")
                require(all(s.member_id != member.id for s in slots), "玩家已有角色，请先取消分配")
                slot.member_id, member.ready = member.id, False
                self.append(
                    session,
                    room,
                    "character.assigned",
                    actor,
                    {"slot_id": slot.id, "member_id": member.id},
                )
            else:
                require(identity.is_host or slot.member_id == actor, "不能取消其他成员的角色", 403)
                require(slot.member_id is not None, "角色尚未分配")
                previous = slot.member_id
                member_by_id[previous].ready = False
                slot.member_id = None
                self.append(
                    session,
                    room,
                    "character.unassigned",
                    actor,
                    {"slot_id": slot.id, "member_id": previous},
                )
        elif action in ("start", "pause", "resume", "end"):
            transitions = {
                "start": ({"lobby"}, "running", "game.started"),
                "pause": ({"running"}, "paused", "game.paused"),
                "resume": ({"paused"}, "running", "game.resumed"),
                "end": ({"running", "paused"}, "ended", "game.ended"),
            }
            sources, destination, event_type = transitions[action]
            require(room.status in sources, "房间状态不允许此转换")
            if action in ("start", "resume"):
                if self.agent_service:
                    await self.agent_service.navigation.require_available(session, room.id)
                players = [m for m in members if m.active and m.role == "player"]
                require(players, "至少需要一个活动玩家席位")
                require(
                    all(m.ready and any(s.member_id == m.id for s in slots) for m in players),
                    "所有活动玩家必须分配角色并 ready",
                )
            room.status = destination
            self.append(session, room, event_type, actor, {"status": destination})
        elif action == "state.patch":
            require(room.status in ("running", "paused"), "游戏开始后才能修改会话状态")
            require(body.expected_revision == room.revision, "房间已更新，请重新加载状态后编辑")
            previous_state = SessionStateV1.model_validate(room.session_state)
            require(
                {k: c.model_dump(exclude={"conditions"}) for k, c in body.state.characters.items()}
                == {
                    k: c.model_dump(exclude={"conditions"})
                    for k, c in previous_state.characters.items()
                },
                "资源更正请使用专用资源事务；疯狂状态请使用 SAN 管理接口",
                422,
            )
            require(
                (body.state.game_minute, body.state.game_round, body.state.sanity_day)
                == (
                    previous_state.game_minute,
                    previous_state.game_round,
                    previous_state.sanity_day,
                ),
                "游戏时间请使用 SAN 管理接口",
                422,
            )
            require(
                set(map(str, body.state.characters)) == set(slot_by_id),
                "运行时角色必须与已发布席位一致",
                422,
            )
            require(
                body.state.active_slot_id is None or str(body.state.active_slot_id) in slot_by_id,
                "当前行动席位不存在",
                422,
            )
            room.session_state = body.state.model_dump(mode="json")
            if self.agent_service:
                await self.agent_service.navigation.reconcile(session, room)
            # Runtime resources travel only in filtered snapshots, never public event payloads.
            self.append(
                session, room, "session.updated", actor, {"state": room.session_state}, "host_only"
            )
            self.append(
                session,
                room,
                "scene.updated",
                actor,
                {
                    "scene_title": room.session_state["scene_title"],
                    "scene_summary": room.session_state["scene_summary"],
                },
            )
        elif action in ("message", "roll"):
            if body.actor_member_id:
                member = player(str(body.actor_member_id))
                require(
                    identity.is_host
                    and member.access_type == "host_managed"
                    and member.controller_type == "human",
                    "只能代本地真人席位操作；Agent 尚未接入自动行动",
                    403,
                )
                actor = member.id
            require(
                identity.is_host or body.visibility != "host_only",
                "玩家请使用仅自己与主机可见",
                403,
            )
            request_hash = digest(action + json.dumps(body.model_dump(mode="json"), sort_keys=True))
            previous = await session.scalar(
                select(RoomEvent).where(
                    RoomEvent.room_id == room.id,
                    RoomEvent.actor_member_id == actor,
                    RoomEvent.client_request_id == str(body.client_request_id),
                )
            )
            if previous:
                require(previous.request_hash == request_hash, "client_request_id 已用于其他内容")
                return {"event": event_view(previous)}
            if action == "roll":
                try:
                    roll = self.dice.roll(body.expression, "room")
                except ValueError as error:
                    raise RoomError(str(error), 422) from error
                payload = {
                    "expression": roll.formula,
                    "dice": roll.dice,
                    "modifier": roll.modifier,
                    "total": roll.total,
                    "reason": body.reason,
                    "operator_member_id": identity.member_id,
                }
            else:
                payload = {"text": body.text, "operator_member_id": identity.member_id}
            event = self.append(
                session,
                room,
                "dice.rolled" if action == "roll" else "chat.message",
                actor,
                payload,
                body.visibility,
                str(body.client_request_id),
                request_hash,
            )
            return {"event": event_view(event)}
        elif action == "snapshot.create":
            require(room.status in ("running", "paused"), "只能在运行或暂停时存档")
            snapshot = RoomSnapshot(
                id=str(uuid4()),
                room_id=room.id,
                name=body.name,
                creator_id=actor,
                event_seq=room.revision,
                format_version=1,
                document=SnapshotDataV1(
                    state=SessionStateV1.model_validate(room.session_state),
                    assignments={s.id: s.member_id for s in slots},
                ).model_dump(mode="json"),
                created_at=utc_now(),
            )
            session.add(snapshot)
            if self.agent_service:
                await self.agent_service.save(session, room, snapshot)
            self.append(
                session,
                room,
                "snapshot.created",
                actor,
                {
                    "snapshot_id": snapshot.id,
                    "name": snapshot.name,
                    "event_seq": snapshot.event_seq,
                },
                "host_only",
            )
            return {"snapshot": snapshot_view(snapshot)}
        elif action == "snapshot.load":
            require(room.status == "paused", "只能在暂停状态载入存档")
            snapshot = await session.get(RoomSnapshot, target)
            require(snapshot is not None and snapshot.room_id == room.id, "存档不存在", 404)
            data = SnapshotDataV1.model_validate(snapshot.document)
            if self.agent_service:
                await self.agent_service.load(session, room, snapshot)
            require(set(map(str, data.assignments)) == set(slot_by_id), "存档角色席位不兼容")
            room.session_state = data.state.model_dump(mode="json")
            if self.agent_service:
                await self.agent_service.entities.reconcile_scene(session, room)
            # Clear first to support swaps with the unique member assignment constraint.
            for slot in slots:
                slot.member_id = None
            await session.flush()
            skipped = []
            for slot_id, member_id in data.assignments.items():
                member = member_by_id.get(str(member_id))
                if member_id and member and member.active and member.role == "player":
                    slot_by_id[str(slot_id)].member_id = member.id
                elif member_id:
                    skipped.append(str(slot_id))
            self.append(
                session,
                room,
                "snapshot.loaded",
                actor,
                {
                    "snapshot_id": snapshot.id,
                    "source_seq": snapshot.event_seq,
                    "unassigned_inactive_slots": skipped,
                },
            )
        else:
            raise RoomError("未知房间操作", 422)
