"""Frozen source-backed handouts, addressed to a member rather than an actor."""

import json
from uuid import uuid4

from sqlalchemy import select

from app.auth import digest
from app.persistence.room_models import RoomEvent
from app.rooms.schemas import SessionStateV1
from app.rooms.service import event_view, require


def character_view(room, slot, member_id, *, keeper=False):
    """A card selection is not a private handout delivery authorization."""
    require(keeper or slot.member_id == member_id, "只能读取本人的私人角色资料", 403)
    card = slot.character_snapshot
    if keeper or not card.get("module_handout"):
        return card
    selection = card["module_handout"]
    delivered = any(
        item["member_id"] == member_id and item["slot_id"] == slot.id
        and item["handout_id"] == selection["handout_id"]
        and item["preparation_id"] == selection["preparation_id"]
        for item in room.session_state.get("handout_assignments", [])
    )
    if delivered:
        return card
    return {**card, "module_handout": None, "module_handout_approval": None,
            "module_handout_effects": []}


def private_handouts(room, member_id, *, keeper=False):
    """Pinned private memories survive history windows and snapshot restoration."""
    return [
        {
            "handout_id": item["handout_id"],
            "member_id": item["member_id"],
            "title": item["title"],
            "text": item["text"],
            "source_pages": item["source_pages"],
            "source_event_seq": item["assigned_event_seq"],
        }
        for item in room.session_state.get("handout_assignments", [])
        if keeper or item["member_id"] == member_id
    ]


async def assigned_recipient(session, room_id, slot_id):
    # A rewind cannot make a disclosed secret unknown to its previous reader.
    # Keep the recipient lock in the append-only ledger, including old branches.
    event = await session.scalar(
        select(RoomEvent).where(
            RoomEvent.room_id == room_id,
            RoomEvent.type == "handout.assigned",
            RoomEvent.payload["assignment"]["slot_id"].as_string() == slot_id,
        ).order_by(RoomEvent.seq)
    )
    return event.payload["recipient_member_id"] if event else None


async def assign_handout(rooms, session, room, identity, body):
    require(identity.is_host, "仅主机可以分配 HO", 403)
    require(room.status in {"lobby", "paused"}, "请在大厅或暂停后分配 HO")
    if rooms.agent_service:
        require(
            not await rooms.agent_service.cycle(session, room.id, active=True),
            "请先等待或取消活动 Agent 回合",
        )
    state = SessionStateV1.model_validate(room.session_state)
    catalog = state.handout_catalog
    require(catalog is not None, "房间未绑定包含 HO 的准备包", 422)
    handout = next((h for h in catalog["handouts"] if h["id"] == body.handout_id), None)
    require(handout is not None, "准备包中不存在此 HO", 404)
    slots = await rooms.slots(session, room)
    slot = next((s for s in slots if s.id == str(body.slot_id)), None)
    require(slot is not None, "角色席位不存在", 404)
    require(slot.member_id == str(body.member_id), "HO 接收者必须是此角色当前成员", 422)
    members = await rooms.members(session, room)
    require(
        any(m.id == slot.member_id and m.active and m.role == "player" for m in members),
        "HO 接收成员不存在或已离开", 422,
    )
    selection = slot.character_snapshot.get("module_handout")
    if handout.get("adjustments") or selection:
        require(
            selection
            and selection.get("preparation_id") == catalog["preparation_id"]
            and selection.get("handout_id") == handout["id"]
            and selection.get("definition") == handout
            and slot.character_snapshot.get("module_handout_approval"),
            "此 HO 与冻结角色的已核准方案不一致，请先完成对应 HO 建卡", 422,
        )
    request_hash = digest(
        "handout.assign" + json.dumps(body.model_dump(mode="json"), sort_keys=True)
    )
    receipt = await rooms.receipt(
        session, room, identity.member_id, body.client_request_id, request_hash,
    )
    historical = list(await session.scalars(select(RoomEvent).where(
        RoomEvent.room_id == room.id,
        RoomEvent.type == "handout.assigned",
    )))
    for event in historical:
        old = event.payload["assignment"]
        same_handout = (
            old["source_hash"] == catalog["source_hash"]
            and old["handout_id"] == handout["id"]
        )
        if same_handout or old["slot_id"] == slot.id:
            require(
                same_handout
                and old["slot_id"] == slot.id
                and old["member_id"] == slot.member_id,
                "此 HO 或角色已有接收者；读档不能撤回已披露秘密或转配他人", 409,
            )
    existing = next((a for a in state.handout_assignments if a["slot_id"] == slot.id), None)
    if existing:
        require(
            existing["handout_id"] == handout["id"]
            and existing["member_id"] == slot.member_id,
            "角色已分配其他 HO", 409,
        )
        return {"handout_assignment": existing, "already_assigned": True}
    if receipt:
        assignment = receipt.payload["assignment"]
        state.handout_assignments.append(assignment)
        room.session_state = state.model_dump(mode="json")
        restored = rooms.append(
            session, room, "handout.restored", identity.member_id,
            {"assignment": assignment, "source_event_seq": receipt.seq},
            "recipient_and_host", recipient_member_id=slot.member_id,
        )
        return {"handout_assignment": assignment, "event": event_view(restored)}
    assignment = {
        "id": str(uuid4()),
        "handout_id": handout["id"],
        "slot_id": slot.id,
        "member_id": slot.member_id,
        "preparation_id": catalog["preparation_id"],
        "source_id": catalog["source_id"],
        "source_hash": catalog["source_hash"],
        "title": handout["title"],
        "text": handout["text"],
        "source_pages": handout["source_pages"],
        "source_block_ids": handout["source_block_ids"],
        "assigned_event_seq": room.revision + 1,
    }
    event = rooms.append(
        session, room, "handout.assigned", identity.member_id,
        {"assignment": assignment}, "recipient_and_host",
        str(body.client_request_id), request_hash, recipient_member_id=slot.member_id,
    )
    state.handout_assignments.append(assignment)
    room.session_state = state.model_dump(mode="json")
    return {"handout_assignment": assignment, "event": event_view(event)}
