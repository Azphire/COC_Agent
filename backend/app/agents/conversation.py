"""Durable serial conversation turns using the existing cycle/event tables.

Only one cycle is executable. A waiting parent can be suspended while its next
message is planned; dice endpoints reject the suspended plan. No model runs in
the room transaction, and every activation/withdrawal is atomic.
"""

import re
from uuid import uuid4

from sqlalchemy import select

from app.persistence.adjudication_models import ActionPlanRecord
from app.persistence.agent_models import AgentCycle, AgentRun, CheckRecord
from app.persistence.room_models import RoomEvent
from app.rooms.service import require


def explicit_withdrawal(text):
    """An explicit cancel command, not general intent or check classification."""
    return bool(
        re.search(r"撤回|取消", text)
        and re.search(r"不掷|尝试|检定", text)
        and not re.search(r"[?？]|能否|如何|怎么|如果|不取消|不撤回", text)
    )


def addressed_targets(text, targets):
    """Resolve an explicit salutation only, using visible people and members."""
    match = re.match(r"^\s*([\w·]{2,20})[，,：:]", text)
    if not match:
        return {}
    name = match[1]
    return {
        t["id"]: t
        for t in targets
        if t["type"] in {"npc", "member"}
        and (
            t["title"].endswith(name)
            or (name.endswith(("先生", "女士")) and t["title"].startswith(name[:-2]))
        )
    }


async def can_withdraw(session, room, pending):
    if (
        not pending
        or pending.status != "pending"
        or pending.document.get("sanity")
        or pending.document.get("settlement")
        or any(s.get("dice") for s in (pending.document.get("compound") or {}).get(
            "participants", []
        ))
    ):
        return False
    # Loading a pre-roll snapshot rewinds the projection, not fixed dice receipts.
    for event in await session.scalars(
        select(RoomEvent).where(
            RoomEvent.room_id == room.id,
            RoomEvent.type.in_(["check.dice_fixed", "check.resolved"]),
        )
    ):
        if event.payload.get("check_id", event.payload.get("id")) == pending.id:
            return False
    return True


def initial_state(room_id, cycle_id, actor, seq, bindings, *, parent=None, origin="human"):
    return {
        "room_id": room_id,
        "cycle_id": cycle_id,
        "triggering_member_id": actor,
        "triggering_event_seq": seq,
        "current_node": "collect_context",
        "request_category": "dialogue",
        "keeper_run_id": None,
        "pending_check_id": None,
        "pending_review_id": None,
        "wait_reason": None,
        "approved_entity_ids_used": [],
        "proposed_entity_ids": [],
        "revealed_entity_ids": [],
        "scene_transition": None,
        "review_count": 0,
        "review_result": None,
        "deferred_tools": [],
        "tool_results": [],
        "teammate_queue": bindings,
        "completed_teammate_ids": [],
        "call_count": 0,
        "tool_count": 0,
        "status": "running",
        "safe_error": None,
        "parent_cycle_id": parent,
        "origin": origin,
    }


async def enqueue_teammate(service, session, room, parent, event, binding):
    cycle_id = str(uuid4())
    state = initial_state(room.id, cycle_id, binding.member_id, event.seq, [], origin="teammate")
    state["related_player_cycle_id"] = parent.id
    session.add(AgentCycle(id=cycle_id, room_id=room.id, status="queued", state=state))


async def activate_next(service, session, room):
    active = await service.cycle(session, room.id, active=True)
    queued = list(
        await session.scalars(
            select(AgentCycle)
            .where(
                AgentCycle.room_id == room.id, AgentCycle.status.in_(["queued", "queued_action"])
            )
            .order_by(AgentCycle.created_at, AgentCycle.id)
        )
    )
    if active and active.status not in {"waiting_for_roll", "waiting_for_review"}:
        return active
    if active:
        # Only fresh messages may interrupt a waiting attempt. Deferred actions
        # wait until their dependency has actually finished.
        child = next((c for c in queued if c.status == "queued"), None)
        if child is None:
            return active
        pending = (
            await session.get(CheckRecord, active.state.get("pending_check_id"))
            if active.state.get("pending_check_id")
            else None
        )
        if pending and pending.status == "resolved":
            return active
        original_status = active.status
        active.status = "suspended"
        active.state = {**active.state, "suspended_status": original_status}
        child.state = {
            **child.state,
            "parent_cycle_id": active.id,
            "conversation_parent": {
                "cycle_id": active.id,
                "actor_member_id": active.state["triggering_member_id"],
                "check": {
                    k: service.check_public(pending.document).get(k)
                    for k in ("id", "reason", "status", "display_name", "result", "settlement")
                }
                if pending
                else None,
                "can_withdraw": await can_withdraw(session, room, pending),
            },
        }
        if pending and pending.document.get("settlement"):
            child.state["conversation_parent"]["check"]["settlement"] = {
                k: pending.document["settlement"].get(k)
                for k in ("stage", "original_result", "push_requested", "review")
            }
        await session.flush()
    else:
        suspended = await session.scalar(
            select(AgentCycle)
            .where(AgentCycle.room_id == room.id, AgentCycle.status == "suspended")
            .order_by(AgentCycle.created_at.desc())
        )
        if suspended:
            suspended.status = suspended.state["suspended_status"]
            service.cycle_event(session, room, suspended)
            return suspended
        child = next(iter(queued), None)
        if child is None:
            return None
        if child.status == "queued_action":
            # A queued plan was made before the result. Retain its audit but
            # replan against the settled world, without an extra classifier.
            record = await session.get(ActionPlanRecord, child.id)
            if record:
                run = await session.get(AgentRun, record.run_id)
                run.graph_node = "deferred_plan_keeper_action"
                await session.delete(record)
            child.state = {**child.state, "restart_plan": True, "keeper_run_id": None}
        child.state = {**child.state, "parent_cycle_id": None, "conversation_parent": None}
    child.status = "running"
    child.state = {**child.state, "status": "running"}
    service.cycle_event(session, room, child)
    return child


