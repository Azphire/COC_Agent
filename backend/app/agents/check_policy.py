"""Project check-necessity policy. Local CoC7 sources: rules/definitions/SOURCES.md."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from app.agents.schemas import CheckRequest
from app.domain.character import DomainModel
from app.rules.checks import check_value

AccessPolicy = Literal["automatic", "requires_check", "requires_condition", "host_review"]


class CheckProposal(CheckRequest):
    purpose: str = Field(default="", max_length=240, json_schema_extra={"x-explicit-output": True})
    method: str = Field(default="", max_length=240, json_schema_extra={"x-explicit-output": True})
    continues_check_id: str | None = None
    alternative_basis: str = Field(default="", max_length=400)
    target_entity_id: str | None = Field(
        default=None, max_length=100, json_schema_extra={"x-explicit-output": True}
    )
    necessity: Literal["required", "optional", "unnecessary"] = Field(
        default="unnecessary", json_schema_extra={"x-explicit-output": True}
    )
    uncertainty: str = Field(
        default="", max_length=240, json_schema_extra={"x-explicit-output": True, "minLength": 1}
    )
    success_effect: str = Field(
        default="", max_length=240, json_schema_extra={"x-explicit-output": True, "minLength": 1}
    )
    failure_consequence: str = Field(
        default="", max_length=240, json_schema_extra={"x-explicit-output": True, "minLength": 1}
    )
    basis_entity_id: str | None = Field(default=None, json_schema_extra={"x-explicit-output": True})
    rule_topic_id: str | None = Field(default=None, json_schema_extra={"x-explicit-output": True})
    risk_quote: str = Field(
        default="", max_length=240, json_schema_extra={"x-explicit-output": True}
    )
    is_repeat_attempt: bool = False


class CheckPolicyDecision(DomainModel):
    allowed: bool
    necessity: Literal["required", "optional", "unnecessary"]
    reason: str
    code: str
    access_policy: AccessPolicy = "automatic"
    state_fingerprint: str = ""
    target_entity_id: str | None = None
    requires_host_review: bool = False
    proposal: CheckProposal


def entity_access(entity):
    pre = entity.get("reveal_conditions", {})
    return pre.get("access_policy") or (
        "requires_check"
        if pre.get("successful_check")
        else "requires_condition"
        if pre.get("required_entity_ids") or pre.get("note")
        else "automatic"
    )


def state_fingerprint(facts):
    # Checks, narration, chat and elapsed cycles do not themselves change the world.
    value = {
        "scene": facts.scene_id,
        "revision": facts.navigation_revision,
        "state": facts.check_state,
        "characters": facts.characters,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class CheckPolicyEvaluator:
    def evaluate(self, proposal, intent, facts):
        p = CheckProposal.model_validate(proposal)
        target = p.target_entity_id or p.clue_id or intent.target_id or facts.scene_id
        entity = facts.approved_entities.get(target, {})
        access = entity_access(entity)
        fingerprint = state_fingerprint(facts)

        def decision(allowed, code, reason, host=False):
            return CheckPolicyDecision(
                allowed=allowed,
                necessity=p.necessity if allowed else "unnecessary",
                reason=reason,
                code=code,
                access_policy=access,
                state_fingerprint=fingerprint,
                target_entity_id=target,
                requires_host_review=host,
                proposal=p,
            )

        try:
            check_value(facts.characters.get(str(p.target_member_id), {}), p.kind, p.name)
        except (ValueError, KeyError):
            return decision(False, "unknown_skill", "检定必须引用本房间真实属性或技能")
        if str(p.target_member_id) != facts.actor_member_id:
            return decision(False, "wrong_actor", "只能为本次行动者请求检定")
        if intent.type in {"wait", "recall", "out_of_character"}:
            return decision(False, "routine_action", "等待、回顾和场外讨论不触发检定")
        if target not in (facts.visible_entity_ids & facts.local_entity_ids) | {facts.scene_id}:
            return decision(False, "not_visible", "目标不在当前场景可见范围")
        if p.clue_id and p.clue_id != target:
            return decision(False, "target_mismatch", "检定目标与关联实体不一致")
        speaking_to_member = intent.type == "converse" and intent.target_id in facts.member_ids
        if (
            intent.target_id
            and intent.target_id not in {target, facts.scene_id}
            and not speaking_to_member
        ):
            return decision(False, "intent_target_mismatch", "检定目标与玩家行动目标不一致")
        if access == "host_review" and not facts.host_review_approved:
            return decision(False, "host_review", "目标访问需要主机审阅", True)
        if p.clue_id and facts.reveal_check_errors.get(target):
            return decision(False, "condition_missing", "目标前置条件尚未满足")
        for old in facts.completed_checks:
            from app.agents.behavior import bigram_jaccard

            same_task = (
                p.continues_check_id == old.get("id")
                or (p.purpose and p.purpose == old.get("attempt_purpose"))
                or bigram_jaccard(p.purpose, old.get("attempt_purpose", "")) >= 0.45
                or (
                    p.method and p.method == old.get("attempt_method") and old.get("name") == p.name
                )
                or not p.purpose
                and not old.get("attempt_purpose")
            )
            if (
                old.get("target_member_id") == str(p.target_member_id)
                and old.get("policy_target_id", old.get("clue_id")) == target
                and (old.get("settlement") or {}).get("push_requested")
                and old.get("cycle_id") != facts.cycle_id
                and same_task
            ):
                return decision(
                    False, "push_exhausted", "该目标已申请过孤注；不能重新提交普通检定绕过"
                )
            if (
                old.get("target_member_id") == str(p.target_member_id)
                and (
                    p.purpose
                    and old.get("attempt_purpose")
                    or old.get("kind") == p.kind
                    and old.get("name") == p.name
                )
                and old.get("policy_target_id", old.get("clue_id")) == target
                and old.get("policy_fingerprint") == fingerprint
                and old.get("cycle_id") != facts.cycle_id
                and same_task
            ):
                return decision(False, "repeat_unchanged", "相同状态下已完成此检定，不再掷骰")
        if p.clue_id and target in facts.revealed_entity_ids:
            return decision(False, "already_public", "信息已经公开，无需再次检定")
        expected = entity.get("reveal_conditions", {}).get("successful_check")
        configured = access == "requires_check" and expected and p.clue_id == target
        if (
            configured
            and (p.kind, p.name, p.difficulty)
            != (
                expected["kind"],
                expected["name"],
                expected["difficulty"],
            )
            and not p.alternative_basis.strip()
        ):
            return decision(False, "check_mismatch", "检定与实体批准条件不匹配")
        if configured and (p.basis_entity_id != target or p.clue_id != target):
            return decision(False, "missing_entity_basis", "检定必须关联配置该检定的实体")
        # Necessity is a KP judgement, not a keyword classification. This grants
        # a roll only; entity reveals and state effects still need their own guards.
        if p.necessity == "unnecessary":
            return decision(False, "unnecessary", "提案标记为无需检定")
        if not configured and p.rule_topic_id != "coc7.skill_check":
            return decision(False, "missing_rule", "检定需要已实现的规则依据")
        if not p.uncertainty.strip() or p.uncertainty.strip() in {"无", "没有", "无不确定性"}:
            return decision(False, "no_uncertainty", "没有真实不确定因素")
        if (
            not p.failure_consequence.strip()
            or p.failure_consequence.strip() in {"无", "没有", "无后果", "无影响", "没有后果"}
            or not p.success_effect.strip()
            or p.failure_consequence.strip() == p.success_effect.strip()
        ):
            return decision(False, "no_consequence", "缺少不同的成功效果与失败后果")
        return decision(
            True,
            "configured_check" if configured else "keeper_judgement",
            "批准实体要求检定" if configured else "KP根据目的、方法与情境裁决检定",
        )
