"""Bind the KP's per-addressee interpretation to original utterance spans."""

import re

from app.agents.adjudication_schemas import TurnFocus, TurnRequest
from app.preparation.action_authority import (
    action_kinds,
    addressed_spans,
    declared_action,
    information_question,
    request_clauses,
    requested_action_kinds,
    speaker_action,
)


def inherited_speaker_action(raw, start, end):
    """A continued attempt inherits the sentence's first-person subject."""
    from app.agents.action_policy import explicit_movement

    text = raw[start:end]
    if re.search(r"你|您|[?？]|请|帮我|麻烦|问", text):
        return False
    prefix = re.split(r"[。；;！？!?\n]", raw[:start])[-1]
    return bool(
        speaker_action(prefix)
        and (declared_action(text) or explicit_movement(text))
    )


def question_request(text, name=""):
    """A group intention is not an unanswered question to a teammate."""
    return bool(
        (name and name in text)
        or information_question(text)
        or re.search(r"你|您|[？?]|什么|怎么|怎样|如何|哪里|哪儿|谁|是否|吗|么|呢|"
                     r"能否|能不能|有没有|告诉|说说|讲讲|解释|介绍|请问", text)
    )


def historical_third_person_question(text, names=()):
    """A question about someone's earlier account is not addressed to them."""
    names = list(dict.fromkeys(n for name in names for n in (name, name.split("·")[0]) if n))
    named = "|".join(re.escape(name) for name in names)
    if re.search(r"你|您", text):
        return False  # Preserve an actual second-person conversational follow-up.
    if named and re.search(
        r"(?:^|[，,。；;！？!?\n])\s*(?:" + named + r")(?:先生|女士)?[，,：:]|"
        r"(?:询问|追问|请教|问)(?:一下)?(?:" + named + r")", text,
    ):
        return False
    subject = "(?:" + "|".join([*(re.escape(name) for name in names), "他", "她"]) + ")"
    return bool(re.search(
        r"(?:^|[，,。；;！？!?\n])\s*" + subject
        + r"[^，,。；;！？!?\n]{0,12}(?:早先|先前|之前|以前|刚才|当时|曾|原先|说过|提过)",
        text,
    ))


def request_scopes(request):
    """Split only independently classified clauses, retaining literal offsets."""
    groups = []
    for piece in request_clauses(request.text):
        text = piece["text"]
        kind = ("cancel" if cancellation(text) else "question"
                if information_question(text) else "delegate"
                if requested_action_kinds(text) else None)
        if not groups:
            groups.append([0, piece["end"], kind])
        elif kind and groups[-1][2] and kind != groups[-1][2]:
            groups.append([piece["start"], piece["end"], kind])
        else:
            groups[-1][1] = piece["end"]
            groups[-1][2] = groups[-1][2] or kind
    if len(groups) < 2:
        if information_question(request.text) and not requested_action_kinds(request.text):
            return [request.model_copy(update={"kind": "question", "operations": []})]
        return [request]
    return [request.model_copy(update={
        "text": request.text[start:end], "kind": kind or request.kind,
        "source_start": request.source_start + start, "source_end": request.source_start + end,
        "target_id": None, "operations": requested_action_kinds(request.text[start:end])
        if kind == "delegate" else [],
    }) for start, end, kind in groups]


