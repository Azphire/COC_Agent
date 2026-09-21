"""Public narration validation and bounded deterministic Chinese fallback."""

import re

from app.rooms.service import require
from app.rules.display import check_display


def question_parts(question):
    """Separate current factual questions joined by a comma, retaining their wording."""
    result = []
    for sentence in re.findall(r"[^？?]+[？?]", question):
        parts = re.split(r"[，,：:]", sentence)
        pending = ""
        for part in parts:
            if re.search(r"什么|为何|为什么|怎么|哪|谁|是否|能否|听得见|感觉|[？?]", part):
                result.append((pending + part).rstrip("？?") + "？")
                pending = ""
            else:
                pending += part + "，"
    return result


def observation_request(text):
    """Keep the actual inspection clause separate from carried equipment."""
    from app.preparation.action_authority import operative_fragments

    for fragment in reversed(operative_fragments(text)):
        match = re.search(
            r"(?:寻找|找找|找|搜索|搜寻|摸索|检查|查看|观察|看看)(?:一下)?(.+)",
            fragment["text"],
        )
        if match:
            return match[1].strip("，,。；;！？!? ")
    return ""


def response_brief(plan, context, results, *, withdrawal=None):
    """Project approved public candidates and receipts, never private KP prose."""
    focus = plan.focus
    intent = plan.parsed_intent
    raw = context["triggering_action"].get("payload", {}).get("text", intent.evidence_quote)
    from app.agents.action_policy import explicit_movement, local_scene_movement
    from app.preparation.inventory import inventory_probe

    scene = context.get("module", {}).get("current_scene") or context.get("module", {}).get(
        "scene", {}
    )
    movement_requested = (
        (
            intent.type == "move"
            or bool(
                re.search(
                    r"(?:进入|走进|前往|返回|回到)[^，。！？；,.!?;\n]{0,24}(?:车厢|房间|大厅|厅|室)",
                    raw,
                )
            )
        )
        and explicit_movement(raw)
        and not local_scene_movement(
            raw,
            [{"type": "scene", "title": scene.get("title", "")}],
            context.get("module", {}).get("outgoing_transitions", []),
        )
    )

    attempt = focus.action if focus else intent.evidence_quote
    attempt = attempt if attempt in raw else ""
    question = focus.question if focus and focus.question in raw else raw
    addressee = (
        focus.addressee_id if focus else (intent.target_id if intent.type == "converse" else None)
    )
    people = context.get("current_participants", {}).get("members", {})
    if focus and focus.requests:
        npc_requests = [r for r in focus.requests if r.addressee_id not in people]
        if npc_requests:
            addressee = npc_requests[0].addressee_id
            question = "\n".join(r.text for r in npc_requests if r.addressee_id == addressee)
        elif focus.action and addressee in people:
            # The player's own result belongs to KP; delegates await their own
            # decision and child cycle, so are not material for this narration.
            question, addressee = "", None
    npc = next(
        (
            e
            for e in context.get("public_entities", [])
            if e["id"] == addressee
            and e["type"] == "npc"
            and e.get("fact_scope", "current_scene") == "current_scene"
        ),
        None,
    )
    if npc and focus and not focus.action and all(
        r.addressee_id == npc["id"] for r in focus.requests
    ):
        # A single conversation may contain several consecutive questions. A
        # model-selected suffix must not silently discard the earlier questions.
        question = raw
    result_ids = {
        e["payload"].get("entity_id", e["payload"].get("id"))
        for e in results["events"]
        if e["type"] in {"clue.revealed", "entity.revealed"}
    }
    wanted = set(focus.public_fact_ids if focus else []) | result_ids
    if focus and focus.action and focus.action_target_id:
        wanted.add(focus.action_target_id)
    options = context.get("PUBLIC_CLAIM_OPTIONS", [])
    if focus:
        current_ids = {
            e["id"]
            for e in context.get("public_entities", [])
            if e.get("fact_scope", "current_scene") == "current_scene"
        }
        current_ids.add(context.get("module", {}).get("scene", {}).get("id"))
        options = [
            c
            for c in options
            if c["claim_id"] in wanted
            or set(c["entity_ids"]) & (wanted | current_ids)
            or c["category"] == "rule"
        ]
    # A profile is portrayal, not testimony or evidence that a conversation happened.
    facts = [c for c in options if not npc or npc["id"] not in c["entity_ids"]]
    written_sources = []
    if not npc and re.search(r"看|观察|检查|文字|记号|字迹|读|原文|内容|写", raw):
        for entity in context.get("public_entities", []):
            if (
                (
                    entity.get("type") == "clue"
                    or entity.get("type") == "item"
                    and re.search(r"读|正文|原文|写.{0,4}(?:什么|啥)|文字内容", raw)
                )
                and entity.get("fact_scope", "current_scene") == "current_scene"
                and (
                    any(
                        name and name in raw
                        for name in [entity["title"], *entity.get("aliases", [])]
                    )
                    or focus
                    and focus.action_target_id == entity["id"]
                    or entity["id"] in result_ids
                )
            ):
                if entity.get("public_summary"):
                    written_sources.append(entity["public_summary"])
    return {
        "trigger_seq": context["triggering_action"]["seq"],
        "inventory_probe": inventory_probe(raw),
        "movement_requested": movement_requested,
        "speaker_id": intent.actor_member_id,
        "question": question,
        "purpose": focus.action if focus and focus.action in raw and focus.action else question,
        "answer_basis": "improvise"
        if focus and focus.answer_basis == "unrecorded"
        else focus.answer_basis
        if focus
        else "social",
        "attempt": attempt,
        "responder": {
            "id": npc["id"],
            "name": npc["title"],
            "kind": "npc",
            "portrayal": npc["public_summary"],
            "interaction_state": npc.get("interaction_state", {}),
        }
        if npc
        else {"id": addressee, "name": people[addressee], "kind": "teammate"}
        if addressee in people
        else {"kind": "keeper"},
        "allowed_facts": [
            {
                "id": c["claim_id"],
                "text": c["statement"],
                "entity_ids": c["entity_ids"],
                "source_kind": c["category"],
            }
            for c in facts
        ],
        "questions": question_parts(question),
        "player_statement": (
            question if npc and focus and focus.requests
            and any(r.addressee_id != addressee for r in focus.requests)
            else raw if npc else attempt or question
        ),
        "source_quotes": list(dict.fromkeys(written_sources))[:3],
        "current_scene": scene,
        "observation_subject": next((
            {k: e[k] for k in ("id", "type", "title", "public_summary") if k in e}
            for e in context.get("public_entities", [])
            if focus and e["id"] == focus.action_target_id and e["type"] != "scene"
        ), None),
        "current_inventory": context.get("item_holders", []),
        "current_state": [
            r
            for e in context.get("public_entities", [])
            if e.get("fact_scope", "current_scene") == "current_scene"
            for r in e.get("current_state_receipts", [])
        ],
        "incidental_memories": context.get("incidental_memories", []),
        "recent_dialogue": context.get("recent_dialogue", [])[-6:],
        "visit_kind": next(
            (
                e["payload"].get("visit_kind", "first")
                for e in reversed(results["events"])
                if e["type"] == "scene.updated"
            ),
            "continuing",
        ),
        "completed_results": results,
        "result_facts": results.get("result_facts", []),
        "ordinary_observation": bool(
            (
                results.get("observation_completed")
                or not npc and addressee not in people and question and not plan.proposed_check
                and not movement_requested
                and re.search(r"看|观察|检查|表面|杂物|东西", raw)
                and re.search(r"什么|字迹|外观|颜色|样子|形状", raw)
            )
            and not results.get("blocked_discovery")
            and not results.get("blocked_operations")
            and not results.get("failed_tools")
        ),
        "withdrawal": withdrawal,
        "pending_decisions": list(
            filter(
                None,
                [
                    plan.next_decision
                    if plan.next_decision in raw
                    else "后续行动仍需你决定。"
                    if plan.next_decision
                    else "",
                    focus.suggestion if focus and focus.suggestion in raw else "",
                    focus.hypothesis if focus and focus.hypothesis in raw else "",
                ],
            )
        ),
    }, facts


