"""Explicit, durable report-only recovery; never enters the action graph."""

import asyncio
import json
import logging
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.answer_parts import (
    contract_version,
    inspect_answer_parts,
    part_origins,
    project_answer_parts,
    server_parts,
    uses_answer_parts,
)
from app.agents.generation_contracts import generation_contract, restore_output
from app.agents.modules import public_module
from app.agents.server_parts import capture_evidence_scope, prepare_server_parts
from app.domain.character import utc_now
from app.memory.events import story_events
from app.models.base import ModelFormatError
from app.persistence.adjudication_models import ActionPlanRecord
from app.persistence.agent_models import AgentCycle, AgentRun
from app.persistence.knowledge_models import AgentModelCall
from app.persistence.room_models import RoomEvent, RoomMember
from app.rooms.service import RoomError, require

NODE = "supplement_keeper_narration"
INTRODUCTION = "关于当时检查结果的补充：\n\n"


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def task_view(run):
    return {"id": run.id, "status": run.status,
            "original_report_seq": run.context["supplement_of"]}


class ReportSupplementService:
    def __init__(self, service):
        self.service, self.rooms = service, service.rooms
        self.tasks = {}
        self.task_rooms = {}
        self.cancelled = {}

    async def original(self, session, room, identity, report_seq):
        requester = await session.get(RoomMember, identity.member_id)
        require(requester and requester.room_id == room.id and requester.active,
                "请求者当前已不在房间中", 403)
        require(not identity.is_host or requester.id == room.host_member_id,
                "请求者当前已无主机权限", 403)
        event = await session.get(RoomEvent, (room.id, int(report_seq)))
        require(event and event.type == "keeper.narration"
                and event.visibility == "public" and not event.payload.get("supplement_of"),
                "原正式报告不存在或当前不可见", 404)
        run = (await session.get(AgentRun, event.client_request_id)
               if event.client_request_id else None)
        require(run and run.room_id == room.id and run.graph_node == "generate_keeper_narration",
                "原报告缺少可恢复的生成记录")
        cycle = await session.get(AgentCycle, run.cycle_id)
        require(cycle and cycle.room_id == room.id
                and event.payload.get("cycle_id") == cycle.id, "原报告与执行回合不匹配")
        requests = cycle.state.get("request_operands", {})
        owners = {r.get("requester_member_id") for r in requests.values()}
        owners.add(cycle.state.get("triggering_member_id"))
        for request in requests.values():
            if request.get("requester_member_id") or not request.get("source_event_seq"):
                continue
            source = await session.get(RoomEvent, (room.id, request["source_event_seq"]))
            start, end = request.get("source_start"), request.get("source_end")
            if (source and source.type == "action.submitted" and source.visibility == "public"
                    and source.payload.get("cycle_id") == cycle.state.get("related_player_cycle_id")
                    and isinstance(start, int) and isinstance(end, int) and 0 <= start < end
                    and source.payload.get("text", "")[start:end] == request.get("text")):
                # Older frozen request operands predate requester_member_id.
                # Recover authority from the exact original attributed span,
                # never from the executor's generated paraphrase or new prose.
                owners.add(source.actor_member_id)
        require(identity.is_host or identity.member_id in owners,
                "只能补齐本人请求或本人执行的报告", 403)
        require(event.payload.get("safe_fallback"), "原报告已完整，无需补齐")
        require(cycle.status in {"completed", "failed"}, "请等待原执行回合结束")
        return event, run, cycle

    async def prepare(self, session, room, identity, report_seq):
        event, original, cycle = await self.original(session, room, identity, report_seq)
        events = await self.rooms.events(session, room, identity)
        active, _ = story_events(events, include_initial_reveals=True)
        visible = {e["seq"]: e for e in active if e.get("visibility") == "public"}
        require(event.seq in visible, "原报告已不在当前存档分支，无法补齐")
        context = deepcopy(original.context)
        require(uses_answer_parts(context), "原报告缺少可恢复的分段需求")
        record = await session.get(ActionPlanRecord, cycle.id)
        require(record is not None, "原执行计划已无法恢复")
        target = context.get("fact_target") or record.document.get("plan", {}).get(
            "focus", {}).get("action_target_id")
        results = await self.service.runtime.public_results(session, room, cycle)
        actual = [f for f in results.get("current_result_facts", [])
                  if f.get("status") == "success" and f.get("cycle_id") == cycle.id
                  and f.get("operation") in {"observe", "search", "reveal"}
                  and f.get("actor_id") == cycle.state.get("triggering_member_id")
                  and f.get("source_action_seq") == cycle.state.get("triggering_event_seq")
                  and f.get("source_event_seq") in visible
                  and (f.get("action_target_id") or f.get("target_id")) == target]
        require(actual and not results.get("blocked_discovery") and not results.get("failed_tools"),
                "原请求缺少匹配的有效执行回执，不能仅补报告结清")
        module = await self.service.module(session, room.id)
        public = await self.service.entities.public(session, room.id)
        current_module = public_module(module)
        if not context.get("prepared_module"):
            public += [{"id": c["id"], "title": c["title"], "type": "clue",
                        "public_summary": c["content"], "fact_scope": "current_scene"}
                       for c in current_module["clues"]]
        old_scene = context.get("module", {}).get("scene", {}).get("id")
        require(not old_scene or current_module["scene"]["id"] == old_scene,
                "检查后所在场景已变化；原结果仍保留，本次无法安全补齐")
        scope_context = {**context, "module": current_module}
        scope = capture_evidence_scope(scope_context, public)
        by_id = {e["id"]: e for e in scope["public_entities"]}
        require(target in by_id, "原检查目标目前不可见，无法恢复原来源")
        old_entities = (context.get("answer_evidence_scope") or {}).get("public_entities", [])
        old_entity = next((e for e in old_entities if e["id"] == target), None)
        require(not old_entity or old_entity.get("public_summary", "")
                == by_id[target].get("public_summary", ""),
                "检查后目标公开状态已变化；原结果仍保留，本次无法安全补齐")
        sources = context["response_brief"].get("answer_sources", [])
        required_sources = {sid for r in context["response_brief"]["answer_requirements"]
                            for sid in r.get("source_ids", [])}
        for source in sources:
            if source["id"] not in required_sources:
                continue
            source_id = source["id"]
            if source_id.startswith("e") and source_id[1:].isdigit():
                source_event = visible.get(int(source_id[1:]))
                require(source_event, "原报告引用的公开来源已失效或不在当前存档分支")
                payload = source_event["payload"]
                material = "\n".join(str(payload.get(k, "")) for k in (
                    "text", "content", "public_summary", "display_text", "summary", "title",
                ))
                require(source["text"] in material
                        or any(f.get("effect") == source["text"] for f in actual
                               if f.get("source_event_seq") == source_event["seq"]),
                        "原报告引用文本已无法与公开原文对应")
            else:
                entity = by_id.get(source_id.removeprefix("entity:").removeprefix("scene:"))
                require(entity and source["text"] in entity.get("public_summary", ""),
                        "原报告引用的实体来源目前不可恢复")
        context.update(answer_evidence_scope=scope, public_tool_results=results,
                       fact_target=target)
        brief = prepare_server_parts(context, context["response_brief"], sources)
        for part in brief.get("server_parts", []):
            question = next(r["text"] for r in brief["answer_requirements"]
                            if r["id"] == part["requirement_id"])
            part["text"] = "仅凭当时的检查结果，" + question + "，还不能确定。"
        context["response_brief"] = brief
        context.pop("_answer_parts_retained", None)
        candidates = deepcopy(original.context.get("retained_answer_parts", []))
        # A previously rejected supplement may have independently verified
        # model parts; never recover prose from coverage or a refused sentence.
        previous = list(await session.scalars(select(AgentRun).where(
            AgentRun.room_id == room.id, AgentRun.graph_node == NODE,
        ).order_by(AgentRun.created_at)))
        for run in previous:
            if run.context.get("supplement_of") == event.seq:
                candidates += run.context.get("retained_answer_parts", [])
        readonly_ids = {p["requirement_id"] for p in server_parts(context)}
        unique = {}
        for part in candidates:
            if part.get("requirement_id") not in readonly_ids:
                unique.setdefault(part["requirement_id"], part)
        audit = inspect_answer_parts({"answer_parts": list(unique.values())}, context)
        retained = audit["verified_parts"]
        context["_answer_parts_retained"] = retained
        context["retained_answer_parts"] = retained
        answered = readonly_ids | {p["requirement_id"] for p in retained}
        missing = [r["id"] for r in brief["answer_requirements"] if r["id"] not in answered]
        context.update(
            supplement_of=event.seq, original_run_id=original.id,
            original_cycle_id=cycle.id, request_keys=cycle.state.get("request_keys", []),
            request_operands=deepcopy(cycle.state.get("request_operands", {})),
            receipt_event_seqs=[f["source_event_seq"] for f in actual],
            missing_requirement_ids=missing, answer_contract_version=contract_version(context),
            recovery_evidence_fingerprint=fingerprint({"receipts": actual, "scope": scope,
                                                     "sources": sources}),
        )
        return original, cycle, context

    async def views(self, session, room, identity):
        visible_events = await self.rooms.events(session, room, identity)
        active_story, _ = story_events(visible_events, include_initial_reveals=True)
        active_seqs = {e["seq"] for e in active_story}
        reports = list(await session.scalars(select(RoomEvent).where(
            RoomEvent.room_id == room.id, RoomEvent.type == "keeper.narration",
            RoomEvent.payload["safe_fallback"].as_boolean().is_(True),
        ).order_by(RoomEvent.seq.desc()).limit(24)))
        tasks = list(await session.scalars(select(AgentRun).where(
            AgentRun.room_id == room.id, AgentRun.graph_node == NODE,
        ).order_by(AgentRun.created_at)))
        latest = {r.context.get("supplement_of"): r for r in tasks
                  if r.status != "completed" or r.context.get("formal_event_seq") in active_seqs}
        output = []
        for event in reports:
            if event.payload.get("supplement_of"):
                continue
            try:
                await self.original(session, room, identity, event.seq)
            except RoomError:
                continue
            task = latest.get(event.seq)
            status = task.status if task else "available"
            reason = task.safe_error if task and status == "failed" else None
            available = room.status == "running" and status not in {"running", "completed"}
            if available:
                try:
                    await self.prepare(session, room, identity, event.seq)
                except RoomError as error:
                    available, reason = False, error.message
            output.append({"report_seq": event.seq, "available": available, "status": status,
                           "reason": reason, "supplement_id": task.id if task else None})
        return output

    async def enqueue(self, session, room, identity, body, report_seq):
        # Authentication applies to replays too. An ID cannot be rebound to a
        # different requester/report after its original operation was persisted.
        await self.original(session, room, identity, report_seq)
        previous = await session.get(AgentRun, str(body.client_request_id))
        if previous:
            require(previous.room_id == room.id and previous.graph_node == NODE
                    and previous.context.get("supplement_of") == int(report_seq)
                    and previous.context.get("requested_by") == identity.member_id,
                    "补述请求 ID 已用于其他内容")
            return {"supplement": task_view(previous)}
        tasks = list(await session.scalars(select(AgentRun).where(
            AgentRun.room_id == room.id, AgentRun.graph_node == NODE,
        ).order_by(AgentRun.created_at.desc())))
        alias = {"requester": identity.member_id, "id": str(body.client_request_id)}
        previous_alias = next((r for r in tasks
                               if alias in r.context.get("idempotency_aliases", [])), None)
        if previous_alias:
            require(previous_alias.context.get("supplement_of") == int(report_seq),
                    "补述请求 ID 已用于其他内容")
            return {"supplement": task_view(previous_alias)}
        visible_events = await self.rooms.events(session, room, identity)
        active_story, _ = story_events(visible_events, include_initial_reveals=True)
        active_seqs = {e["seq"] for e in active_story}
        active = next((r for r in tasks if r.context.get("supplement_of") == int(report_seq)
                       and (r.status == "running" or r.status == "completed"
                            and r.context.get("formal_event_seq") in active_seqs)), None)
        if active:
            active.context = {**active.context, "idempotency_aliases": [
                *active.context.get("idempotency_aliases", []), alias,
            ]}
            return {"supplement": task_view(active)}
        require(room.status == "running", "请先恢复游戏")
        original, cycle, context = await self.prepare(session, room, identity, report_seq)
        context.update(requested_by=identity.member_id, requested_as_host=identity.is_host,
                       model_call_count=0)
        run = AgentRun(
            id=str(body.client_request_id), room_id=room.id, cycle_id=cycle.id,
            profile_id=original.profile_id, actor_member_id=original.actor_member_id,
            graph_node=NODE, status="running", input_seq_start=original.input_seq_start,
            input_seq_end=original.input_seq_end, provider=self.service.settings.model_provider,
            model=self.service.settings.model_name, context=context, tool_results=[],
        )
        session.add(run)
        self.rooms.append(session, room, "agent.report_supplement_requested", identity.member_id,
                          {"supplement_id": run.id, "supplement_of": int(report_seq),
                           "original_cycle_id": cycle.id, "original_run_id": original.id},
                          "host_only")
        return {"supplement": task_view(run)}

    def schedule(self, room_id, task_id):
        if task_id in self.tasks or self.service.runtime.closing:
            return
        task = asyncio.create_task(self.execute(str(room_id), task_id),
                                   name="report-supplement-" + task_id)
        self.tasks[task_id] = task
        self.task_rooms[task_id] = str(room_id)

        def finished(_):
            self.tasks.pop(task_id, None)
            self.task_rooms.pop(task_id, None)
        task.add_done_callback(finished)

    def cancel_room(self, room_id):
        entries = [(key, task) for key, task in list(self.tasks.items())
                   if self.task_rooms.get(key) == str(room_id)]
        tasks = [task for _, task in entries]
        self.cancelled.setdefault(str(room_id), set()).update(key for key, _ in entries)
        for task in tasks:
            task.cancel()
        return tasks

    async def finish_cancellations(self, room_id):
        for key in self.cancelled.pop(str(room_id), set()):
            await self.fail(str(room_id), key, "补述因暂停或存档恢复而中断；原行动结果已保留")

    async def close(self):
        entries = [(key, self.task_rooms[key]) for key in self.tasks]
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for key, room_id in entries:
            await self.fail(room_id, key, "补述因服务停止而中断；原行动结果已保留")

    async def execute(self, room_id, task_id):
        from app.agents.narration_stream import NarrationStream
        from app.rooms.service import Identity

        try:
            async with self.rooms.database.sessions() as session:
                run = await session.get(AgentRun, task_id)
                if not run or run.status != "running":
                    return
                context = deepcopy(run.context)
                cycle = await session.get(AgentCycle, run.cycle_id)
                state = {**cycle.state, "room_id": room_id, "cycle_id": cycle.id}
            contract = generation_contract(KeeperNarration, context)
            snapshot = await NarrationStream.create(self.service.runtime, state, task_id, contract)

            async def validate(output):
                restored = restore_output(output, KeeperNarration, context)
                snapshot.check_private(restored.public_narration)
                try:
                    await self.service.runtime.validate_narration_output(
                        None, snapshot.snapshot["room"], snapshot.snapshot["cycle"],
                        snapshot.snapshot["run"], restored, prefix_snapshot=snapshot.snapshot,
                    )
                except RoomError as error:
                    raise ModelFormatError("补充正文未通过核对", [{
                        "field": "answer_parts", "code": error.message,
                    }]) from None

            async def call_started():
                async def update(session, room):
                    run = await session.get(AgentRun, task_id)
                    require(room.status == "running" and run.status == "running",
                            "游戏或补述已暂停，本次不调用模型")
                    identity = Identity(context["requested_by"], context["requested_as_host"])
                    _, _, fresh = await self.prepare(session, room, identity,
                                                     context["supplement_of"])
                    require(fresh["recovery_evidence_fingerprint"]
                            == context["recovery_evidence_fingerprint"],
                            "等待期间公开来源或存档分支已变化，本次不调用模型")
                    require(run.context.get("model_call_count", 0) == 0,
                            "此次显式补述已使用模型调用")
                    run.context = {**run.context, "model_call_count": 1}
                await self.service.mutate(room_id, update)

            async def call_finished(document):
                raw = document.get("raw_output") or document.get("generated_output") or {}
                audit = inspect_answer_parts(raw, context)
                verified = []
                local = {k: v for k, v in context.items() if k != "_answer_parts_retained"}
                for part in audit["verified_parts"]:
                    component = project_answer_parts([part], local, partial=True)
                    try:
                        snapshot.check_private(component.public_narration)
                        await self.service.runtime.validate_narration_output(
                            None, snapshot.snapshot["room"], snapshot.snapshot["cycle"],
                            snapshot.snapshot["run"], component, partial=True,
                            prefix_snapshot=snapshot.snapshot,
                        )
                    except (RoomError, ValueError):
                        continue
                    verified.append(part)
                async def update(session, room):
                    run = await session.get(AgentRun, task_id)
                    retained = {}
                    for part in [*context.get("retained_answer_parts", []), *verified]:
                        retained.setdefault(part["requirement_id"], part)
                    run.context = {**run.context, "retained_answer_parts": list(retained.values())}
                    run.token_usage = document.get("token_usage")
                    run.latency_ms = document.get("latency_ms", 0)
                    session.add(AgentModelCall(id=str(uuid4()), run_id=run.id, document={
                        **document, "supplement_of": context["supplement_of"],
                        "answer_contract_version": contract_version(context),
                        "answer_parts_audit": audit,
                    }))
                await self.service.mutate(room_id, update)

            if context["missing_requirement_ids"]:
                prompt = [{"role": "system", "content":
                           "你只补齐当时检查的正式报告。不能执行或宣布新行动。"
                           "只根据给定公开原文写尚缺的answer_parts，保留来源原文。"
                           "server_parts是固定说明，不由你生成或改写。"},
                          {"role": "user", "content": json.dumps({
                              "answer_requirements": context["response_brief"][
                                  "answer_requirements"],
                              "answer_sources": context["response_brief"]["answer_sources"],
                              "server_parts": context["response_brief"].get("server_parts", []),
                              "retained_answer_parts": context["retained_answer_parts"],
                          }, ensure_ascii=False)}]
                result, _ = await self.service.model.generate(
                    prompt, response_schema=contract, max_attempts=1,
                    on_call=call_started, on_result=call_finished, validate_output=validate,
                )
                output = restore_output(result.structured, KeeperNarration, context)
            else:
                output = project_answer_parts([], context)
                snapshot.check_private(output.public_narration)
                await self.service.runtime.validate_narration_output(
                    None, snapshot.snapshot["room"], snapshot.snapshot["cycle"],
                    snapshot.snapshot["run"], output, prefix_snapshot=snapshot.snapshot,
                )

            rendered = KeeperNarration.model_validate({**output.model_dump(mode="json"),
                "public_narration": INTRODUCTION + output.public_narration})
            snapshot.check_private(rendered.public_narration)
            await self.service.runtime.validate_narration_output(
                None, snapshot.snapshot["room"], snapshot.snapshot["cycle"],
                snapshot.snapshot["run"], rendered, prefix_snapshot=snapshot.snapshot,
            )

            async def publish(session, room):
                run = await session.get(AgentRun, task_id)
                if run.status != "running":
                    return
                require(room.status == "running", "游戏已暂停，本次补述未发布")
                requester = await session.get(RoomMember, context["requested_by"])
                require(requester and requester.room_id == room.id and requester.active,
                        "请求者当前已不在房间中，本次补述未发布", 403)
                require(not context["requested_as_host"] or requester.id == room.host_member_id,
                        "请求者当前已无原主机权限，本次补述未发布", 403)
                identity = Identity(context["requested_by"], context["requested_as_host"])
                _, cycle, fresh = await self.prepare(
                    session, room, identity, context["supplement_of"],
                )
                require(fresh["recovery_evidence_fingerprint"]
                        == context["recovery_evidence_fingerprint"],
                        "补述期间公开来源或目标状态已变化，本次未结清")
                validation = {"valid": True, "answer_complete": True,
                              "answer_origin": "mixed" if server_parts(context) else "supplement",
                              "answer_contract_version": contract_version(context),
                              "evidence_assessments": [r.get("evidence_assessment") for r in
                                  context["response_brief"]["answer_requirements"]],
                              "server_parts": context["response_brief"].get("server_parts", []),
                              "repair_count": 0, "supplement_of": context["supplement_of"]}
                self.rooms.append(session, room, "agent.narration_validated", room.host_member_id,
                                  {"cycle_id": cycle.id, "run_id": run.id, **validation},
                                  "host_only")
                event = self.rooms.append(session, room, "keeper.narration", run.actor_member_id, {
                    "text": rendered.public_narration, "cycle_id": cycle.id,
                    "supplement_of": context["supplement_of"], "supplement_id": run.id,
                    "safe_fallback": False, "answer_origin": validation["answer_origin"],
                    "actor_name": context.get("profile", {}).get("name", "KP"),
                    "controller_type": "agent", "scene_id": context.get("module", {}).get(
                        "scene", {}).get("id"), "incidental_details": [],
                }, request_id=run.id)
                run.status, run.finished_at = "completed", utc_now()
                run.structured_output = rendered.model_dump(mode="json")
                run.context = {**run.context, "narration_validation": validation,
                               "formal_event_seq": event.seq,
                               "part_origins": part_origins({**context,
                                   "_answer_parts_retained": run.context.get(
                                       "retained_answer_parts", [])}, [])}
                from app.agents.conversation import settle_report_supplement

                await settle_report_supplement(self.service, session, room, cycle, run, event)
            await self.service.mutate(room_id, publish)
        except asyncio.CancelledError:
            await self.fail(room_id, task_id, "补述因服务停止而中断；原行动与有效报告已保留")
            raise
        except Exception as error:
            logging.getLogger(__name__).exception("Report-only recovery failed: %s",
                                                  getattr(error, "issues", []))
            reason = (error.message if isinstance(error, RoomError) else
                      "补充正文未通过依据或权限核对；已保留此前有效片段")
            await self.fail(room_id, task_id, reason)

    async def fail(self, room_id, task_id, reason):
        async def save(session, room):
            run = await session.get(AgentRun, task_id)
            if run and run.status == "running":
                run.status, run.finished_at, run.safe_error = "failed", utc_now(), reason
                run.error_type = "report_supplement_incomplete"
                self.rooms.append(session, room, "agent.report_supplement_failed",
                                  room.host_member_id, {
                                      "supplement_id": run.id,
                                      "supplement_of": run.context["supplement_of"],
                                      "reason": reason,
                                  }, "host_only")
        try:
            await self.service.mutate(room_id, save)
        except RoomError:
            # Ending a room forbids world mutations, but the interrupted task's
            # durable internal status must still prevent a replayed model call.
            async with self.rooms.lock(room_id):
                async with self.rooms.transaction() as session:
                    run = await session.get(AgentRun, task_id)
                    if run and run.status == "running":
                        run.status, run.finished_at, run.safe_error = "failed", utc_now(), reason
                        run.error_type = "report_supplement_interrupted"
