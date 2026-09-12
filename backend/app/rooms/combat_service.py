"""Combat reducer: room transaction, serial waits, immutable dice, snapshot-local effects."""

import re
from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy import select

from app.agents.conversation import initial_state
from app.persistence.agent_models import AgentCycle
from app.persistence.room_models import RoomEvent, RoomMember
from app.rooms.combat_schemas import Combatant, Weapon, unarmed
from app.rooms.schemas import SessionStateV1
from app.rooms.service import require
from app.rules.check_options import luck_options
from app.rules.checks import roll_check
from app.rules.combat import apply_injury, damage_bounds, damage_plan, melee_result


def load_state(room):
    state = SessionStateV1.model_validate(room.session_state)
    for p in state.combat.participants.values():
        if p.slot_id and UUID(p.slot_id) in state.characters:
            c = state.characters[UUID(p.slot_id)]
            p.hp, p.armor, p.injury = c.hp, c.armor, c.injury
            p.hp_max, p.weapons = c.hp_max or p.hp_max, c.weapons or p.weapons
    return state


def store_state(room, state):
    for p in state.combat.participants.values():
        if p.slot_id and UUID(p.slot_id) in state.characters:
            c = state.characters[UUID(p.slot_id)]
            for key in ("hp", "hp_max", "armor", "injury", "weapons"):
                setattr(c, key, getattr(p, key))
    room.session_state = state.model_dump(mode="json")


def current_actor(combat):
    return combat.order[combat.index] if combat.active and combat.order else None


def capable(p):
    return not (p.injury.dead or p.injury.unconscious or p.injury.dying or p.hp == 0)


def weapon_for(p, weapon_id):
    weapon = next((w for w in p.weapons if w.id == weapon_id), None)
    require(weapon and weapon.quantity > 0, "武器不存在或数量不足", 422)
    return weapon