def action_lead(intent, results):
    if intent == "recall":
        return "你回顾了先前获知的信息。"
    if intent == "converse":
        return "你向对方询问了情况。"
    if intent == "move":
        return (
            "你已抵达新的位置。"
            if any(e["type"] == "scene.updated" for e in results["events"])
            else "你仍在原处，可以继续确认通行条件。"
        )
    if intent == "investigate":
        return "你仔细检查了眼前的目标。"
    if intent == "observe":
        return "你环顾四周，确认了眼前的情况。"
    if intent in {"interact", "use_item", "assist"}:
        return "你尝试了这次动作。"
    return ""


class NarrationValidator:
    def validate(
        self, output, *, documents, public_ids, scene_id, results, brief=None, inventory_state=None
    ):
        text = "\n".join(
            [
                output.public_narration,
                output.npc_speech.text if output.npc_speech else "",
                *output.incidental_details,
            ]
        )
        if output.npc_speech:
            for question in (brief or {}).get("questions", []):
                # Repeating the user's question as the NPC's own question does
                # not answer it. Quoted acknowledgements without '?' are fine.
                tail = re.split(r"[，,：:]", question)[-1].strip()
                require(
                    len(tail) < 7 or tail not in output.npc_speech.text,
                    "NPC复述了玩家的问题，请回答该问题或说明具体未知之处：" + tail,
                    422,
                )
        require(
            not re.search(r"[a-z][a-z0-9]*_[a-z0-9_]+", text, re.I), "公开叙事包含内部标识", 422
        )
        require(
            not re.search(r"Traceback|Exception|HTTPError|工具调用|系统错误", text),
            "公开叙事包含系统内容",
            422,
        )
        require(
            not output.current_scene_reference or output.current_scene_reference == scene_id,
            "叙事引用了非当前场景",
            422,
        )
        require(set(output.public_entity_references) <= public_ids, "叙事引用了不可见实体", 422)
        for claim in documents:
            require(claim["visibility"] == "public", "叙事泄漏私密内容", 422)
            require(set(claim["entity_ids"]) <= public_ids, "叙事引用了非当前公开实体", 422)
        checks = {
            e["payload"].get("id", e["payload"].get("check_id")): e["payload"]
            for e in results["events"]
            if e["type"] == "check.resolved"
        }
        from app.agents.results import result_error

        items = (inventory_state or {}).get("known_items", [])
        error = result_error(text, results.get("result_facts", []), items=items) or result_error(
            output.public_narration, results.get("result_facts", []), narration=True,
            items=items,
        )
        require(not error, "叙事声称了未获实际结算支持的效果：" + (error or ""), 422)
        negative = [f for f in results.get("current_result_facts", [])
                    if f.get("status") in {"failure", "not_executed", "technical_failure"}]
        if negative and output.public_narration:
            from app.agents.results import answer_result_error

            require(not answer_result_error(
                output.public_narration, (brief or {}).get("attempt", ""), negative,
            ), "本次操作未执行，须说明实际没有做成什么，而非只描述周围环境", 422)
            require(
                bool(re.search(r"失败|未|没|无法|不能|尚|不成功|不奏效", output.public_narration)),
                "本轮存在未成功的实际结果，反馈必须说明而不能省略："
                + "；".join(f.get("effect", "") for f in negative), 422,
            )
        scene_text = str((brief or {}).get("current_scene", {}))
        if not re.search(r"白天|清晨|日光|阳光|晴天|午后", scene_text):
            require(not re.search(r"阳光|日光|晨光", text), "叙事与当前夜间场景冲突", 422)
        transitions = {str(e["seq"]) for e in results["events"] if e["type"] == "scene.updated"}
        require(
            not output.check_result_reference or output.check_result_reference in checks,
            "检定尚未完成",
            422,
        )
        require(
            not output.transition_result_reference
            or output.transition_result_reference in transitions,
            "转场尚未发生",
            422,
        )
        if not transitions and (
            results.get("failed_tools")
            or results.get("blocked_operations")
            or (brief or {}).get("movement_requested")
        ):
            require(
                not result_error(text, [{"operation": "move", "status": "not_executed",
                                         "source_event_seq": 0}], narration=True),
                "失败的转场不能叙述为已经到达",
                422,
            )
        discovered = [
            e["payload"].get("public_summary", e["payload"].get("content", ""))
            for e in results["events"]
            if e["type"] in {"entity.revealed", "clue.revealed"}
        ]
        interactions = [
            e["payload"].get("text", "")
            for e in results["events"]
            if e["type"] == "module.interaction"
        ]
        sources = [*(brief or {}).get("source_quotes", []), *discovered, *interactions]
        if sources:
            material = re.sub(r"[\W_]", "", "\n".join(sources))
            if any(re.search(r"写着|写道|写有|内容是", s) for s in sources):
                require(
                    not re.search(
                        r"(?:没有|并无|没见到|看不到)(?:任何|什么)?(?:文字|字迹|内容)", text
                    ),
                    "叙事否定了已公开的文字内容",
                    422,
                )
            for literal in re.findall(
                r"(?:写着|写道|写有|上写|内容是)[：:\s“‘\"]*([^。；！？”\"]+)", text
            ):
                compact = re.sub(r"[\W_]", "", literal)
                require(not compact or compact in material, "文字内容没有对应的已公开来源", 422)
            for source in sources:
                # Short labelled values (names, dates, codes) are source facts,
                # not licence to substitute a different value in natural prose.
                for label, value in re.findall(
                    r"(?:^|[。；\n])([^：:\n]{2,16})[：:]([^。；\n]{1,30})", source
                ):
                    if re.search(r"写着|写道|写有|内容是", label):
                        continue
                    if label[-2:] in text:
                        require(
                            re.sub(r"[\W_]", "", value) in re.sub(r"[\W_]", "", text),
                            "叙事改写了本轮已确认的具体内容：" + source,
                            422,
                        )
        uncertain = re.search(r"未|没|无法|不能|不清|不确定|难以|仍需|尚需", text)
        if not any(discovered) and (
            (brief or {}).get("unconfirmed_target") or results.get("blocked_discovery")
        ):
            require(bool(uncertain), "未确认的调查不能叙述为已经发现内容", 422)
        if results.get("blocked_operations") and not any(interactions):
            for clause in re.split(r"[。；\n]", text):
                if not re.search(r"未|没|无法|不能|如果|假如", clause):
                    require(
                        not re.search(
                            r"接过|拿到了|收到了|已交给|交到了|成功.{0,8}(?:给|取|拿)", clause
                        ),
                        "没有完成的物品操作不能叙述为已收到",
                        422,
                    )
        if (brief or {}).get("inventory_probe") and inventory_state is not None:
            actor = (brief or {}).get("speaker_id")
            holders = [
                h
                for h in inventory_state.get("holders", [])
                if not actor or h.get("holder_id") == actor
            ]
            for clause in re.split(r"[。；\n]", text):
                if not re.search(r"未|没|如果|假如", clause) and re.search(
                    r"拿着|拿到了|找到|发现|取出|口袋里有", clause
                ):
                    require(
                        any(h.get("title") and h["title"] in clause for h in holders),
                        "随身物发现必须与实际登记的持有状态一致",
                        422,
                    )
        if checks:
            passed = checks.get(output.check_result_reference, next(iter(checks.values())))[
                "result"
            ]["passed"]
            contradictory = r"检定失败|未通过|没有成功" if passed else r"检定成功|检定通过|成功地"
            require(not re.search(contradictory, text), "叙事与真实检定结果冲突", 422)
            require(
                passed or any(discovered) or bool(uncertain),
                "检定失败后需要说明实际后果或未能确认的内容",
                422,
            )
            visible_text = output.public_narration + (
                output.npc_speech.text if output.npc_speech else ""
            )

            def includes_feedback(source):
                grams = {
                    source[i : i + 2]
                    for i in range(len(source) - 1)
                    if re.fullmatch(r"[\w\u4e00-\u9fff]{2}", source[i : i + 2])
                }
                return sum(g in visible_text for g in grams) >= min(3, len(grams))

            require(
                not passed
                or not any(discovered)
                or any(includes_feedback(s) for s in discovered if s),
                "成功后的具体反馈没有出现在发言中，请自然解释本轮实际发现："
                + "；".join(discovered)[:500],
                422,
            )
        require(
            not re.search(
                r"(?:受到|扣除|损失|恢复|获得).{0,6}[一二三四五六七八九十百\d]+点?(?:伤害|生命|HP|MP|幸运)",
                text,
            ),
            "资源变化必须使用实际结算记录",
            422,
        )
        # These are structural and literal checks, not proof of semantic truth.
        # The public narrator never receives private KP material. Its prose and
        # incidental details do not create entities, resources, exits or results.
        return {
            "valid": True,
            "checks": len(checks),
            "scene_id": scene_id,
            "scope": "visibility_and_result_references; semantic correctness not proven",
        }


