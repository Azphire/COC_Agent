"""Public, extractive rule answers with no action plan or gameplay tools."""

import re
import time
from uuid import uuid4

from sqlalchemy import select

from app.domain.character import utc_now
from app.persistence.agent_models import AgentCycle, AgentRun, ProfileRecord
from app.persistence.room_models import RoomEvent
from app.rooms.service import require
from app.rules.topics import RuleTopicRegistry


def question_parts(question):
    # Preserve unknown clauses, including those adjacent to a recognized topic.
    # Never let lexical topic extraction silently discard the rest of a question.
    return list(
        dict.fromkeys(
            p.strip() for p in re.split(r"[？?；;。\n]|以及|并且|另外|还有", question) if p.strip()
        )
    )


async def answer_rule_question(runtime, state):
    service = runtime.service
    started = time.monotonic()

    async def answer(session, room):
        cycle = await session.get(AgentCycle, state["cycle_id"])
        require(room.status == "running" and cycle.status == "running", "请先恢复游戏")
        existing = await session.scalar(
            select(AgentRun).where(
                AgentRun.cycle_id == cycle.id, AgentRun.graph_node == "answer_rule_question"
            )
        )
        if existing and existing.status == "completed":
            return
        trigger = await session.get(RoomEvent, (room.id, state["triggering_event_seq"]))
        require(trigger and trigger.type == "rules.question", "规则问题不存在")
        binding = next(
            b
            for b in await service.bindings(session, room.id)
            if b.member_id == room.host_member_id
        )
        profile = await session.get(ProfileRecord, binding.profile_id)
        run_id = str(uuid4())
        question = trigger.payload["text"]
        run = AgentRun(
            id=run_id,
            room_id=room.id,
            cycle_id=cycle.id,
            profile_id=profile.id,
            actor_member_id=room.host_member_id,
            graph_node="answer_rule_question",
            status="running",
            input_seq_start=trigger.seq,
            input_seq_end=trigger.seq,
            provider="deterministic",
            model="public_rule_evidence",
            context={"question": question, "RULE_EVIDENCE": []},
            tool_results=[],
            latency_ms=0,
        )
        session.add(run)
        await session.flush()
        answers, citations = [], {}
        configured = await service.knowledge.binding(session, room.id)
        for part in question_parts(question):
            evidence, audit = [], None
            if configured and configured.get("enabled"):
                evidence, audit = await service.knowledge.search(
                    session,
                    room,
                    run_id=run_id,
                    profile=profile,
                    actor_id=trigger.actor_member_id,
                    query=part,
                    kind="rules",
                    public_only=True,
                    allow_structured=RuleTopicRegistry.covers_question(part),
                )
            # All returned excerpts belong to the room's frozen public rule sources.
            # They are the answer itself, so no model can attach invented facts to citations.
            ids = []
            for item in evidence:
                if len(item["excerpt"].strip()) < 12:
                    continue
                ids.append(item["evidence_id"])
                citations[item["evidence_id"]] = item
            if audit:
                audit.injected_ids = ids
                audit.source_filters = {**audit.source_filters, "purpose": "rule_answer"}
            structured = bool(ids) and audit.source_filters["mode"] == "structured"
            answers.append(
                {
                    "question": part,
                    "status": "topic_reference"
                    if structured
                    else "related_excerpt"
                    if ids
                    else "not_found",
                    "text": "\n".join(citations[i]["excerpt"] for i in ids)
                    if ids
                    else "未找到对应的公开规则依据。",
                    "evidence_ids": ids,
                }
            )
        text = "\n\n".join(
            f"{i}. {a['question']}\n"
            + (
                "已核对的主题说明：\n"
                if a["status"] == "topic_reference"
                else "检索到相关原文（未确认覆盖问题的全部细节）：\n"
                if a["status"] == "related_excerpt"
                else ""
            )
            + a["text"]
            for i, a in enumerate(answers, 1)
        )
        text += "\n\n规则说明只供参考；游戏操作仍以当前已实现并验证的功能为准。"
        run.context = {"question": question, "RULE_EVIDENCE": list(citations.values())}
        run.structured_output, run.status = {"answers": answers}, "completed"
        run.latency_ms, run.finished_at = int((time.monotonic() - started) * 1000), utc_now()
        service.rooms.append(
            session,
            room,
            "rules.answered",
            room.host_member_id,
            {
                "text": text,
                "question_seq": trigger.seq,
                "cycle_id": cycle.id,
                "answers": answers,
                "citations": list(citations.values()),
            },
            request_id=run.id,
        )
        cycle.status, cycle.finished_at = "completed", utc_now()
        cycle.state = {
            **cycle.state,
            "status": "completed",
            "current_node": "answer_rule_question",
            "safe_error": None,
            "wait_reason": None,
            "rule_answer_run_id": run.id,
        }
        service.cycle_event(session, room, cycle)
        from app.agents.conversation import activate_next

        await session.flush()
        await activate_next(service, session, room)

    await service.mutate(state["room_id"], answer)