class CombatService:
    def __init__(self, agents):
        self.agents, self.rooms = agents, agents.rooms

    @staticmethod
    def route(room, text):
        # Routing only. A model still distinguishes statements, questions and actual attempts.
        return bool(
            room.session_state.get("combat", {}).get("active")
            or re.search(
                r"攻击|挥拳|出拳|一拳|开枪|射击|拔枪|砍向|刺向|打向|掏枪|战斗|急救|包扎|医学治疗",
                text,
            )
        )

    async def automatic(self, session, p):
        if not p.member_id:
            return True
        member = await session.get(RoomMember, p.member_id)
        return bool(member and member.controller_type == "agent")

    async def authorize(self, session, room, identity, p):
        if not p.member_id:
            require(identity.is_host, "NPC仅由KP或主机操作", 403)
            return
        member = await session.get(RoomMember, p.member_id)
        require(member and member.room_id == room.id and member.active, "参与者不再有效", 403)
        require(
            identity.member_id == p.member_id
            or identity.is_host
            and (member.access_type == "host_managed" or member.controller_type == "agent"),
            "真人行动和防御只能由本人选择",
            403,
        )

    def member_profile(self, slot, state, scene, **overrides):
        snapshot, c = slot.character_snapshot, state.characters[UUID(slot.id)]
        require(
            snapshot.get("ruleset_id") == "coc7-character-creation", "仅支持已核对的CoC7角色", 422
        )
        c.hp_max = c.hp_max or snapshot["derived_values"]["hp"]
        return Combatant(
            id=slot.member_id,
            member_id=slot.member_id,
            slot_id=slot.id,
            label=slot.public_summary["name"],
            scene_id=scene,
            attributes=snapshot["effective_attributes"],
            skills=snapshot["skill_values"],
            hp=c.hp,
            hp_max=c.hp_max,
            armor=c.armor,
            injury=c.injury,
            weapons=c.weapons or [unarmed()],
            damage_bonus=str(snapshot["derived_values"].get("damage_bonus", "0")).removeprefix("+"),
            source=f"角色快照:{slot.id}",
            **overrides,
        )

    async def setup(self, session, room, body):
        require(body.expected_revision == room.revision, "房间已更新")
        state = load_state(room)
        require(not state.combat.active and not state.combat.pending_id, "战斗中不能改写数值")
        require(not await self.agents.cycle(session, room.id, active=True), "请先完成待处理事项")
        module = await self.agents.module(session, room.id)
        require(module, "请先绑定模组")
        scene = module.state["scene_id"]
        if body.member_id:
            slot = await self.agents.sanity.slot(session, room, member_id=str(body.member_id))
            c = state.characters[UUID(slot.id)]
            c.armor, c.weapons = body.armor, body.weapons
            p = self.member_profile(
                slot, state, scene, team=body.team, stats_public=body.stats_public
            )
            p.source += f"；主机配置:{body.reason}"
        else:
            p = body.npc.model_copy(deep=True)
            require(p.scene_id == scene, "NPC必须位于当前场景", 422)
            require(
                p.id not in {m.id for m in await self.rooms.members(session, room)},
                "NPC ID与成员冲突",
                422,
            )
            p.npc_id = p.id
            p.source = f"主机明确创建:{body.reason}；{p.source}"
        state.combat.participants[p.id] = p
        store_state(room, state)
        self.rooms.append(
            session,
            room,
            "combat.configured",
            room.host_member_id,
            {"participant": p.model_dump(mode="json")},
            "host_only",
        )

    async def ensure_members(self, session, room, state, scene):
        for slot in await self.rooms.slots(session, room):
            if not slot.member_id or slot.member_id in state.combat.participants:
                continue
            member = await session.get(RoomMember, slot.member_id)
            if (
                member.active
                and slot.character_snapshot.get("ruleset_id") == "coc7-character-creation"
            ):
                state.combat.participants[member.id] = self.member_profile(slot, state, scene)

    def order(self, combat, remaining=False):
        priority = []

        def key(pid):
            p = combat.participants[pid]
            ready = [
                w for w in p.weapons if w.kind == "firearm" and w.ready and w.ammo and not w.jammed
            ]
            if ready:
                priority.append(pid)
            skill = max((p.skills[w.skill] for w in ready or p.weapons), default=0)
            return (-p.attributes["dex"] - (50 if ready else 0), -skill, pid)

        if remaining:
            combat.order[combat.index :] = sorted(combat.order[combat.index :], key=key)
        else:
            combat.order.sort(key=key)
        combat.firearm_priority = priority

    def start(self, session, room, state, scene, reason):
        combat = state.combat
        combat.id, combat.active, combat.round, combat.index = str(uuid4()), True, 1, 0
        combat.order = [
            p.id
            for p in combat.participants.values()
            if p.scene_id == scene and p.public and capable(p)
        ]
        require(len(combat.order) >= 2, "至少需要两名有来源的战斗参与者", 422)
        combat.defenses, combat.skip_turn = {}, []
        combat.turn_key += 1
        self.order(combat)
        combat.reason = reason
        self.rooms.append(
            session,
            room,
            "combat.started",
            room.host_member_id,
            {"combat_id": combat.id, "reason": reason, "round": 1},
        )

    async def fixed(self, session, room, action, key, *, check=None, formula=None):
        stage_key = action["id"] + ":" + key
        for event in await session.scalars(
            select(RoomEvent).where(
                RoomEvent.room_id == room.id, RoomEvent.type == "combat.dice_fixed"
            )
        ):
            if event.payload["key"] == stage_key:
                require(event.payload["basis"] == (check or formula), "固定骰的依据不一致")
                return deepcopy(event.payload["roll"])
        if check:
            dice, result = roll_check(
                self.rooms.dice,
                check["value"],
                check["difficulty"],
                check.get("bonus", 0),
                check.get("penalty", 0),
            )
            roll = {"dice": dice, "raw": result, "result": result}
        else:
            lo, hi = damage_bounds(formula)
            roll = (
                {"formula": formula, "total": lo}
                if lo == hi
                else self.rooms.dice.roll(formula, "combat").model_dump(mode="json")
            )
        self.rooms.append(
            session,
            room,
            "combat.dice_fixed",
            room.host_member_id,
            {"key": stage_key, "basis": check or formula, "roll": roll},
            "host_only",
        )
        return deepcopy(roll)

    def roll_basis(self, p, name, *, kind="skill", difficulty="regular", bonus=0, penalty=0):
        values = p.attributes if kind == "attribute" else p.skills
        require(name in values, "参与者缺少此技能或属性数值", 422)
        return {
            "participant_id": p.id,
            "kind": kind,
            "name": name,
            "value": values[name],
            "difficulty": difficulty,
            "bonus": min(2, bonus),
            "penalty": min(2, penalty),
        }

    async def create_action(
        self, session, room, cycle, actor_id, decision, *, key=None, turn_key=None
    ):
        state = load_state(room)
        combat, action_id = state.combat, key or cycle.id
        if action_id in combat.actions:
            return combat.actions[action_id]
        require(not combat.pending_id, "先完成当前待防御或待掷事项")
        module = await self.agents.module(session, room.id)
        scene = module.state["scene_id"]
        await self.ensure_members(session, room, state, scene)
        actor = combat.participants.get(actor_id)
        require(
            actor and actor.scene_id == scene and capable(actor),
            "行动者缺少当前场景数据或无法行动",
            422,
        )
        op = decision.operation
        target = combat.participants.get(decision.target_id)
        if op in {"attack", "first_aid", "medicine"}:
            require(
                target and target.scene_id == scene and target.public and not target.injury.dead,
                "目标不在当前可见场景或已死亡",
                422,
            )
        if op == "attack" and not combat.active:
            require(turn_key is None or turn_key == combat.turn_key, "行动顺序已变化，请刷新")
            self.start(session, room, state, scene, decision.reason)
            turn_key = None
            # Starting combat never grants a free attack outside the DEX order.
            if current_actor(combat) != actor_id:
                store_state(room, state)
                cycle.state = {**cycle.state, "combat_started_only": True}
                return None
        if combat.active:
            require(turn_key is None or turn_key == combat.turn_key, "行动顺序已变化，请刷新")
            require(current_actor(combat) == actor_id, "还未轮到此参与者行动")
            selected = next((w for w in actor.weapons if w.id == decision.weapon_id), None)
            if actor_id in combat.firearm_priority and not (
                op == "attack" and selected and selected.kind == "firearm"
            ):
                for w in actor.weapons:
                    w.ready = False
                self.order(combat, remaining=True)
                combat.turn_key += 1
                if current_actor(combat) != actor_id:
                    store_state(room, state)
                    cycle.state = {
                        **cycle.state,
                        "combat_started_only": True,
                        "combat_rejection": "本次不射击，放弃枪械先手，按通常敏捷等待行动。",
                    }
                    return None
        action = {
            "id": action_id,
            "cycle_id": cycle.id,
            "actor_id": actor_id,
            "operation": op,
            "target_id": decision.target_id,
            "reason": decision.reason,
            "round": combat.round,
            "stage": "finish",
            "rolls": {},
            "bases": {},
            "decisions": {},
            "turn_key": combat.turn_key,
            "damage": None,
            "summary": "",
        }
        if op == "attack":
            require(
                target.id != actor_id and capable(target) and target.id in combat.order,
                "基础攻击需要仍在战斗中的可行动目标；处决由主机裁定",
                422,
            )
            weapon = weapon_for(actor, decision.weapon_id or "unarmed")
            require(
                not weapon.jammed and (weapon.kind != "firearm" or weapon.ammo > 0),
                "武器故障或弹药不足",
                422,
            )
            action.update(
                stage="defense",
                weapon=weapon.model_dump(mode="json"),
                damage_bonus=actor.damage_bonus,
                range_band=decision.range_band,
            )
            difficulty = {
                "base": "regular",
                "point_blank": "regular",
                "long": "hard",
                "extreme": "extreme",
            }[decision.range_band]
            action["bases"]["attack"] = self.roll_basis(
                actor,
                weapon.skill,
                difficulty=difficulty if weapon.kind == "firearm" else "regular",
                bonus=int(weapon.kind == "melee" and combat.defenses.get(target.id, 0) >= 1)
                + int(weapon.kind == "firearm" and decision.range_band == "point_blank"),
            )
        elif op in {"first_aid", "medicine"}:
            require(target.hp < target.hp_max or target.injury.stabilized, "目标不需要治疗", 422)
            if op == "medicine":
                require(not combat.active, "医学治疗至少一小时，请先结束战斗", 422)
                require(
                    not target.injury.dying or target.injury.stabilized, "濒死者须先急救稳定", 422
                )
                require(
                    not target.injury.medicine_attempted, "此伤势已尝试医学治疗，不能反复掷骰", 422
                )
                require(
                    not any(
                        p.injury.dying and not p.injury.stabilized and not p.injury.dead
                        for p in combat.participants.values()
                    ),
                    "先稳定所有濒死者，不能跳过一小时内的濒死检定",
                    422,
                )
            else:
                require(not target.injury.stabilized, "伤势已暂时稳定，接下来需要医学治疗", 422)
                require(
                    target.injury.dying or not target.injury.first_aid_attempted,
                    "此伤势已尝试急救；后续需孤注裁定",
                    422,
                )
                require(
                    target.injury.dying
                    or target.injury.last_damage_minute is not None
                    and state.game_minute - target.injury.last_damage_minute <= 60,
                    "已超过急救的一小时窗口",
                    422,
                )
            action["bases"]["treatment"] = self.roll_basis(
                actor,
                "first_aid" if op == "first_aid" else "medicine",
                difficulty="hard"
                if op == "medicine" and state.sanity_day > target.injury.damage_day
                else "regular",
            )
            action["stage"] = "treatment_roll"
        elif op == "reload":
            weapon = weapon_for(actor, decision.weapon_id)
            require(
                weapon.kind == "firearm"
                and not weapon.jammed
                and weapon.reserve > 0
                and weapon.ammo < weapon.capacity,
                "无法装填此武器",
                422,
            )
            count = min(2, weapon.capacity - weapon.ammo, weapon.reserve)
            weapon.ammo += count
            weapon.reserve -= count
            weapon.ready = True
            action["summary"] = f"{actor.label}装填{count}发弹药。"
        elif op == "end":
            require(combat.active, "当前没有战斗")
            combat.order.remove(actor_id)
            # Removal shifts the next actor into this index. Let the normal turn
            # advance handle wrapping, skipped turns and end-of-round health.
            combat.index -= 1
            action["summary"] = f"{actor.label}脱离战斗。"
        elif op == "pass":
            actor.injury.prone = False
            action["summary"] = f"{actor.label}结束本次行动。"
        else:
            require(False, "此操作不属于战斗资源结算", 422)
        combat.actions[action_id], combat.pending_id = action, action_id
        cycle.state = {**cycle.state, "combat_action_id": action_id, "combat_flow": True}
        store_state(room, state)
        self.rooms.append(
            session,
            room,
            "combat.action_created",
            actor.member_id,
            {
                "action_id": action_id,
                "cycle_id": cycle.id,
                "actor": actor.label,
                "operation": op,
                "target": target.label if target else None,
                "reason": decision.reason,
            },
        )
        await self.advance(session, room, cycle)
        return load_state(room).combat.actions[action_id]

    async def choose_defense(self, session, room, state, action, operation, weapon_id=None):
        combat, target = state.combat, state.combat.participants[action["target_id"]]
        ranged = action["weapon"]["kind"] == "firearm"
        require(
            operation in ({"cover", "take"} if ranged else {"dodge", "fight_back"}),
            "该攻击不适用此防御",
            422,
        )
        if operation == "fight_back":
            weapon = weapon_for(target, weapon_id or "unarmed")
            require(weapon.kind == "melee", "反击只能使用近战武器", 422)
            action["counter_weapon"], action["counter_bonus"] = (
                weapon.model_dump(mode="json"),
                target.damage_bonus,
            )
            basis = self.roll_basis(target, weapon.skill)
        else:
            basis = self.roll_basis(target, "dodge") if operation != "take" else None
        action["defense"] = operation
        if basis:
            action["bases"]["defense"] = basis
        if not ranged:
            combat.defenses[target.id] = combat.defenses.get(target.id, 0) + 1
        if operation == "cover" and target.id not in combat.skip_turn:
            combat.skip_turn.append(target.id)
        action["stage"] = "defense_roll" if operation == "cover" else "attack_roll"

    def options(self, state, action, role):
        basis, roll = action["bases"][role], action["rolls"][role]
        p = state.combat.participants[basis["participant_id"]]
        luck = state.characters[UUID(p.slot_id)].luck if p.slot_id else None
        return luck_options(
            {**basis, "malfunction": roll.get("malfunction", False), "result": roll["raw"]},
            luck,
            state.luck_spending,
        )

    async def advance(self, session, room, cycle):
        state = load_state(room)
        combat = state.combat
        action = combat.actions[cycle.state["combat_action_id"]]
        if action["stage"] == "done":
            return
        for _ in range(40):
            stage = action["stage"]
            if stage == "defense":
                p = combat.participants[action["target_id"]]
                if not await self.automatic(session, p):
                    break
                melee = next((w.id for w in p.weapons if w.kind == "melee" and w.quantity), None)
                choice = (
                    "take"
                    if action["weapon"]["kind"] == "firearm"
                    else "fight_back"
                    if melee
                    else "dodge"
                )
                await self.choose_defense(session, room, state, action, choice, melee)
            elif stage.endswith("_roll"):
                role = stage.removesuffix("_roll")
                p = combat.participants[action["bases"][role]["participant_id"]]
                if not await self.automatic(session, p):
                    break
                await self.perform_roll(session, room, state, action, role)
            elif stage.endswith("_choice"):
                role = stage.removesuffix("_choice")
                p = combat.participants[action["bases"][role]["participant_id"]]
                if not await self.automatic(session, p) and self.options(state, action, role):
                    break
                self.after_roll(state, action, role)
            elif stage == "damage":
                await self.settle_damage(session, room, state, action)
            elif stage == "treatment":
                await self.treat(session, room, state, action)
            elif stage == "finish":
                self.finish_action(session, room, state, action)
            elif stage == "health":
                due = next((p for p in combat.participants.values() if p.injury.con_pending), None)
                if not due:
                    self.normalize_turn(session, room, state)
                    action["stage"], combat.pending_id = "done", None
                    break
                role = "health_" + due.id + "_" + due.injury.con_pending
                action["bases"][role] = self.roll_basis(due, "con", kind="attribute")
                action["health_kind"], action["stage"] = due.injury.con_pending, role + "_roll"
            else:
                break
        store_state(room, state)
        cycle.status = "waiting_for_roll"
        cycle.state = {
            **cycle.state,
            "status": cycle.status,
            "wait_reason": None if action["stage"] == "done" else "combat:" + action["stage"],
        }
        self.agents.cycle_event(session, room, cycle)

    async def perform_roll(self, session, room, state, action, role):
        if role not in action["rolls"]:
            basis = action["bases"][role]
            firearm = role == "attack" and action["weapon"]["kind"] == "firearm"
            if firearm:
                w = weapon_for(
                    state.combat.participants[action["actor_id"]], action["weapon"]["id"]
                )
                require(w.ammo > 0 and not w.jammed, "枪械当前无法击发", 422)
                w.ammo -= 1
                w.ready, action["ammo_after"] = True, w.ammo
            roll = await self.fixed(session, room, action, role, check=basis)
            if firearm:
                roll["malfunction"] = roll["raw"]["total"] >= action["weapon"]["malfunction"]
                if roll["malfunction"]:
                    w.jammed = True
                    roll["result"] = {**roll["raw"], "passed": False, "outcome": "malfunction"}
            action["rolls"][role] = roll
        action["stage"] = role + "_choice"

    def after_roll(self, state, action, role):
        if role.startswith("health_"):
            p = state.combat.participants[action["bases"][role]["participant_id"]]
            injury, kind = p.injury, action["health_kind"]
            passed = action["rolls"][role]["result"]["passed"]
            if not passed:
                if kind == "wound":
                    injury.unconscious = True
                elif kind == "dying":
                    injury.dead, injury.dying = True, False
                else:
                    p.hp, injury.stabilized = 0, False
                    injury.dying = injury.unconscious = True
                    injury.dying_since_round = state.combat.round
            if kind == "hourly" and passed:
                injury.check_due_minute = state.game_minute + 60
            injury.con_pending, action["stage"] = None, "health"
        elif role == "treatment":
            action["stage"] = "treatment"
        elif role == "defense" and action["defense"] == "cover":
            action["bases"]["attack"]["penalty"] += int(action["rolls"][role]["result"]["passed"])
            action["stage"] = "attack_roll"
        elif role == "attack" and action["defense"] in {"dodge", "fight_back"}:
            action["stage"] = "defense_roll"
        else:
            action["stage"] = "damage"

    async def settle_damage(self, session, room, state, action):
        attack, mode = action["rolls"]["attack"]["result"], action["defense"]
        winner = (
            ("attack" if attack["passed"] else None)
            if mode in {"cover", "take"}
            else melee_result(attack, action["rolls"]["defense"]["result"], mode)
        )
        action["winner"] = winner
        actor, target = (state.combat.participants[action[k]] for k in ("actor_id", "target_id"))
        if winner:
            counter = winner == "defense"
            p = actor if counter else target
            weapon = Weapon.model_validate(action["counter_weapon" if counter else "weapon"])
            bonus = action["counter_bonus" if counter else "damage_bonus"]
            maximum, formulas = damage_plan(
                weapon,
                bonus,
                action["rolls"][winner]["result"],
                counter=counter,
                difficulty=action["bases"]["attack"]["difficulty"],
            )
            rolls = [
                await self.fixed(session, room, action, f"damage_{i}", formula=f)
                for i, f in enumerate(formulas)
            ]
            amount = max(0, maximum + sum(r["total"] for r in rolls))
            receipt = apply_injury(
                p,
                amount,
                armor_applies=True,
                key=action["id"],
                reason=action["reason"],
                minute=state.game_minute,
                round_number=state.combat.round,
                day=state.sanity_day,
            )
            action["damage"] = {**receipt, "target_id": p.id, "rolls": rolls, "maximum": maximum}
            action["summary"] = f"{target.label if counter else actor.label}命中{p.label}。"
        else:
            action["summary"] = "此次攻击未造成伤害。"
        action["stage"] = "finish"

    async def treat(self, session, room, state, action):
        p = state.combat.participants[action["target_id"]]
        passed, injury, before = action["rolls"]["treatment"]["result"]["passed"], p.injury, p.hp
        if action["operation"] == "first_aid":
            injury.first_aid_attempted = True
            if passed:
                p.hp = min(p.hp_max, p.hp + 1)
                if injury.dying:
                    injury.stabilized, injury.check_due_minute, injury.con_pending = (
                        True,
                        state.game_minute + 60,
                        None,
                    )
                else:
                    injury.unconscious = False
        else:
            injury.medicine_attempted = True
            state.game_minute += 60
            if passed:
                roll = await self.fixed(session, room, action, "medicine_healing", formula="1d3")
                # PDF102 excludes the dying patient's temporary first-aid HP from
                # the ordinary additive healing rule.
                baseline = 0 if injury.dying and injury.stabilized else p.hp
                p.hp = min(p.hp_max, baseline + roll["total"])
                injury.dying = injury.stabilized = False
                injury.check_due_minute = None
            for other in state.combat.participants.values():
                if (
                    other.injury.stabilized
                    and other.injury.check_due_minute is not None
                    and other.injury.check_due_minute <= state.game_minute
                ):
                    other.injury.con_pending = "hourly"
        if passed and not injury.dying and p.hp * 2 >= p.hp_max:
            injury.major_wound = False
        action["treatment"] = {
            "hp_before": before,
            "hp_after": p.hp,
            "stabilized": injury.stabilized,
        }
        action["summary"] = (
            f"{p.label}的"
            f"{'急救' if action['operation'] == 'first_aid' else '医学治疗'}"
            f"{'成功' if passed else '失败'}。"
        )
        action["stage"] = "finish"

    def next_turn(self, combat, state):
        if (
            not combat.active
            or not combat.order
            or not any(capable(combat.participants[pid]) for pid in combat.order)
        ):
            return
        for _ in range(len(combat.order) + 1):
            combat.index += 1
            if combat.index >= len(combat.order):
                combat.index = 0
                state.game_round += 1
                for p in combat.participants.values():
                    if (
                        p.injury.dying
                        and not p.injury.stabilized
                        and not p.injury.dead
                        and combat.round > (p.injury.dying_since_round or 0)
                    ):
                        p.injury.con_pending = "dying"
                combat.round += 1
                combat.defenses = {}
                self.order(combat)
            actor = current_actor(combat)
            if actor in combat.skip_turn:
                combat.skip_turn.remove(actor)
                continue
            if capable(combat.participants[actor]):
                break
        combat.turn_key += 1

    def finish_action(self, session, room, state, action):
        combat = state.combat
        if not action.get("turn_applied"):
            if not action.get("no_advance"):
                self.next_turn(combat, state)
            teams = {
                combat.participants[pid].team
                for pid in combat.order
                if capable(combat.participants[pid])
            }
            if len(teams) <= 1 and combat.active:
                combat.active = False
                self.rooms.append(
                    session,
                    room,
                    "combat.ended",
                    room.host_member_id,
                    {"combat_id": combat.id, "reason": "冲突一方已脱离或失去行动能力"},
                )
            action["turn_applied"] = True
        action["stage"] = "health"

    def normalize_turn(self, session, room, state):
        combat = state.combat
        if not combat.active:
            return
        living = [pid for pid in combat.order if capable(combat.participants[pid])]
        if len({combat.participants[pid].team for pid in living}) <= 1:
            combat.active = False
            self.rooms.append(
                session,
                room,
                "combat.ended",
                room.host_member_id,
                {"combat_id": combat.id, "reason": "一方失去行动能力"},
            )
        elif current_actor(combat) not in living:
            self.next_turn(combat, state)

    async def step(self, session, room, identity, body):
        state = load_state(room)
        combat, stage = state.combat, body.stage
        action = combat.actions.get(body.action_id)
        require(action, "战斗事项不存在", 404)
        role = stage.rsplit("_", 1)[0]
        pid = (
            action["target_id"]
            if stage == "defense"
            else action.get("bases", {}).get(role, {}).get("participant_id")
        )
        require(pid in combat.participants, "阶段不合法", 422)
        await self.authorize(session, room, identity, combat.participants[pid])
        decision = body.model_dump(mode="json")
        if stage in action["decisions"]:
            require(action["decisions"][stage] == decision, "该阶段已固定，不能改变选择")
            return
        require(
            room.status == "running"
            and combat.pending_id == body.action_id
            and action["stage"] == stage,
            "不是当前等待阶段",
        )
        cycle = await self.agents.cycle(session, room.id, active=True)
        require(cycle and cycle.id == action["cycle_id"], "先处理当前串行会话")
        if stage == "defense":
            await self.choose_defense(session, room, state, action, body.operation, body.weapon_id)
        elif stage.endswith("_roll"):
            require(body.operation == "roll", "请确认掷骰", 422)
            await self.perform_roll(session, room, state, action, role)
        elif stage.endswith("_choice"):
            require(body.operation in {"accept", "luck"}, "战斗不能孤注一掷", 422)
            if body.operation == "luck":
                selected = next(
                    (o for o in self.options(state, action, role) if o["spend"] == body.spend), None
                )
                require(selected, "幸运花费不合法，大成功／大失败／故障不能改写", 422)
                p = combat.participants[pid]
                state.characters[UUID(p.slot_id)].luck -= body.spend
                action["rolls"][role].update(result=selected["result"], luck_spent=body.spend)
            self.after_roll(state, action, role)
        else:
            require(False, "阶段不支持操作", 422)
        action["decisions"][stage] = decision
        store_state(room, state)
        await self.advance(session, room, cycle)

    async def make_cycle(self, session, room, actor, text, *, automatic=False, key=None):
        await self.agents.ensure_config(session, room)
        cycle_id = key or str(uuid4())
        event = self.rooms.append(
            session,
            room,
            "combat.action_submitted",
            actor,
            {"text": text, "cycle_id": cycle_id},
            "host_only" if automatic else "public",
        )
        state = initial_state(room.id, cycle_id, actor, event.seq, [], origin="combat")
        state.update(combat_flow=True, combat_actor_id=actor, combat_automatic=automatic)
        cycle = AgentCycle(id=cycle_id, room_id=room.id, status="running", state=state)
        session.add(cycle)
        self.agents.cycle_event(session, room, cycle)
        return cycle

    async def queue_automatic(self, session, room):
        if room.status != "running" or await self.agents.cycle(session, room.id, active=True):
            return
        state = load_state(room)
        pid = current_actor(state.combat)
        if pid and await self.automatic(session, state.combat.participants[pid]):
            await self.make_cycle(
                session, room, pid, "轮到你行动，依据公开局势选择自己的行动。", automatic=True
            )

    async def command(self, session, room, identity, operation, body):
        if operation == "control":
            require(identity.is_host, "仅主机推进规则时间或确认冲突结束", 403)
            require(body.expected_revision == room.revision, "房间已更新")
            require(
                not await self.agents.cycle(session, room.id, active=True), "请先完成待处理事项"
            )
            state = load_state(room)
            require(not state.combat.pending_id, "请先完成战斗结算")
            if body.operation == "end":
                state.combat.active = False
                self.rooms.append(
                    session,
                    room,
                    "combat.ended",
                    room.host_member_id,
                    {"combat_id": state.combat.id, "reason": body.reason},
                )
            else:
                require(not state.combat.active, "战斗中由行动顺序推进轮次")
                require(
                    not body.minutes
                    or not any(
                        p.injury.dying and not p.injury.stabilized and not p.injury.dead
                        for p in state.combat.participants.values()
                    ),
                    "濒死未稳定时请逐轮处理，不能跳过多分钟",
                )
                state.game_minute += body.minutes
                state.game_round += 1
                for p in state.combat.participants.values():
                    if p.injury.stabilized and p.injury.check_due_minute <= state.game_minute:
                        p.injury.con_pending = "hourly"
                    elif p.injury.dying and not p.injury.stabilized and not p.injury.dead:
                        p.injury.con_pending = "dying"
                self.rooms.append(
                    session,
                    room,
                    "combat.time_advanced",
                    room.host_member_id,
                    {"minute": state.game_minute, "round": state.game_round, "reason": body.reason},
                )
            store_state(room, state)
            await self.queue_health(session, room, body.reason)
            return {}
        if operation == "setup":
            require(identity.is_host, "仅主机配置有来源的战斗资料", 403)
            await self.setup(session, room, body)
        elif operation == "step":
            await self.step(session, room, identity, body)
        elif operation == "action":
            state = load_state(room)
            p = state.combat.participants.get(body.actor_id)
            require(p, "参与者不存在", 404)
            await self.authorize(session, room, identity, p)
            previous = state.combat.actions.get(str(body.client_request_id))
            if previous:
                require(
                    previous.get("request") == body.model_dump(mode="json"),
                    "请求ID已经用于其他内容",
                )
                return {}
            require(
                room.status == "running"
                and not await self.agents.cycle(session, room.id, active=True),
                "先完成当前事项",
            )
            previous_cycle = await session.get(AgentCycle, str(body.client_request_id))
            if previous_cycle:
                require(
                    previous_cycle.room_id == room.id
                    and previous_cycle.state.get("combat_request") == body.model_dump(mode="json"),
                    "请求ID已经使用",
                )
                return {}
            cycle = await self.make_cycle(
                session, room, p.id, body.reason, key=str(body.client_request_id)
            )
            cycle.state = {**cycle.state, "combat_request": body.model_dump(mode="json")}
            action = await self.create_action(
                session,
                room,
                cycle,
                p.id,
                body,
                key=str(body.client_request_id),
                turn_key=body.turn_key,
            )
            if action:
                state = load_state(room)
                state.combat.actions[action["id"]]["request"] = body.model_dump(mode="json")
                store_state(room, state)
        elif operation == "damage":
            require(identity.is_host, "只有主机能确认规则外伤害", 403)
            require(not await self.agents.cycle(session, room.id, active=True), "先完成当前事项")
            await self.confirm_damage(session, room, body)
        return {}

    async def queue_health(self, session, room, reason):
        state = load_state(room)
        due = next((p for p in state.combat.participants.values() if p.injury.con_pending), None)
        if not due:
            return
        cycle = await self.make_cycle(session, room, due.id, reason)
        key = cycle.id
        action = {
            "id": key,
            "cycle_id": key,
            "actor_id": due.id,
            "target_id": due.id,
            "stage": "health",
            "operation": "health",
            "rolls": {},
            "bases": {},
            "decisions": {},
            "summary": "伤势检查已完成。",
        }
        state.combat.actions[key], state.combat.pending_id = action, key
        cycle.state = {**cycle.state, "combat_action_id": key}
        store_state(room, state)
        await self.advance(session, room, cycle)

    async def confirm_damage(self, session, room, body):
        state, key = load_state(room), str(body.client_request_id)
        p = state.combat.participants.get(body.target_id)
        require(p, "参与者不存在", 404)
        existing = next(
            (other for other in state.combat.participants.values() if key in other.injury.receipts),
            None,
        )
        require(not existing or existing.id == p.id, "请求ID已用于其他目标")
        previous = p.injury.receipts.get(key)
        if previous:
            require(
                previous["raw_damage"] == body.amount
                and previous["reason"] == body.reason
                and previous.get("armor_applies") == body.armor_applies,
                "请求ID已使用",
            )
            return
        receipt = apply_injury(
            p,
            body.amount,
            armor_applies=body.armor_applies,
            key=key,
            reason=body.reason,
            minute=state.game_minute,
            round_number=state.combat.round,
            day=state.sanity_day,
        )
        store_state(room, state)
        self.rooms.append(
            session,
            room,
            "combat.damage_confirmed",
            p.member_id,
            receipt,
            "actor_and_host" if p.member_id else "host_only",
        )
        if p.injury.con_pending:
            cycle = await self.make_cycle(session, room, p.id, body.reason)
            action = {
                "id": key,
                "cycle_id": cycle.id,
                "actor_id": p.id,
                "target_id": p.id,
                "stage": "health",
                "operation": "damage",
                "rolls": {},
                "bases": {},
                "decisions": {},
                "summary": "已确认伤害。",
                "damage": {**receipt, "target_id": p.id},
            }
            state.combat.actions[key], state.combat.pending_id = action, key
            cycle.state = {**cycle.state, "combat_action_id": key}
            store_state(room, state)
            await self.advance(session, room, cycle)

    async def consequence_damage(self, session, room, record, formula, armor_applies, reason):
        """Host/KP-confirmed push damage, including its mandatory injury check before narration."""
        state = load_state(room)
        module = await self.agents.module(session, room.id)
        await self.ensure_members(session, room, state, module.state["scene_id"])
        p = state.combat.participants[record.target_member_id]
        key = record.id + ":consequence"
        action = {
            "id": key,
            "cycle_id": record.cycle_id,
            "actor_id": p.id,
            "target_id": p.id,
            "stage": "health",
            "operation": "damage",
            "rolls": {},
            "bases": {},
            "decisions": {},
            "summary": "已执行确认的伤害后果。",
        }
        roll = await self.fixed(session, room, action, "consequence_damage", formula=formula)
        receipt = apply_injury(
            p,
            roll["total"],
            armor_applies=armor_applies,
            key=key,
            reason=reason,
            minute=state.game_minute,
            round_number=state.combat.round,
            day=state.sanity_day,
        )
        action["damage"] = {**receipt, "target_id": p.id, "rolls": [roll]}
        state.combat.actions[key] = action
        state.combat.pending_id = key
        cycle = await session.get(AgentCycle, record.cycle_id)
        cycle.state = {
            **cycle.state,
            "combat_flow": True,
            "combat_return": True,
            "combat_action_id": key,
        }
        store_state(room, state)
        self.rooms.append(
            session, room, "combat.damage_confirmed", p.member_id, receipt, "actor_and_host"
        )
        await self.advance(session, room, cycle)

    async def view(self, session, room, identity):
        state, result = load_state(room), {}
        combat = state.combat
        result.update(
            {
                k: getattr(combat, k)
                for k in ("id", "active", "round", "index", "pending_id", "turn_key")
            }
        )
        allowed = {p.id for p in combat.participants.values() if identity.is_host or p.public}
        result["order"] = [pid for pid in combat.order if pid in allowed]
        result["current_actor_id"] = (
            current_actor(combat) if current_actor(combat) in allowed else None
        )
        result["participants"] = {}
        for pid, p in combat.participants.items():
            if pid not in allowed:
                continue
            full = identity.is_host or p.member_id == identity.member_id or p.stats_public
            result["participants"][pid] = {
                "id": pid,
                "label": p.label,
                "member_id": p.member_id,
                "team": p.team,
                "incapacitated": not capable(p),
                **(
                    {
                        "hp": p.hp,
                        "hp_max": p.hp_max,
                        "armor": p.armor,
                        "injury": p.injury.model_dump(exclude={"receipts", "wounds"}),
                        "weapons": [
                            w.model_dump(exclude=set() if identity.is_host else {"source"})
                            for w in p.weapons
                        ],
                    }
                    if full
                    else {}
                ),
                **({"source": p.source} if identity.is_host else {}),
            }
        action = combat.actions.get(combat.pending_id)
        result["pending"] = self.public_action(state, action, identity) if action else None
        if action:
            stage, role = action["stage"], action["stage"].rsplit("_", 1)[0]
            pid = (
                action["target_id"]
                if stage == "defense"
                else action.get("bases", {}).get(role, {}).get("participant_id")
            )
            result["pending"]["participant_id"] = pid
            result["pending"]["weapon_kind"] = action.get("weapon", {}).get("kind")
            result["pending"]["luck_options"] = (
                self.options(state, action, role)
                if stage.endswith("_choice")
                and pid
                and (identity.is_host or combat.participants[pid].member_id == identity.member_id)
                else []
            )
        return result

    def public_action(self, state, action, identity):
        if action is None:
            return None
        result = {
            k: deepcopy(action[k])
            for k in (
                "id",
                "cycle_id",
                "actor_id",
                "target_id",
                "stage",
                "operation",
                "summary",
                "winner",
                "defense",
            )
            if k in action
        }
        result["rolls"] = {}
        for field in ("actor", "target"):
            p = state.combat.participants.get(action.get(field + "_id"))
            if p and (identity.is_host or p.public):
                result[field] = p.label
        for role, roll in action["rolls"].items():
            p = state.combat.participants[action["bases"][role]["participant_id"]]
            result["rolls"][role] = (
                {**deepcopy(roll), "basis": deepcopy(action["bases"][role])}
                if identity.is_host or p.stats_public or p.member_id == identity.member_id
                else {
                    "result": {"level": roll["result"]["level"], "passed": roll["result"]["passed"]}
                }
            )
        damage = action.get("damage")
        if damage:
            p = state.combat.participants[damage["target_id"]]
            result["damage"] = (
                deepcopy(damage)
                if identity.is_host or p.stats_public or p.member_id == identity.member_id
                else {
                    "target_id": p.id,
                    "injured": damage["damage"] > 0,
                    "incapacitated": not capable(p),
                    "armor_blocked": damage["armor"] > 0 and damage["damage"] == 0,
                }
            )
        if action.get("treatment"):
            p = state.combat.participants[action["target_id"]]
            result["treatment"] = (
                deepcopy(action["treatment"])
                if identity.is_host or p.stats_public or p.member_id == identity.member_id
                else {"treated": True}
            )
        return result