async def route(runtime, state):
    async def operation(session, room):
        child = await session.get(AgentCycle, state["cycle_id"])
        if child.state.get("conversation_routed") or not child.state.get("parent_cycle_id"):
            return child.state
        record = await session.get(ActionPlanRecord, child.id)
        from app.agents.adjudication_schemas import AdjudicationRecord

        doc = AdjudicationRecord.model_validate(record.document)
        plan = doc.plan
        parent = await session.get(AgentCycle, child.state["parent_cycle_id"])
        pending_id = parent.state.get("pending_check_id")
        pending = await session.get(CheckRecord, pending_id) if pending_id else None
        choice = plan.pending_action
        if choice in {"replace", "withdraw"}:
            allowed = bool(
                parent.status == "suspended"
                and pending
                and await can_withdraw(session, room, pending)
                and child.state["triggering_member_id"] == pending.target_member_id
            )
            if allowed:
                pending.document = {**pending.document, "superseded_by": child.id}
                await runtime.service.cancel_cycle(session, room, parent)
                for queued in await session.scalars(
                    select(AgentCycle).where(
                        AgentCycle.room_id == room.id, AgentCycle.status == "queued_action"
                    )
                ):
                    if queued.state.get("parent_cycle_id") == parent.id:
                        queued.status = "cancelled"
                        queued.state = {**queued.state, "status": "cancelled"}
                child.state = {**child.state, "parent_cycle_id": None}
                if choice == "withdraw":
                    plan.proposed_check = None
                    plan.proposed_tool_calls = []
                    plan.proposed_reveal_entity_ids = []
                    plan.proposed_transition_id = None
                    reply = "好，这次尚未掷骰的尝试已撤回。你可以继续说话或换个做法。"
                    child.state = {**child.state, "withdrawal_result": {
                        "check_id": pending.id, "withdrawn": True, "message": reply,
                    }}
                    if not plan.focus or not (plan.focus.question or plan.focus.addressee_id):
                        plan.parsed_intent.type = "out_of_character"
                        child.state = {**child.state, "conversation_reply": reply}
                        runtime.rooms.append(
                            session, room, "keeper.narration", room.host_member_id,
                            {"text": reply, "cycle_id": child.id},
                            request_id=child.id + ":withdraw",
                        )
            else:
                choice = "independent"
                plan.parsed_intent.type = (
                    "converse" if plan.focus and plan.focus.addressee_id else "out_of_character"
                )
                plan.proposed_check = None
                plan.proposed_tool_calls = []
                plan.proposed_reveal_entity_ids = []
                plan.proposed_transition_id = None
                plan.next_decision = "原骰或遭遇已经发生，不能撤销；仍可选择当前可用的结算选项。"
                child.state = {**child.state, "withdrawal_result": {
                    "withdrawn": False, "message": plan.next_decision,
                }}
        if choice == "independent" and (
            plan.proposed_check
            or plan.proposed_reveal_entity_ids
            or plan.proposed_transition_id
            or any(
                t.name not in {"search_rules", "get_public_scene", "inspect_character"}
                for t in plan.proposed_tool_calls
            )
        ):
            choice = "defer"
        child.state = {**child.state, "conversation_routed": True}
        record.document = doc.model_dump(mode="json")
        if choice == "defer":
            child.status = "queued_action"
            child.state = {**child.state, "status": "queued_action"}
            runtime.rooms.append(
                session,
                room,
                "keeper.narration",
                room.host_member_id,
                {
                    "text": "这一步等眼前的结果确定后再继续。你仍可以补充方法或和同伴交谈。",
                    "cycle_id": child.id,
                    "deferred": True,
                },
                request_id=child.id + ":defer",
            )
            await session.flush()
            await activate_next(runtime.service, session, room)
        return child.state

    return await runtime.service.mutate(state["room_id"], operation)


async def ensure_no_earlier_message(session, room, cycle):
    queued = await session.scalar(
        select(AgentCycle).where(AgentCycle.room_id == room.id, AgentCycle.status == "queued")
    )
    require(not queued, "KP正在处理已接受的补充，请等回应后按新方案掷骰")
