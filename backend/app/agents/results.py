"""Compact public result facts shared by narration, speech and task memory."""

import re


def result_facts(events):
    facts = []
    actors = {e['payload'].get('cycle_id'): e.get('actor_member_id') for e in events
              if e['type'] in {'action.submitted', 'agent.action_proposed'}}
    originals = {e["seq"]: e for e in events}
    for event in events:
        p = event["payload"]
        cycle = p.get("cycle_id")
        if event["type"] in {"action.submitted", "agent.action_proposed"}:
            actors[cycle] = event.get("actor_member_id")
        if event["type"] == "action.result":
            for saved in p.get("facts", []):
                original = originals.get(saved.get("source_event_seq"), {})
                source = original.get("payload", {})
                if source.get("cycle_id") and source["cycle_id"] != cycle:
                    source = {}
                fact = {
                    **saved,
                    "cycle_id": saved.get("cycle_id") or cycle,
                    "actor_id": saved.get("actor_id") or actors.get(cycle),
                    "operated_items": saved.get("operated_items")
                    or source.get("operated_items", []),
                }
                if fact.get("status") in {"not_executed", "technical_failure"}:
                    fact["source_action_seq"] = fact.get(
                        "source_action_seq", fact.get("source_event_seq")
                    )
                    fact["result_event_seq"] = event["seq"]
                    fact["action_text"] = fact.get("action_text") or source.get("text", "")
                    if (fact.get("target_name") and fact.get("action_text")
                            and fact["target_name"] not in fact["action_text"]
                            and not fact["operated_items"]):
                        fact["reported_target_id"] = fact.get("target_id")
                        fact["reported_target_name"] = fact["target_name"]
                        fact["target_id"], fact["target_name"] = None, None
                facts.append(fact)
            continue
        if event["type"] not in {
            "combat.resolved", "module.interaction", "check.resolved", "scene.updated",
        }:
            continue
        if event["type"] == "scene.updated":
            p = {**p, "operation": "move", "passed": True,
                 "target_id": p.get("scene_id"), "target_name": p.get("scene_title"),
                 "summary": "已到达" + p.get("scene_title", "当前场景") + "。"}
        operation = p.get("operation")
        operations = p.get("operations", []) or ([operation] if operation else [])
        if event["type"] == "check.resolved" and not operations:
            # A die settlement is evidence of that check, not proof that every
            # requested item operation or scene transition also happened.
            operations = ["check"]
        if not operations and event["type"] == "module.interaction":
            from app.preparation.action_authority import action_kinds

            operations = action_kinds(p.get("text", ""))
        result = p.get("result") or {}
        if operation in {"first_aid", "medicine"}:
            result = p.get("rolls", {}).get("treatment", {}).get("result", {})
        passed = p.get("passed", result.get("passed"))
        if event["type"] == "module.interaction" and passed is None:
            passed = True
        for op in operations:
            facts.append({
                "actor_id": p.get("actor_id") or actors.get(cycle)
                or p.get("target_member_id") or event.get("actor_member_id"),
                "target_id": p.get("target_id") or p.get("entity_id"),
                "target_name": p.get("target_name") or p.get("target"),
                "operation": op,
                "status": "success" if passed is True else "failure"
                if passed is False else "attempted",
                "effect": p.get("summary") or p.get("text") or p.get("display_text", ""),
                "operated_items": p.get("operated_items", []),
                "source_event_seq": event["seq"], "cycle_id": cycle,
                **({"source_action_seq": p["source_event_seq"]}
                   if p.get("source_event_seq") is not None else {}),
                **{k: p[k] for k in ("action_target_id", "action_text") if k in p},
                **({"check_id": p.get("id", p.get("check_id")),
                    "check_name": p.get("display_name") or p.get("name", "")}
                   if event["type"] == "check.resolved" else {}),
            })
    targets = {}
    for fact in facts:
        key = (fact["source_event_seq"], fact["operation"], fact.get("actor_id"),
               item_identity(fact))
        if fact.get("target_id"):
            targets.setdefault(key, set()).add(fact["target_id"])
    for fact in facts:
        key = (fact["source_event_seq"], fact["operation"], fact.get("actor_id"),
               item_identity(fact))
        known = targets.get(key, set())
        if not fact.get("target_id") and len(known) == 1:
            fact["target_id"] = next(iter(known))
    return list({(f["source_event_seq"], f["operation"], f.get("actor_id"),
                  f.get("target_id"), item_identity(f)): f
                 for f in facts}.values())