def bind_requests(focus, raw, people, actor, *, npc_ids=(), explicit_spans=()):
    """Keep valid model interpretations; repair only explicit ownership boundaries.

    Old saves/plans lack requests. The lexical compatibility path runs here once,
    never independently in scheduling, normalization or authority checks.
    """
    explicit = addressed_spans(raw, {mid: name for mid, name in people.items() if mid != actor})
    explicit.extend(explicit_spans)
    explicit.sort(key=lambda span: span[1])
    # A request to treat the sole present NPC can be followed by addressing
    # that patient as "him/her". Keep the original spans, not rewritten prose.
    if len(npc_ids) == 1 and any(
        "first_aid" in requested_action_kinds(raw[start:end]) for _, start, end in explicit
    ):
        npc_id = next(iter(npc_ids))
        for pronoun in ("他", "她"):
            explicit.extend(
                span for span in addressed_spans(raw, {npc_id: pronoun})
                if re.match(r"\s*我(?:蹲在|站在|坐在|走到|来到|转向|看着|靠近)", raw[span[1]:])
            )
        explicit.sort(key=lambda span: span[1])
    requests = []
    for request in focus.requests:
        if request.addressee_id not in people or request.addressee_id == actor:
            continue
        if not request.text or raw[request.source_start : request.source_end] != request.text:
            continue
        if request.text.strip(" ，,：:；;。") == people[request.addressee_id]:
            continue  # A salutation alone must not hide the actual request span.
        owners = {
            mid
            for mid, start, end in explicit
            if start < request.source_end and end > request.source_start
        }
        if owners and owners != {request.addressee_id}:
            continue
        if (request.addressee_id in npc_ids and not owners
                and historical_third_person_question(
                    request.text, [people[request.addressee_id]],
                )):
            continue
        if request.kind == "question" and not owners and not question_request(request.text):
            continue
        if not owners and request.kind == "delegate" and re.match(
            r"\s*(?:咱们|我们)(?!请你|让你|希望你)", request.text
        ):
            continue
        if not owners and (
            speaker_action(request.text)
            or inherited_speaker_action(raw, request.source_start, request.source_end)
        ):
            continue
        if (
            request.kind == "question"
            and owners
            and (
                requested_action_kinds(request.text)
                or (
                    not re.search(
                        r"[？?]|什么|怎么|哪里|哪根|是否|能否|能不能|有没有", request.text
                    )
                    and (
                        declared_action(request.text)
                        or re.match(r"\s*(?:你)?(?:请|帮|麻烦|照看|照顾|留意|负责)", request.text)
                    )
                )
            )
        ):
            request.kind = "delegate"
        request.operations = (
            requested_action_kinds(request.text) if request.kind == "delegate" else []
        )
        if cancellation(request.text):
            request.kind, request.operations = "cancel", []
        if (request.kind == "delegate" and information_question(request.text)
                and not requested_action_kinds(request.text)):
            request.kind, request.operations = "question", []
        if not any(
            r.addressee_id == request.addressee_id
            and r.source_start == request.source_start
            and r.source_end == request.source_end
            for r in requests
        ):
            requests.append(request)
    for mid, start, end in explicit:
        if any(r.addressee_id == mid and start <= r.source_start < end for r in requests):
            continue
        text = raw[start:end]
        operations = requested_action_kinds(text)
        hypothetical = re.search(r"如果|假如|假设|建议|不如|要不要", text)
        delegated = bool(operations or re.search(r"照看|照顾|留意|帮忙|负责", text))
        kind = (
            "cancel" if cancellation(text) else "hypothesis"
            if hypothetical and not information_question(text)
            else "question"
            if information_question(text)
            else "delegate"
            if delegated
            else "question"
        )
        requests.append(
            TurnRequest(
                kind=kind,
                addressee_id=mid,
                text=text,
                source_start=start,
                source_end=end,
                operations=operations if kind == "delegate" else [],
            )
        )
    # Unnamed follow-up questions retain a valid KP/NPC binding.
    if (
        not any(r.addressee_id == focus.addressee_id for r in requests)
        and focus.addressee_id in people
        and focus.question in raw
        and focus.question
        and question_request(focus.question, people.get(focus.addressee_id, ""))
        and not (
            focus.addressee_id in npc_ids
            and historical_third_person_question(focus.question, [people[focus.addressee_id]])
        )
        and not re.match(r"\s*(?:咱们|我们)(?!请你|让你|希望你)", focus.question)
        and not speaker_action(focus.question)
        and not inherited_speaker_action(
            raw, raw.index(focus.question), raw.index(focus.question) + len(focus.question)
        )
        and not any(
            mid != focus.addressee_id
            and start < raw.index(focus.question) + len(focus.question)
            and end > raw.index(focus.question)
            for mid, start, end in explicit
        )
    ):
        start = raw.index(focus.question)
        end = start + len(focus.question)
        end = min([end, *[r.source_start for r in requests if r.source_start > start]])
        question = raw[start:end]
        operations = requested_action_kinds(focus.question)
        requests.append(
            TurnRequest(
                kind="delegate" if operations else "question",
                addressee_id=focus.addressee_id,
                text=question,
                source_start=start,
                source_end=end,
                operations=operations,
            )
        )
    expanded = [scoped for request in requests for scoped in request_scopes(request)]
    focus.requests = sorted(expanded, key=lambda r: r.source_start)[:12]
    for request in focus.requests:
        targets = [
            mid
            for mid, name in people.items()
            if mid != request.addressee_id and name in request.text
        ]
        if len(targets) == 1:
            request.target_id = targets[0]
    return focus.requests


