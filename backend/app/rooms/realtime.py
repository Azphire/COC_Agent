"""Authenticated room delivery and bounded, validated public narration drafts."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

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
    bootstrap: list = field(default_factory=list)
    closing: bool = False
    close_code: int = 1013
    connection_id: str = field(default_factory=lambda: str(uuid4()))

    def put(self, message):
        if self.closing:
            return
        if "close" in message:
            self.closing, self.close_code = True, message["close"]
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(message)
            return
        if message.get("type") == "pong" and not self.queue.full():
            pending = []
            while not self.queue.empty():
                pending.append(self.queue.get_nowait())
            self.queue.put_nowait(message)
            for queued in pending:
                self.queue.put_nowait(queued)
            return
        if self.queue.full():
            self.closing = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"close": 1013})
        else:
            self.queue.put_nowait(message)

    def discard_stream_messages(self):
        # Transient drafts may be replaced by a snapshot or a committed event.
        retained = []
        while not self.queue.empty():
            message = self.queue.get_nowait()
            if not message.get("type", "").startswith("keeper.stream."):
                retained.append(message)
        for message in retained:
            self.queue.put_nowait(message)

    def put_stream(self, message, snapshot):
        if message.get("type") in {"keeper.stream.end", "keeper.stream.interrupt"}:
            self.discard_stream_messages()
        if self.queue.qsize() >= 32:
            self.discard_stream_messages()
            self.put(snapshot)
        else:
            self.put(message)


@dataclass
class NarrationStream:
    cycle_id: str
    stream_id: str
    attempt: int
    index: int = 0
    text: str = ""
    status: str = "responding"
    reason: str | None = None
    event_seq: int | None = None
    updated: float = field(default_factory=time.monotonic)
    pending: str = ""
    pending_offset: int = 0
    timer: asyncio.TimerHandle | None = None

    def data(self):
        return {
            "cycle_id": self.cycle_id, "stream_id": self.stream_id,
            "attempt": self.attempt, "index": self.index, "text": self.text,
            "status": self.status, "event_seq": self.event_seq, "reason": self.reason,
        }


class RoomHub:
    def __init__(self, service):
        self.service = service
        self.connections: dict[str, set[Connection]] = {}
        self.streams: dict[str, NarrationStream] = {}
        self.stream_max_chars = 65536
        self.stream_max_rooms = 128
        self.stream_ttl = 120.0
        self.stream_merge_seconds = 0.04

    def stream_snapshot(self, room_id):
        stream = self.streams.get(str(room_id))
        if stream and time.monotonic() - stream.updated > self.stream_ttl:
            self.invalidate_stream(room_id, reason="expired")
            stream = self.streams.get(str(room_id))
        return {"type": "keeper.stream.snapshot", "data": stream.data() if stream else None}

    def _stream_emit(self, room_id, kind, stream, **data):
        message = {"type": f"keeper.stream.{kind}", "data": {
            "cycle_id": stream.cycle_id, "stream_id": stream.stream_id,
            "attempt": stream.attempt, "index": stream.index,
            "status": stream.status, **data,
        }}
        snapshot = {"type": "keeper.stream.snapshot", "data": stream.data()}
        for connection in list(self.connections.get(str(room_id), set())):
            connection.put_stream(message, snapshot)

    @staticmethod
    def _cancel_pending(stream):
        if stream.timer:
            stream.timer.cancel()
            stream.timer = None
        stream.pending = ""

    def _flush_stream(self, room_id, stream):
        stream.timer = None
        if self.streams.get(str(room_id)) is not stream or not stream.pending:
            return
        text, offset = stream.pending, stream.pending_offset
        stream.pending = ""
        self._stream_emit(room_id, "delta", stream, text=text, offset=offset)

    def _matching_stream(self, room_id, cycle_id, stream_id, attempt):
        stream = self.streams.get(str(room_id))
        if (stream and stream.cycle_id == cycle_id and stream.stream_id == stream_id
                and stream.attempt == attempt and stream.status in {"responding", "waiting"}):
            return stream
        return None

    async def stream_start(self, room_id, cycle_id, *, attempt=1):
        room_id = str(room_id)
        self.invalidate_stream(room_id, reason="replaced")
        # Evict oldest room, invalidating its in-flight callbacks before removal.
        if room_id not in self.streams and len(self.streams) >= self.stream_max_rooms:
            oldest = min(self.streams, key=lambda key: self.streams[key].updated)
            self.invalidate_stream(oldest, reason="expired")
            self.streams.pop(oldest, None)
        stream = NarrationStream(str(cycle_id), str(uuid4()), attempt)
        self.streams[room_id] = stream
        self._stream_emit(room_id, "start", stream, text="")
        return stream.stream_id

    async def stream_delta(self, room_id, cycle_id, stream_id, text, *, attempt=1):
        """Caller must supply only the newly accepted public prose, never raw model chunks."""
        stream = self._matching_stream(room_id, cycle_id, stream_id, attempt)
        if not stream or stream.status == "waiting" or not text:
            return False
        if len(stream.text) + len(text) > self.stream_max_chars:
            self._cancel_pending(stream)
            stream.index += 1
            stream.text, stream.status, stream.reason = "", "waiting", "buffer_limit"
            stream.updated = time.monotonic()
            self._stream_emit(room_id, "replace", stream, text="", reason=stream.reason)
            return False
        if not stream.pending:
            stream.pending_offset = len(stream.text)
        stream.text += text
        stream.pending += text
        stream.index += 1
        stream.updated = time.monotonic()
        if stream.timer is None:
            stream.timer = asyncio.get_running_loop().call_later(
                self.stream_merge_seconds, self._flush_stream, str(room_id), stream,
            )
        return True

    async def stream_replace(self, room_id, cycle_id, stream_id, text, *, attempt=1):
        stream = self._matching_stream(room_id, cycle_id, stream_id, attempt)
        if not stream:
            return False
        self._cancel_pending(stream)
        stream.index += 1
        within_limit = len(text) <= self.stream_max_chars
        stream.text = text if within_limit else ""
        stream.status = "responding" if within_limit else "waiting"
        stream.reason = None if within_limit else "buffer_limit"
        stream.updated = time.monotonic()
        self._stream_emit(room_id, "replace", stream, text=stream.text, reason=stream.reason)
        return within_limit

    async def stream_end(self, room_id, cycle_id, stream_id, *, event_seq, attempt=1):
        stream = self._matching_stream(room_id, cycle_id, stream_id, attempt)
        if not stream:
            return False
        self._cancel_pending(stream)
        stream.index += 1
        stream.status, stream.event_seq = "ended", event_seq
        stream.text = ""
        stream.updated = time.monotonic()
        self._stream_emit(room_id, "end", stream, event_seq=event_seq)
        return True

    def invalidate_stream(self, room_id, reason="cancelled"):
        """Synchronous cancellation barrier: queued/late callbacks cannot revive a draft."""
        stream = self.streams.get(str(room_id))
        if not stream or stream.status in {"ended", "interrupted"}:
            return False
        self._cancel_pending(stream)
        stream.index += 1
        stream.status, stream.reason, stream.text = "interrupted", reason, ""
        stream.updated = time.monotonic()
        for connection in self.connections.get(str(room_id), set()):
            connection.discard_stream_messages()
        self._stream_emit(room_id, "interrupt", stream, reason=reason)
        return True

    async def stream_interrupt(self, room_id, *, cycle_id=None, stream_id=None, attempt=None,
                               reason="cancelled"):
        stream = self.streams.get(str(room_id))
        if not stream or (cycle_id is not None and stream.cycle_id != cycle_id) or (
            stream_id is not None and stream.stream_id != stream_id
        ) or (attempt is not None and stream.attempt != attempt):
            return False
        return self.invalidate_stream(room_id, reason=reason)

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
        with self.service.timings.span("broadcast", room_id=str(room_id), event_count=len(events)):
            await self._broadcast(room_id, events)

    async def _broadcast(self, room_id, events):
        async with self.service.database.sessions() as session:
            room = await self.service.room(session, room_id)
            # Reuse only an identical authorization projection within this one
            # broadcast/transaction. Reconnects and later broadcasts rebuild it.
            projections = {}
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
                        stream = self.streams.get(str(room_id))
                        if (event.type == "keeper.narration" and stream
                                and event.payload.get("cycle_id") == stream.cycle_id):
                            connection.discard_stream_messages()
                        with self.service.timings.span(
                            "formal.publish_enqueue" if event.type == "keeper.narration"
                            else "event.enqueue", member_id=connection.identity.member_id,
                            connection_id=connection.connection_id,
                            event_seq=event.seq, cycle_id=event.payload.get("cycle_id"),
                            run_id=event.payload.get("run_id"),
                        ):
                            connection.put({"type": "room.event", "data": event_view(event)})
                key = (connection.identity.member_id, connection.identity.is_host)
                if key not in projections:
                    with self.service.timings.span(
                        "snapshot.build", member_id=connection.identity.member_id,
                        connection_id=connection.connection_id,
                        audience="host" if connection.identity.is_host else "player",
                    ) as built:
                        snapshot = await self.service.view(session, room, connection.identity)
                    projections[key] = (snapshot, built["span_id"])
                    projection_source = "built"
                else:
                    snapshot, source_id = projections[key]
                    with self.service.timings.span(
                        "snapshot.reuse", member_id=connection.identity.member_id,
                        connection_id=connection.connection_id,
                        source_snapshot_span_id=source_id,
                    ):
                        pass
                    projection_source = "reused"
                with self.service.timings.span(
                    "snapshot.enqueue", member_id=connection.identity.member_id,
                    connection_id=connection.connection_id,
                    projection_source=projection_source,
                    source_snapshot_span_id=projections[key][1],
                ):
                    connection.put({"type": "room.snapshot", "data": snapshot})
                    connection.put({"type": "room.synced", "data": {"seq": room.revision}})
        stream = self.streams.get(str(room_id))
        for event in events:
            if (event.type == "keeper.narration" and stream
                    and event.payload.get("cycle_id") == stream.cycle_id):
                await self.stream_end(room_id, stream.cycle_id, stream.stream_id,
                                      event_seq=event.seq, attempt=stream.attempt)
        self.presence_changed(room_id)

    async def send(self, websocket, message):
        await asyncio.wait_for(websocket.send_json(message), timeout=5)

    async def writer(self, connection):
        # Authentication/replay was captured under the room lock. No network I/O
        # holds that lock, including a slow client's first synchronization.
        for message in connection.bootstrap:
            if connection.closing:
                await connection.websocket.close(code=connection.close_code)
                return
            await self.send(connection.websocket, message)
        connection.bootstrap.clear()
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
                    with self.service.timings.span(
                        "snapshot.reconnect_build", room_id=str(room_id),
                        member_id=identity.member_id,
                        audience="host" if identity.is_host else "player",
                    ):
                        snapshot = await self.service.view(session, room, identity)
                    events = await self.service.events(session, room, identity, auth.after_seq)
                connection = Connection(websocket, identity)
                # No await between capture and subscription: a stream frame either
                # precedes this snapshot or is queued after it. Durable replay is
                # still ordered by the existing room command lock.
                connection.bootstrap = [
                    {"type": "auth.ok", "data": {"member_id": identity.member_id}},
                    {"type": "room.snapshot", "data": snapshot},
                    *[{"type": "room.event", "data": event} for event in events],
                    {"type": "room.synced", "data": {"seq": snapshot["latest_seq"]}},
                    self.stream_snapshot(room_id),
                ]
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
