"""Deterministic authorization. Model output cannot authorize its own side effects."""

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.agents.adjudication_schemas import (
    ActionRejection,
    KeeperPlan,
    PlayerIntent,
    ValidatedAction,
    ValidatedActionPlan,
)
from app.agents.check_policy import CheckPolicyEvaluator, CheckProposal
from app.agents.schemas import PlannedTool
from app.module_ir.schemas import TransitionRequest
from app.rooms.service import RoomError

READ_TOOLS = frozenset(
    {
        "get_current_scene",
        "list_scene_contents",
        "open_module_node",
        "lookup_module_entity",
        "list_scene_transitions",
        "get_public_scene",
        "list_public_entities",
        "lookup_public_entity",
        "inspect_approved_entities",
        "inspect_public_entities",
        "inspect_public_state",
        "inspect_character",
        "search_rules",
        "search_module",
        "get_evidence_excerpt",
    }
)
PROPOSAL_TOOLS = frozenset(
    {"request_skill_check", "request_sanity_check", "propose_module_fact", "request_host_review"}
)
STATE_TOOLS = frozenset({"reveal_entity", "reveal_clue", "transition_scene", "update_scene"})


def explicit_movement(text: str) -> bool:
    """A conservative corroboration of a model-parsed move, never an intent classifier."""
    compact = re.sub(r"\s+", "", text).lower()
    if re.search(r"\b(?:move|enter|return|leave|walk)\b", text.lower()):
        return not re.search(r"\b(?:not|never|suggest|suggests|ask|asks|if)\b|[?？]", text.lower())
    if re.search(r"(?:同伴|队友|别人|他|她).*(?:建议|说|提议|希望)", compact):
        return False
    if re.search(
        r"[?？]|要不要|是否|能否|可否|可以.*吗|如果|假如|假设|等.+再|不要|不想|不打算|没有|并未|别去|先不|暂不|想问|询问|问问|听听",
        compact,
    ):
        return False
    movement = re.search(
        r"进入|走进|走入|迈进|迈入|踏入|前往|走向|走到|离开|返回|回到|退回|穿过|过去|进去|出发|赶往|回去|移步|抵达|进到|进门|出门|跨进|跨入|穿门|\b(?:move|enter|return|leave|walk)\b",
        compact,
    )
    if not movement:
        return False
    return not re.search(r"观察|检查|调查|打听|建议|提议", compact[: movement.start()])


@dataclass
class ActionFacts:
    room_id: str
    cycle_id: str
    raw_text: str
    actor_member_id: str
    actor_slot_id: str
    actor_authorized: bool
    scene_id: str
    navigation_revision: int = 0
    structure: bool = False
    approved_entities: dict = field(default_factory=dict)
    visible_entity_ids: set = field(default_factory=set)
    local_entity_ids: set = field(default_factory=set)
    node_ids: set = field(default_factory=set)
    allowed_node_ids: set = field(default_factory=set)
    observed_node_ids: set = field(default_factory=set)
    observed_entity_ids: set = field(default_factory=set)
    observed_evidence_ids: set = field(default_factory=set)
    transitions: dict = field(default_factory=dict)
    available_transition_ids: set = field(default_factory=set)
    characters: dict = field(default_factory=dict)
    reveal_errors: dict = field(default_factory=dict)
    reveal_check_errors: dict = field(default_factory=dict)
    trusted_target_id: str | None = None
    host_review_approved: bool = False
    revealed_entity_ids: set = field(default_factory=set)
    completed_checks: list = field(default_factory=list)
    check_state: dict = field(default_factory=dict)
    member_ids: set = field(default_factory=set)
    can_move_party: bool = True


