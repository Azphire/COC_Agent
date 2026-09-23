"""Small deterministic novelty/cooldown policy, using public inputs only."""

import hashlib
import json
import re
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


def public_fingerprint(context, target_id=None, actor_id=None):
    material = {
        "scene": context.get("public_state", {}).get("scene"),
        "entities": [e for e in context.get("public_entities", []) if e.get("id") == target_id],
        "checks": [
            c
            for c in context.get("checks", [])
            if c.get("status") == "resolved"
            and c.get("target_member_id") == actor_id
            and c.get("policy_target_id") == target_id
        ],
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def technical_task_retry(decision, state, active_requests, fingerprint):
    """Only an unexecuted attempt of this request/target/operation can retry."""
    from app.preparation.action_authority import action_kinds

    if decision.mode not in {"act", "assist"} or not decision.target_id:
        return False
    operations = sorted(set(action_kinds(decision.action_text or "")) - {"converse", "pass"})
    if not operations:
        return False
    active = {r["key"]: r for r in active_requests
              if r.get("key") and r.get("kind") == "delegate" and r.get("available", True)}
    matches = {}
    for request in state.pending_requests:
        key = request.get("key")
        failure = request.get("technical_failure", {})
        current = active.get(key)
        if (not current or request.get("last_result_kind") != "generation_failed"
                or request.get("completion_event_seqs")
                or failure.get("executed") is not False or not failure.get("cycle_id")
                or failure.get("target_id") != decision.target_id
                or sorted(failure.get("operations", [])) != operations):
            continue
        requested = set(current.get("operations", []))
        if "search" in operations and "observe" in requested:
            requested.add("search")
        if not set(operations) <= requested:
            continue
        targets = {current.get("target_id"), request.get("target_id")}
        targets.update(o.get("target_id")
                       for o in current.get("operation_operands", {}).values())
        if decision.target_id not in targets:
            continue
        if (state.last_attempt_result.get("cycle_id") == failure["cycle_id"]
                and state.last_attempt_result.get("kind") in {"attempted", "completed", "blocked"}):
            continue
        matches[key] = failure["cycle_id"]
    if not matches:
        return False
    # A legacy cooldown, or a cooldown from a different actual execution, cannot
    # be erased merely because some still-pending request failed to generate.
    return all(
        c.task_cycle_id and sorted(c.operations) == operations
        and any(matches.get(key) == c.task_cycle_id for key in c.request_keys)
        for c in state.cooldowns
        if c.remaining_cycles > 0 and c.target_id == decision.target_id
        and c.action_type == decision.action_type and c.state_fingerprint == fingerprint
    )


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
        requested_text=None,
        inventory_state=None,
        actor_id=None,
        requester_id=None,
        actor_name="",
        information_request=False,
        requested_targets=(),
        named_people=None,
        public_accounts=(),
        result_facts=(),
        treatment_options=(),
        active_requests=(),
        fingerprint_context=None,
    ):
        if information_request and decision.mode == "pass":
            return BehaviorRejection(accepted=False, reason="question_requires_answer_not_action")
        if decision.mode == "pass":
            return BehaviorRejection(
                accepted=not explicit_action_request,
                reason="action_request_requires_response" if explicit_action_request else "",
            )
        if decision.related_player_action_seq != action_seq:
            return BehaviorRejection(accepted=False, reason="unrelated_player_action")
        speech, action = decision.speech_text or "", decision.action_text or ""
        from app.agents.results import answer_result_error, result_error
        from app.memory.facts import readonly_recall
        items = (inventory_state or {}).get("known_items", [])
        if readonly_recall(player_text) and speech:
            error = answer_result_error(speech, player_text, result_facts)
            if error:
                return BehaviorRejection(accepted=False, reason=error)

        # A public scene may already name the injured person without revealing
        # their full NPC profile. Identity/presence is enough to attempt aid;
        # the combat executor still validates the actual participant and result.
        public_ids = public_ids | {
            p["id"] for p in (inventory_state or {}).get("other_actors", [])
            if p.get("fact_scope") == "current_scene"
        }
        if decision.mode in {"act", "assist"}:
            if actor_name and re.search(re.escape(actor_name) + r"[^，。；;]*", speech):
                # Optional third-person narration is not the character talking
                # to themself. Preserve the separate first-person attempt.
                if re.match(r"我", action) and re.match(
                    re.escape(actor_name) + r"(?:过去|正在|开始|在这里|去|检查|观察)", speech,
                ):
                    decision.speech_text = speech = ""
            # Preserve a valid attempt when its tail prematurely supplies a
            # discovery. The ensuing KP subcycle supplies the actual result.
            attempt = re.split(r"[，,]\s*(?:并)?(?:发现|看到了|查明了)", action, maxsplit=1)[0]
            if attempt != action:
                decision.action_text = action = attempt.rstrip("，,") + "。"
            # Keep an authorized attempt while removing unsupported outcome
            # clauses, using the same receipt check as final speech/narration.
            pieces = re.split(r"([，,。；;])", action)
            kept = ""
            for index in range(0, len(pieces), 2):
                clause = pieces[index]
                if not result_error(clause, result_facts, actor_id=actor_id, names=named_people,
                                    items=items):
                    kept += clause + (pieces[index + 1] if index + 1 < len(pieces) else "")
            if kept and kept != action:
                decision.action_text = action = kept.rstrip("，,。；;") + "。"
            if action and result_error(speech, result_facts, actor_id=actor_id, names=named_people,
                                       items=items):
                # The actual attempt remains useful even if its accompanying
                # success announcement has no receipt. Its KP subcycle answers it.
                decision.speech_text = speech = ""
        error = result_error(
            speech + "\n" + action, result_facts, actor_id=actor_id, names=named_people,
            items=items,
        )
        if error:
            return BehaviorRejection(accepted=False, reason=error)
        # Quoting a discovered inscription is a factual claim, not free dialogue.
        # Use receipts/testimony, never another teammate's unverified opinion.
        quotes = re.findall(r"[‘“「『]([^’”」』]{4,})[’”」』]", speech)
        sources = [r["text"] for r in public_accounts
                   if r["kind"] in {"result", "testimony"} and r["status"] == "current"]
        sources.append(state.last_attempt_result.get("text", ""))
        if sources and any(
            not any(normalized(q) in normalized(s) for s in sources) for q in quotes
        ):
            return BehaviorRejection(accepted=False, reason="unsupported_quotation")
        if re.search(
            r"我(?:刚才|刚|之前|已经|先前)|(?:刚才|之前|先前)我|"
            r"我[^，。；!?]{0,12}(?:过|了)(?!去|来)", speech,
        ):
            from app.preparation.action_authority import action_kinds

            recalled = set(action_kinds(speech)) - {"converse"}
            own = set().union(*(set(action_kinds(r["text"])) for r in public_accounts
                                if r["ownership"] == "self" and r["kind"] in {"attempt", "result"}))
            others = set().union(*(set(action_kinds(r["text"])) for r in public_accounts
                                   if r["ownership"] == "other" and r["kind"] == "attempt"))
            if recalled & others - own:
                return BehaviorRejection(accepted=False, reason="borrowed_experience")
            own_results = set().union(*(
                set(action_kinds(r["text"])) for r in public_accounts
                if r["ownership"] == "self" and r["kind"] == "result"
            )) | {f["operation"] for f in result_facts
                  if f.get("actor_id") == actor_id and f.get("status") == "success"}
            if public_accounts and recalled & {"first_aid", "medicine"} - own_results:
                return BehaviorRejection(accepted=False, reason="unconfirmed_experience")
        if (
            decision.mode in {"act", "assist"}
            and re.match(r"我(?:注意到|看到|听说|记得|觉得|认为)", action.strip())
            and not re.search(
                r"[，,。；;]\s*我(?!们|注意到|看到|听说|记得|觉得|认为)", action
            )
        ):
            # A comment about another attempt is speech, not authority to
            # perform the verbs embedded in that comment as this character.
            decision.mode = "speak"
            decision.speech_text = speech or action
            decision.action_text = None
            decision.action_type = "converse"
            decision.target_id = None
            decision.related_public_entity_ids = []
            speech, action = decision.speech_text, ""
        if decision.mode in {"act", "assist"} and re.fullmatch(
            r"我?(?:先|也|马上)?(?:跟上(?:大家|你们|你|同伴)?|走到同伴旁边)"
            r"(?:[，,]?(?:先)?(?:看看|检查|观察|留意)(?:一下)?"
            r"(?:前头|前面|车头|周围|车厢|房间|现场)?"
            r"(?:有没?有|能不能)?(?:什么)?(?:能)?(?:的)?(?:帮(?:上)?忙的?|异常|情况|帮什么))?"
            r"[。！!]?",
            action.strip(),
        ):
            # Co-located following is a gesture, even when a model selected move.
            # Normalize before response-mode, public-target and inventory guards.
            decision.mode = "speak"
            decision.speech_text = speech or action
            decision.action_text = None
            decision.action_type = "interact"
            decision.target_id = None
            decision.related_public_entity_ids = []
            speech, action = decision.speech_text, ""
        if decision.mode == "speak":
            from app.preparation.action_authority import action_kinds

            promised = set(action_kinds(speech))
            if "search" in promised:
                promised.add("observe")
            if (explicit_action_request and requested_operations
                    and not promised & set(requested_operations)
                    and re.search(r"我(?:会|来|先|这就|马上)", speech)
                    and not re.search(r"不|没|未|如果|等.+再|建议", speech)):
                return BehaviorRejection(accepted=False, reason="accepted_task_needs_attempt")
            if (
                re.search(r"(?:^|[，,。；;])\s*(?:好[的吧]?[,，。]?\s*)?我", speech)
                and (
                    explicit_action_request and promised & set(requested_operations or [])
                    or promised & {"first_aid", "medicine", "give", "use"}
                    and re.search(r"我(?:现在|这就|来|先|要|准备|打算)", speech)
                    and not re.search(r"刚才|之前|已经|先前", speech)
                )
                and not re.search(
                    r"不想|不愿|不懂|不能|无法|没法|没有条件|先不|如果|建议|等.+再|可以请", speech
                )
            ):
                from app.preparation.action_authority import speaker_action

                immediate = speaker_action(speech)
                if immediate and not information_request and (re.match(
                    r"我(?:现在|马上|立刻|这就|先|来|就|准备)?(?:过去|上前)?"
                    r"(?:给|为|替|帮|用|开始|进行|做|急救|包扎|按|压|检查|查看|看看|搜索|拿|取|掏|交)",
                    immediate,
                ) or explicit_action_request
                        and not re.match(r"我(?:们)?(?:会|以后|将来)", immediate)
                        and set(action_kinds(immediate)) & set(requested_operations or [])):
                    # The character already chose this immediate attempt; a
                    # wrong response-mode label must not swallow it. It goes
                    # through the normal queued cycle, never straight to success.
                    decision.mode = "act"
                    decision.action_text = action = immediate
                    decision.speech_text = speech = ""
                    decision.action_type = "interact"
                    decision.goal_status = "continue"
                    if not decision.target_id and len(set(requested_targets)) == 1:
                        decision.target_id = requested_targets[0]
                else:
                    return BehaviorRejection(accepted=False, reason="accepted_task_needs_attempt")
        if decision.mode in {"act", "assist"}:
            from app.preparation.action_authority import action_kinds

            promised_effects = set(action_kinds(speech)) & {"first_aid", "medicine", "give", "use"}
            if (
                promised_effects - set(action_kinds(action))
                and re.search(r"(?:^|[，,。；;])\s*我", speech)
                and not re.search(r"已经|成功|好了|如果|建议|不能|没法|不愿|等.+再", speech)
            ):
                return BehaviorRejection(accepted=False, reason="accepted_task_needs_attempt")
            if re.search(
                r"(?:^|[，。；,;])\s*(?:你|您|他|她)(?:们)?(?:去|来|先|帮|负责|照看)", action
            ):
                return BehaviorRejection(accepted=False, reason="action_assigns_someone_else")
            if requested_targets:
                named = {
                    mid
                    for mid, name in (named_people or {}).items()
                    if name and name in action and mid != actor_id
                }
                if named and not named.intersection(requested_targets):
                    return BehaviorRejection(accepted=False, reason="request_target_mismatch")
        if actor_name and actor_name in re.sub(
            r"我(?:叫|是)" + re.escape(actor_name), "", speech + action
        ):
            return BehaviorRejection(accepted=False, reason="addressing_self")
        if speech and action and normalized(speech) == normalized(action):
            decision.speech_text = None
            speech = ""
        elif decision.mode in {"act", "assist"} and speech and any(
            bigram_jaccard(speech, old) >= self.threshold for old in recent_outputs[-3:]
        ):
            # Keep a new chosen attempt when only its optional speech repeats.
            decision.speech_text = None
            speech = ""
        elif speech and action and bigram_jaccard(speech, action) >= self.threshold:
            decision.speech_text = None
            speech = ""
        if decision.mode in {"act", "assist"} and re.search(
            r"(?:已经|已|成功|终于).{0,10}(?:包扎|止血|治好|恢复|给药|喂药|拿到|找到)|"
            r"我(?:们)?(?:检查|搜索|查看|打开|找到|拿到)了.{0,30}(?:看起来|发现|里面有|藏着)|"
            r"(?:伤口|疼痛|伤势).{0,8}(?:好转|缓解|控制住)|(?:包扎|给药|止血)完(?:了|毕)",
            action + speech,
        ):
            return BehaviorRejection(accepted=False, reason="unsettled_result")
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

                search = requested_search_attempt(requested_text or player_text)
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
                or any(
                    "你" in c and inventory_probe(c)
                    for c in re.split(r"[，。；！？,;.!?\n]", player_text)
                )
            )
            if addressed_probe and explicit_action_request and decision.mode == "speak":
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
            if addressed_probe and not explicit_action_request and not action_kinds(player_text):
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
            if re.search(
                r"或许|我建议|可以考虑|要不要|我们可以|我们一起(?:去|过去|前往)",
                decision.action_text or "",
            ):
                return BehaviorRejection(accepted=False, reason="suggestion_requires_speak_mode")
        # Correct the requested response mode before diagnosing an invented
        # tool. Otherwise an opinion gets repaired into an inventory answer.
        from app.preparation.inventory import inventory_question

        if (
            information_request and decision.mode != "speak"
            and not inventory_question(requested_text or player_text, inventory_state or {})
        ):
            return BehaviorRejection(accepted=False, reason="question_requires_answer_not_action")
        if inventory_state is not None:
            from app.preparation.inventory import validate_resource_claims
            from app.rooms.service import RoomError

            try:
                validate_resource_claims(output_text(decision), inventory_state)
            except RoomError:
                return BehaviorRejection(accepted=False, reason="item_not_held")
        if inventory_state is not None:
            from app.preparation.dialogue import current_item_statements
            from app.preparation.inventory import bind_item_prose
            from app.rooms.service import RoomError

            try:
                decision.item_instance_ids = bind_item_prose(
                    current_item_statements(output_text(decision)), inventory_state, actor_id
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
        if decision.mode in {"act", "assist"} and decision.action_type == "move":
            return BehaviorRejection(accepted=False, reason="teammate_cannot_move_scene")
        if decision.mode == "assist" and requested_operations:
            from app.preparation.action_authority import action_kinds

            attempted = set(action_kinds(decision.action_text or ""))
            # Inspecting the requested target satisfies a request to look at it.
            # Looking alone still cannot satisfy transfer, use, or search tasks.
            if "search" in attempted:
                attempted.add("observe")
            if not attempted & set(requested_operations):
                return BehaviorRejection(
                    accepted=False, reason="assistance_does_not_attempt_requested_operation"
                )
        # Ownership repairs above may change the action. Recheck the final
        # publication, rather than trusting checks made before normalization.
        if actor_name and actor_name in re.sub(
            r"我(?:叫|是)" + re.escape(actor_name), "", output_text(decision)
        ):
            return BehaviorRejection(accepted=False, reason="addressing_self")
        if information_request and decision.mode != "speak":
            return BehaviorRejection(accepted=False, reason="question_requires_answer_not_action")
        text = output_text(decision)
        if decision.mode in {"act", "assist"}:
            from app.preparation.action_authority import action_kinds

            operations = set(action_kinds(decision.action_text or ""))
            for option in treatment_options:
                if option["operation"] not in operations or not option.get("restriction"):
                    continue
                if (
                    decision.target_id == option["target_id"]
                    or option.get("target_name")
                    and option["target_name"] in (decision.action_text or "")
                    or not decision.target_id
                    and len({o["target_id"] for o in treatment_options}) == 1
                ):
                    return BehaviorRejection(accepted=False, reason="treatment_not_available")
        if normalized(text) in {"继续调查", "四周很安静", "四周安静下来"}:
            return BehaviorRejection(accepted=False, reason="empty_template", repetition_score=1)
        recent = recent_outputs[-3:]
        # Earlier ownership/response-mode normalization may change the target.
        # Novelty and cooldown must see the target that will actually execute.
        if fingerprint_context is not None:
            fingerprint = public_fingerprint(fingerprint_context, decision.target_id, actor_id)
        relevant_change = any(
            c.target_id == decision.target_id and c.state_fingerprint != fingerprint
            for c in state.cooldowns
        )
        technical_retry = bool(
            explicit_action_request
            and technical_task_retry(decision, state, active_requests, fingerprint)
        )
        if relevant_change or technical_retry:
            recent = []  # A changed world permits a new attempt through the normal KP/check gates.
        compared = [*recent, *other_outputs]
        if not (explicit_action_request and decision.mode in {"act", "assist"}):
            compared.append(player_text)
        score = max((bigram_jaccard(text, old) for old in compared), default=0)
        if score >= self.threshold:
            return BehaviorRejection(
                accepted=False, reason="repeated_output", repetition_score=score
            )
        if (
            decision.mode in {"act", "assist"}
            and not technical_retry
            and any(
                c.remaining_cycles > 0
                and c.action_type == decision.action_type
                and c.target_id == decision.target_id
                and c.state_fingerprint == fingerprint
                for c in state.cooldowns
            )
        ):
            return BehaviorRejection(
                accepted=False, reason="target_action_cooldown", repetition_score=score
            )
        return BehaviorRejection(accepted=True, repetition_score=score)

    def advance(self, state, decision, *, cycle_id, fingerprint, safe_goal,
                requests=(), task_cycle_id=None):
        if state.last_acted_cycle == cycle_id:
            return state
        cooldowns = [
            c.model_copy(update={"remaining_cycles": c.remaining_cycles - 1})
            for c in state.cooldowns
            if c.remaining_cycles > 1
        ]
        if decision.mode in {"act", "assist"}:
            from app.preparation.action_authority import action_kinds

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
                    request_keys=[r["key"] for r in requests
                                  if r.get("kind") == "delegate" and r.get("key")],
                    operations=sorted(set(action_kinds(decision.action_text or ""))
                                      - {"converse", "pass"}),
                    task_cycle_id=task_cycle_id,
                )
            )
        return BehaviorState(
            pending_requests=state.pending_requests,
            request_history=state.request_history,
            task_status=state.task_status,
            task_scene_id=state.task_scene_id,
            task_cycle_id=state.task_cycle_id,
            last_result=state.last_result,
            last_attempt_result=state.last_attempt_result,
            # A model saying 'complete' is not a receipt. Settlement clears
            # the goal only after the requested operation actually succeeds.
            current_short_term_goal=(safe_goal or "")
            if decision.goal_status in {"abandon", "adjust"}
            else safe_goal or state.current_short_term_goal,
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