def item_identity(fact):
    return tuple(sorted((i.get("instance_id") or i["id"])
                        for i in fact.get("operated_items", [])))


def ordinary_check_receipts(events, *, cycle_id, actor_id, check_id, authority):
    """Bind a local stealth attempt to its own settled ordinary check.

    Route transitions, item effects and other operations still need their own
    receipts. Never infer all requested effects from a successful check.
    """
    if not check_id or authority.get("route") or authority.get("operation_item_ids", {}).get(
        "pass"
    ):
        return []
    fragments = [f for f in authority.get("actual_fragments", [])
                 if f.get("operations") == ["pass"]
                 and re.search(r"潜行|\bsneak\b", f.get("text", ""), re.I)]
    if not fragments:
        return []
    for event in events:
        p = event["payload"]
        if (event["type"] != "check.resolved" or p.get("cycle_id") != cycle_id
                or p.get("id", p.get("check_id")) != check_id
                or p.get("target_member_id") != actor_id
                or p.get("opposed") or p.get("combined")
                or p.get("kind") != "skill"
                or p.get("name", "").lower() not in {"stealth", "潜行"}):
            continue
        passed = (p.get("result") or {}).get("passed")
        if passed is not True and passed is not False:
            return []
        return [{
            "actor_id": actor_id, "target_id": None, "target_name": None,
            "operation": "pass", "status": "success" if passed else "failure",
            "action_text": "".join(f["text"] for f in fragments),
            "effect": "本次潜行检定已通过。" if passed else "本次潜行检定未通过。",
            "operated_items": [], "source_event_seq": event["seq"], "cycle_id": cycle_id,
            "check_id": check_id, "check_name": p.get("display_name") or "潜行",
        }]
    return []


def check_result_error(text, checks, *, reference=None):
    """Check outcome language is separate from the outcome of other operations."""
    for clause in re.split(r"[。；;！？!?\n，,]", text):
        if "检定" not in clause or re.search(r"如果|假如|是否|能否|等待|尚未结算", clause):
            continue
        named = [c for c in checks if any(n and n in clause for n in (
            c.get("display_name"), c.get("name"),
        ))]
        selected = named or [c for c in checks if c.get("id", c.get("check_id")) == reference]
        selected = selected or checks
        outcomes = {(c.get("result") or {}).get("passed") for c in selected}
        negative = re.search(r"失败|未通过|没(?:有)?(?:成功|通过)|未成功|不成功", clause)
        positive = re.search(r"成功|通过", clause) and not negative
        if (outcomes == {True} and negative) or (outcomes == {False} and positive):
            return "contradictory_check_result"
    return None


def quote_request(text):
    return bool(re.search(r"复述|原话|原文|逐字|写着|写了|说过什么|说了什么", text))


def question_operations(text):
    from app.preparation.action_authority import VERBS

    operations = {op for op, pattern in VERBS.items() if re.search(pattern, text, re.I)}
    if "search" in operations:
        # The object being sought is not an operation already performed on it:
        # finding a switch/medical kit must not borrow lighting/treatment receipts.
        return {"search"}
    operations.update(op for op, pattern in EFFECTS.items() if re.search(pattern, text))
    if MEDICAL_TOPIC.search(text) or "治疗" in text:
        operations.update({"first_aid", "medicine"})
    if re.search(r"灯|照明|亮度|开关", text):
        operations.add("light")
    if re.search(r"拿到|取得|取到|到手", text):
        operations.add("take")
    return operations - {"converse"}