def repair_attribution(plan, raw, people, actor, *, npc_ids=(), explicit_spans=()):
    focus = plan.focus or TurnFocus()
    # A negative account of a prior attempt is context for the question, not
    # permission to repeat that attempt. Apply this before request attribution.
    from app.preparation.action_authority import action_kinds

    if focus.action and re.search(r"没|未|不曾", focus.action) and not action_kinds(focus.action):
        focus.action, focus.action_target_id, focus.obstacle = "", None, ""
        plan.focus = focus
        plan.parsed_intent.type = "converse"
        plan.proposed_check = plan.proposed_transition_id = None
        plan.proposed_tool_calls, plan.proposed_reveal_entity_ids = [], []
    had_requests = bool(focus.requests)
    requests = bind_requests(
        focus, raw, people, actor, npc_ids=npc_ids, explicit_spans=explicit_spans,
    )
    if (focus.addressee_id in npc_ids
            and not any(r.addressee_id == focus.addressee_id for r in requests)
            and historical_third_person_question(
                focus.question or raw, [people.get(focus.addressee_id, "")],
            )):
        focus.addressee_id = None
        plan.focus = focus
    if not requests:
        own = speaker_action(raw)
        if own and (had_requests or not focus.action):
            from app.agents.action_policy import explicit_movement
            focus.action, focus.question, focus.addressee_id = raw[raw.index(own):], "", None
            focus.action_target_id = focus.action_target_id or plan.parsed_intent.target_id
            plan.focus = focus
            kinds = set(action_kinds(focus.action))
            plan.parsed_intent.type = (
                "move" if explicit_movement(focus.action)
                and (plan.parsed_intent.type == "move" or not kinds)
                else "observe" if kinds <= {"observe"} else "investigate"
            )
            plan.needs_clarification = plan.parsed_intent.requires_clarification = False
        return
    plan.focus = focus
    # Request spans are speech, never part of the player's selected attempt.
    action = focus.action
    if action and action in raw:
        start, end = raw.index(action), raw.index(action) + len(action)
        intervals = [(start, end)]
        for request in requests:
            intervals = [
                part
                for left, right in intervals
                for part in (
                    (left, min(right, request.source_start)),
                    (max(left, request.source_end), right),
                )
                if part[0] < part[1]
            ]
        # The existing primary action is one original span. Preserve its full
        # operative continuation, not a manufactured concatenation across people.
        attempts = [raw[left:right] for left, right in intervals]
        action = next((a for a in attempts if re.search(r"我(?:们)?", a)), next(iter(attempts), ""))
    if not action:
        # Compatibility for a KP that put the entire mixed turn in question.
        first = min(r.source_start for r in requests)
        prefix = raw[:first]
        if speaker_action(prefix):
            action = prefix
        last = max(r.source_end for r in requests)
        tail = raw[last:]
        if not action and re.match(r"\s*我(?:们)?(?!是|想问|觉得|不|没|知道)", tail):
            action = tail
    focus.action = action
    primary = next((r for r in requests if r.addressee_id == focus.addressee_id), requests[0])
    if not focus.question or any(
        r.source_start <= raw.find(focus.question) < r.source_end for r in requests
    ):
        focus.addressee_id = primary.addressee_id
        focus.question = primary.text
    if not action:
        focus.action_target_id = None
        plan.parsed_intent.type = "converse"
        plan.parsed_intent.target_id = focus.addressee_id or primary.addressee_id
        plan.proposed_check = None
        plan.proposed_tool_calls = []
        plan.proposed_transition_id = None
        if not any(r.addressee_id in npc_ids for r in requests):
            plan.proposed_reveal_entity_ids = []
    plan.needs_clarification = plan.parsed_intent.requires_clarification = False


def requests_for(plan, member_id):
    return [
        r
        for r in (plan.focus.requests if plan.focus else [])
        if r.addressee_id == member_id and r.kind in {"question", "delegate", "cancel"}
    ]


def cancellation(text):
    # Stop scope is grammatical, not a list of task verbs. Matching the
    # stopped task against existing keys happens separately below.
    # A truthfulness constraint on the answer does not withdraw the task;
    # explicit "取消/停止" still carries its ordinary cancellation meaning.
    return bool(re.search(
        r"(?:^|[，,。；;])[^，,。；;！？!?“”\"]{0,24}?"
        r"(?:停止|取消|(?:不用|不必|不要|别|先不)(?!(?:再)?(?:声称|宣称|断言)))"
        r"(?!担心|害怕|紧张|客气)"
        r"[^，,。；;！？!?]+|算了|(?:全部|都)(?:停|别|不用)", text,
    ))


