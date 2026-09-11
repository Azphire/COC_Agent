"""One pending check, frozen participants, and transaction-bound opposed decisions."""

from copy import deepcopy
from types import SimpleNamespace

from app.agents.schemas import PendingCheck
from app.domain.character import utc_now
from app.persistence.room_models import RoomMember
from app.rooms.sanity_service import current_check_value, runtime_character
from app.rooms.service import require
from app.rules.check_options import luck_options
from app.rules.compound import opposed_result
from app.rules.display import resolve_check_name


class CompoundCheckService:
    def __init__(self, agents):
        self.agents, self.rooms = agents, agents.rooms

    async def freeze(self, session, room, check, facts):
        """Only authoritative character slots or approved room NPC snapshots."""
        slots = await self.rooms.slots(session, room)

        async def member_side(member_id, kind, name, bonus, penalty):
            member = await session.get(RoomMember, member_id)
            slot = next((s for s in slots if s.member_id == member_id), None)
            require(
                member
                and member.room_id == room.id
                and member.active
                and member.role == "player"
                and slot,
                "对抗参与者必须是本房间已分配角色的活跃玩家",
                422,
            )
            try:
                value = current_check_value(room, slot, kind, name)
            except (ValueError, KeyError) as error:
                require(False, str(error), 422)
            return {
                "participant_id": member_id,
                "member_id": member_id,
                "npc_id": None,
                "slot_id": slot.id,
                "label": member.display_name,
                "controller": member.controller_type,
                "kind": kind,
                "name": name,
                "value": value,
                "difficulty": "regular",
                "bonus_dice": bonus,
                "penalty_dice": penalty,
                "display_name": resolve_check_name(name, kind)["display_name"],
                "dice": None,
                "result": None,
                "settlement": None,
            }

        if check.combined:
            slot = next(s for s in slots if s.id == str(check.slot_id))
            second = check.combined
            try:
                value = current_check_value(room, slot, "skill", second.name)
            except (ValueError, KeyError) as error:
                require(False, str(error), 422)
            check.compound = {
                "components": [
                    {
                        "name": check.name,
                        "display_name": check.display_name,
                        "value": check.value,
                        "difficulty": check.difficulty,
                    },
                    {
                        "name": second.name,
                        "display_name": resolve_check_name(second.name, "skill")["display_name"],
                        "value": value,
                        "difficulty": second.difficulty,
                    },
                ]
            }
            return
        other = check.opposed
        sides = [
            await member_side(
                str(check.target_member_id),
                check.kind,
                check.name,
                check.bonus_dice,
                check.penalty_dice,
            )
        ]
        if other.opponent_member_id:
            sides.append(
                await member_side(
                    other.opponent_member_id,
                    other.kind,
                    other.name,
                    other.bonus_dice,
                    other.penalty_dice,
                )
            )
        else:
            npc_id = other.opponent_npc_id
            require(
                npc_id in facts.visible_entity_ids & facts.local_entity_ids,
                "对手 NPC 必须在当前场景可见",
                422,
            )
            npc = facts.approved_entities.get(npc_id, {})
            stats = npc.get("check_stats") or {}
            value = stats.get("attributes" if other.kind == "attribute" else "skills", {}).get(
                other.name
            )
            require(
                npc.get("type") == "npc" and type(value) is int and 0 <= value <= 999,
                "NPC 没有已准备并冻结的该项数值",
                422,
            )
            sides.append(
                {
                    "participant_id": npc_id,
                    "member_id": None,
                    "npc_id": npc_id,
                    "slot_id": None,
                    "label": npc["title"],
                    "controller": "npc",
                    "kind": other.kind,
                    "name": other.name,
                    "value": value,
                    "difficulty": "regular",
                    "bonus_dice": other.bonus_dice,
                    "penalty_dice": other.penalty_dice,
                    "display_name": resolve_check_name(other.name, other.kind)["display_name"],
                    "source": stats.get("source"),
                    "source_page": stats.get("page"),
                    "dice": None,
                    "result": None,
                    "settlement": None,
                }
            )
        check.compound = {
            "stage": "rolling",
            "participants": sides,
            "choice_index": None,
            "choice_order": [s["participant_id"] for s in sides],
            "tie_policy": "stalemate",
        }

    async def side_options(self, session, room, side):
        if side["controller"] != "human" or side.get("settlement") is None:
            return {"luck": [], "push": False}
        slot = await self.agents.sanity.slot(session, room, slot_id=side["slot_id"])
        _, character = runtime_character(room, slot)
        return {
            "luck": luck_options(side, character.luck, room.session_state.get("luck_spending")),
            "push": False,
        }

    async def authorize(self, session, room, identity, record, participant_id):
        require(
            record and record.room_id == room.id and record.document.get("opposed"),
            "对抗检定不存在",
            404,
        )
        side = next(
            (
                s
                for s in record.document["compound"]["participants"]
                if s["participant_id"] == (participant_id or identity.member_id)
            ),
            None,
        )
        require(side and side["controller"] == "human", "真人只能操作自己的对抗骰", 403)
        member = await session.get(RoomMember, side["member_id"])
        require(
            member and member.active and member.controller_type == side["controller"],
            "参与者已变更",
            403,
        )
        require(
            identity.member_id == side["member_id"]
            or identity.is_host
            and member.access_type == "host_managed",
            "只能由本人操作；主机仅可代管本地真人",
            403,
        )
        slots = await self.rooms.slots(session, room)
        require(
            any(s.id == side["slot_id"] and s.member_id == member.id for s in slots),
            "角色分配已变化",
        )
        require(room.status == "running", "请先恢复游戏")
        if record.status != "resolved":
            from app.agents.conversation import ensure_no_earlier_message

            await ensure_no_earlier_message(session, room, None)
            cycle = await self.agents.cycle(session, room.id, active=True)
            require(
                record.status == "pending"
                and cycle
                and cycle.id == record.cycle_id
                and cycle.state.get("pending_check_id") == record.id
                and cycle.status == "waiting_for_roll",
                "检定不属于当前等待项",
            )
        return side["participant_id"]

    async def roll_side(self, session, room, record, check, side):
        if side["dice"]:
            return
        # The check id + participant index stage keys survive save rewinds.
        index = check.compound["participants"].index(side)
        dice, raw = await self.agents.settlement.fixed_roll(
            session, room, record, SimpleNamespace(**side, combined=None), f"opposed:{index}"
        )
        side["dice"] = dice
        side["settlement"] = {
            "stage": "choice",
            "original_result": raw,
            "original_dice": dice,
            "luck_spent": 0,
            "push_requested": False,
        }

    async def advance(self, session, room, record, participant_id=None):
        if record.status != "pending":
            return
        check = PendingCheck.model_validate(deepcopy(record.document))
        progress = check.compound
        for side in progress["participants"]:
            if side["controller"] != "human" or side["participant_id"] == participant_id:
                await self.roll_side(session, room, record, check, side)
        if all(s["dice"] for s in progress["participants"]):
            progress["stage"] = "choice"
            for index, side in enumerate(progress["participants"]):
                if side["result"]:
                    continue
                options = await self.side_options(session, room, side)
                if options["luck"]:
                    progress["choice_index"] = index
                    break
                side["result"] = side["settlement"]["original_result"]
                side["settlement"].update(
                    stage="final", choice={"operation": "accept", "spend": None, "effort": ""}
                )
            else:
                check.result = opposed_result(progress["participants"])
                check.status, check.resolved_at = "resolved", utc_now()
                progress.update(stage="final", choice_index=None)
                record.status = "resolved"
        document = check.model_dump(mode="json")
        changed = record.document != document
        record.document = document
        if changed:
            self.agents.settlement.event(
                session,
                room,
                record,
                "check.resolved" if record.status == "resolved" else "check.rolled",
            )
        if record.status == "pending":
            await self.agents.settlement.wait(
                session,
                room,
                record,
                "opposed_choice" if progress["stage"] == "choice" else "opposed_roll",
            )

    async def choice(self, session, room, record, participant_id, body):
        check = PendingCheck.model_validate(deepcopy(record.document))
        progress = check.compound
        index = next(
            i
            for i, s in enumerate(progress["participants"])
            if s["participant_id"] == participant_id
        )
        side = progress["participants"][index]
        settlement = side.get("settlement") or {}
        receipt = body.model_dump()
        if settlement.get("stage") == "final":
            require(settlement.get("choice") == receipt, "该方已经完成选择")
            return
        require(
            progress["stage"] == "choice" and progress["choice_index"] == index,
            "双方掷骰完成后，按卡片顺序选择结果",
        )
        require(body.operation in {"accept", "luck"}, "对抗不能孤注一掷", 422)
        options = await self.side_options(session, room, side)
        result = settlement["original_result"]
        if body.operation == "luck":
            selected = next((o for o in options["luck"] if o["spend"] == body.spend), None)
            require(selected, "幸运花费不合法或余额不足", 422)
            slot = await self.agents.sanity.slot(session, room, slot_id=side["slot_id"])
            state, character = runtime_character(room, slot)
            before = character.luck
            character.luck -= body.spend
            settlement.update(luck_spent=body.spend, luck_before=before, luck_after=character.luck)
            room.session_state = state.model_dump(mode="json")
            result = selected["result"]
        side["result"] = result
        settlement.update(stage="final", choice=receipt, final_result=result)
        record.document = check.model_dump(mode="json")
        self.agents.settlement.event(
            session,
            room,
            record,
            "check.luck_spent" if body.operation == "luck" else "check.choice_made",
        )
        await self.advance(session, room, record)