def search_question_subject(text):
    """Extract the questioned search operand, independently of modeled entities."""
    from app.preparation.action_authority import VERBS

    if question_operations(text) != {"search"}:
        return ""
    if re.search(r"怎么|如何|经过|哪些|什么|原话|复述|历史|历次|每次", text):
        return ""  # Historical accounts/quotations are not a yes/no operand check.
    subject = re.sub(VERBS["search"], "", text, flags=re.I)
    subject = re.sub(
        r"刚才|之前|先前|这次|现在|目前|真的|已经|到底|究竟|有没有|是否|"
        r"了没有|了吗|了么|了没|没有|成功|结果|什么|一下|那个|这个|我们|你们|"
        r"我|你|还|到|了|吗|么|的|[，,。；;！？?\s]", "", subject,
    )
    return subject if len(subject) >= 2 else ""


def matching_results(facts, text, *, actor_id=None):
    """Match the questioned operand before recency; never borrow a nearby success."""
    selected = list(facts)
    operations = question_operations(text)
    if operations:
        selected = [f for f in selected if f["operation"] in operations]
    if subject := search_question_subject(text):
        selected = [f for f in selected if subject in " ".join(filter(None, [
            f.get("target_name"), f.get("action_text"), f.get("effect"),
            *[n for i in f.get("operated_items", []) for n in i.get("names", [])],
        ]))]
    named = [f for f in selected if any(n and n in text for n in [
        f.get("target_name"), *[n for i in f.get("operated_items", []) for n in i.get("names", [])],
    ])]
    if named:
        selected = named
    elif any(f.get("action_text") for f in selected):
        # Unmodeled requested objects keep their original words. A mistaken
        # entity binding must not make the failed attempt disappear from recall.
        from app.knowledge.text import tokens

        words = set(tokens(text)) - set(tokens("我 刚才 现在 什么 怎么 成功 结果 一下"))
        specific = [f for f in selected if words & set(tokens(f.get("action_text", "")))]
        if specific:
            selected = specific
    elif not operations:
        return []
    if actor_id:
        selected = [f for f in selected if f.get("actor_id") == actor_id]
    # A history request can intentionally ask about an older state. Do not
    # replace it with the current projection or the last receipt.
    ordered = sorted(selected, key=lambda f: f.get("source_event_seq", 0))
    if re.search(r"最初|第一次|起初|开场", text):
        return ordered[:1]
    if re.search(r"历史|先后|经过|每次|历次", text):
        return ordered
    if ordered and not named:
        # Without a named operand, "did it work?" refers to the latest
        # matching attempt, not every earlier device with the same operation.
        latest = ordered[-1]
        ordered = [f for f in ordered if (
            f.get("cycle_id") == latest["cycle_id"] if latest.get("cycle_id")
            else f.get("source_event_seq") == latest.get("source_event_seq"))]
    latest = {}
    for f in ordered:
        latest[(f.get("actor_id"), f.get("target_id"), f["operation"], item_identity(f))] = f
    return list(latest.values())


def describe_result(fact):
    if fact["operation"] == "check":
        if fact.get("effect"):
            return fact["effect"]
        name = fact.get("check_name") or "本次"
        return name + "检定" + {
            "success": "已通过。", "failure": "未通过。",
        }.get(fact["status"], "尚未确认结果。")
    labels = {"first_aid": "急救", "medicine": "治疗", "take": "取物", "light": "照明操作",
              "throw": "投掷", "observe": "检查", "search": "搜索", "move": "移动",
              "give": "交接", "open": "打开", "close": "关闭", "use": "使用", "pass": "通行"}
    outcomes = {"success": "成功了", "failure": "没有成功", "not_executed": "没有执行成功",
                "technical_failure": "还没有得到结果", "attempted": "尚未确认结果"}
    target = fact.get("target_name") or ""
    items = [next(iter(i.get("names", [])), "") for i in fact.get("operated_items", [])]
    if any(items):
        target = "、".join(filter(None, items))
    prefix = f"对{target}的" if target else "这次"
    if not target and fact.get("action_text"):
        prefix = "刚才“" + fact["action_text"].strip("，,。；; ") + "”的"
    answer = (
        prefix
        + labels.get(fact["operation"], "操作")
        + outcomes.get(fact["status"], "尚无结果")
        + "。"
    )
    if fact["status"] in {"success", "failure"} and fact.get("effect"):
        answer += fact["effect"]
    if fact["status"] == "not_executed" and fact["operation"] == "light":
        answer += "目前还不能确认能控制这处照明，也没有因此改变灯光。可以先查看实际的开关或光源。"
    return answer


