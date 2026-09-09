"""Ephemeral connections only. Every snapshot and replay is read from SQLite."""

import asyncio
import json
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.domain.character import utc_now
from app.persistence.room_models import RoomMember
from app.rooms.schemas import SocketAuth
from app.rooms.service import Identity, RoomError, event_view, visible


@dataclass(eq=False)
class Connection:
    websocket: WebSocket
    identity: Identity
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))

    def put(self, message):
        if self.queue.full():
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"close": 1013})
        else:
            self.queue.put_nowait(message)


class RoomHub:
    def __init__(self, service):
        self.service = service
        self.connections: dict[str, set[Connection]] = {}

    def presence(self, room_id):
        return sorted({c.identity.member_id for c in self.connections.get(room_id, set())})

    def presence_changed(self, room_id):
        message = {
            "type": "presence.changed",
            "data": {"online_member_ids": self.presence(room_id)},
        }
        for connection in self.connections.get(room_id, set()):
            connection.put(message)

    async def broadcast(self, room_id, events):
        async with self.service.database.sessions() as session:
            room = await self.service.room(session, room_id)
            for connection in list(self.connections.get(room_id, set())):
                member = await session.get(RoomMember, connection.identity.member_id)
                if member is None or not member.active:
                    self.connections.get(room_id, set()).discard(connection)
                    while not connection.queue.empty():
                        connection.queue.get_nowait()
                    connection.put({"close": 4401})
                    continue
                for event in events:
                    if visible(event, connection.identity):
                        connection.put({"type": "room.event", "data": event_view(event)})
                connection.put(
                    {
                        "type": "room.snapshot",
                        "data": await self.service.view(session, room, connection.identity),
                    }
                )
                connection.put({"type": "room.synced", "data": {"seq": room.revision}})
        self.presence_changed(room_id)

    async def send(self, websocket, message):
        await asyncio.wait_for(websocket.send_json(message), timeout=5)

    async def writer(self, connection):
        while True:
            message = await connection.queue.get()
            if "close" in message:
                await connection.websocket.close(code=message["close"])
                return
            await self.send(connection.websocket, message)

    async def reader(self, connection):
        while True:
            raw = await asyncio.wait_for(connection.websocket.receive_text(), timeout=45)
            if len(raw) > 4096:
                await connection.websocket.close(code=1009)
                return
            try:
                message = json.loads(raw)
            except ValueError:
                message = None
            if isinstance(message, dict) and message.get("type") == "ping":
                connection.put({"type": "pong", "data": {}})
            else:
                connection.put(
                    {"type": "error", "data": {"message": "状态修改请使用 REST；此连接仅接受 ping"}}
                )

    async def connect(self, websocket, room_id):
        await websocket.accept()
        connection = None
        tasks = []
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            if len(raw) > 4096:
                raise RoomError("认证消息过长", 401)
            auth = SocketAuth.model_validate_json(raw)
            async with self.service.lock(room_id):
                async with self.service.transaction() as session:
                    room = await self.service.room(session, room_id)
                    identity = await self.service.identity(
                        session, room, auth.token.get_secret_value(), auth.credential_type
                    )
                    if room.status != "ended":
                        member = await session.get(RoomMember, identity.member_id)
                        member.last_seen_at = utc_now()
                    snapshot = await self.service.view(session, room, identity)
                    events = await self.service.events(session, room, identity, auth.after_seq)
                await self.send(
                    websocket, {"type": "auth.ok", "data": {"member_id": identity.member_id}}
                )
                await self.send(websocket, {"type": "room.snapshot", "data": snapshot})
                for event in events:
                    await self.send(websocket, {"type": "room.event", "data": event})
                await self.send(
                    websocket, {"type": "room.synced", "data": {"seq": snapshot["latest_seq"]}}
                )
                connection = Connection(websocket, identity)
                self.connections.setdefault(room_id, set()).add(connection)
                self.presence_changed(room_id)
            tasks = [
                asyncio.create_task(self.writer(connection)),
                asyncio.create_task(self.reader(connection)),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (RoomError, ValidationError, ValueError):
            await self.send(
                websocket, {"type": "error", "data": {"message": "认证失败或消息格式无效"}}
            )
            await websocket.close(code=4401)
        except (WebSocketDisconnect, OSError, RuntimeError):
            pass
        except (TimeoutError, SQLAlchemyError):
            try:
                await websocket.close(code=1013)
            except RuntimeError:
                pass
        finally:
            # Clear presence before any cancellation point. The ASGI task can itself
            # be cancelled while awaiting writer cleanup or the room command lock.
            # These in-memory mutations contain no await and run on the hub's loop.
            if connection:
                self.connections.get(room_id, set()).discard(connection)
                self.presence_changed(room_id)
                if not self.connections.get(room_id):
                    self.connections.pop(room_id, None)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
