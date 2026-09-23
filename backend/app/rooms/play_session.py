"""A host explicitly plays one local human with the ordinary player projection."""

import hashlib
import hmac

from sqlalchemy import select

from app.persistence.room_models import RoomMember
from app.rooms.service import Identity, require


def local_token(settings, room_id, member_id):
    message = f"local-player:{room_id}:{member_id}"
    key = settings.host_admin_token.get_secret_value().encode()
    signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return f"local.{member_id}.{signature}"


async def local_identity(rooms, session, room, token):
    if not token.startswith("local."):
        return None
    parts = token.split(".")
    require(len(parts) == 3, "本地玩家凭据无效", 401)
    member = await session.get(RoomMember, parts[1])
    require(
        member
        and member.room_id == room.id
        and member.active
        and member.role == "player"
        and member.controller_type == "human"
        and member.access_type == "host_managed"
        and bool(rooms.settings.host_admin_token.get_secret_value())
        and hmac.compare_digest(token, local_token(rooms.settings, room.id, member.id)),
        "本地玩家凭据无效",
        401,
    )
    return Identity(member.id, False)


async def play_session(rooms, room_id, member_id=None):
    async with rooms.lock(str(room_id)), rooms.database.sessions() as session:
        room = await rooms.room(session, room_id)
        members = list(
            await session.scalars(
                select(RoomMember)
                .where(
                    RoomMember.room_id == room.id,
                    RoomMember.active.is_(True),
                    RoomMember.role == "player",
                    RoomMember.controller_type == "human",
                    RoomMember.access_type == "host_managed",
                )
                .order_by(RoomMember.joined_at, RoomMember.id)
            )
        )
        selected = next((m for m in members if m.id == str(member_id)), None)
        if selected is None and len(members) == 1:
            selected = members[0]
        return {
            "local_members": [{"id": m.id, "display_name": m.display_name} for m in members],
            "selected_member_id": selected.id if selected else None,
            "member_token": local_token(rooms.settings, room.id, selected.id) if selected else None,
            "room": await rooms.view(session, room, Identity(selected.id, False))
            if selected
            else None,
        }