def fallback_narration(
    intent_type, results, public_scene, *, rejected=False, brief=None, inventory_state=None
):
    if (brief or {}).get("inventory_probe") and inventory_state is not None:
        if any(
            m.get("starting_belongings") == "undetermined"
            for m in inventory_state.get("members", [])
        ):
            from app.preparation.inventory import inventory_reply

            pending = any(e["type"] == "check.requested" for e in results["events"])
            return ("请先完成本次随身物检定。\n" if pending else "") + "\n".join(
                inventory_reply(inventory_state, m["id"]).replace("我", m.get("name", "调查员"), 1)
                for m in inventory_state.get("members", [])
                if not (brief or {}).get("speaker_id") or m["id"] == brief["speaker_id"]
            )
    interactions = [
        e["payload"].get("text")
        for e in results["events"]
        if e["type"] == "module.interaction" and e["payload"].get("text")
    ]
    medical = [e["payload"].get("summary", "") for e in results["events"]
               if e["type"] == "combat.resolved"]
    unsettled = [f for f in results.get("current_result_facts", [])
                 if f["status"] in {"not_executed", "technical_failure", "failure"}]
    if medical or unsettled:
        from app.agents.results import describe_result

        effects = list(dict.fromkeys([*medical, *(describe_result(f) for f in unsettled)]))
        return "\n".join(filter(None, [*interactions, *effects]))
    if interactions:
        return "\n".join(dict.fromkeys(interactions))
    if (brief or {}).get("resource_gate") == "unconfirmed_item" and not any(
        e["type"] == "check.resolved" for e in results["events"]
    ):
        return "你检查了随身物品，但还没有完成确认物品所需的检定。"
    checks = [e["payload"] for e in results["events"] if e["type"] == "check.resolved"]
    transitions = [e["payload"] for e in results["events"] if e["type"] == "scene.updated"]
    reveals = [
        e["payload"].get("public_summary", e["payload"].get("content", ""))
        for e in results["events"]
        if e["type"] in {"entity.revealed", "clue.revealed"}
    ]
    if checks:
        check = checks[-1]
        if check.get("opposed"):
            return "\n".join([check["display_text"], *reveals])
        outcome = (
            "检定成功，行动达到了本次检定的目标。"
            if check["result"]["passed"]
            else "检定失败，这次尝试未能达到目标。"
        )
        task_feedback = list(dict.fromkeys([*reveals, *(brief or {}).get("source_quotes", [])]))
        if not task_feedback:
            task_feedback = [
                "你完成了这次尝试，但尚未确认额外的线索内容。"
                if check["result"]["passed"]
                else "你没能确认这次要查明的内容；可以换一个有新依据的方法。"
            ]
        return "\n".join(
            filter(
                None,
                [
                    outcome,
                    check.get("display_text") or check_display(check)["display_text"],
                    *task_feedback,
                ],
            )
        )
    if transitions:
        latest = transitions[-1]
        if latest.get("visit_kind") == "revisit":
            return (
                "你回到"
                + latest.get("scene_title", "先前的地方")
                + "，可以根据眼前的状态继续行动。"
            )
        return latest.get("scene_summary") or "你已抵达当前场景，可以继续查看周围。"
    if results.get("blocked_operations"):
        if reveals:
            return "这次请求的操作未全部完成。\n" + "\n".join(dict.fromkeys(reveals))
        return "这次动作没有完成，当前状态未因这次尝试改变。"
    if (brief or {}).get("movement_requested"):
        return (
            "本次没有完成转场；当前位置仍是"
            + brief.get("current_scene", {}).get("title", "原场景")
            + "。"
        )
    if reveals:
        return "你查看了眼前的目标。\n" + "\n".join(reveals)
    if results.get("blocked_discovery"):
        return "这次尚未确认新的线索内容，仍需满足该目标的调查条件。"
    brief = brief or {}
    if brief.get("unconfirmed_target"):
        return "你尚未实际确认这个目标的内容，可以先尝试调查眼前的目标。"
    if brief.get("source_quotes"):
        return "\n".join(brief["source_quotes"])
    if brief.get("attempt") and intent_type in {"observe", "investigate"}:
        subject = brief.get("observation_subject") or {}
        scope = observation_request(brief["attempt"])
        known = subject.get("public_summary", "") if subject.get("title", "") in scope else ""
        feedback = ("这次还没查清“" + scope + "”，可以换个位置或方法继续查看。"
                    if scope else "这次查看还没得到新的结果，可以换个位置或方法继续。")
        return "\n".join(filter(None, [known, feedback]))
    if brief.get("responder", {}).get("kind") == "teammate" and not brief.get("attempt"):
        return "你向" + brief["responder"].get("name", "队友") + "说出了这番话。"
    topic = " ".join(str(brief.get(k, "")) for k in ("attempt", "question", "purpose"))
    if inventory_state is not None and (
        re.search(r"照明|屏幕|点亮|光|交接|递给|交给|随身|口袋", topic)
        or any(
            name and name in topic
            for item in inventory_state.get("known_items", [])
            for name in item["names"]
        )
    ):
        from app.preparation.inventory import recalled_inventory

        current = recalled_inventory(inventory_state, "当前持有物品")
        return "这次没有完成新的物品操作。\n" + "\n".join(r["text"] for r in current)
    facts = [f["text"] for f in brief.get("allowed_facts", []) if f.get("text")]
    # A failed generation cannot recover by republishing an unchecked old
    # improvisation. Ordinary generation still receives those attributed memories.
    available = list(dict.fromkeys(facts[:3])) or ([public_scene] if public_scene else [])
    directions = (
        "你可以继续追问具体细节，或查看这些信息提到的地方；接下来怎么做由你决定。"
        if intent_type == "converse"
        else "你可以继续查看眼前的人或物，也可以换个方法尝试；接下来怎么做由你决定。"
    )
    return "\n".join([*available, directions])
