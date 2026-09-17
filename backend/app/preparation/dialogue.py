"""Source-limited NPC answers and actual ownership of teammate actions."""

import re

from app.agents.adjudication_schemas import KeeperPlan, TurnFocus
from app.persistence.agent_models import AgentCycle, AgentRun
from app.preparation.action_authority import speaker_action, teammate_request


def public_npc_name(entity, scene_text):
    names = [entity["title"], *entity.get("aliases", [])]
    # A public description may include an adjective between title words.
    names += [entity["title"][i:] for i in range(1, len(entity["title"]) - 2)]
    return next((n for n in names if len(n) >= 2 and n in scene_text), None)


def question_after_treatment(raw, label):
    match = re.search(r"(?:询问|提问|问)(?:他|她|对方)?[：:，,]?([^。；]+)", raw)
    if not match or re.search(r"包扎|急救|治疗|处理伤口", match[1]):
        return None
    return label + "，" + match[1]


def compound_treatment(raw):
    """An explicit present treatment followed by speech remains two intentions."""
    action = re.split(r"(?:并|同时|一边|然后).{0,3}(?:询问|提问|问)", raw, maxsplit=1)
    if len(action) < 2 or re.search(
        r"如果|假如|能否|可否|是否|不要|不做|不想|刚才|之前", action[0]
    ):
        return None
    if re.search(r"我(?:们)?(?:给|为|对|开始|进行|先|用|做|包扎|急救)", action[0]):
        if "医学治疗" in action[0]:
            return "medicine"
        if re.search(r"包扎|急救", action[0]):
            return "first_aid"
    return None


def can_speak(entity, participants):
    actual = next((n for n in participants.values() if n.get("npc_id") == entity["id"]), None)
    injury = (actual or entity.get("combat_template") or {}).get("injury", {})
    return not (injury.get("dead") or injury.get("unconscious"))


async def continue_treatment_question(service, session, room, cycle, action):
    """Continue an already requested question after the original treatment settles."""
    from uuid import uuid4

    from app.agents.conversation import initial_state
    from app.persistence.room_models import RoomEvent

    if not action and cycle.state.get("combat_rejection"):
        action = cycle.state.get("combat_decision")
    if (
        not action
        or action.get("operation") not in {"first_aid", "medicine"}
        or cycle.state.get("question_continuation")
    ):
        return
    original = await session.get(RoomEvent, (room.id, cycle.state["triggering_event_seq"]))
    target = (
        room.session_state.get("combat", {})
        .get("participants", {})
        .get(action.get("target_id"), {})
    )
    if not original or not target.get("npc_id"):
        return
    question = question_after_treatment(original.payload["text"], target["label"])
    if not question:
        return
    child_id = str(uuid4())
    event = service.rooms.append(
        session,
        room,
        "action.submitted",
        original.actor_member_id,
        {
            "text": question,
            "category": "dialogue",
            "cycle_id": child_id,
            "continuation_of": original.seq,
            "question_quote": original.payload["text"],
        },
        request_id=cycle.id + ":question",
    )
    state = initial_state(
        room.id, child_id, original.actor_member_id, event.seq, [], parent=cycle.id
    )
    session.add(AgentCycle(id=child_id, room_id=room.id, status="queued", state=state))
    cycle.state = {**cycle.state, "question_continuation": child_id}


def dialogue_target(raw, candidates, previous=None, selected=None, *, member_names=()):
    if not re.search(r"问|说|你好|您好|听得见|[?？]|告诉|请教|聊|打招呼", raw):
        return None
    salutation = re.match(r"\s*([\w·]{2,20}?)(?:先生|女士)?[，,：:]", raw)
    if any(
        name
        and re.match(
            r"\s*(?:(?:你好|您好)[，,]\s*)?" + re.escape(name) + r"(?:先生|女士)?[，,：:]", raw
        )
        for name in member_names
    ):
        return None
    named = [
        e
        for e in candidates
        if any(n in raw for n in [e["title"], *e.get("aliases", [])])
        or salutation
        and (e["title"].endswith(salutation[1]) or salutation[1] in e.get("public_summary", ""))
    ]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        return None
    if (
        len(candidates) == 1
        and salutation
        and salutation[1] in {"师傅", "先生", "女士", "大哥", "大姐", "你好", "您好"}
    ):
        # Candidates already exclude hidden, remote and unable-to-speak NPCs.
        # A first greeting does not require their full prepared entity title.
        return candidates[0]
    return next((e for e in candidates if e["id"] == selected), None) or (
        next((e for e in candidates if e["id"] == previous), None)
        if re.search(r"他|她|你|接着|继续|追问|那么|那", raw)
        else None
    )