class ActionPolicyValidator:
    def validate_intent(self, intent: PlayerIntent, plan: KeeperPlan, facts: ActionFacts):
        if not facts.actor_authorized or (
            intent.actor_member_id != facts.actor_member_id
            or intent.actor_character_slot_id != facts.actor_slot_id
        ):
            return "rejected", "行动者或角色席位无权行动"
        if not intent.evidence_quote.strip() or intent.evidence_quote not in facts.raw_text:
            return "clarification_required", "行动依据不是玩家原文的真实子串"
        if plan.cycle_id != facts.cycle_id:
            return "rejected", "计划不属于当前回合"
        if intent.requires_clarification or plan.needs_clarification or intent.type == "unknown":
            return "clarification_required", "行动意图不明确"
        if intent.type == "out_of_character":
            return None
        if intent.type == "recall":
            from app.module_ir.facts import recalling

            if not recalling(facts.raw_text) or (
                intent.target_id and intent.target_id not in facts.revealed_entity_ids
            ):
                return "clarification_required", "只能回顾实际公开过的信息"
            if facts.trusted_target_id and intent.target_id != facts.trusted_target_id:
                return "clarification_required", "回顾目标与玩家所选目标不一致"
            return None
        if facts.trusted_target_id and intent.target_id != facts.trusted_target_id:
            return "clarification_required", "计划目标与玩家所选目标不一致"
        candidates = [
            e
            for eid, e in facts.approved_entities.items()
            if eid in facts.local_entity_ids
            and e.get("title")
            and (e["title"] == intent.target_text or e["title"] in facts.raw_text)
        ]
        if not facts.trusted_target_id and len({e["id"] for e in candidates}) > 1:
            titles = [e["title"] for e in candidates]
            if len(titles) != len(set(titles)):
                return "clarification_required", "当前场景有多个同名目标"
        if intent.type == "move":
            if not explicit_movement(facts.raw_text) or not explicit_movement(
                intent.evidence_quote
            ):
                return "clarification_required", "玩家尚未明确表示移动"
            transitions = [t for t in facts.transitions.values() if t.get("approved")]
            matching = [
                t
                for t in transitions
                if intent.target_id
                in {t["transition_id"], t["target_scene_node_id"], t.get("target_entity_id")}
                and intent.target_id is not None
            ]
            if len(matching) != 1:
                return "clarification_required", "移动目标不能唯一匹配当前批准出口"
        elif intent.target_id:
            if intent.target_id not in facts.visible_entity_ids | facts.member_ids | {
                facts.scene_id
            }:
                return "clarification_required", "目标不存在或不在当前可见范围"
        if intent.type == "converse" and intent.target_id not in facts.member_ids | {None}:
            entity = facts.approved_entities.get(intent.target_id)
            if (
                not entity
                or entity.get("type") != "npc"
                or intent.target_id not in facts.local_entity_ids
            ):
                return "clarification_required", "需要选择当前可见人物作为交谈目标"
        return None

    def validate(
        self,
        intent: PlayerIntent,
        plan: KeeperPlan,
        facts: ActionFacts,
        actions: list[PlannedTool],
        *,
        after_check=False,
    ):
        from app.agents.tools import TOOLS

        result = ValidatedActionPlan(
            plan_id=plan.plan_id,
            status="approved",
            expected_navigation_revision=facts.navigation_revision,
        )
        invalid = self.validate_intent(intent, plan, facts)
        if invalid:
            result.status, reason = invalid
            result.validation_reasons.append(reason)
            if result.status == "clarification_required":
                # KP free prose can contain unrevealed discoveries. A rejected
                # plan cannot publish that prose through the clarification path.
                result.clarification_question = {
                    "行动依据不是玩家原文的真实子串": "这一步你想先尝试哪一个动作？",
                    "行动意图不明确": "这一步你准备实际做什么，还是先和谁说话？",
                    "玩家尚未明确表示移动": "你是现在过去，还是先在原地查看？",
                    "移动目标不能唯一匹配当前批准出口": "你打算前往哪个位置？",
                    "当前场景有多个同名目标": "同名目标不止一个，你指的是哪处？",
                }.get(reason, "你指的是眼前哪一个人或物件？")
            result.rejected_actions = [
                ActionRejection(
                    index=i,
                    tool=t.name,
                    code="permission_denied"
                    if result.status == "rejected"
                    else "precondition_failed",
                    reason=reason,
                )
                for i, t in enumerate(actions)
            ]
            return result
        if intent.type in {"out_of_character", "recall"}:
            result.validation_reasons.append("场外讨论或回顾不执行角色行动或检定")
            return result
        if plan.focus and not plan.focus.action.strip():
            result.validation_reasons.append("本轮只有交流、建议或假设，不执行世界动作")
            result.approved_actions = [
                ValidatedAction(index=i, tool=t, phase="read")
                for i, t in enumerate(actions)
                if t.name in READ_TOOLS
            ]
            result.rejected_actions = [
                ActionRejection(
                    index=i,
                    tool=t.name,
                    code="precondition_failed",
                    reason="没有当前行动，提问或建议不能产生结果",
                )
                for i, t in enumerate(actions)
                if t.name not in READ_TOOLS
            ]
            return result
        if not facts.can_move_party and (
            plan.proposed_transition_id
            or any(t.name in {"transition_scene", "update_scene"} for t in actions)
        ):
            result.status = "rejected"
            result.validation_reasons.append("队友不能替全队移动，需要玩家决定")
            result.rejected_actions = [
                ActionRejection(
                    index=i,
                    tool=t.name,
                    code="permission_denied",
                    reason=result.validation_reasons[-1],
                )
                for i, t in enumerate(actions)
            ]
            return result
        if plan.current_scene_id != facts.scene_id:
            result.status = "rejected"
            result.validation_reasons.append("计划场景已改变，不能沿用旧目标")
            result.rejected_actions = [
                ActionRejection(
                    index=i,
                    tool=t.name,
                    code="revision_conflict",
                    reason=result.validation_reasons[0],
                )
                for i, t in enumerate(actions)
            ]
            return result
        if plan.expected_navigation_revision != facts.navigation_revision:
            result.status = "rejected"
            result.rejected_actions = [
                ActionRejection(
                    index=i,
                    tool=t.name,
                    code="revision_conflict",
                    reason="navigation revision 已改变",
                )
                for i, t in enumerate(actions)
            ]
            return result
        if len(actions) > 4:
            result.status = "rejected"
            result.validation_reasons.append("每个计划最多四个工具")
            result.rejected_actions = [
                ActionRejection(
                    index=i, tool=t.name, code="invalid_arguments", reason="工具数量超限"
                )
                for i, t in enumerate(actions)
            ]
            return result
        destinations = [
            t
            for t in facts.transitions.values()
            if intent.type == "move"
            and intent.target_id
            in {t.get("target_scene_node_id"), t.get("target_entity_id"), t.get("transition_id")}
        ]
        destination_nodes = {t["target_scene_node_id"] for t in destinations}
        destination_entities = {
            t["target_entity_id"] for t in destinations if t.get("target_entity_id")
        }
        if (
            not set(plan.source_node_ids) <= facts.allowed_node_ids
            or not set(plan.target_node_ids) <= facts.allowed_node_ids | destination_nodes
        ):
            result.status = "rejected"
            result.validation_reasons.append("节点不属于当前结构快照和授权范围")
            return result
        if (
            not set(plan.source_entity_ids) <= facts.local_entity_ids
            or not set(plan.target_entity_ids) <= facts.local_entity_ids | destination_entities
        ):
            result.status = "rejected"
            result.validation_reasons.append("实体不属于当前房间批准快照")
            return result
        if not set(plan.source_evidence_ids) <= facts.observed_evidence_ids:
            result.status = "rejected"
            result.validation_reasons.append("证据未进入本轮授权上下文")
            return result
        for i, tool in enumerate(actions):
            code, reason = None, None
            spec = TOOLS.get(tool.name)
            if (
                not spec
                or "keeper" not in spec.roles
                or tool.name not in READ_TOOLS | PROPOSAL_TOOLS | STATE_TOOLS
            ):
                code, reason = "permission_denied", "工具未注册或不属于 KP 行动权限"
            else:
                try:
                    schema = (
                        TransitionRequest
                        if facts.structure and tool.name == "transition_scene"
                        else spec.arguments
                    )
                    args = schema.model_validate(tool.arguments)
                except ValidationError:
                    code, reason = "invalid_arguments", "参数不符合工具 schema"
                else:
                    data = args.model_dump(mode="json")
                    if tool.name == "search_module" and data.get("scope") != "current_scene":
                        code, reason = "permission_denied", "行动恢复不能执行全模组或跨场景检索"
                    elif tool.name == "open_module_node":
                        if data["node_id"] not in facts.allowed_node_ids:
                            code, reason = "permission_denied", "节点超出当前批准场景范围"
                    elif tool.name in {"transition_scene", "update_scene"}:
                        if intent.type != "move":
                            code, reason = "precondition_failed", "只有玩家明确移动才能转场"
                        else:
                            tid = plan.proposed_transition_id
                            transition = facts.transitions.get(tid)
                            target = data.get("target_scene_node_id") or data.get("scene_id")
                            if not transition or target != transition["target_scene_node_id"]:
                                code, reason = "entity_not_found", "工具目标与批准转换不一致"
                            elif intent.target_id not in {
                                tid,
                                target,
                                transition.get("target_entity_id"),
                            }:
                                code, reason = "precondition_failed", "工具目标与玩家移动目标不一致"
                            elif transition["source_scene_node_id"] != facts.scene_id:
                                code, reason = "precondition_failed", "出口不属于当前场景"
                            elif (
                                data.get("expected_revision", facts.navigation_revision)
                                != facts.navigation_revision
                            ):
                                code, reason = (
                                    "revision_conflict",
                                    "工具 navigation revision 不一致",
                                )
                            elif (
                                tid not in facts.available_transition_ids
                                and not facts.host_review_approved
                            ):
                                code, reason = (
                                    "precondition_failed",
                                    "转换前置条件尚未满足，需要主机处理",
                                )
                            elif (
                                transition.get("transition_type") == "host_only"
                                and not facts.host_review_approved
                            ):
                                code, reason = "permission_denied", "此转换仅允许主机操作"
                    elif tool.name in {"reveal_entity", "reveal_clue"}:
                        target = data.get("entity_id") or data.get("clue_id")
                        if target not in facts.approved_entities:
                            code, reason = "entity_not_found", "实体不是本房间批准实体"
                        elif target not in facts.local_entity_ids:
                            code, reason = "permission_denied", "实体不属于当前场景"
                        elif target not in facts.observed_entity_ids:
                            code, reason = "context_missing", "当前上下文省略了目标实体"
                        elif facts.reveal_errors.get(target):
                            if (
                                not after_check
                                and plan.proposed_check
                                and str(plan.proposed_check.clue_id) == target
                                and not facts.reveal_check_errors.get(target)
                            ):
                                pass
                            else:
                                code, reason = "precondition_failed", facts.reveal_errors[target]
                    elif tool.name == "request_sanity_check":
                        entity = facts.approved_entities.get(data["entity_id"], {})
                        if intent.type in {"recall", "out_of_character", "unknown"}:
                            code, reason = "precondition_failed", "回顾或场外问题不是新的 SAN 遭遇"
                        elif data["entity_id"] not in facts.local_entity_ids:
                            code, reason = "permission_denied", "SAN 实体不属于当前场景"
                        elif not any(
                            e["id"] == data["effect_id"] for e in entity.get("sanity_effects", [])
                        ):
                            code, reason = "precondition_failed", "缺少批准 SAN 配置，请主机裁定"
                    elif tool.name == "request_skill_check":
                        proposal = plan.proposed_check or CheckProposal(**data)
                        decision = CheckPolicyEvaluator().evaluate(proposal, intent, facts)
                        result.check_decisions.append(decision)
                        if not decision.allowed:
                            code, reason = "precondition_failed", decision.reason
                            if decision.requires_host_review:
                                entity = facts.approved_entities[decision.target_entity_id]
                                result.approved_actions.append(
                                    ValidatedAction(
                                        index=i,
                                        phase="proposal",
                                        tool=PlannedTool(
                                            name="request_host_review",
                                            arguments={
                                                "request_type": "reveal_entity",
                                                "entity_type": entity["type"],
                                                "entity_id": decision.target_entity_id,
                                                "proposed_title": entity["title"],
                                                "proposed_public_summary": entity.get(
                                                    "public_summary", ""
                                                ),
                                                "keeper_reason": decision.reason,
                                            },
                                        ),
                                    )
                                )
                                result.required_interrupt = "host_review"
                    elif (
                        tool.name == "inspect_character"
                        and data["member_id"] not in facts.characters
                    ):
                        code, reason = "entity_not_found", "角色不在本房间"
            if code:
                result.rejected_actions.append(
                    ActionRejection(index=i, tool=tool.name, code=code, reason=reason)
                )
                if (
                    tool.name == "transition_scene"
                    and code == "precondition_failed"
                    and reason == "转换前置条件尚未满足，需要主机处理"
                ):
                    result.approved_actions.append(
                        ValidatedAction(index=i, tool=tool, phase="proposal")
                    )
                    result.required_interrupt = "host_review"
            else:
                phase = (
                    "read"
                    if tool.name in READ_TOOLS
                    else "proposal"
                    if tool.name in PROPOSAL_TOOLS
                    else "state"
                )
                result.approved_actions.append(ValidatedAction(index=i, tool=tool, phase=phase))
        if result.rejected_actions:
            result.status = "partially_approved" if result.approved_actions else "rejected"
            result.validation_reasons.extend(r.reason for r in result.rejected_actions)
        if plan.needs_host_review or result.required_interrupt == "host_review":
            result.status = "host_review_required"
            result.required_interrupt = "host_review"
        elif any(a.tool.name == "request_skill_check" for a in result.approved_actions):
            result.required_interrupt = "human_roll"
        return result


def error_category(error):
    from app.models.base import ModelError
    from app.models.ollama import ModelFormatError

    if isinstance(error, ModelFormatError):
        return "model_schema_error"
    if isinstance(error, TimeoutError) or isinstance(error, ModelError) and "超时" in str(error):
        return "model_timeout"
    if isinstance(error, ValidationError):
        return "invalid_arguments"
    if isinstance(error, RoomError):
        if "revision" in error.message:
            return "revision_conflict"
        if "context_missing" in error.message:
            return "context_missing"
        if error.status == 403:
            return "permission_denied"
        if error.status == 404:
            return "entity_not_found"
        if error.status == 422:
            return "invalid_arguments"
        return "precondition_failed"
    return "internal_error"