def answer_result_error(text, question, facts):
    """Validate the answer against the questioned receipts, not other successes."""
    selected = matching_results(facts, question)
    if quote_request(question):
        return None
    if subject := search_question_subject(question):
        relevant = [c for c in re.split(r"[。；;！？!?]", text) if subject in c]
        if not relevant:
            return "unrelated_result_target"
        if not selected and not any(
            re.search(r"没|未|尚|不确定|不能确认|不清楚|不知道", c)
            and re.search(r"找到|找着|搜到|查到|确定|确认|位置|在哪|看见|见到|发现|结果", c)
            for c in relevant
        ):
            return "unconfirmed_result:search"
    if not selected:
        return None
    error = result_error(text, selected)
    if error:
        return error
    if all(f["status"] == "not_executed" for f in selected) and not re.search(
        r"没有(?:成功|执行|做成|操作|变化|改变|因此|拿到|取到|取得)|没能|还没|尚未|"
        r"未(?:能|执行|成功|改变)|不能确认|没有变|没起作用|没生效|"
        r"(?:和|跟).{0,8}(?:之前|刚才).{0,4}一样", text,
    ):
        return "missing_execution_status"
    from app.knowledge.text import tokens

    stop = set(tokens("我 你 我们 刚才 之前 现在 这次 已经 真正 到底 成功 结果 "
                      "没有 是否 什么 怎么 一下 试着 拿到 取出 取物 操作"))
    subjects = set(tokens(question)) & set(tokens(" ".join(
        (f.get("target_name") or "") + " " + f.get("action_text", "")
        + " " + " ".join(n for i in f.get("operated_items", []) for n in i.get("names", []))
        for f in selected))) - stop
    if subjects and not subjects & set(tokens(text)):
        return "unrelated_result_target"
    return None


# Shared effect vocabulary, independent of speaker, voice or response mode.
EFFECTS = {
    "first_aid": (r"止(?:住)?血|血止住|包扎(?:好|完)|伤口.{0,6}(?:处理好|稳定|不再流血)"
                  r"|出血.{0,6}(?:控制|停止|减缓|减轻|减少|缓解)"),
    "take": r"拿到|取到|收好|取出|拿出|捡起|拾起|到手|握在.{0,6}手|放进.{0,6}口袋",
    "light": r"灯光.{0,6}(?:变暗|熄灭)|灯.{0,4}(?:关掉|关上|关闭|灭了)|关(?:掉|上|闭)了?.{0,6}灯",
    "throw": r"扔出|抛出|投出|引开|引走|撞击声",
    "observe": r"畅通|没有(?:明显的?)?(?:异常|障碍|危险)|没有被.{0,8}挡住|通道.{0,5}没问题",
    "move": r"(?!)",  # Reuse the movement grammar below, not tense-specific prose patterns.
}
NON_ASSERTION = re.compile(
    r"没有|还没|并没|未|没能|不能|无法|没法|不曾|不代表|不等于|不算|不再|失败|"
    r"是否|有没有|[？?]|如果|假如|要是|希望|建议|打算|准备|试着|尝试|"
    r"(?:我|我们)(?:先|来|会|要|想)|需要|得先|等.+再|尚需|仍需|可以|不妨"
)
MEDICAL_TOPIC = re.compile(r"血|伤口|伤势|伤者|伤员|患者|病人|急救|包扎")


