"""SAN settlement inside RoomService's lock/transaction; dice events are immutable."""

from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select

from app.agents.schemas import PendingCheck
from app.domain.character import utc_now
from app.persistence.agent_models import AgentCycle, CheckRecord
from app.persistence.room_models import RoomEvent, RoomMember
from app.rooms.sanity_schemas import SanityEffect, SanityProgress
from app.rooms.schemas import SessionStateV1
from app.rooms.service import require
from app.rules.checks import check_value, judge
from app.rules.sanity import insanity_trigger, judge_sanity, loss_bounds


def runtime_character(room, slot):
    state = SessionStateV1.model_validate(room.session_state)
    character = state.characters[UUID(slot.id)]
    if character.hp_max is None:
        character.hp_max = slot.character_snapshot.get("derived_values", {}).get("hp")
    mythos = slot.character_snapshot.get("skill_values", {}).get("cthulhu_mythos", 0)
    character.san_max = max(0, 99 - mythos - character.sanity.mythos_gain)
    return state, character


def runtime_context(room, slot_id):
    character = room.session_state.get("characters", {}).get(slot_id, {})
    sanity = character.get("sanity", {})
    result = {
        k: character[k]
        for k in ("hp", "hp_max", "mp", "mp_max", "mp_recovery_progress", "san", "san_max", "luck")
        if character.get(k) is not None
    }
    if character.get("conditions"):
        result["conditions"] = character["conditions"]
    if sanity.get("kind", "none") != "none":
        result["sanity"] = {k: sanity.get(k) for k in ("kind", "phase", "symptom")}
    if sanity.get("mythos_gain"):
        result["mythos_gain"] = sanity["mythos_gain"]
    return result


def current_check_value(room, slot, kind, name):
    if kind == "attribute" and name == "luck":
        value = room.session_state.get("characters", {}).get(str(slot.id), {}).get("luck")
        if type(value) is not int or not 0 <= value <= 99:
            raise ValueError("角色尚无当前幸运值")
        return value
    value = check_value(slot.character_snapshot, kind, name)
    if kind == "skill" and name == "cthulhu_mythos":
        gain = (
            room.session_state.get("characters", {})
            .get(slot.id, {})
            .get("sanity", {})
            .get("mythos_gain", 0)
        )
        value = min(99, value + gain)
    return value


def sanity_public(document):
    progress = SanityProgress.model_validate(document["sanity"])
    names = {
        "san": "SAN",
        "loss": "理智损失骰",
        "int": "INT 理解检定",
        "duration": "临时疯狂持续小时",
        "symptom": "等待主机确认症状",
        "done": "已结算",
    }
    label = names[progress.stage]
    message = f"SAN {progress.before}"
    if progress.after is not None:
        message += f" → {progress.after}，损失 {progress.loss}"
    if document.get("result"):
        message += f"；SAN 骰点 {document['result']['total']}，" + (
            "通过"
            if document["result"]["passed"]
            else "大失败"
            if document["result"]["level"] == "fumble"
            else "失败"
        )
    message += f"；{label}"
    if progress.insanity_kind != "none":
        kind = {"temporary": "临时性疯狂", "indefinite": "不定性疯狂", "permanent": "永久性疯狂"}
        message += f"；{kind[progress.insanity_kind]}，{progress.symptom or '等待主机确认症状'}"
    public = {
        k: v
        for k, v in document.items()
        if k not in {"sanity", "policy_fingerprint", "policy_target_id", "clue_id"}
    }
    public.update(
        display_name="SAN",
        display_text=message,
        difficulty_display="SAN 二元判定",
        sanity={
            "origin": progress.origin,
            "stage": progress.stage,
            "before": progress.before,
            "after": progress.after,
            "loss": progress.loss,
            "immune": progress.immune,
            "insanity_kind": progress.insanity_kind,
            "phase": progress.phase,
            "symptom": progress.symptom,
            "rolls": progress.rolls,
        },
    )
    if public.get("result"):
        public["result"] = {**public["result"], "display_text": message}
    return public