def repair_dialogue_focus(plan, raw, candidates, previous=None, *, member_names=()):
    from app.agents.generation_contracts import utterance_clauses
    from app.memory.facts import readonly_recall

    direct = any(
        re.match(re.escape(e["title"]) + r"(?:先生|女士)?[，,：:]", raw) for e in candidates
    )
    focus = plan.focus or TurnFocus()
    npc = dialogue_target(
        raw,
        candidates,
        previous,
        focus.addressee_id or focus.action_target_id,
        member_names=member_names,
    )
    if not npc:
        return None
    if readonly_recall(raw) and (
        re.search(r"回顾|复述|记错|原话|说过|告诉我们", raw)
        or not (direct or focus.action or re.search(r"你|您", focus.question))
    ):
        return None
    clauses = utterance_clauses(raw)
    questions = [
        i
        for i, c in enumerate(clauses)
        if re.search(r"问|说|你好|您好|听得见|[?？]|告诉|请教|打招呼", c["text"])
    ]
    if not focus.question or focus.question not in raw:
        focus.question = (
            "".join(c["text"] for c in clauses[min(questions) : max(questions) + 1])
            if questions
            else raw
        )
    focus.addressee_id = npc["id"]
    if focus.action and re.search(r"[?？]", focus.action):
        # Repair a purely addressed question; a separate present first-person
        # declaration (including verbs outside the operation lexicon) survives.
        declarations = [
            c["text"]
            for c in utterance_clauses(focus.action)
            if re.match(r"\s*我(?:们)?", c["text"])
            and not re.search(r"[?？]|(?:询问|问|说|建议|请教)", c["text"])
        ]
        from app.agents.action_policy import explicit_movement
        from app.preparation.action_authority import action_kinds

        if (
            not declarations
            and not set(action_kinds(focus.action)) - {"converse"}
            and not explicit_movement(focus.action)
            and not compound_treatment(focus.action)
        ):
            focus.action = ""
            focus.action_target_id = None
    # Dialogue repair binds a speaker, not a second action classifier. The
    # selected action clauses still pass the ordinary authority/check guards.
    # An incomplete verb whitelist used to erase valid simultaneous actions.
    plan.focus = focus
    if not focus.action:
        plan.parsed_intent.type = "converse"
        plan.parsed_intent.target_id = npc["id"]
        plan.parsed_intent.target_kind = "npc"
    plan.needs_clarification = plan.parsed_intent.requires_clarification = False
    return npc


def repair_teammate_focus(plan, raw, members, actor):
    request = teammate_request(raw, members, actor)
    own_action = (
        speaker_action(plan.focus.action)
        if plan.focus and plan.focus.action
        else speaker_action(raw)
    )
    if not own_action and plan.focus and plan.focus.action:
        from app.agents.generation_contracts import utterance_clauses

        # The primary KP has already selected actual action clauses. Preserve
        # its first-person statement even when it uses an unlisted action verb.
        own_action = next(
            (
                c["text"]
                for c in utterance_clauses(plan.focus.action)
                if re.match(r"\s*我(?:们)?(?!.*[?？])", c["text"])
                and not re.search(r"如果|假如|假设|我(?:想问|问|说|建议)", c["text"])
                and not re.match(r"\s*我(?:们)?(?:没|不|是|有|觉得|知道|听说|还得)", c["text"])
            ),
            None,
        )
    if (
        own_action
        and plan.focus
        and plan.focus.action
        and plan.focus.action in raw
        and plan.focus.action.startswith(own_action)
    ):
        # A selected span can include speech between two actual operations.
        # The clause contract already preserves its questions separately. Do
        # not discard the later operation merely because that span contains '?'.
        own_action = plan.focus.action
        from app.agents.generation_contracts import utterance_clauses

        selected = utterance_clauses(own_action)
        for index, clause in enumerate(selected):
            if not re.match(r"\s*我(?:们)?", clause["text"]) and teammate_request(
                clause["text"], members, actor
            ):
                own_action = "".join(c["text"] for c in selected[:index])
                break
    if request and own_action and own_action in raw:
        # Preserve the model's actual first-person action; only the separate
        # request belongs to the addressee. Do not manufacture another act.
        plan.focus.action = own_action
        if not plan.focus.addressee_id or plan.focus.addressee_id in members:
            if not plan.focus.question:
                plan.focus.question = raw.replace(own_action, "", 1)
            plan.focus.addressee_id = request
        plan.addressed_member_id = request
    elif request:
        plan.focus = TurnFocus(question=raw, addressee_id=request, answer_basis="teammate")
        plan.parsed_intent.type = "converse"
        plan.parsed_intent.target_id = request
        plan.addressed_member_id = request
        plan.proposed_check = None
        plan.proposed_tool_calls = []
        plan.proposed_transition_id = None
        plan.proposed_reveal_entity_ids = []
        plan.needs_clarification = plan.parsed_intent.requires_clarification = False


