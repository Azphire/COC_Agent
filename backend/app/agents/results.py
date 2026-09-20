"""Compact public result facts shared by narration, speech and task memory."""

import re


def result_facts(events):
    facts, actors = [], {}
    for event in events:
        p = event["payload"]
        cycle = p.get("cycle_id")
        if event["type"] in {"action.submitted", "agent.action_proposed"}:
            actors[cycle] = event.get("actor_member_id")
        if event["type"] == "action.result":
            facts.extend({**f, "source_action_seq": f.get("source_event_seq"),
                          "source_event_seq": event["seq"]}
                         if f.get("status") in {"not_executed", "technical_failure"} else f
                         for f in p.get("facts", []))
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
            })
    return list({(f["source_event_seq"], f["operation"], f.get("target_id")): f
                 for f in facts}.values())


# Shared effect vocabulary, independent of speaker, voice or response mode.
EFFECTS = {
    "first_aid": (r"止(?:住)?血|血止住|包扎(?:好|完)|伤口.{0,6}(?:处理好|稳定|不再流血)"
                  r"|出血.{0,6}(?:控制|停止|减缓|减轻|减少|缓解)"),
    "take": r"拿到|取到|收好|取出|拿出|到手|握在.{0,6}手|放进.{0,6}口袋",
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
                r"已经|已|成功|好了|完了|了(?!解|然)|到手|止住|变暗|熄灭|引开|引走", clause,
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
    operations = {op for op, pattern in EFFECTS.items() if re.search(pattern, text)}
    if MEDICAL_TOPIC.search(text):
        operations.update({"first_aid", "medicine"})
    selected = [f for f in relevant_results(facts, actor_id=actor_id, text=text)
                if f["operation"] in operations and f.get("effect")
                and f.get("actor_id") == actor_id]
    if not selected:
        return "", []
    labels = {"first_aid": "急救", "medicine": "治疗", "take": "取物", "light": "照明操作",
              "throw": "投掷", "observe": "检查"}
    outcomes = {"success": "成功了", "failure": "没有成功", "not_executed": "没有执行",
                "technical_failure": "没能得到结果"}
    selected = [f for f in selected if f["status"] in outcomes][-2:]
    return "".join(f"刚才的{labels[f['operation']]}{outcomes[f['status']]}。"
                   for f in selected), [f["source_event_seq"] for f in selected]


def relevant_results(facts, *, actor_id=None, target_id=None, operations=(), text="", limit=8):
    latest = {}
    for fact in facts:
        latest[(fact.get("actor_id"), fact.get("target_id"), fact["operation"])] = fact
    return sorted(latest.values(), key=lambda f: (
        bool(text and f.get("target_name") and f["target_name"] in text)
        or bool(text and re.search(EFFECTS.get(f["operation"], r"(?!)"), text)),
        bool(target_id and f.get("target_id") == target_id)
        or bool(operations and f["operation"] in operations)
        or bool(actor_id and f.get("actor_id") == actor_id),
        f.get("source_event_seq", 0),
    ))[-limit:]
