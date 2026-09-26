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
    from app.preparation.action_authority import address_candidates

    people = {t["id"]: t["title"] for t in targets if t["type"] in {"npc", "member"}}
    found = address_candidates(text, people)
    owners = found[0][0] if found and not text[:found[0][1]].strip() else set()
    return {t["id"]: t for t in targets if t["id"] in owners}


async def movement_reply(service, session, room, actor, raw, clarification_seq=None):
    """Bind a short destination answer to its actual, still-local pending move."""
    from app.agents.action_policy import explicit_movement

    if re.search(r"[?？]|取消|不去|不走|不再|改为|先问", raw):
        return None
    if not re.search(r"车厢|门|另一端|内部|大厅|走廊|房间|楼|室|出口", raw):
        return None
    clarification = (
        await session.get(RoomEvent, (room.id, clarification_seq))
        if clarification_seq
        else await session.scalar(
            select(RoomEvent)
            .where(RoomEvent.room_id == room.id, RoomEvent.type == "action.clarification_requested")
            .order_by(RoomEvent.seq.desc())
            .limit(1)
        )
    )
    if not clarification or clarification.payload.get("actor_member_id") != actor:
        return None
    cycle = await session.get(AgentCycle, clarification.payload.get("cycle_id"))
    if not cycle:
        return None
    original = await session.get(RoomEvent, (room.id, cycle.state["triggering_event_seq"]))
    latest = await session.scalar(
        select(RoomEvent)
        .where(RoomEvent.room_id == room.id, RoomEvent.type == "action.submitted")
        .order_by(RoomEvent.seq.desc())
        .limit(1)
    )
    if (
        not original
        or latest.seq != original.seq
        or not explicit_movement(original.payload["text"])
    ):
        return None
    record = await session.get(ActionPlanRecord, cycle.id)
    nav = await service.navigation.state(session, room.id)
    if (
        not record
        or not nav
        or record.document["plan"]["current_scene_id"] != nav.current_scene_node_id
        or record.document["plan"].get("expected_navigation_revision") != nav.navigation_revision
    ):
        return None
    text = (
        raw
        if explicit_movement(raw)
        else ("我潜行前往" if "潜行" in original.payload["text"] else "我前往") + raw
    )
    return {
        "text": text,
        "submitted_text": raw,
        "clarification_source_seq": original.seq,
        "clarification_original_text": original.payload["text"],
        "clarification_event_seq": clarification.seq,
    }