class SanityService:
    def __init__(self, agents):
        self.agents, self.rooms = agents, agents.rooms
        from app.rooms.encounters import EncounterService

        self.encounters = EncounterService(self)

    async def slot(self, session, room, member_id=None, slot_id=None):
        slots = await self.rooms.slots(session, room)
        slot = (
            next((s for s in slots if s.id == str(slot_id)), None)
            if slot_id
            else next((s for s in slots if s.member_id == str(member_id)), None)
        )
        require(slot is not None, "角色席位不存在", 404)
        require(
            slot.character_snapshot.get("ruleset_id") == "coc7-character-creation",
            "SAN 结算仅支持已核对的第七版角色",
            422,
        )
        return slot

    async def request(self, session, room, args, run=None, encounter=None):
        require(room.status == "running", "请先恢复游戏")
        member = await session.get(RoomMember, str(args.target_member_id))
        require(
            member and member.room_id == room.id and member.active and member.role == "player",
            "SAN 目标不存在",
            404,
        )
        slot = await self.slot(session, room, member.id)
        entity = await self.agents.entities.entity(session, room.id, args.entity_id)
        require(entity and entity.snapshot.get("status") == "approved", "SAN 实体未批准", 403)
        effect = next(
            (
                SanityEffect.model_validate(e)
                for e in entity.snapshot.get("sanity_effects", [])
                if e["id"] == args.effect_id
            ),
            None,
        )
        require(effect, "缺少批准的 SAN 效果，请主机在准备工作台裁定", 422)
        source = await session.get(RoomEvent, (room.id, args.source_event_seq))
        require(source is not None, "遭遇事件不存在", 404)
        if encounter:
            participants = encounter["target_member_ids"]
            require(
                encounter["status"] == "approved"
                and member.id in participants
                and slot.id == encounter["slot_ids"][participants.index(member.id)],
                "遭遇者或角色席位已变化",
                403,
            )
        elif not run:
            require(
                getattr(args, "encounter_confirmed", False) and args.reason.strip(),
                "主机须确认实际遭遇与受影响角色；选中实体不等于目睹恐怖",
                422,
            )
        else:
            require(False, "模型只能提出 SAN 提案，须等待行动后遭遇验证", 403)
        if (
            effect.trigger == "action_target"
            or effect.kp_enabled
            and source.type in {"action.submitted", "agent.action_proposed"}
        ):
            require(
                source.type in {"action.submitted", "agent.action_proposed"}
                and (encounter or source.actor_member_id == member.id)
                and (encounter or source.payload.get("target_entity_id") == args.entity_id),
                "SAN 效果不匹配具体调查遭遇",
                403,
            )
        else:
            require(
                source.type == "entity.revealed"
                and source.payload.get("entity_id", source.payload.get("id")) == args.entity_id,
                "SAN 效果不匹配具体实体揭示事件",
                403,
            )
        check_id = str(
            uuid5(NAMESPACE_URL, f"{room.id}:{slot.id}:{source.seq}:{effect.id}:{args.entity_id}")
        )
        old = await session.get(CheckRecord, check_id)
        for prior in await session.scalars(
            select(CheckRecord).where(
                CheckRecord.room_id == room.id, CheckRecord.target_member_id == member.id
            )
        ):
            previous = prior.document.get("sanity") or {}
            if (previous.get("source_event_seq"), previous.get("entity_id")) == (
                source.seq,
                args.entity_id,
            ):
                require(
                    previous["effect"]["id"] == effect.id, "同一角色的同一遭遇事件已经选定 SAN 效果"
                )
        if old and not old.document.get("sanity_rewound"):
            return old
        prior = await self.encounters.previous(session, room, args.entity_id, effect.id, member.id)
        if prior:
            require(
                effect.repeat == "host_confirmed"
                and (
                    encounter.get("repeat_confirmed")
                    if encounter
                    else getattr(args, "repeat_confirmed", False)
                ),
                "此恐怖已经遭遇；重复遭遇须有配置及主机明确确认",
            )
        cycle = await self.agents.cycle(session, room.id, active=True)
        if run:
            require(
                cycle
                and cycle.id == run.cycle_id
                and cycle.state.get("request_category") != "rule_question",
                "SAN 请求不属于当前调查回合",
                403,
            )
            require(
                source.seq == cycle.state["triggering_event_seq"]
                or source.payload.get("cycle_id") == cycle.id
                or encounter
                and any(
                    item.get("status") == "approved"
                    and item.get("source_event_seq") == source.seq
                    and item.get("entity_id") == args.entity_id
                    and item.get("effect_id") == args.effect_id
                    and item.get("answer_event_seq") == cycle.state["triggering_event_seq"]
                    and member.id in item.get("target_member_ids", [])
                    for item in cycle.state.get("sanity_resumed", [])
                ),
                "SAN 遭遇不属于本回合",
                403,
            )
            require(not cycle.state.get("pending_check_id"), "本轮已有等待项")
        else:
            require(cycle is None, "请等待当前回合结束，再由主机发起遭遇")
            cycle_id = str(uuid4())
            cycle = AgentCycle(
                id=cycle_id,
                room_id=room.id,
                status="waiting_for_roll",
                state={
                    "cycle_id": cycle_id,
                    "room_id": room.id,
                    "request_category": "san_encounter",
                    "triggering_event_seq": source.seq,
                    "triggering_member_id": member.id,
                    "current_node": "wait_for_human_roll",
                    "status": "waiting_for_roll",
                    "pending_check_id": check_id,
                    "wait_reason": "human_roll",
                    "call_count": 0,
                    "tool_count": 0,
                    "tool_results": [],
                    "completed_teammate_ids": [],
                },
            )
            session.add(cycle)
        state, character = runtime_character(room, slot)
        require(
            character.san is not None and 0 < character.san <= character.san_max,
            "当前 SAN 为零、未定义或超出上限，请主机核对角色",
            422,
        )
        if character.sanity.day_start_san is None:
            character.sanity.day_start_san = character.san
        progress = SanityProgress(
            origin=encounter["origin"] if encounter else "host",
            effect=effect,
            source_event_seq=source.seq,
            entity_id=args.entity_id,
            before=character.san,
        )
        check = PendingCheck(
            id=UUID(check_id),
            room_id=UUID(room.id),
            slot_id=UUID(slot.id),
            target_member_id=args.target_member_id,
            name="SAN",
            kind="attribute",
            value=character.san,
            display_name="SAN",
            requester=UUID(room.host_member_id),
            agent_run_id=UUID(run.id) if run else uuid4(),
            reason="已批准的理智遭遇",
            visibility=effect.visibility,
            sanity=progress.model_dump(mode="json"),
        )
        if old:
            record = old
            record.cycle_id, record.status, record.document = (
                cycle.id,
                "pending",
                check.model_dump(mode="json"),
            )
        else:
            record = CheckRecord(
                id=check_id,
                room_id=room.id,
                cycle_id=cycle.id,
                target_member_id=member.id,
                agent_run_id=str(check.agent_run_id),
                status="pending",
                document=check.model_dump(mode="json"),
            )
            session.add(record)
        cycle.state = {**cycle.state, "pending_check_id": check_id}
        room.session_state = state.model_dump(mode="json")
        self.rooms.append(
            session,
            room,
            "check.requested",
            member.id,
            {**sanity_public(record.document), "cycle_id": cycle.id},
            effect.visibility,
        )
        if character.sanity.phase in {"bout", "awaiting_symptom"}:
            progress.immune, progress.loss, progress.after, progress.stage = (
                True,
                0,
                character.san,
                "done",
            )
            await self.finish(session, room, record, check, progress)
        self.agents.cycle_event(session, room, cycle)
        return record

    async def fixed_roll(self, session, room, record, stage, formula):
        # Survives rewind. Amounts/resources never come from this ledger.
        for event in await session.scalars(
            select(RoomEvent).where(
                RoomEvent.room_id == room.id, RoomEvent.type == "sanity.dice_fixed"
            )
        ):
            if event.payload["check_id"] == record.id and event.payload["stage"] == stage:
                require(event.payload["roll"]["formula"] == formula, "既有骰点公式不一致")
                return event.payload["roll"]
        roll = self.rooms.dice.roll(formula, "sanity:" + stage).model_dump(mode="json")
        self.rooms.append(
            session,
            room,
            "sanity.dice_fixed",
            room.host_member_id,
            {"check_id": record.id, "stage": stage, "roll": roll},
            "host_only",
        )
        return roll

    async def roll(self, session, room, record, expected_stage, automatic=False):
        check = PendingCheck.model_validate(record.document)
        progress = SanityProgress.model_validate(check.sanity)
        if expected_stage in progress.rolls or progress.stage == "done":
            return
        require(
            record.status == "pending" and progress.stage == expected_stage,
            "阶段已变化，请刷新后确认当前骰子",
        )
        require(progress.stage in {"san", "loss", "int", "duration"}, "请主机选择疯狂症状")
        member = await session.get(RoomMember, record.target_member_id)
        require(
            member and member.active and (member.controller_type == "agent") == automatic,
            "检定操作来源不匹配",
            403,
        )
        slot = await self.slot(session, room, member.id)
        require(slot.id == str(check.slot_id), "角色分配已变化")
        state, character = runtime_character(room, slot)
        formula = (
            progress.formula
            if progress.stage == "loss"
            else "1d10"
            if progress.stage == "duration"
            else "1d100"
        )
        roll = await self.fixed_roll(session, room, record, progress.stage, formula)
        progress.rolls[progress.stage] = roll
        total = roll["total"]
        if progress.stage == "san":
            require(character.san == progress.before, "待检定期间 SAN 已变化")
            check.dice = {"selected": total, "roll_record": roll}
            check.result = judge_sanity(character.san, total)
            progress.formula = (
                progress.effect.success_loss
                if check.result["passed"]
                else progress.effect.failure_loss
            )
            low, high = loss_bounds(progress.formula)
            if check.result["level"] == "fumble":
                progress.loss = high
            elif low == high:
                progress.loss = low
            else:
                progress.stage = "loss"
            if not check.result["passed"]:
                self.rooms.append(
                    session,
                    room,
                    "sanity.involuntary",
                    member.id,
                    {"cycle_id": record.cycle_id, "text": progress.effect.involuntary_action},
                    check.visibility,
                )
        elif progress.stage == "loss":
            progress.loss = total
        elif progress.stage == "int":
            value = check_value(slot.character_snapshot, "attribute", "int")
            progress.rolls["int"]["result"] = judge(value, "regular", total)
            if progress.rolls["int"]["result"]["passed"]:
                self.start_insanity(state, character, record, progress, "temporary")
            else:
                progress.stage = "done"
        else:
            character.sanity.ends_minute = state.game_minute + total * 60
            progress.stage = "symptom"
        if progress.loss is not None and progress.after is None:
            actual = min(character.san, progress.loss)
            character.san -= actual
            character.sanity.day_loss += actual
            progress.loss, progress.after = actual, character.san
            trigger = insanity_trigger(
                character.san,
                actual,
                character.sanity.day_start_san or 0,
                character.sanity.day_loss,
                character.sanity.kind,
            )
            if trigger in {"temporary", "indefinite", "permanent"}:
                self.start_insanity(state, character, record, progress, trigger)
            else:
                progress.stage = "int" if trigger == "int" else "done"
            character.sanity.history.append(
                {
                    "event": "loss",
                    "check_id": record.id,
                    "source_event_seq": progress.source_event_seq,
                    "entity_id": progress.entity_id,
                    "effect_id": progress.effect.id,
                    "minute": state.game_minute,
                    "day": state.sanity_day,
                    "before": progress.before,
                    "after": progress.after,
                    "loss": actual,
                }
            )
        room.session_state = state.model_dump(mode="json")
        await self.finish(session, room, record, check, progress)

    def start_insanity(self, state, character, record, progress, kind):
        sanity = character.sanity
        new = sanity.kind == "none"
        changed_kind = sanity.kind != kind
        sanity.kind = kind
        sanity.phase = "bout" if kind == "permanent" else "awaiting_symptom"
        sanity.symptom = ""
        sanity.bout_end_minute = sanity.bout_end_round = None
        sanity.trigger_id = record.id
        if changed_kind:
            sanity.started_minute = state.game_minute
        if kind != "temporary":
            sanity.ends_minute = None
        if changed_kind and kind in {"temporary", "indefinite"} and progress.effect.mythos:
            sanity.mythos_gain = min(
                99, sanity.mythos_gain + (5 if not sanity.mythos_insanity_count else 1)
            )
            sanity.mythos_insanity_count += 1
            character.san_max = max(
                0, character.san_max - (5 if sanity.mythos_insanity_count == 1 else 1)
            )
            character.san = min(character.san, character.san_max)
            progress.after = character.san
        if character.san == 0:
            sanity.kind, sanity.phase = "permanent", "bout"
        progress.stage = (
            "done"
            if sanity.kind == "permanent"
            else "duration"
            if new and kind == "temporary"
            else "symptom"
        )
        sanity.history.append(
            {
                "event": "insanity",
                "kind": sanity.kind,
                "mythos_gain": sanity.mythos_gain,
                "san_max": character.san_max,
                "san": character.san,
                "check_id": record.id,
                "minute": state.game_minute,
                "source_event_seq": progress.source_event_seq,
            }
        )

    async def finish(self, session, room, record, check, progress):
        character = SessionStateV1.model_validate(room.session_state).characters[check.slot_id]
        progress.insanity_kind = character.sanity.kind
        progress.phase, progress.symptom = character.sanity.phase, character.sanity.symptom
        check.sanity = progress.model_dump(mode="json")
        if progress.stage == "done":
            check.status, check.resolved_at = "resolved", utc_now()
        record.document, record.status = check.model_dump(mode="json"), check.status
        self.rooms.append(
            session,
            room,
            "check.resolved" if record.status == "resolved" else "sanity.progressed",
            record.target_member_id,
            {**sanity_public(record.document), "cycle_id": record.cycle_id},
            check.visibility,
        )
        cycle = await session.get(AgentCycle, record.cycle_id)
        if progress.stage == "symptom":
            cycle.status = "waiting_for_roll"
            cycle.state = {
                **cycle.state,
                "status": "waiting_for_roll",
                "wait_reason": "sanity_symptom",
            }
            self.agents.cycle_event(session, room, cycle)
        if cycle.state.get("request_category") == "san_encounter" and record.status == "resolved":
            cycle.status = "completed"
            cycle.state = {**cycle.state, "status": "completed", "wait_reason": None}
            self.agents.cycle_event(session, room, cycle)

    async def manage(self, session, room, body):
        require(
            body.damage is None or body.operation == "symptom",
            "伤害仅用于已确认疯狂发作的后果",
            422,
        )
        require(body.reason.strip(), "请填写裁定依据", 422)
        require(body.expected_revision == room.revision, "房间已更新，请刷新")
        require(room.status in {"paused", "running"}, "请先开始游戏")
        cycle = await self.agents.cycle(session, room.id, active=True)
        require(
            not cycle or cycle.status == "waiting_for_roll" and body.operation == "symptom",
            "请先完成待处理检定再推进时间或恢复",
        )
        state = SessionStateV1.model_validate(room.session_state)
        if body.operation in {"advance", "new_day"}:
            require(
                not state.combat.active
                and not any(
                    c.injury.dying or c.injury.con_pending for c in state.characters.values()
                )
                and not any(
                    p.injury.dying or p.injury.con_pending
                    for p in state.combat.participants.values()
                ),
                "存在待处理伤势，请使用战斗伤势时间推进以保留必要体质检定",
            )
            if body.minute is not None:
                require(body.minute >= state.game_minute, "游戏时间不能倒退")
                from app.rooms.resource_service import advance_time

                advance_time(
                    state,
                    body.minute,
                    key=f"sanity:{room.revision}",
                    source={"operation": body.operation, "reason": body.reason},
                    mode="sanity",
                )
            if body.round is not None:
                require(body.round >= state.game_round, "游戏轮不能倒退")
                state.game_round = body.round
            if body.operation == "new_day":
                state.sanity_day += 1
                for c in state.characters.values():
                    c.sanity.day_start_san, c.sanity.day_loss = c.san, 0
            room.session_state = state.model_dump(mode="json")
        else:
            require(body.slot_id, "请选择角色", 422)
            slot = await self.slot(session, room, slot_id=body.slot_id)
            state, character = runtime_character(room, slot)
            sanity = character.sanity
            if body.operation == "symptom":
                require(sanity.phase == "awaiting_symptom", "没有待确认的疯狂发作")
                require(body.symptom.strip(), "请主机选择并填写具体症状", 422)
                record = await session.get(CheckRecord, sanity.trigger_id)
                require(record and record.status == "pending", "待处理遭遇不存在")
                duration = await self.fixed_roll(session, room, record, "bout_duration", "1d10")
                sanity.symptom, sanity.phase = body.symptom, "bout"
                sanity.bout_end_round = (
                    state.game_round + duration["total"] if body.mode == "realtime" else None
                )
                sanity.bout_end_minute = (
                    state.game_minute + 60 * duration["total"] if body.mode == "summary" else None
                )
                check = PendingCheck.model_validate(record.document)
                progress = SanityProgress.model_validate(check.sanity)
                progress.rolls["bout_duration"] = duration
                progress.stage = "done"
                room.session_state = state.model_dump(mode="json")
                await self.finish(session, room, record, check, progress)
            elif body.operation == "end_bout":
                require(sanity.phase == "bout" and sanity.kind != "permanent", "当前发作不能解除")
                require(
                    (
                        sanity.bout_end_round is not None
                        and state.game_round >= sanity.bout_end_round
                    )
                    or (
                        sanity.bout_end_minute is not None
                        and state.game_minute >= sanity.bout_end_minute
                    ),
                    "尚未到达发作结束时间",
                )
                sanity.phase = "underlying"
            else:
                require(sanity.kind in {"temporary", "indefinite"}, "当前疯狂不能恢复")
                if sanity.kind == "temporary":
                    require(
                        body.recovery_basis == "safe_sleep"
                        or body.recovery_basis == "elapsed"
                        and sanity.ends_minute is not None
                        and state.game_minute >= sanity.ends_minute,
                        "临时疯狂尚未满足持续时间或安全睡眠条件",
                    )
                else:
                    require(
                        body.recovery_basis in {"host_treatment", "chapter_end"},
                        "不定性疯狂须主机记录治疗检定依据或章节结束裁定",
                    )
                sanity.kind, sanity.phase = "none", "none"
                sanity.ends_minute = sanity.bout_end_minute = sanity.bout_end_round = None
            sanity.history.append(
                {
                    "event": body.operation,
                    "minute": state.game_minute,
                    "round": state.game_round,
                    "reason": body.reason,
                    "symptom": sanity.symptom,
                    "basis": body.recovery_basis,
                    "kind": sanity.kind,
                    "phase": sanity.phase,
                }
            )
            room.session_state = state.model_dump(mode="json")
            self.rooms.append(
                session,
                room,
                "sanity.state_changed",
                slot.member_id,
                {
                    "operation": body.operation,
                    "kind": sanity.kind,
                    "phase": sanity.phase,
                    "symptom": sanity.symptom,
                    "minute": state.game_minute,
                    "reason": body.reason,
                },
                "actor_and_host",
            )
        self.rooms.append(
            session,
            room,
            "sanity.managed",
            room.host_member_id,
            body.model_dump(mode="json"),
            "host_only",
        )

        if body.damage is not None:
            await self.agents.combat.consequence_damage(
                session,
                room,
                record,
                str(body.damage),
                body.armor_applies,
                body.reason,
            )

    async def correct(self, session, room, body):
        require(body.reason.strip(), "请填写更正依据", 422)
        require(not await self.agents.cycle(session, room.id, active=True), "请先完成或取消回合")
        require(body.expected_revision == room.revision, "房间已更新，请刷新")
        slot = next(
            (s for s in await self.rooms.slots(session, room) if s.id == str(body.slot_id)), None
        )
        require(slot is not None, "角色席位不存在", 404)
        state = SessionStateV1.model_validate(room.session_state)
        character = state.characters[body.slot_id]
        if body.resource == "hp" and any(
            p.slot_id == str(body.slot_id) for p in state.combat.participants.values()
        ):
            require(
                body.value is not None and body.value <= (character.hp_max or 0),
                "战斗HP须在0与最大HP之间",
                422,
            )
        if body.resource == "san":
            require(
                slot.character_snapshot.get("ruleset_id") == "coc7-character-creation",
                "SAN 更正仅支持已核对的第七版角色",
                422,
            )
            state, character = runtime_character(room, slot)
            require(body.value is not None, "SAN 现值不能留空", 422)
            require(body.value <= character.san_max, "SAN 不能超过 99 − 克苏鲁神话", 422)
        before = getattr(character, body.resource)
        if body.resource == "mp":
            require(
                body.value is not None
                and character.mp_max is not None
                and body.value <= character.mp_max,
                "MP须在0与当前上限之间",
                422,
            )
            if body.value == character.mp_max:
                character.mp_recovery_progress = 0
        setattr(character, body.resource, body.value)
        if body.resource == "san" and body.value == 0:
            character.sanity.kind, character.sanity.phase = "permanent", "bout"
            character.sanity.started_minute = state.game_minute
            character.sanity.ends_minute = None
        if body.resource == "san":
            character.sanity.history.append(
                {
                    "event": "correction",
                    "minute": state.game_minute,
                    "before": before,
                    "after": body.value,
                    "reason": body.reason,
                    "kind": character.sanity.kind,
                }
            )
        room.session_state = state.model_dump(mode="json")
        self.rooms.append(
            session,
            room,
            "resource.corrected",
            slot.member_id,
            {**body.model_dump(mode="json"), "before": before},
            "actor_and_host",
        )