async def prepare_dialogue(runtime, state, run_id):
    service = runtime.service

    async def operation(session, room):
        run = await session.get(AgentRun, run_id)
        plan = KeeperPlan.model_validate(run.structured_output)
        cycle = await session.get(AgentCycle, state["cycle_id"])
        facts = await service.adjudication.facts(session, room, cycle, run)
        if not facts.actor_authorized:
            return
        scene_text = room.session_state.get("scene_summary", "")
        prepared = await service.entities.binding(session, room.id)
        candidates = [
            {
                "id": eid,
                "title": e["title"],
                "aliases": e.get("aliases", []),
                "public_summary": e.get("public_summary", "")
                if eid in facts.revealed_entity_ids or not prepared
                else scene_text,
                "type": "npc",
                "fact_scope": "current_scene",
            }
            for eid, e in facts.approved_entities.items()
            if e.get("type") == "npc"
            and eid in facts.local_entity_ids
            and (eid in facts.visible_entity_ids or public_npc_name(e, scene_text))
            and can_speak(
                {**e, "id": eid}, room.session_state.get("combat", {}).get("participants", {})
            )
        ]
        from app.memory.events import story_events
        from app.rooms.service import Identity

        history, _ = story_events(
            await service.rooms.events(session, room, Identity(facts.actor_member_id, False))
        )
        previous = next(
            (e["payload"].get("entity_id") for e in reversed(history) if e["type"] == "npc.spoke"),
            None,
        )
        members = {
            m.id: m.display_name
            for m in await service.rooms.members(session, room)
            if m.active and m.role == "player"
        }
        npc = repair_dialogue_focus(
            plan, facts.raw_text, candidates, previous, member_names=members.values()
        )
        if npc:
            cycle.state = {**cycle.state, "dialogue_npc": npc}
        repair_teammate_focus(plan, facts.raw_text, members, facts.actor_member_id)
        # Only a specifically addressed NPC and topic can disclose this clue.
        for eid, entity in facts.approved_entities.items():
            if entity.get("type") != "npc" or eid not in facts.local_entity_ids:
                continue
            focus = plan.focus
            if not focus or not (focus.question or plan.parsed_intent.type == "converse"):
                continue
            if not npc or focus.addressee_id != eid:
                continue
            for topic in entity.get("dialogue_topics", []):
                if not any(word in facts.raw_text for word in topic.get("keywords", [])):
                    continue
                if not set(topic.get("required_entity_ids", [])) <= facts.revealed_entity_ids:
                    continue
                plan.focus.addressee_id = eid
                plan.focus.question = plan.focus.question or facts.raw_text
                plan.focus.answer_basis = "facts"
                # The NPC is publicly described in the current scene, and the
                # topic's own source-approved disclosure gates remain in force.
                if eid in {n["id"] for n in candidates}:
                    for revealed in topic.get("reveal_entity_ids", []):
                        await service.entities.reveal(
                            session,
                            room,
                            revealed,
                            room.host_member_id,
                            cycle_id=cycle.id,
                        )
                    plan.focus.public_fact_ids = list(
                        dict.fromkeys(
                            [
                                *plan.focus.public_fact_ids,
                                *topic.get("reveal_entity_ids", []),
                            ]
                        )
                    )[:5]
                    cycle.state = {
                        **cycle.state,
                        "dialogue_fact_ids": list(
                            dict.fromkeys(
                                [
                                    *cycle.state.get("dialogue_fact_ids", []),
                                    *topic.get("reveal_entity_ids", []),
                                ]
                            )
                        ),
                    }
                    if not plan.focus.action:
                        plan.parsed_intent.type = "converse"
                        plan.parsed_intent.target_id = eid
                        plan.proposed_reveal_entity_ids = []
        run.structured_output = plan.model_dump(mode="json")

    await service.mutate(state["room_id"], operation)


def sourced_dialogue_reply(question, answers):
    """Keep source testimony literal and acknowledge unsupported follow-up parts.

    Ordinary unsourced dialogue remains free prose. A selected source quotation
    cannot silently replace another question in the same utterance.
    """
    from app.knowledge.text import tokens

    stop = set(tokens("你 我 我们 什么 怎么 哪些 还有 需要 请问 师傅 先生 女士 说的 告诉 一下"))
    material = set(tokens(" ".join(answers))) - stop
    replies = list(dict.fromkeys(answers))
    for part in re.findall(r"[^?？]+[?？]", question):
        part = re.split(r"[，,：:]", part)[-1].strip()
        wanted = set(tokens(part)) - stop
        if wanted and len(wanted & material) < 2:
            replies.append("至于你问的“" + part.rstrip("?？") + "”，我不能确定更多细节。")
    return "\n".join(replies)