def cancellation_keys(request, pending, name=""):
    """Resolve against existing tasks, never a newly guessed scene target."""
    text = request["text"]
    tasks = [r for r in pending if r.get("kind") == "delegate"]
    if re.search(r"(?:全部|都)(?:停|别|不用)|取消全部", text):
        return [r["key"] for r in tasks]
    positive = re.sub(r"不用|不必|不要|停止|取消|先不|别", "", text)
    operations = set(action_kinds(positive))
    if operations & {"first_aid", "medicine"}:
        operations.update({"first_aid", "medicine"})
    # Compare actual target words after removing the shared request grammar.
    def topics(value):
        if name:
            value = value.replace(name, "")
        value = re.sub(
            r"先|一下|不用|不必|不要|停止|取消|先不|别|了|请|帮我|帮忙|检查|查看|观察|查|看看|看|找",
            "", value,
        )
        return {value[i:i + 2] for i in range(len(value) - 1)
                if not re.search(r"[，。；,;\s]", value[i:i + 2])}
    scores = []
    for old in tasks:
        overlap = operations & set(old.get("operations", []))
        target_match = request.get("target_id") and request["target_id"] == old.get("target_id")
        shared = topics(positive) & topics(old["text"])
        # Single-character physical targets such as 门 remain meaningful.
        short_target = any(n in positive and n in old["text"]
                           for n in ("门", "灯", "包", "伤", "血"))
        score = (4 if shared or short_target else 0) + (2 if target_match else 0) + bool(overlap)
        if score:
            scores.append((score, old["key"]))
    if scores:
        best = max(s for s, _ in scores)
        matches = [key for s, key in scores if s == best]
        return matches if len(matches) == 1 else []
    return [tasks[0]["key"]] if len(tasks) == 1 and not operations else []


def reconcile_requests(behavior, requests, *, seq, scene_id, reachable_ids, name=""):
    """Update the existing ledger before eligibility; preserve retired work for audit.

    A new question replaces unanswered questions, not unrelated unfinished work.
    A carried/reachable target and an explicit ongoing goal survive travel.
    """
    incoming = [{**r.model_dump(mode="json"), "key": f"{seq}:{r.source_start}",
                 "source_event_seq": seq, "scene_id": scene_id} for r in requests]
    for new in incoming:
        if new["kind"] == "cancel":
            new["cancelled_keys"] = cancellation_keys(new, behavior.pending_requests, name)
            new["needs_clarification"] = not bool(new["cancelled_keys"])
    active, retired = [], []
    for old in behavior.pending_requests:
        # Correct only a provable historical classification error, using the
        # stored original utterance. Preserve the request identity and audit;
        # unfinished real delegations and mixed requests remain untouched.
        if (old.get("kind") == "delegate" and information_question(old.get("text", ""))
                and not requested_action_kinds(old.get("text", ""))):
            retired.append({**old, "status": "reclassified", "updated_event_seq": seq,
                            "reason": "original_request_is_information_question"})
            old = {**old, "kind": "question", "operations": []}
        status = None
        if old.get("kind") == "question" and not question_request(old["text"], name):
            status = "superseded"
        for new in incoming:
            same_target = not new.get("target_id") or new.get("target_id") == old.get("target_id")
            if new["kind"] == "cancel" and old["key"] in new["cancelled_keys"]:
                status = "cancelled"
            elif new.get("replaces_prior") or (
                new["kind"] == old["kind"] == "question"
                or new["kind"] == old["kind"] == "delegate" and same_target
                and set(new.get("operations", [])) & set(old.get("operations", []))
                and new.get("text", "").strip() == old.get("text", "").strip()
            ):
                status = status or "superseded"
        if (
            not status and old.get("scene_id") and old["scene_id"] != scene_id
            and old.get("continuity", "scene") != "ongoing"
            and old.get("target_id") not in reachable_ids
        ):
            status = "unavailable"
        if status:
            retired.append({**old, "status": status, "updated_event_seq": seq})
        else:
            # A continuing goal survives travel but a remote physical task must
            # wait until its target is reachable. It is not a fresh direct request.
            available = not (
                old.get("kind") == "delegate" and old.get("target_id")
                and old.get("scene_id") != scene_id
                and old["target_id"] not in reachable_ids
            )
            current = dict(old)
            if available:
                current.pop("available", None)
            else:
                current["available"] = False
            active.append(current)
    keys = {r["key"] for r in active}
    active.extend(r for r in incoming if r["key"] not in keys)
    behavior.pending_requests = active
    behavior.request_history = [*behavior.request_history, *retired][-24:]
    if active and behavior.task_status != "proposed":
        behavior.task_status = "pending"
    elif retired and not active:
        behavior.task_status = retired[-1]["status"]
        behavior.current_short_term_goal = ""
    return retired
