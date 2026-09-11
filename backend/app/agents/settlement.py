"""Ordinary check settlement in the existing room transaction and single interrupt."""

from sqlalchemy import select

from app.agents.schemas import PendingCheck
from app.domain.character import utc_now
from app.persistence.agent_models import AgentCycle
from app.persistence.room_models import RoomEvent, RoomMember
from app.rooms.sanity_service import runtime_character
from app.rooms.service import require
from app.rules.check_options import can_push, luck_options
from app.rules.checks import roll_check
from app.rules.compound import check_result, result_signature


class CheckSettlementService:
    def __init__(self, agents):
        self.agents, self.rooms = agents, agents.rooms

    async def fixed_roll(self, session, room, record, check, stage):
        legacy = None
        for event in await session.scalars(
            select(RoomEvent)
            .where(
                RoomEvent.room_id == room.id,
                RoomEvent.type.in_(("check.dice_fixed", "check.resolved")),
            )
            .order_by(RoomEvent.seq)
        ):
            if (
                event.type == "check.dice_fixed"
                and event.payload["check_id"] == record.id
                and event.payload["stage"] == stage
            ):
                return event.payload["dice"], event.payload["result"]
            if (
                stage == "original"
                and event.type == "check.resolved"
                and event.payload.get("id") == record.id
                and not event.payload.get("settlement")
                and event.payload.get("dice")
                and legacy is None
            ):
                legacy = (event.payload["dice"], event.payload["result"])
        dice, result = legacy or roll_check(
            self.rooms.dice, check.value, check.difficulty, check.bonus_dice, check.penalty_dice
        )
        if check.combined:
            result = check_result(check.model_dump(mode="json"), result["total"])
        self.rooms.append(
            session,
            room,
            "check.dice_fixed",
            room.host_member_id,
            {
                "check_id": record.id,
                "stage": stage,
                "dice": dice,
                "result": result,
            },
            "host_only",
        )
        return dice, result

    async def options(self, session, room, document):
        slot = await self.agents.sanity.slot(session, room, slot_id=document["slot_id"])
        _, character = runtime_character(room, slot)
        return {
            "luck": luck_options(document, character.luck, room.session_state.get("luck_spending")),
            "push": can_push(document),
        }

    def event(self, session, room, record, kind):
        self.rooms.append(
            session,
            room,
            kind,
            record.target_member_id,
            {
                **self.agents.check_public(record.document),
                "cycle_id": record.cycle_id,
            },
            record.document["visibility"],
        )

    async def wait(self, session, room, record, reason):
        cycle = await session.get(AgentCycle, record.cycle_id)
        cycle.status = "waiting_for_roll"
        cycle.state = {**cycle.state, "status": "waiting_for_roll", "wait_reason": reason}
        self.agents.cycle_event(session, room, cycle)

    def finish(self, session, room, record, check, result):
        check.result = result
        check.status, check.resolved_at = "resolved", utc_now()
        check.settlement = {**check.settlement, "stage": "final", "final_result": result}
        record.status, record.document = "resolved", check.model_dump(mode="json")
        self.event(session, room, record, "check.resolved")

    async def rolled(self, session, room, record, check, automatic):
        dice, raw = await self.fixed_roll(session, room, record, check, "original")
        check.dice, check.result = dice, None
        check.settlement = {
            "stage": "choice",
            "original_dice": dice,
            "original_result": raw,
            "luck_spent": 0,
            "push_requested": False,
        }
        record.document = check.model_dump(mode="json")
        options = await self.options(session, room, record.document)
        meaningful_luck = any(
            result_signature(o["result"]) != result_signature(raw)
            for o in options["luck"]
        )
        if automatic or not (meaningful_luck or options["push"]):
            self.finish(session, room, record, check, raw)
        else:
            self.event(session, room, record, "check.rolled")
            await self.wait(session, room, record, "check_choice")

    async def choice(self, session, room, record, body):
        check = PendingCheck.model_validate(record.document)
        progress = check.settlement
        require(progress and not check.sanity, "此检定没有普通结果选择", 422)
        if progress.get("choice") == body.model_dump() and progress.get("stage") != "choice":
            return
        if record.status == "resolved":
            # Same decision is an idempotent receipt; a different decision cannot edit it.
            require(progress.get("choice") == body.model_dump(), "检定已最终结算")
            return
        require(progress["stage"] == "choice", "检定已进入其他结算阶段")
        options = await self.options(session, room, record.document)
        progress["choice"] = body.model_dump()
        if body.operation == "accept":
            self.finish(session, room, record, check, progress["original_result"])
        elif body.operation == "luck":
            selected = next((o for o in options["luck"] if o["spend"] == body.spend), None)
            require(selected, "幸运花费不合法或当前幸运不足", 422)
            slot = await self.agents.sanity.slot(session, room, slot_id=check.slot_id)
            state, character = runtime_character(room, slot)
            before = character.luck
            character.luck -= body.spend
            progress.update(luck_spent=body.spend, luck_before=before, luck_after=character.luck)
            room.session_state = state.model_dump(mode="json")
            record.document = check.model_dump(mode="json")
            self.event(session, room, record, "check.luck_spent")
            self.finish(session, room, record, check, selected["result"])
        else:
            require(options["push"], "此检定不能孤注一掷", 422)
            require(body.effort.strip(), "请说明额外努力或时间花费", 422)
            progress.update(stage="push_review", push_requested=True, effort=body.effort.strip())
            record.document = check.model_dump(mode="json")
            self.event(session, room, record, "check.push_requested")
            await self.wait(session, room, record, "push_review")

    async def review(self, session, room, record, body):
        check = PendingCheck.model_validate(record.document)
        progress = check.settlement or {}
        if progress.get("review") == body.model_dump(mode="json"):
            return
        require(
            record.status == "pending" and progress.get("stage") == "push_review",
            "没有待核准的孤注申请",
        )
        require(not body.approve or body.consequence, "掷骰前必须确认更严重的失败后果", 422)
        progress["review"] = body.model_dump(mode="json")
        progress["stage"] = "push_roll" if body.approve else "final"
        record.document = check.model_dump(mode="json")
        self.event(session, room, record, "check.push_reviewed")
        if body.approve:
            await self.wait(session, room, record, "push_roll")
        else:
            self.finish(session, room, record, check, progress["original_result"])

    async def keeper_review(self, runtime, state, check_id):
        from app.agents.settlement_schemas import PushReview
        from app.persistence.agent_models import AgentRun, CheckRecord

        async with self.rooms.database.sessions() as session:
            check = await session.get(CheckRecord, check_id)
            context = {"push_attempt": self.agents.check_public(check.document)}
        run_id = await runtime.generate_action_run(
            state,
            await runtime.keeper_binding(state),
            "review_push",
            PushReview,
            "你是CoC KP。只返回PushReview，判断玩家额外努力是否实质改变方法或增加投入。"
            "不是简单重复才approve=true，并在掷骰前说明具体更严重的失败后果。"
            "只依据公开情境。已实现后果kind=time（明确minutes）或condition（明确condition）；"
            "伤害、丢失物品等未实现效果用host_manual，不可假装已经结算。"
            "原检定资格由服务端核验；不要掷骰或改变原结果。",
            context,
        )

        async def apply(session, room):
            record = await session.get(CheckRecord, check_id)
            run = await session.get(AgentRun, run_id)
            if (record.document.get("settlement") or {}).get("stage") != "push_review":
                return
            await self.review(
                session, room, record, PushReview.model_validate(run.structured_output)
            )
            run.status, run.finished_at = "completed", utc_now()

        await self.agents.mutate(state["room_id"], apply)

    async def push_roll(self, session, room, record):
        check = PendingCheck.model_validate(record.document)
        progress = check.settlement or {}
        if progress.get("push_dice"):
            return
        require(
            record.status == "pending" and progress.get("stage") == "push_roll",
            "孤注尚未核准或已经掷骰",
        )
        consequence = progress["review"]["consequence"]
        slot = await self.agents.sanity.slot(session, room, slot_id=check.slot_id)
        state, character = runtime_character(room, slot)
        # Capacity failures must be found before rolling: a transaction rollback
        # after an unsuccessful push would otherwise let retries roll fresh dice.
        if consequence["kind"] == "condition":
            require(
                consequence["condition"] in character.conditions or len(character.conditions) < 30,
                "角色状态已满，请主机处理后再确认掷骰",
            )
        elif consequence["kind"] == "time":
            require(state.game_minute + consequence["minutes"] <= 1_000_000, "游戏时间超出上限")
        dice, result = await self.fixed_roll(session, room, record, check, "push")
        progress.update(push_dice=dice, push_result=result)
        # Retain original dice in check.dice; final outcome is explicitly separate.
        if result["passed"]:
            progress["consequence_status"] = "not_applicable"
            self.finish(session, room, record, check, result)
            return
        if consequence["kind"] == "condition":
            if consequence["condition"] not in character.conditions:
                character.conditions.append(consequence["condition"])
        elif consequence["kind"] == "time":
            state.game_minute += consequence["minutes"]
        else:
            progress.update(stage="consequence", consequence_status="awaiting_host")
            record.document = check.model_dump(mode="json")
            self.event(session, room, record, "check.consequence_pending")
            await self.wait(session, room, record, "push_consequence")
            return
        room.session_state = state.model_dump(mode="json")
        progress["consequence_status"] = "applied"
        record.document = check.model_dump(mode="json")
        self.event(session, room, record, "check.consequence_applied")
        self.finish(session, room, record, check, result)

    async def handled(self, session, room, record, body):
        check = PendingCheck.model_validate(record.document)
        progress = check.settlement or {}
        if progress.get("handled_reason") == body.reason:
            return
        require(
            record.status == "pending" and progress.get("stage") == "consequence",
            "没有待处理的孤注后果",
        )
        progress.update(consequence_status="host_handled", handled_reason=body.reason)
        record.document = check.model_dump(mode="json")
        self.event(session, room, record, "check.consequence_applied")
        self.finish(session, room, record, check, progress["push_result"])

    async def authorize(self, session, room, identity, record, host_only=False):
        require(record and record.room_id == room.id, "检定不存在", 404)
        member = await session.get(RoomMember, record.target_member_id)
        # Remote humans control themselves. Host can manage local humans and AI seats.
        require(
            identity.is_host
            if host_only
            else (
                identity.member_id == record.target_member_id
                or identity.is_host
                and member
                and (member.controller_type == "agent" or member.access_type == "host_managed")
            ),
            "只能由本人选择；主机仅可代管本地真人或 AI 席位",
            403,
        )
        require(room.status == "running", "请先恢复游戏")
        if record.status != "resolved":
            from app.agents.conversation import ensure_no_earlier_message

            await ensure_no_earlier_message(session, room, None)
            cycle = await self.agents.cycle(session, room.id, active=True)
            require(
                cycle
                and cycle.id == record.cycle_id
                and cycle.state.get("pending_check_id") == record.id
                and cycle.status == "waiting_for_roll",
                "检定不属于当前等待项",
            )
