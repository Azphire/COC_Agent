"""Bind the KP's per-addressee interpretation to original utterance spans."""

import re

from app.agents.adjudication_schemas import TurnFocus, TurnRequest
from app.preparation.action_authority import (
    addressed_spans,
    declared_action,
    information_question,
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


def bind_requests(focus, raw, people, actor):
    """Keep valid model interpretations; repair only explicit ownership boundaries.

    Old saves/plans lack requests. The lexical compatibility path runs here once,
    never independently in scheduling, normalization or authority checks.
    """
    explicit = addressed_spans(raw, {mid: name for mid, name in people.items() if mid != actor})
    requests = []
    for request in focus.requests:
        if request.addressee_id not in people or request.addressee_id == actor:
            continue
        if not request.text or raw[request.source_start : request.source_end] != request.text:
            continue
        owners = {
            mid
            for mid, start, end in explicit
            if start < request.source_end and end > request.source_start
        }
        if owners and owners != {request.addressee_id}:
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
        if request.kind == "delegate" and information_question(request.text):
            request.kind = "question"
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
            "hypothesis"
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
    focus.requests = sorted(requests, key=lambda r: r.source_start)[:12]
    for request in focus.requests:
        targets = [
            mid
            for mid, name in people.items()
            if mid != request.addressee_id and name in request.text
        ]
        if len(targets) == 1:
            request.target_id = targets[0]
    return focus.requests


def repair_attribution(plan, raw, people, actor, *, npc_ids=()):
    focus = plan.focus or TurnFocus()
    had_requests = bool(focus.requests)
    requests = bind_requests(focus, raw, people, actor)
    if not requests:
        own = speaker_action(raw)
        if own and (had_requests or (focus.question == raw and not focus.action)):
            from app.agents.action_policy import explicit_movement
            from app.preparation.action_authority import action_kinds

            focus.action, focus.question, focus.addressee_id = raw[raw.index(own):], "", None
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
        last = max(r.source_end for r in requests)
        tail = raw[last:]
        if re.match(r"\s*我(?:们)?(?!是|想问|觉得|不|没|知道)", tail):
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
        if r.addressee_id == member_id and r.kind in {"question", "delegate"}
    ]
