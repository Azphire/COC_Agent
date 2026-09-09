"""One bounded summary attempt; failure never blocks a game cycle."""

import asyncio
import json
import re
from uuid import uuid4

from sqlalchemy import func, select

from app.agents.adjudication_schemas import SummaryRecoveryState
from app.agents.schemas import SummaryOutput
from app.domain.character import utc_now
from app.persistence.adjudication_models import SummaryRecoveryRecord
from app.persistence.agent_models import AgentCycle, AgentMemory, AgentRun, ProfileRecord
from app.rooms.service import Identity, require


class SummaryRecoveryService:
    def __init__(self, agents):
        self.agents = agents
        self.locks = {}

    async def update(self, room_id, cycle_id, manual=False):
        async with self.locks.setdefault(room_id, asyncio.Lock()):
            return await self._update(room_id, cycle_id, manual)

    async def _update(self, room_id, cycle_id, manual):
        async with self.agents.rooms.database.sessions() as session:
            bindings = await self.agents.bindings(session, room_id)
            old_states = {
                r.profile_id: SummaryRecoveryState.model_validate(r.document)
                for r in await session.scalars(
                    select(SummaryRecoveryRecord).where(SummaryRecoveryRecord.room_id == room_id)
                )
            }
        bindings.sort(
            key=lambda b: (
                not old_states.get(b.profile_id, SummaryRecoveryState()).stale,
                old_states.get(b.profile_id, SummaryRecoveryState()).last_successful_summary_seq,
                b.id,
            )
        )
        for binding in bindings:
            run_id = None

            async def prepare(session, room):
                nonlocal run_id
                cycle = await session.get(AgentCycle, cycle_id)
                ordinal = await session.scalar(
                    select(func.count())
                    .select_from(AgentCycle)
                    .where(AgentCycle.room_id == room.id)
                )
                if manual:
                    require(
                        not await self.agents.cycle(session, room.id, active=True),
                        "请等待回合结束再重建摘要",
                    )
                row = await session.get(SummaryRecoveryRecord, (room.id, binding.profile_id))
                recovery = (
                    SummaryRecoveryState.model_validate(row.document)
                    if row
                    else SummaryRecoveryState()
                )
                if not manual and (
                    recovery.last_attempted_cycle == cycle.id
                    or cycle.state.get("summary_attempted")
                    or ordinal < recovery.next_retry_cycle
                    or recovery.automatic_retry_stopped
                    or cycle.state["call_count"] >= self.agents.settings.agent_max_calls
                ):
                    return None
                profile = await session.get(ProfileRecord, binding.profile_id)
                events = await self.agents.rooms.events(
                    session, room, Identity(binding.member_id, profile.role == "keeper")
                )
                events = [
                    e
                    for e in events
                    if not e["type"].startswith(
                        ("agent.", "snapshot.", "invite.", "module.context")
                    )
                    or e["type"] in {"agent.spoke", "agent.action_proposed"}
                ]
                old = await session.scalar(
                    select(AgentMemory)
                    .where(
                        AgentMemory.room_id == room.id,
                        AgentMemory.profile_id == binding.profile_id,
                        AgentMemory.kind == "summary",
                        AgentMemory.active.is_(True),
                    )
                    .order_by(AgentMemory.created_at.desc())
                    .limit(1)
                )
                cutoff = old.coverage_end if old else 0
                eligible = [
                    e
                    for e in (
                        events
                        if manual or recovery.stale
                        else events[: -self.agents.settings.agent_event_window]
                    )
                    if e["seq"] > cutoff
                ]
                if not eligible:
                    return None
                context = {
                    "phase": "summary",
                    "previous_summary": old.content if old else None,
                    "events": [],
                }
                budget = min(
                    self.agents.settings.agent_context_chars,
                    max(
                        2500,
                        self.agents.settings.model_context_limit
                        - self.agents.settings.model_output_limit
                        - 1200,
                    ),
                )
                for event in eligible:
                    proposed = {**context, "events": [*context["events"], event]}
                    if len(json.dumps(proposed, ensure_ascii=False)) > budget:
                        break
                    context = proposed
                require(context["events"], "摘要事件超过预算")
                recovery.pending_start_seq, recovery.pending_end_seq = (
                    context["events"][0]["seq"],
                    context["events"][-1]["seq"],
                )
                recovery.last_attempted_cycle, recovery.last_attempted_time = cycle.id, utc_now()
                recovery.next_retry_cycle = ordinal + 1
                recovery.last_successful_summary_seq = cutoff
                if not manual:
                    cycle.state = {**cycle.state, "summary_attempted": True}
                run_id = str(uuid4())
                session.add(
                    AgentRun(
                        id=run_id,
                        room_id=room.id,
                        cycle_id=cycle.id,
                        profile_id=binding.profile_id,
                        actor_member_id=binding.member_id,
                        graph_node="update_summary",
                        status="running",
                        input_seq_start=recovery.pending_start_seq,
                        input_seq_end=recovery.pending_end_seq,
                        provider=self.agents.settings.model_provider,
                        model=self.agents.settings.model_name,
                        context=context,
                        tool_results=[],
                        latency_ms=0,
                    )
                )
                if row:
                    row.document = recovery.model_dump(mode="json")
                else:
                    session.add(
                        SummaryRecoveryRecord(
                            room_id=room.id,
                            profile_id=binding.profile_id,
                            document=recovery.model_dump(mode="json"),
                        )
                    )
                return context, old.id if old else None, profile.role

            try:
                prepared = await self.agents.mutate(room_id, prepare)
                if not prepared:
                    continue
                context, old_id, role = prepared
                calls = 0

                async def once():
                    nonlocal calls
                    require(calls == 0, "摘要每回合最多尝试一次")
                    calls += 1
                    if not manual:

                        async def consume(session, room):
                            cycle = await session.get(AgentCycle, cycle_id)
                            require(
                                cycle.state["call_count"] < self.agents.settings.agent_max_calls,
                                "摘要预算不足",
                            )
                            cycle.state = {
                                **cycle.state,
                                "call_count": cycle.state["call_count"] + 1,
                            }

                        await self.agents.mutate(room_id, consume)

                result, latency = await self.agents.model.generate(
                    [
                        {
                            "role": "system",
                            "content": (
                                "只依据可见事件和旧摘要更新简短摘要，区分事实与推测；"
                                "不得添加新的实体、证据或事件 ID。返回 content，不输出推理。"
                            ),
                        },
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                    ],
                    response_schema=SummaryOutput,
                    on_call=once,
                    on_result=self.agents.runtime.call_recorder(room_id, run_id),
                )

                async def save(session, room):
                    content = await self.agents.sanitize(session, room, result.structured.content)
                    source = json.dumps(context, ensure_ascii=False)
                    ids = re.findall(r"(?:event|事件|seq)[ #:=：]*(\d+)", content)
                    known_seqs = {e["seq"] for e in context["events"]}
                    if old_id:
                        previous = await session.get(AgentMemory, old_id)
                        known_seqs.update(previous.source_event_ids)
                    require(all(int(i) in known_seqs for i in ids), "摘要引用了不存在的事件", 422)
                    for identifier in re.findall(
                        r"(?:ev_|entity_|node_|block_)[\w-]+|[0-9a-f]{8}-[0-9a-f-]{27,}", content
                    ):
                        require(identifier in source, "摘要不能创建新实体或证据 ID", 422)
                    for identifier in re.findall(
                        r"(?:entity_id|evidence_id|node_id|block_id|clue_id|scene_id|event_id)[\s:=：\"']+([\w-]+)",
                        content,
                    ):
                        require(identifier in source, "摘要引用了未提供的标识", 422)
                    old = await session.get(AgentMemory, old_id) if old_id else None
                    require(not old or old.active, "摘要已被其他请求更新")
                    if old:
                        old.active = False
                    seqs = [e["seq"] for e in context["events"]]
                    session.add(
                        AgentMemory(
                            id=str(uuid4()),
                            room_id=room.id,
                            profile_id=binding.profile_id,
                            kind="summary",
                            scope="keeper_only" if role == "keeper" else "agent_private",
                            content=content,
                            source_event_ids=sorted(
                                set(seqs) | set(old.source_event_ids if old else [])
                            ),
                            salience=9,
                            supersedes_id=old_id,
                            coverage_start=old.coverage_start if old else min(seqs),
                            coverage_end=max(seqs),
                            active=True,
                        )
                    )
                    row = await session.get(SummaryRecoveryRecord, (room.id, binding.profile_id))
                    previous = SummaryRecoveryState.model_validate(row.document)
                    row.document = SummaryRecoveryState(
                        last_successful_summary_seq=max(seqs),
                        last_attempted_cycle=cycle_id,
                        last_attempted_time=previous.last_attempted_time,
                    ).model_dump(mode="json")
                    run = await session.get(AgentRun, run_id)
                    run.structured_output, run.status, run.finished_at, run.latency_ms = (
                        {"content": content},
                        "completed",
                        utc_now(),
                        latency,
                    )
                    self.agents.rooms.append(
                        session,
                        room,
                        "agent.summary_rebuilt",
                        room.host_member_id,
                        {
                            "profile_id": binding.profile_id,
                            "cycle_id": cycle_id,
                            "coverage_start": min(seqs),
                            "coverage_end": max(seqs),
                        },
                        "host_only",
                    )

                await self.agents.mutate(room_id, save)
            except Exception as error:
                from app.agents.action_policy import error_category

                safe_category = error_category(error)
                if run_id:

                    async def failed(session, room):
                        row = await session.get(
                            SummaryRecoveryRecord, (room.id, binding.profile_id)
                        )
                        recovery = SummaryRecoveryState.model_validate(row.document)
                        recovery.stale = True
                        recovery.failure_count += 1
                        recovery.last_safe_error = "摘要失败，保留旧摘要并继续游戏"
                        recovery.automatic_retry_stopped = (
                            recovery.failure_count >= self.agents.settings.summary_max_failures
                        )
                        row.document = recovery.model_dump(mode="json")
                        run = await session.get(AgentRun, run_id)
                        run.status, run.safe_error, run.error_type = (
                            "failed",
                            recovery.last_safe_error,
                            safe_category,
                        )
                        run.finished_at = utc_now()
                        self.agents.rooms.append(
                            session,
                            room,
                            "agent.summary_stale",
                            room.host_member_id,
                            {
                                "cycle_id": cycle_id,
                                "profile_id": binding.profile_id,
                                "failure_count": recovery.failure_count,
                                "automatic_retry_stopped": recovery.automatic_retry_stopped,
                            },
                            "host_only",
                        )

                    await self.agents.mutate(room_id, failed)
            # At most one attempted summary across all profiles in this cycle.
            if run_id:
                return
