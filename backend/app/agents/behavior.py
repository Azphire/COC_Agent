"""Small deterministic novelty/cooldown policy, using public inputs only."""

import hashlib
import json
import unicodedata

from app.agents.adjudication_schemas import BehaviorRejection, BehaviorState, Cooldown


def normalized(text, punctuation=True):
    value = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(
        c
        for c in value
        if not c.isspace()
        and (not punctuation or not unicodedata.category(c).startswith(("P", "S")))
    )


def bigram_jaccard(left, right):
    left, right = normalized(left), normalized(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    a, b = (
        {left[i : i + 2] for i in range(len(left) - 1)},
        {right[i : i + 2] for i in range(len(right) - 1)},
    )
    return len(a & b) / len(a | b) if a | b else 0.0


def output_text(decision):
    if decision.mode == "speak":
        return (decision.speech_text or decision.action_text or "").strip()
    parts = [t.strip() for t in (decision.action_text, decision.speech_text) if t and t.strip()]
    return "\n".join(t for i, t in enumerate(parts) if not any(t in old for old in parts[:i]))


def public_fingerprint(context):
    material = {
        "scene": context.get("public_state"),
        "entities": context.get("public_entities", []),
        "checks": [c for c in context.get("checks", []) if c.get("status") == "resolved"],
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class TeammateBehaviorPolicy:
    def __init__(self, threshold=0.65, cooldown_cycles=2):
        self.threshold, self.cooldown_cycles = threshold, cooldown_cycles

    def validate(
        self,
        decision,
        *,
        state,
        recent_outputs,
        other_outputs,
        player_text,
        player_intent,
        public_ids,
        action_seq,
        fingerprint,
        fact_scopes=None,
    ):
        if decision.mode == "pass":
            return BehaviorRejection(accepted=True)
        if decision.related_player_action_seq != action_seq:
            return BehaviorRejection(accepted=False, reason="unrelated_player_action")
        refs = set(decision.related_public_entity_ids)
        if decision.target_id:
            refs.add(decision.target_id)
        if not refs <= public_ids:
            return BehaviorRejection(accepted=False, reason="target_not_public")
        historical = {e for e in refs if (fact_scopes or {}).get(e) in {"historical", "unknown"}}
        if historical:
            from app.module_ir.facts import recalling

            if decision.mode != "speak" or not recalling(output_text(decision)):
                return BehaviorRejection(accepted=False, reason="target_not_current")
        if decision.action_type == "move" and decision.mode in {"act", "assist"}:
            return BehaviorRejection(accepted=False, reason="teammate_cannot_move_scene")
        text = output_text(decision)
        if normalized(text) in {"继续调查", "四周很安静", "四周安静下来"}:
            return BehaviorRejection(accepted=False, reason="empty_template", repetition_score=1)
        compared = [*recent_outputs[-3:], *other_outputs, player_text]
        score = max((bigram_jaccard(text, old) for old in compared), default=0)
        if score >= self.threshold:
            return BehaviorRejection(
                accepted=False, reason="repeated_output", repetition_score=score
            )
        if decision.mode == "act" and any(
            c.remaining_cycles > 0
            and c.action_type == decision.action_type
            and c.target_id == decision.target_id
            and c.state_fingerprint == fingerprint
            for c in state.cooldowns
        ):
            return BehaviorRejection(
                accepted=False, reason="target_action_cooldown", repetition_score=score
            )
        return BehaviorRejection(accepted=True, repetition_score=score)

    def advance(self, state, decision, *, cycle_id, fingerprint, safe_goal):
        if state.last_acted_cycle == cycle_id:
            return state
        cooldowns = [
            c.model_copy(update={"remaining_cycles": c.remaining_cycles - 1})
            for c in state.cooldowns
            if c.remaining_cycles > 1 and c.state_fingerprint == fingerprint
        ]
        if decision.mode != "pass":
            cooldowns = [
                c
                for c in cooldowns
                if (c.action_type, c.target_id) != (decision.action_type, decision.target_id)
            ]
            cooldowns.append(
                Cooldown(
                    action_type=decision.action_type,
                    target_id=decision.target_id,
                    remaining_cycles=self.cooldown_cycles,
                    state_fingerprint=fingerprint,
                )
            )
        return BehaviorState(
            current_short_term_goal=safe_goal or state.current_short_term_goal,
            last_action_type=decision.action_type
            if decision.mode != "pass"
            else state.last_action_type,
            last_target_id=decision.target_id if decision.mode != "pass" else state.last_target_id,
            last_public_output_hash=hashlib.sha256(output_text(decision).encode()).hexdigest()
            if decision.mode != "pass"
            else state.last_public_output_hash,
            recent_novelty_keys=list(
                dict.fromkeys([*state.recent_novelty_keys, *decision.novelty_keys])
            )[-24:],
            cooldowns=cooldowns,
            last_acted_cycle=cycle_id,
            consecutive_pass_count=state.consecutive_pass_count + 1
            if decision.mode == "pass"
            else 0,
        )