def result_error(text, facts, *, actor_id=None, names=None, target_id=None, narration=False,
                 items=()):
    """Reject effect assertions contradicted by, or missing, the same operation.

    Ordinary feelings, plans and gestures are outside this check. Attempted
    operation names never prove success; unrelated successes cannot backfill it.
    """
    lights = sorted((f for f in facts if f["operation"] == "light" and f["status"] == "success"),
                    key=lambda f: f.get("source_event_seq", 0))
    if lights and re.search(r"关|熄灭|变暗", lights[-1].get("effect", "")) and re.search(
        r"(?:更|更容易|更加|能更).{0,8}(?:看清|清楚|观察)|视野.{0,6}(?:改善|清晰)", text,
    ) and not re.search(r"适应|等|如果|未|没|不能|难以", text):
        return "unconfirmed_result:visibility"
    medical_names = {f.get("target_name") for f in facts
                     if f.get("operation") in {"first_aid", "medicine"} and f.get("target_name")}
    medical_context = False
    previous_clause, previous_operations = "", set()
    for clause in re.split(r"([。；！;!\n])|[，,]", text or ""):
        if not clause:
            continue
        if re.fullmatch(r"[。；！;!\n]", clause):
            medical_context = False
            previous_clause, previous_operations = "", set()
            continue
        clause = re.sub(r"没(?!有|法|能|关系|问题)", "没有", clause)
        from app.preparation.action_authority import VERBS, action_kinds

        if re.search(r"失败|没有(?:执行)?成功|未成功|没能完成|未能完成", clause) and not re.search(
            r"如果|假如|是否|[？?]|不代表|不等于", clause,
        ):
            # A settled success cannot be negated merely because prose generation
            # failed. Match the actual operation/operand, not another check's
            # success or another object's receipt in the same turn.
            for operation, pattern in VERBS.items():
                if not re.search(pattern, clause, re.I):
                    continue
                candidates = [f for f in facts if f["operation"] == operation]
                named = [f for f in candidates if f.get("target_name")
                         and f["target_name"] in clause]
                if named:
                    candidates = named
                if actor_id:
                    candidates = [f for f in candidates if f.get("actor_id") == actor_id]
                if target_id:
                    candidates = [f for f in candidates if f.get("target_id") in {None, target_id}]
                latest = {}
                for fact in sorted(candidates, key=lambda f: f.get("source_event_seq", 0)):
                    key = (fact.get("actor_id"), fact.get("target_id"), item_identity(fact))
                    latest[key] = fact
                if latest and all(f["status"] == "success" for f in latest.values()):
                    return "contradictory_result:" + operation

        # An attribution can omit its object: "I didn't close the light; X did."
        # Carry only this sentence's operation, then validate the named actor
        # against the same facts as a full assertion, for every operation.
        attribution = next((m for name in (names or {}).values() if name and (m := re.fullmatch(
            r"\s*(?:是|由)" + re.escape(name) + r"(.{1,6})的\s*", clause,
        ))), None)
        inherited = (previous_operations if attribution and attribution[1] in previous_clause
                     else set())
        described = set(action_kinds(clause)) | inherited
        if not described:
            described = {op for op, pattern in EFFECTS.items() if re.search(pattern, clause)}
        previous_clause = clause
        previous_operations = described | {
            op for op, pattern in VERBS.items() if re.search(pattern, clause, re.I)
        }
        medical_context = medical_context or bool(MEDICAL_TOPIC.search(clause)) \
            or any(name in clause for name in medical_names)
        # Absence of an obstacle is an affirmative observation result, whereas
        # failure to observe one is uncertainty. They must not share a negation gate.
        absence = re.search(
            r"(?<!有)没有(?:明显的?)?(?:异常|障碍|危险)|(?<!有)没有被.{0,8}挡住", clause,
        )
        qualification = clause[:absence.start()] + clause[absence.end():] if absence else clause
        if NON_ASSERTION.search(qualification):
            continue
        # Aspect markers do not change the claimed effect. Normalize them once
        # for every operation instead of maintaining voice/tense-specific rules.
        effect_text = re.sub(r"已经|已然|[了着过]", "", clause)
        operations = set(EFFECTS) | (described & {f["operation"] for f in facts})
        for operation in sorted(operations):
            pattern = EFFECTS.get(operation, r"(?!)")
            from app.agents.action_policy import explicit_movement

            scene_move = operation == "move" and explicit_movement(clause) and (
                any(f.get("target_name") and f["target_name"] in clause
                    for f in facts if f["operation"] == "move")
                or re.search(r"车厢|房间|大厅|走廊|驾驶室|仓库|营地|楼层", clause)
            )
            # Small local gestures are free. A claimed scene change needs its
            # corresponding result; historical movement cannot license this turn.
            scene_move = scene_move and any(f["operation"] == "move" for f in facts)
            health_change = operation == "first_aid" and medical_context and re.search(
                r"好转|改善|稳定|减缓|减轻|减少|缓解|(?:比|变|更).{0,6}(?:慢|轻|少|好)", clause,
            )
            if not scene_move and not health_change and not re.search(pattern, effect_text) and (
                operation not in described or operation == "observe"
            ):
                continue
            completed = bool(inherited) or re.search(
                r"已经|已|成功|好了|完了|了(?!解|然)|到手|止住|变暗|熄灭|引开|引走|"
                r"(?:看到|看见).{0,12}(?:捡起|拾起|拿到)", clause,
            )
            if not completed and not health_change and not scene_move and not (
                operation == "observe" and (absence or re.search(pattern, clause))
            ) and not (
                narration and operation in described
                and operation not in {"observe", "search", "converse", "rest"}
            ):
                continue
            candidates = [f for f in facts if f["operation"] == operation]
            if operation in {"take", "throw", "give", "place", "consume", "light", "use"}:
                known_items = [*items, *(i for f in candidates
                                        for i in f.get("operated_items", []))]
                specified = {i["id"] for i in known_items
                             if any(name and name in clause for name in i.get("names", []))}
                if specified:
                    candidates = [f for f in candidates if specified <= {
                        f.get("target_id"), *(i["id"] for i in f.get("operated_items", [])),
                    }]
            named = [mid for mid, name in (names or {}).items() if name and name in clause]
            owner = (actor_id if re.search(r"我(?!们)", clause)
                     else named[0] if len(named) == 1 else None)
            if owner:
                candidates = [f for f in candidates if f.get("actor_id") == owner]
            if target_id:
                candidates = [f for f in candidates if f.get("target_id") in {None, target_id}]
            subjects = [f for f in candidates if f.get("target_name")
                        and f["target_name"] in clause]
            if subjects:
                candidates = subjects
            latest = {}
            for fact in sorted(candidates, key=lambda f: f.get("source_event_seq", 0)):
                latest[(fact.get("actor_id"), fact.get("target_id"))] = fact
            if not latest or any(f["status"] != "success" for f in latest.values()):
                return "unconfirmed_result:" + operation
    return None


