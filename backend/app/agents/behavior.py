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
        public_change_after_last_output=False,
        explicit_action_request=False,
        requested_operations=None,
        inventory_state=None,
        actor_id=None,
        requester_id=None,
    ):
        if decision.mode == "pass":
            return BehaviorRejection(
                accepted=not explicit_action_request,
                reason="action_request_requires_response" if explicit_action_request else "",
            )
        if decision.related_player_action_seq != action_seq:
            return BehaviorRejection(accepted=False, reason="unrelated_player_action")
        if decision.mode in {"act", "assist"} and inventory_state is not None:
            from app.preparation.inventory import held_item_acknowledgement, inventory_reply

            if held_item_acknowledgement(output_text(decision), inventory_state, actor_id):
                decision.speech_text = inventory_reply(inventory_state, actor_id)
                decision.action_text = None
                decision.action_type = "converse"
                decision.mode = "speak"
                decision.target_id = None
                decision.related_public_entity_ids = []
                decision.fact_ids = []
        from app.memory.facts import readonly_recall

        if readonly_recall(player_text) and decision.mode in {"act", "assist"}:
            return BehaviorRejection(accepted=False, reason="recall_has_no_new_action")
        if inventory_state is not None and not readonly_recall(player_text):
            from app.preparation.action_authority import action_kinds
            from app.preparation.inventory import (
                inventory_probe,
                inventory_reply,
                requested_handover,
            )

            if decision.mode == "assist" and set(requested_operations or []) == {"give"}:
                handover = requested_handover(player_text, inventory_state, actor_id, requester_id)
                if handover:
                    # assist agrees to the current request. Its actual item and
                    # recipient cannot be replaced by an unrelated phone plan.
                    decision.action_text = handover["action_text"]
                    decision.speech_text = None
                    decision.action_type = "interact"
                    decision.target_id = handover["item_id"]
                    decision.related_public_entity_ids = [handover["item_id"]]
                    decision.item_instance_ids = [handover["instance_id"]]
                    decision.fact_ids = []

            if (
                decision.mode == "assist"
                and explicit_action_request
                and "search" in (requested_operations or [])
                and set(requested_operations or []) <= {"search", "observe"}
                and not inventory_probe(player_text)
            ):
                from app.preparation.action_authority import requested_search_attempt

                search = requested_search_attempt(player_text)
                if search:
                    decision.action_text = search
                    decision.speech_text = None
                    decision.action_type = "investigate"
                    decision.target_id = None
                    decision.related_public_entity_ids = []
                    decision.fact_ids = []
                    decision.novelty_keys = []
                    decision.short_term_goal = None

            own_probe = inventory_probe(decision.action_text or "")
            kinds = set(action_kinds(decision.action_text or ""))
            addressed_probe = inventory_probe(player_text) and (
                explicit_action_request
                or "你" in player_text
                or any(
                    m.get("name") and m["name"] in player_text
                    for m in inventory_state.get("members", [])
                    if m["id"] == actor_id
                )
            )
            if addressed_probe and explicit_action_request and decision.mode == "speak":
                import re

                member = next(
                    (m for m in inventory_state.get("members", []) if m["id"] == actor_id), {}
                )
                said = output_text(decision)
                if (
                    member.get("starting_belongings") == "undetermined"
                    and re.match(r"\s*我(?:现在|这就|先)?(?:来)?检查", said)
                    and not re.search(r"不想|不愿|不检查|没有检查|没检查|先不|之前|刚才", said)
                ):
                    # Saying yes by describing a check is agreement to attempt
                    # it, never proof of its result. Submit the normal action.
                    decision.mode = "act"
                    decision.action_text = "我检查自己的口袋与随身物，确认实际保留下来的物品。"
                    own_probe, kinds = True, {"search", "observe"}
            if addressed_probe and not action_kinds(player_text):
                # Asking what is held is not an instruction to search again.
                decision.mode = "speak"
                decision.action_type = "converse"
                decision.action_text = None
                decision.speech_text = inventory_reply(inventory_state, actor_id)
                decision.target_id = None
                decision.related_public_entity_ids = []
                decision.fact_ids = []
            elif (
                decision.mode in {"act", "assist"}
                and own_probe
                and kinds & {"search", "observe"}
                and kinds <= {"search", "observe"}
            ):
                # This is the investigator's chosen attempt, not a discovered
                # item list. Only the following actual check may supply results.
                decision.action_text = "我检查自己的口袋与随身物，确认实际保留下来的物品。"
                decision.speech_text = None
                decision.action_type = "investigate"
                decision.target_id = None
                decision.related_public_entity_ids = []
                decision.fact_ids = []
                decision.novelty_keys = []
                decision.short_term_goal = None
                decision.reason_summary = "确认实际保留的随身物，结果由后续检定决定。"
            elif decision.mode == "speak" and (
                addressed_probe or inventory_probe(output_text(decision))
            ):
                decision.speech_text = inventory_reply(inventory_state, actor_id)
                decision.action_text = None
                decision.fact_ids = []
        if decision.mode in {"act", "assist"}:
            import re

            if re.search(
                r"或许|我建议|可以考虑|要不要|我们可以|我们一起(?:去|过去|前往)",
                decision.action_text or "",
            ):
                return BehaviorRejection(accepted=False, reason="suggestion_requires_speak_mode")
        if inventory_state is not None and not (
            decision.mode == "speak" and readonly_recall(player_text)
        ):
            from app.preparation.inventory import bind_item_prose
            from app.rooms.service import RoomError

            try:
                decision.item_instance_ids = bind_item_prose(
                    output_text(decision), inventory_state, actor_id
                )
            except RoomError:
                return BehaviorRejection(accepted=False, reason="item_not_held")
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
        if decision.mode == "assist" and requested_operations:
            from app.preparation.action_authority import action_kinds

            if not set(action_kinds(decision.action_text or "")) & set(requested_operations):
                return BehaviorRejection(
                    accepted=False, reason="assistance_does_not_attempt_requested_operation"
                )
        text = output_text(decision)
        if normalized(text) in {"继续调查", "四周很安静", "四周安静下来"}:
            return BehaviorRejection(accepted=False, reason="empty_template", repetition_score=1)
        recent = recent_outputs[-3:]
        if (public_change_after_last_output or explicit_action_request) and decision.mode in {
            "act",
            "assist",
        }:
            recent = []  # A changed world permits a new attempt through the normal KP/check gates.
        compared = [*recent, *other_outputs]
        if not (explicit_action_request and decision.mode in {"act", "assist"}):
            compared.append(player_text)
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