async def can_withdraw(session, room, pending):
    if (
        not pending
        or pending.status != "pending"
        or pending.document.get("sanity")
        or pending.document.get("settlement")
        or any(
            s.get("dice") for s in (pending.document.get("compound") or {}).get("participants", [])
        )
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


async def enqueue_teammate(service, session, room, parent, event, binding, requests=()):
    cycle_id = str(uuid4())
    state = initial_state(room.id, cycle_id, binding.member_id, event.seq, [], origin="teammate")
    state["related_player_cycle_id"] = parent.id
    state["request_keys"] = [r["key"] for r in requests]
    from app.preparation.action_authority import action_kinds

    state["teammate_attempt"] = {
        "target_id": event.payload.get("target_id"),
        "operations": sorted(set(action_kinds(event.payload.get("text", "")))
                             - {"converse", "pass"}),
    }
    if requests:
        from app.agents.task_receipts import bind_task_operands, current_task_targets
        from app.preparation.inventory import inventory_context

        inventory = await inventory_context(service, session, room,
                                            "\n".join(r.get("text", "") for r in requests))
        targets = await current_task_targets(
            session, room.id, await service.entities.public(session, room.id),
        )
        state["request_operands"] = {
            r["key"]: bind_task_operands(r, inventory, binding.member_id,
                                        parent.state.get("triggering_member_id"), targets=targets)
            for r in requests
        }
    if service.combat.route(room, event.payload["text"]):
        state.update(combat_flow=True, combat_actor_id=binding.member_id)
    session.add(AgentCycle(id=cycle_id, room_id=room.id, status="queued", state=state))
    return cycle_id


async def cancelled_request(session, cycle):
    """A queued attempt cannot outlive cancellation of its originating tasks."""
    from app.persistence.adjudication_models import AgentBehaviorRecord

    keys = set(cycle.state.get("request_keys", []))
    if not keys:
        return False
    row = await session.scalar(select(AgentBehaviorRecord).where(
        AgentBehaviorRecord.room_id == cycle.room_id,
        AgentBehaviorRecord.member_id == cycle.state["triggering_member_id"],
    ))
    retired = {r["key"] for r in (row.document if row else {}).get("request_history", [])
               if r.get("status") in {"cancelled", "superseded"}}
    return keys <= retired


async def activate_next(service, session, room):
    await settle_teammate_tasks(service, session, room)
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
    for child in list(queued):
        if await cancelled_request(session, child):
            child.status = "cancelled"
            child.state = {**child.state, "status": "cancelled", "cancelled_request": True}
            service.cycle_event(session, room, child)
            queued.remove(child)
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


async def settle_teammate_tasks(service, session, room):
    """Feed actual serial subcycle receipts back into saved behavior, once.

    Read-only observations can finish without any state mutation. A check failure
    or rejected operation remains blocked, with its actual public feedback.
    """
    from app.agents.adjudication_schemas import BehaviorState
    from app.persistence.adjudication_models import AgentBehaviorRecord

    for row in await session.scalars(
        select(AgentBehaviorRecord).where(AgentBehaviorRecord.room_id == room.id)
    ):
        behavior = BehaviorState.model_validate(row.document)
        if behavior.task_status != "proposed" or not behavior.task_cycle_id:
            continue
        cycle = await session.get(AgentCycle, behavior.task_cycle_id)
        if not cycle or cycle.status not in {"completed", "failed", "cancelled"}:
            continue
        public_events = list(await session.scalars(
                select(RoomEvent)
                .where(RoomEvent.room_id == room.id, RoomEvent.visibility == "public")
                .order_by(RoomEvent.seq)
            ))
        from app.memory.events import story_events

        active_story, _ = story_events([
            {"seq": e.seq, "type": e.type, "payload": e.payload,
             "visibility": e.visibility, "actor_member_id": e.actor_member_id}
            for e in public_events
        ], include_initial_reveals=True)
        active_seqs = {e["seq"] for e in active_story}
        events = [e for e in public_events if e.seq in active_seqs
                  and e.payload.get("cycle_id") == cycle.id]
        feedback = [
            e
            for e in events
            if e.type
            in {"keeper.narration", "check.resolved", "module.interaction", "combat.resolved",
                "entity.revealed", "clue.revealed"}
        ]
        record = await session.get(ActionPlanRecord, cycle.id)
        rejected = (
            (record.document.get("validation") or {}).get("rejected_actions", []) if record else []
        )
        checks = list(
            await session.scalars(select(CheckRecord).where(CheckRecord.cycle_id == cycle.id))
        )
        failed_check = any((c.document.get("result") or {}).get("passed") is False for c in checks)
        # Medical attempts settle in the combat receipts, not pending_checks.
        # Completing that subcycle does not mean the treatment succeeded.
        failed_check = failed_check or any(
            e.type == "combat.resolved"
            and e.payload.get("operation") in {"first_aid", "medicine"}
            and e.payload.get("rolls", {}).get("treatment", {}).get("result", {}).get("passed")
            is False
            for e in events
        )
        public_results = (
            await service.runtime.public_results(session, room, cycle)
            if record and service.runtime
            else {}
        )
        if not record:
            from app.agents.results import result_facts

            public_results["current_result_facts"] = result_facts([
                {"seq": e.seq, "type": e.type, "payload": e.payload,
                 "actor_member_id": e.actor_member_id} for e in events
            ])
        narration_validation = (record.document.get("narration_validation") or {}) if record else {}
        narration_text = ((record.document.get("narration") or {}).get("public_narration", "")
                          if record else "")
        observation_reply = next((e for e in reversed(feedback)
                                  if e.type == "keeper.narration"
                                  and not e.payload.get("safe_fallback")
                                  and e.payload.get("text", "").strip()
                                  and e.payload.get("text") == narration_text), None)
        # public_results' flag describes what the plan could observe. Only this
        # child's validated, published response proves a completed read-only
        # observation; a proposal or an earlier already-revealed event does not.
        observation_completed = bool(
            public_results.get("observation_completed")
            and not public_results.get("blocked_discovery")
            and not public_results.get("failed_tools")
            and narration_validation.get("valid") is True
            and narration_validation.get("answer_complete") is not False
            and observation_reply
        )
        if narration_validation.get("answer_origin") == "mixed":
            # Completion means this inspection was performed and reported, not
            # that every queried property became a known world fact.
            requests = cycle.state.get("request_operands", {})
            relevant = [r for r in behavior.pending_requests
                        if r.get("key") in cycle.state.get("request_keys", [])]
            actual = public_results.get("current_result_facts", [])
            observation_completed = observation_completed and bool(relevant) and all(
                any(f.get("status") == "success" and f.get("cycle_id") == cycle.id
                    and f.get("operation") in {"search", "reveal", "observe"}
                    and f.get("actor_id") == row.member_id
                    and f.get("source_action_seq") == cycle.state.get("triggering_event_seq")
                    and f.get("source_event_seq") in active_seqs
                    and (f.get("action_target_id") or f.get("target_id"))
                    == requests.get(r["key"], r).get("target_id")
                    for f in actual)
                and requests.get(r["key"], r).get("executor_member_id", row.member_id)
                    == row.member_id for r in relevant
            )
        settled = bool(observation_completed) or any(
            e.type in {
                "check.resolved", "module.interaction", "combat.resolved",
                "entity.revealed", "clue.revealed",
            }
            for e in events
        )
        narration_failed = narration_validation.get("valid") is False
        technical = cycle.status != "cancelled" and not settled and (
            cycle.status == "failed" or cycle.state.get("teammate_attempt_failed")
            or narration_failed
        )
        blocked = bool(
            (rejected and not observation_completed)
            or failed_check
            or cycle.state.get("combat_rejection")
            or cycle.state.get("requires_clarification")
            or cycle.status == "cancelled"
            or not feedback
        )
        from app.agents.task_receipts import receipt_progress, restore_task_operands

        result_seqs = {e.seq for e in events if e.type in {
            "check.resolved", "module.interaction", "combat.resolved", "scene.updated",
            "entity.revealed", "clue.revealed",
        }}
        facts = [f for f in public_results.get("current_result_facts", [])
                 if f.get("source_event_seq") in active_seqs
                 and (f.get("status") not in {"success", "failure"}
                      or f.get("source_event_seq") in result_seqs)]
        public_results["current_result_facts"] = facts
        if observation_completed:
            plan = record.document.get("plan") or {}
            focus = plan.get("focus") or {}
            from app.preparation.action_authority import action_kinds

            operations = set((plan.get("action_authority") or {}).get("kinds", [])
                             or action_kinds(focus.get("action", ""))) & {"observe", "search"}
            # A physically performed inspection also observes its target; only
            # this child's validated formal answer can settle a read-only check.
            operations.add("observe")
            for operation in sorted(operations):
                facts.append({
                    "operation": operation, "status": "success", "actor_id": row.member_id,
                    "cycle_id": cycle.id, "target_id": focus.get("action_target_id")
                    or (plan.get("parsed_intent") or {}).get("target_id"),
                    "source_event_seq": observation_reply.seq,
                })
        outcomes, consumed = {}, set()
        for request in behavior.pending_requests:
            if request.get("key") not in cycle.state.get("request_keys", []):
                continue
            frozen = cycle.state.get("request_operands", {}).get(request["key"], {})
            operands = restore_task_operands(request, frozen)
            outcomes[request["key"]] = receipt_progress(
                operands, facts, actor=row.member_id, cycle_id=cycle.id, consumed=consumed,
            )
        confirmed = bool(outcomes) and all(done for _, done in outcomes.values())
        confirmed = confirmed and not any(
            r.get("kind") == "delegate" and r.get("key") not in outcomes
            for r in behavior.pending_requests
        )
        if not outcomes and not behavior.pending_requests:
            confirmed = observation_completed or any(
                f.get("status") == "success" and f.get("operation") != "check"
                and f.get("actor_id") == row.member_id for f in facts
            )
        behavior.task_status = (
            "generation_failed"
            if technical
            else "cancelled"
            if cycle.status == "cancelled"
            else "blocked"
            if blocked
            else "completed"
            if confirmed
            else "attempted"
        )
        behavior.last_result = {
            "result_facts": public_results.get("current_result_facts", []),
            "cycle_id": cycle.id,
            "kind": behavior.task_status,
            "event_seqs": [e.seq for e in feedback],
            "narration_complete": bool(
                observation_reply and narration_validation.get("valid") is True
                and narration_validation.get("answer_complete") is not False
            ),
            "unknown_properties": [
                {"object_id": a["object_id"], "question": a["question"], "status": "unknown"}
                for a in narration_validation.get("evidence_assessments", [])
                if a and a.get("state") == "not_confirmed"
            ],
            "text": "" if technical else "\n".join(
                e.payload.get("text") or e.payload.get("display_text")
                or e.payload.get("summary") or e.payload.get("public_summary")
                or e.payload.get("content", "") for e in feedback[-2:]
            )[:1000],
            "reason": cycle.state.get("safe_error") or "本次生成未得到有效行动反馈"
            if technical
            else "; ".join(r.get("reason", "") for r in rejected)[:300]
            or ("未得到本次尝试的有效公开反馈" if not feedback else ""),
        }
        if not technical:
            from app.preparation.action_authority import action_kinds

            action = cycle.state.get("combat_decision") or {}
            operations = {action["operation"]} if action.get("operation") else set()
            if record:
                plan = record.document.get("plan", {})
                operations.update(action_kinds((plan.get("focus") or {}).get("action", "")))
            if "search" in operations:
                operations.add("observe")
            behavior.last_result = {**behavior.last_result, "operations": sorted(operations)}
            behavior.last_attempt_result = dict(behavior.last_result)
            parent = await session.get(AgentCycle, cycle.state.get("related_player_cycle_id"))
            if parent:
                pending = []
                for request in behavior.pending_requests:
                    if request.get("key") not in outcomes:
                        pending.append(request)
                        continue
                    remaining, completed = outcomes[request["key"]]
                    if (request.get("kind") == "delegate"
                            and behavior.task_status != "cancelled"
                            and not completed):
                        pending.append({**remaining,
                                        "last_result_kind": behavior.task_status})
                    else:
                        behavior.request_history = [*behavior.request_history, {
                            **request, "status": "completed" if completed else behavior.task_status,
                            "result_cycle_id": cycle.id,
                            "result_event_seqs": [e.seq for e in feedback],
                        }][-24:]
                behavior.pending_requests = pending
        else:
            attempt = cycle.state.get("teammate_attempt", {})
            behavior.pending_requests = [
                {**restore_task_operands(
                    r, cycle.state.get("request_operands", {}).get(r.get("key"), {})),
                 "last_result_kind": "generation_failed",
                 "technical_failure": {
                     "cycle_id": cycle.id, "target_id": attempt.get("target_id"),
                     "operations": attempt.get("operations", []), "executed": False,
                 }}
                if r.get("key") in cycle.state.get("request_keys", []) else r
                for r in behavior.pending_requests
            ]
        if behavior.task_status == "completed" and not behavior.pending_requests:
            behavior.current_short_term_goal = ""
        row.document = behavior.model_dump(mode="json")
        service.rooms.append(
            session,
            room,
            "agent.teammate_task_updated",
            room.host_member_id,
            {"member_id": row.member_id, **behavior.last_result},
            "host_only",
        )


async def settle_report_supplement(service, session, room, cycle, run, report):
    """Settle only the original request against its execution and new report."""
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.task_receipts import receipt_progress, restore_task_operands
    from app.memory.events import story_events
    from app.persistence.adjudication_models import AgentBehaviorRecord
    from app.rooms.service import Identity, require

    validation = run.context.get("narration_validation", {})
    require(run.status == "completed" and cycle.status in {"completed", "failed"}
            and run.cycle_id == cycle.id and report.type == "keeper.narration"
            and report.payload.get("cycle_id") == cycle.id
            and report.payload.get("supplement_of") == run.context.get("supplement_of")
            and report.payload.get("text") == (run.structured_output or {}).get("public_narration")
            and not report.payload.get("safe_fallback") and validation.get("valid")
            and validation.get("answer_complete"), "补述未形成匹配的正式完整报告")
    events = await service.rooms.events(session, room, Identity(room.host_member_id, True))
    active, _ = story_events(events, include_initial_reveals=True)
    active_seqs = {e["seq"] for e in active if e.get("visibility") == "public"}
    require(report.seq in active_seqs, "补述报告不在当前公开记录中")
    results = await service.runtime.public_results(session, room, cycle)
    require(not results.get("failed_tools") and not results.get("blocked_discovery"),
            "原执行仍受阻，不能仅凭补述结清")
    executor = cycle.state["triggering_member_id"]
    row = await session.get(AgentBehaviorRecord, (room.id, executor))
    if not row:
        return
    behavior = BehaviorState.model_validate(row.document)
    requests = run.context.get("request_operands", {})
    plan_record = await session.get(ActionPlanRecord, cycle.id)
    plan = plan_record.document.get("plan", {}) if plan_record else {}
    from app.preparation.action_authority import action_kinds

    executed_operations = set((plan.get("action_authority") or {}).get("kinds", [])
                              or action_kinds((plan.get("focus") or {}).get("action", "")))
    executed_operations &= {"observe", "search"}
    if "search" in executed_operations:
        executed_operations.add("observe")
    pending, completed, consumed = [], [], set()
    for request in behavior.pending_requests:
        original = requests.get(request.get("key"))
        if not original or request.get("key") not in run.context.get("request_keys", []):
            pending.append(request)
            continue
        stable = ("key", "source_event_seq", "executor_member_id",
                  "requester_member_id", "target_id")
        require(all(request.get(k) == original.get(k) for k in stable),
                "原委派的请求或执行对象已变化，不能借用旧回执结清")
        actual = [f for f in results.get("current_result_facts", [])
                  if f.get("status") == "success" and f.get("cycle_id") == cycle.id
                  and f.get("operation") in {"observe", "search", "reveal"}
                  and f.get("actor_id") == executor and f.get("source_event_seq") in active_seqs
                  and f.get("source_action_seq") == cycle.state.get("triggering_event_seq")
                  and (f.get("action_target_id") or f.get("target_id"))
                  == original.get("target_id")]
        require(actual and original.get("executor_member_id", executor) == executor,
                "原委派没有匹配执行者、目标和回合的有效回执")
        facts = [*results["current_result_facts"], *[{
            "operation": operation, "status": "success", "actor_id": executor,
            "cycle_id": cycle.id, "target_id": original["target_id"],
            "source_event_seq": report.seq, "original_receipt_event_seqs": [
                f["source_event_seq"] for f in actual], "report_supplement_id": run.id,
        } for operation in sorted(executed_operations)]]
        remaining, done = receipt_progress(restore_task_operands(request, original), facts,
                                           actor=executor, cycle_id=cycle.id, consumed=consumed)
        if done:
            completed.append({**request, "status": "completed", "result_cycle_id": cycle.id,
                              "result_event_seqs": [report.seq], "supplement_id": run.id,
                              "supplement_of": run.context["supplement_of"]})
        else:
            pending.append(remaining)
    if not completed:
        return
    behavior.pending_requests = pending
    behavior.request_history = [*behavior.request_history, *completed][-24:]
    feedback = {"cycle_id": cycle.id, "kind": "completed", "narration_complete": True,
                "event_seqs": [report.seq], "result_facts": results["current_result_facts"],
                "supplement_id": run.id, "supplement_of": run.context["supplement_of"],
                "text": report.payload["text"][:1000],
                "unknown_properties": [
                    {"object_id": a["object_id"], "question": a["question"], "status": "unknown"}
                    for a in validation.get("evidence_assessments", [])
                    if a and a.get("state") == "not_confirmed"],
                "operations": sorted({op for r in completed for op in r.get("operations", [])})}
    # A later action may already own the current behavior state. Only the old
    # request's history changes in that case; its new task remains untouched.
    if behavior.task_cycle_id == cycle.id:
        behavior.task_status = "completed" if not pending else "attempted"
        behavior.last_result = feedback
        behavior.last_attempt_result = dict(feedback)
        if not pending:
            behavior.current_short_term_goal = ""
    row.document = behavior.model_dump(mode="json")
    service.rooms.append(session, room, "agent.teammate_task_updated", room.host_member_id,
                         {"member_id": executor, **feedback}, "host_only")


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
                    child.state = {
                        **child.state,
                        "withdrawal_result": {
                            "check_id": pending.id,
                            "withdrawn": True,
                            "message": reply,
                        },
                    }
                    if not plan.focus or not (plan.focus.question or plan.focus.addressee_id):
                        plan.parsed_intent.type = "out_of_character"
                        child.state = {**child.state, "conversation_reply": reply}
                        runtime.rooms.append(
                            session,
                            room,
                            "keeper.narration",
                            room.host_member_id,
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
                child.state = {
                    **child.state,
                    "withdrawal_result": {
                        "withdrawn": False,
                        "message": plan.next_decision,
                    },
                }
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