def result_reply(facts, text, actor_id):
    """A failed conversational generation may still report its confirmed result.

    This does not invent a strategy, attempt or outcome, and does not answer an
    unrelated question just because some previous operation has a receipt.
    """
    from app.memory.facts import readonly_recall

    if not readonly_recall(text) and not (
        question_operations(text) and re.search(r"吗|么|[?？]|怎样|怎么样|如何|是否|有没有", text)
    ):
        return "", []
    selected = matching_results(
        facts, text, actor_id=actor_id if re.search(r"你(?:刚才|之前|有没有)", text) else None
    )
    if not selected:
        return "", []
    selected = selected[-2:]
    return "".join(describe_result(f) for f in selected), [f["source_event_seq"] for f in selected]


def relevant_results(facts, *, actor_id=None, target_id=None, operations=(), text="", limit=8):
    latest = {}
    for fact in facts:
        latest[
            (fact.get("actor_id"), fact.get("target_id"), fact["operation"], item_identity(fact))
        ] = fact
    return sorted(latest.values(), key=lambda f: (
        bool(text and f.get("target_name") and f["target_name"] in text)
        or bool(text and re.search(EFFECTS.get(f["operation"], r"(?!)"), text)),
        bool(target_id and f.get("target_id") == target_id)
        or bool(operations and f["operation"] in operations)
        or bool(actor_id and f.get("actor_id") == actor_id),
        f.get("source_event_seq", 0),
    ))[-limit:]
