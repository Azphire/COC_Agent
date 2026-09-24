"""Bounded KP answer coverage against this turn's selected public evidence.

This is a conservative publication contract, not another model or a general
semantic judge. Exact body/source spans, question subjects, quantities and
epistemic qualifiers are checked independently of the model's claimed IDs.
"""

import re
from hashlib import sha256

from app.memory.recall import terms

UNKNOWN = (
    r"不知|不清|不明|不记|记不|说不|不(?:能|敢)?(?:确定|确认|肯定)|无法|尚未|未能|未确认|"
    r"没有.{0,6}(?:记录|依据|提及)|没(?:有)?(?:说|提|记|列|确认)|未知|待核"
)
ESTIMATE = (
    r"估计|估算|大约|大概|约莫|约(?:有|为|是)?(?=[零一二两三四五六七八九十\d])|"
    r"左右|上下|至少|至多|可能|似乎|印象|粗略"
)
HISTORICAL = r"当时|此前|先前|早先|之前|以前|过去|曾|原话|旧|记录|说过|提过"
TESTIMONY = r"说|称|表示|提到|记得|回忆|估计|估算|证词|据|认为|印象|交代|所述"
ADVICE = r"建议|下一步|还需|有待|不妨|可以(?:先|再)?(?:核对|检查|查看|留意)|是否|能否"
CONDITION = r"(?:只有|如果|假如|若)([^，。；;！？!?\n]{2,40})"
FORMAT = (
    r"(?:请|要求|回答|答复).*(?:分句|完整句|句子|格式|逐项|分别|不要合成|简短|分点)|"
    r"(?:分句|逐项|分别).*(?:回答|说明)"
)
QUANTITY = re.compile(
    r"(\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+)\s*(美元|英镑|小时|分钟|年|月|日|本|件|个|把|张|枚|份|支|瓶|扇|元)"
)


def _stable_id(prefix, *parts):
    return prefix + sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:10]


def _compact(text):
    return re.sub(r"[\W_]", "", text).lower()


def _topic_terms(text):
    # Strip grammatical scaffolding, not object names or source-specific values.
    text = re.sub(
        r"有没有|有无|会不会|能否|什么|多少|是否|知道|具体|早先|估计|请|回答|观察|查看|检查|环顾|"
        r"我(?:们)?|你(?:们)?|他(?:们)?|她(?:们)?|这次|本轮|现在",
        " ",
        text,
    )
    return terms(text)


def _quantities(text):
    from app.agents.task_receipts import quantity_value

    return {
        (float(n) if re.fullmatch(r"\d+(?:\.\d+)?", n) else quantity_value(n), unit)
        for n, unit in QUANTITY.findall(text)
    }


def _answer_quantities(text, requirement, source):
    """An indefinite article for the quoted document is not a total count.

    This is confined to a verbatim source-text answer. Exact totals, other
    objects, plural quantities and numeric questions retain their usual guard.
    """
    if (not requirement.get("verbatim") or source.get("kind") != "source_text"
            or re.search(r"多少|几[张份本]|数量|数目|总数", requirement.get("text", ""))):
        return _quantities(text)

    def article(match):
        prefix, noun = match["prefix"], match["noun"]
        if (noun in source["text"] and not re.search(
                r"只有|仅|只|总共|一共|共计|恰好|正好|准确|数量|数目", prefix)):
            return prefix + noun
        return match[0]

    prose = re.sub(
        r"(?P<prefix>[^，,。；;！？!?\n]{0,20}?(?:贴着|贴有|有))一[张份本]"
        r"(?P<noun>[\u4e00-\u9fff]{2,8})(?=[，,。；;！？!?：:\n]|$)", article, text,
    )
    return _quantities(prose)


def _requested_quantities(text, question):
    values = _quantities(text)
    units = {unit for _, unit in values if unit in question}
    if re.search(r"多少钱|金额|费用|价格|报酬", question):
        units |= {"美元", "英镑", "元"}
    if re.search(r"何时|几时|日期|时间|多久", question):
        units |= {"年", "月", "日", "小时", "分钟"}
    return {(n, unit) for n, unit in values if not units or unit in units}


def _observation_subjects(attempt):
    pieces = re.split(r"[，,。；;！？!?\n]", attempt)
    observations = []
    for piece in pieces:
        match = re.search(r"(?:查看|观察|检查|看看|环顾|端详|打量|检视|看一眼)(?:一下)?(.+)", piece)
        if match:
            observations.append(("环顾" in piece, match[1]))
    # A room-wide lead-in is scope when a later clause names actual objects.
    if any(not broad for broad, _ in observations):
        observations = [(b, s) for b, s in observations if not b]
    return list(
        dict.fromkeys(
            s.strip()
            for _, text in observations
            for s in re.split(r"和|以及|及|与|、", text)
            if s.strip()
        )
    )


def _format_only_question(question):
    """Exclude a pure presentation request, retaining any factual remainder."""
    if not re.search(r"完整句|句子|分句|逐项|分别|分点|分段|一句话|表格|格式|简短", question):
        return False
    vocabulary = (
        "可不可以|能不能|好不好|完整句子|完整句|一句话|不要合成|不要合并|"
        "请你们|请你|麻烦你|能否|可否|可以|回答|答复|说明|表达|使用|按照|"
        "句子|分句|逐项|分别|分点|分段|表格|格式|简短|形式|方式|一下|好吗|"
        "能|请|用|以|按|的|吗|呢"
    )
    return not re.sub(r"[\W_]", "", re.sub(vocabulary, "", question))


def _selected_result_sources(context):
    brief = context.get("response_brief", {})
    if (brief.get("responder", {}).get("kind") != "keeper"
            or not brief.get("current_action_results") or not brief.get("answer_requirements")):
        return None
    return {source["text"] for source in brief.get("answer_sources", [])}


def narration_claim_options(context):
    """Generate claims from the same final sources as the result answer."""
    selected = _selected_result_sources(context)
    claims = context.get("PUBLIC_CLAIM_OPTIONS", [])
    return claims if selected is None else [c for c in claims if c["statement"] in selected]


def project_result_answer_sources(prompt):
    """Do not reintroduce omitted opening/history through alternate projections.

    The caller already copied the prompt; the complete run context remains the
    authority for validation, permissions, anti-repeat and the result ledger.
    Requested historical/mixed evidence remains in canonical answer_sources.
    """
    selected = _selected_result_sources(prompt)
    if selected is None:
        return
    brief = prompt["response_brief"]
    # Actual receipts supersede a planner's pre-execution 'unrecorded' guess.
    brief["answer_basis"] = "facts"
    brief["allowed_facts"] = [f for f in brief.get("allowed_facts", [])
                              if f["text"] in selected]
    for key in ("historical_memory", "incidental_memories", "recent_dialogue"):
        brief[key] = [row for row in brief.get(key, []) if row.get("text") in selected]
    brief["source_quotes"] = [text for text in brief.get("source_quotes", []) if text in selected]
    for key, field in (("current_scene", "public_description"),
                       ("observation_subject", "public_summary")):
        if brief.get(key) and brief[key].get(field) not in selected:
            brief[key].pop(field, None)
    task = prompt.get("current_task", {})
    for key in ("observation_subject", "source_quotes"):
        if key in task:
            task[key] = brief.get(key)
    if "fact_evidence" in prompt:
        prompt["fact_evidence"] = [f for f in prompt["fact_evidence"] if f.get("text") in selected]
    if "PUBLIC_CLAIM_OPTIONS" in prompt:
        prompt["PUBLIC_CLAIM_OPTIONS"] = narration_claim_options(prompt)


def prepare_response_contract(context):
    """Build requirements after routing and final public memory selection.

    Returns a new brief. Sources are projections of the selected visible rows;
    omitted memory, NPC profiles and summaries never authorize testimony.
    """
    brief = dict(context.get("response_brief", {}))
    if context.get("readonly_recall") or brief.get("responder", {}).get("kind") != "keeper":
        return brief
    sources = []

    def add(key, text, **metadata):
        if text and not any(
            s["text"] == text
            and s.get("historical") == metadata.get("historical")
            and s.get("kind") == metadata.get("kind")
            and s.get("speaker") == metadata.get("speaker")
            for s in sources
        ):
            if any(s["id"] == key for s in sources):
                key = _stable_id(key + ":", text)
            sources.append({"id": key, "text": text, **metadata})

    scene = brief.get("current_scene", {})
    add(
        "scene:" + str(scene.get("id", "current")),
        scene.get("public_description", ""),
        kind="observation",
    )
    for fact in brief.get("allowed_facts", []):
        if fact.get("source_kind") != "rule":
            add(fact["id"], fact["text"], kind="public_fact")
    for entity in context.get("public_entities", []):
        if (
            entity.get("type") != "npc"
            and entity.get("fact_scope", "current_scene") == "current_scene"
        ):
            add("entity:" + entity["id"], entity.get("public_summary", ""), kind="observation")
    for entry in brief.get("historical_memory", []):
        if entry.get("source", {}).get("visibility") == "public" and entry.get("kind") in {
            "source_text",
            "npc_statement",
        }:
            add(
                entry["source"]["ref"],
                entry.get("text", ""),
                kind=entry["kind"],
                historical=True,
                speaker=entry.get("speaker", ""),
                epistemic=entry.get("epistemic", ""),
                title=entry.get("title", ""),
            )
    from app.memory.facts import selected_answer_facts

    for entry in selected_answer_facts(context):
        if entry.get("kind") in {"source_text", "npc_statement"} and isinstance(
            entry.get("source_event_seq"), int
        ):
            add(
                "e" + str(entry["source_event_seq"]), entry["text"],
                kind=entry["kind"], historical=True,
                speaker=entry.get("speaker", ""), title=entry.get("title", ""),
            )
    # Bound current receipts take priority when duplicate text also appears in
    # a different event. Retain the current action's actual source reference.
    current_results = brief.get("current_action_results", [])
    for result in current_results:
        add("e" + str(result["source_event_seq"]), result["effect"], kind="receipt")
    for event in context.get("public_tool_results", {}).get("events", []):
        if event["type"] in {"clue.revealed", "entity.revealed", "module.interaction"}:
            payload = event["payload"]
            add(
                "e" + str(event["seq"]),
                payload.get("public_summary") or payload.get("content") or payload.get("text", ""),
                kind="receipt",
            )
    requirements, formats = [], []
    raw = brief.get("player_statement", "")
    for clause in re.split(r"[。；;！？!?\n]", raw):
        if re.search(FORMAT, clause):
            formats.append(clause.strip())
    seq = brief.get("trigger_seq", context.get("triggering_action", {}).get("seq", 0))
    demands = [("observation", s) for s in _observation_subjects(brief.get("attempt", ""))]
    delegated = brief.get("delegated_requests", [])
    # Only requests already bound to this child/actor/target arrive here. An
    # abbreviated execution proposal must not discard the original inspection
    # question. The probe expresses the purpose of inspecting that same object;
    # it replaces a generic restatement such as 'inspect the note's edge'.
    probes = list(dict.fromkeys(
        subject for request in delegated
        for subject in _observation_subjects(request["text"])
        if re.search(r"有没有|有无|是否", subject)
    ))
    if probes:
        demands = [("observation", subject) for subject in probes]
    if not demands and brief.get("ordinary_observation") and brief.get("attempt"):
        subject = brief.get("observation_subject") or {}
        demands = [("observation", subject.get("title") or brief["attempt"])]
    # Formatting attached to a factual question never deletes that question.
    from app.agents.narration import question_parts

    questions = list(dict.fromkeys([
        *brief.get("questions", []),
        *(question for request in delegated for question in question_parts(request["text"])),
    ]))
    for question in questions:
        if _format_only_question(question):
            if question not in formats:
                formats.append(question)
        else:
            demands.append(("question", question))
    last_speaker = ""
    people = [
        (e["title"], [e["title"], *e.get("aliases", []), *re.split(r"[·・\s]", e["title"])])
        for e in context.get("public_entities", [])
        if e.get("type") == "npc"
    ]
    for entry in sources:
        if entry.get("kind") == "npc_statement" and entry.get("speaker"):
            name = entry["speaker"]
            people.append((name, [name, *re.split(r"[·・\s]", name)]))
    for kind, demand in demands:
        from app.agents.results import quote_request

        verbatim = kind == "question" and quote_request(demand)
        topic = _topic_terms(demand)
        generic = kind == "observation" and bool(
            re.fullmatch(
                r"(?:这|那|整个)?(?:四周|周围|周边|现场|环境|这里|附近|房间|屋内|室内)",
                demand,
            )
        )
        speaker = next(
            (
                name
                for name, aliases in people
                if any(len(alias) >= 2 and alias in demand for alias in aliases)
            ),
            "",
        )
        if not speaker and re.match(r"[他她]", demand):
            speaker = last_speaker
        historical = kind == "question" and bool(speaker or re.search(HISTORICAL, demand))
        ranked = [
            (len(topic & terms(s["text"] + " " + s.get("speaker", ""))), s)
            for s in sources
            if kind != "observation" or not s.get("historical")
        ]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected = [s["id"] for score, s in ranked if score and score >= max(1, ranked[0][0] // 2)][
            :4
        ]
        if verbatim:
            originals = selected_answer_facts(context, demand)
            original_ids = {"e" + str(r["source_event_seq"]) for r in originals
                            if r.get("kind") in {"source_text", "npc_statement"}}
            selected = [s["id"] for s in sources if s["id"] in original_ids]
        if generic:
            selected = [
                s["id"] for s in sources if s["id"] == "scene:" + str(scene.get("id", "current"))
            ]
        requirements.append(
            {
                "id": _stable_id("r", seq, kind, demand),
                "kind": kind,
                "text": demand,
                "source_ids": selected,
                **({"answer_instruction": "这项尚未确认，须在正文说明未知；"
                   "其他需求的来源不能证明它，不可即兴补成肯定或否定。"}
                   if not selected else {}),
                **({"generic_scope": True} if generic else {}),
                **({"speaker": speaker} if speaker else {}),
                **({"historical": True} if historical else {}),
                **({"verbatim": True} if verbatim else {}),
            }
        )
        if speaker:
            last_speaker = speaker
    for result in current_results:
        source_id = "e" + str(result["source_event_seq"])
        # The same effect may have both a reveal and an interaction receipt.
        source = next((s for s in sources if s["id"] == source_id), None)
        if source is None:
            source = next((s for s in sources if s["kind"] == "receipt"
                           and s["text"] == result["effect"]), None)
        if source is None or any(r.get("result_effect") == result["effect"]
                                 for r in requirements):
            continue
        requirements.append({
            "id": _stable_id("r", seq, "result", source["id"]),
            "kind": "result",
            "text": "本次实际结果：" + (result.get("target_name") or result["effect"]),
            "source_ids": [source["id"]],
            "result_effect": result["effect"],
            **({"verbatim": True} if re.search(r'“[^”]+”|「[^」]+」|\"[^\"]+\"',
                                                result["effect"]) else {}),
        })
    selected_ids = {key for r in requirements for key in r["source_ids"]}
    brief["answer_requirements"] = requirements[:12]
    brief["answer_sources"] = [s for s in sources if s["id"] in selected_ids]
    brief["answer_format_requests"] = formats
    return brief


def _content_topics(requirement):
    topic = requirement["text"]
    for name in [
        requirement.get("speaker", ""),
        *re.split(r"[·・\s]", requirement.get("speaker", "")),
    ]:
        if name:
            topic = topic.replace(name, "")
    return _topic_terms(topic)


def _unknown_for_requirement(text, requirement):
    topics = _content_topics(requirement)
    for sentence in re.split(r"[。；;！？!?\n]", text):
        clauses = [s.strip() for s in re.split(r"[，,]|但是|但|不过|然而", sentence) if s.strip()]
        for index, clause in enumerate(clauses):
            if not re.search(UNKNOWN, clause):
                continue
            if topics & terms(clause):
                return True
            # Bind the epistemic predicate across one comma in this sentence,
            # without accepting an unrelated sentence about another subject.
            if index and topics & terms(clauses[index - 1]) and re.match(
                r"^(?:但|而|[他她我]|仍|还|尚|目前|暂时|现在|眼下|并)*" + "(?:" + UNKNOWN + ")",
                clause,
            ):
                return True
    return False


def unknown_assertion_errors(body, requirement):
    """Unknown prose cannot hide a positive/negative probe claim in any part."""
    if requirement.get("source_ids"):
        return []
    predicate = re.split(r"有没有|有无|是否", requirement["text"], maxsplit=1)[-1]
    topic = _topic_terms(predicate)
    errors = []
    assertions = re.sub(r"但是|然而|不过|并且|而且|同时|以及|但|而|却|且|并", "，", body)
    for sentence in re.split(r"[。；;！？!?\n]", assertions):
        for clause in _factual_clauses(sentence):
            if not topic & terms(clause):
                continue
            remainder = re.sub(r"有没有|有无|是否", "", clause)
            if re.match(
                r"\s*(?:你|我)(?:们)?(?:正在|正|试图|尝试|仔细|开始|先|继续|用手)?"
                r"(?:检查|查看|搜索|寻找|观察)", clause,
            ) and not re.search(
                r"发现|看到|看见|确认|确定|表明|没有|并无|不存在|未见|存在|是", remainder,
            ):
                continue
            # A scoped question/topic followed by its epistemic predicate is
            # natural unknown prose. It is not a negative assertion containing
            # the substring '没有' in '有没有'. Explicit claims remain checked.
            stripped = re.sub(UNKNOWN, "", remainder)
            explicit = re.search(
                r"没有|并无|不存在|未见|没见|存在|确实|有|是|为|留着|可见", stripped,
            )
            if re.search(r"有没有|有无|是否", clause) and _unknown_for_requirement(
                sentence, requirement,
            ) and not explicit:
                continue
            if not explicit and _unknown_for_requirement(sentence, requirement):
                continue
            if not re.search(UNKNOWN, clause) or explicit:
                errors.append("正文给出了本轮来源未确认的检查结论：" + clause)
    return errors


def _polarity_errors(answer, quote):
    def negative(clause):
        # Unknown epistemic state is separate from denying an observable fact.
        clause = re.sub(UNKNOWN, "", clause)
        return bool(re.search(
            r"没有|并无|不存在|未见|没见|不曾|从未|不是|并非|不在|并不|"
            r"(?:不|没|未)(?=显眼|明显|留|开|关|少|多|丢|失|看|见)", clause,
        ))

    source_clauses = [c for c in re.split(r"[，,。；;！？!?\n]", quote) if c]
    errors = []
    for clause in re.split(r"[，,。；;！？!?\n]", answer):
        matching = sorted(
            source_clauses, key=lambda c: len(_topic_terms(c) & terms(clause)), reverse=True
        )
        if not matching or len(_topic_terms(matching[0]) & terms(clause)) < 2:
            continue
        if negative(matching[0]) != negative(clause):
            errors.append("正文改变了来源断言的肯定/否定：" + matching[0])
    return errors


def _factual_clauses(text):
    """Separate proposals/questions from assertions, including mixed sentences."""
    advice = False
    for match in re.finditer(r"[^，,。；;！？!?\n]+[，,。；;！？!?\n]*", text):
        clause = match[0].strip()
        suggested = bool(re.search(ADVICE, clause) or re.search(r"[？?]", clause))
        inherited = advice and bool(re.match(r"(?:同时|并且|以及|并|再|还要)", clause))
        embedded_fact = bool(
            re.search(
                r"并不存在|并无|没有|已证实|已经证实|确定|确实|准确|正好|书名是|名称是", clause,
            )
            or _quantities(clause)
        ) and not re.search(r"是否|能否|会不会|有没有", clause)
        if not suggested and not inherited or embedded_fact:
            yield clause
        advice = (suggested or inherited) and not bool(re.search(r"[。；;！？!?\n]$", clause))


def _condition_errors(answer, support):
    errors = []
    actual_conditions = re.findall(CONDITION, answer)
    for condition in re.findall(CONDITION, support):
        # Conditions are small factual operands, not a generated template.
        # Unknown equivalence stays conservative rather than dropping a premise.
        if not any(_compact(condition) in _compact(actual) for actual in actual_conditions):
            errors.append("正文未保留来源条件：" + condition)
    return errors


def _requirement_fact_text(answer, requirement):
    """Scope retained predicates to this requirement, not its entire source."""
    clauses = list(_factual_clauses(answer))
    if re.search(r"多少|数量|数目|几(?:本|件|个|把|张|枚|份)", requirement["text"]):
        units = set(re.findall(r"本|件|个|把|张|枚|份|支|瓶|扇|元", requirement["text"]))
        matching = [c for c in clauses if any(
            not units or unit in units for _, unit in _quantities(c)
        )]
    elif re.search(r"具体|名称|名字|书名|知道|清楚", requirement["text"]):
        matching = [c for c in clauses if _unknown_for_requirement(c, requirement)]
        if not matching:
            matching = [c for c in clauses if _content_topics(requirement) & terms(c)]
    else:
        topic = _content_topics(requirement)
        matching = [c for c in clauses if topic & terms(c)]
    return "".join(matching) or answer


def _observation_binding_errors(answer, requirement, source_text):
    if requirement["kind"] != "observation" or requirement.get("generic_scope"):
        return []
    topic = _content_topics(requirement)
    clauses = list(_factual_clauses(source_text))
    scores = [len(topic & terms(c)) for c in clauses]
    if not scores or not max(scores):
        return []
    selected = [c for c, score in zip(clauses, scores) if score == max(scores)]
    other = [c for c, score in zip(clauses, scores) if score != max(scores)]
    supported = _fact_atoms("".join(selected))
    foreign = _fact_atoms("".join(other)) - supported
    errors = []
    for clause in _factual_clauses(answer):
        if not topic & terms(clause):
            continue
        # A lead-in can list several visible objects before describing each.
        # It does not assign one object's descriptive predicate to another.
        listing = re.search(r"(?:注意到|看见|看到|观察到|查看|观察).*(?:和|以及|与|、)", clause)
        if listing and not re.search(
            r"有|留|是|为|显眼|明显|不存在|没有|布满|遍布|覆盖|打开|关闭", clause,
        ):
            continue
        borrowed = [atom for atom in foreign if len(atom) >= 2 and atom in clause]
        if borrowed:
            errors.append("正文把同一来源中其他对象的事实移给当前对象：" + clause)
    return errors


def _source_errors(requirement, answer, row, source):
    """Check a selected source against the actual KP span, never auxiliary prose."""
    errors = []
    question, quote = requirement["text"], row.get("source_quote", "")
    source_text = source["text"]
    if not quote.strip() or quote not in source_text:
        return ["source_quote须为本轮所选可见来源中的原样片段"]
    if requirement.get("verbatim"):
        literals = re.findall(r'“([^”]+)”|「([^」]+)」|\"([^\"]+)\"', source_text)
        expected_text = [next(piece for piece in parts if piece) for parts in literals]
        if not expected_text and source.get("kind") == "source_text":
            expected_text = [re.sub(r"^.{1,24}?(?:写着|写了)[：:]", "", source_text)]
        if any(_compact(piece) not in _compact(answer) for piece in expected_text):
            errors.append("正文须实际答出当前问题所问的原文，不能只提到其对象或放入附属字段")
    if not requirement.get("generic_scope") and not (
        _topic_terms(question) & terms(quote + " " + source.get("speaker", ""))
    ):
        errors.append("来源片段未涉及当前需求")
    if requirement.get("speaker") and source.get("kind") == "npc_statement":
        expected_speaker = set(re.split(r"[·・\s]", requirement["speaker"]))
        actual_speaker = set(re.split(r"[·・\s]", source.get("speaker", "")))
        if not {name for name in expected_speaker & actual_speaker if len(name) >= 2}:
            errors.append("所选证词的实际说话人不属于当前问题，不能改署名")
    errors += _polarity_errors(answer, quote)
    errors += _observation_binding_errors(answer, requirement, source_text)
    # A model cannot cite an uninformative slice to discard the value/qualifier.
    source_sentences = re.split(r"[。；;！？!?\n]", source_text)
    relevant = []
    for index, sentence in enumerate(source_sentences):
        if not _topic_terms(question) & terms(sentence):
            continue
        if index and re.search(CONDITION, source_sentences[index - 1]):
            relevant.append(source_sentences[index - 1])
        relevant.append(sentence)
    support = "。".join(relevant) or quote
    errors += _condition_errors(answer, support)
    quantity_question = bool(
        re.search(
            r"多少|几(?:本|件|个|把|张|枚|份|支|瓶|年|月|日)|数量|数目|金额|日期|何时", question
        )
    )
    expected = _requested_quantities(support, question) if quantity_question else set()
    actual = _answer_quantities(answer, requirement, source)
    if (
        expected and re.search(r"少了|缺少|丢失|失窃", support)
        and re.search(r"多了|增加|增多", answer)
    ):
        errors.append("正文改变了来源数量的增减方向")
    if expected and not expected <= actual:
        errors.append("正文未保留来源的具体数值/单位：" + support)
    if actual and not actual <= _quantities(source_text):
        errors.append("正文数值没有该可见来源支持")
    estimate = (
        expected and re.search(ESTIMATE, support + question)
        or requirement["kind"] == "observation" and re.search(ESTIMATE, support)
    )
    if estimate and not re.search(ESTIMATE, answer):
        errors.append("来源只给估计/范围，正文不能改成确定事实")
    if (
        estimate
        and re.search(r"(?:确定|确实|准确|正好|已经证实)(?!未知)", answer)
        and not re.search(
            r"不(?:能|敢)?确定|无法确定|尚未确定|未确定",
            answer,
        )
    ):
        errors.append("正文把估计升级为已经证实")
    unknown_question = bool(re.search(r"(?:知道|确认|清楚|具体|哪些|名字|名称|书名)", question))
    if (
        unknown_question
        and _unknown_for_requirement(support, requirement)
        and not (_unknown_for_requirement(answer, requirement))
    ):
        errors.append("来源未确认该具体内容，正文须说明具体未知项")
    if any(title not in source_text for title in re.findall(r"《[^》]+》", answer)):
        errors.append("正文给出了来源未提供的具体名称")
    if re.search(UNKNOWN, answer) and expected:
        # A known estimate is not erased by saying the exact count is unknown.
        if not expected <= actual:
            errors.append("具体确值虽未确认，仍须保留已知估计")
    if source.get("kind") == "npc_statement" or requirement.get("speaker"):
        speaker = requirement.get("speaker") or source.get("speaker", "")
        # A source's own display name can have an abbreviated public first name.
        aliases = [speaker, *re.split(r"[·・\s]", speaker)] if speaker else []
        if not any(len(name) >= 2 and name in answer for name in aliases):
            errors.append("正文须将证词归属于来源说话人：" + speaker)
        if not re.search(TESTIMONY, answer):
            errors.append("证词须保留转述性质")
        if (source.get("historical") or requirement.get("historical")) and not re.search(
            HISTORICAL, answer
        ):
            errors.append("旧证词不能写成本轮新发现")
        # Naming the expected person elsewhere (e.g. "林先生当时在场")
        # does not make another person's quantitative claim their testimony.
        for clause in _factual_clauses(answer):
            if not (_quantities(clause) or _unknown_for_requirement(clause, requirement)):
                continue
            explicit = re.search(
                r"(?:^|[，,。；;])\s*([\u4e00-\u9fff·]{2,16}?)"
                r"(?:早先|此前|当时|之前|曾经|曾|先前)?(?:估计|估算|表示|说|称|认为)", clause,
            )
            if explicit and not any(
                len(name) >= 2 and name in explicit[1] for name in aliases
            ) and not re.match(r"[他她其]", explicit[1]) and not (
                _topic_terms(explicit[1]) <= _content_topics(requirement)
            ):
                errors.append("正文实际断言的说话人不属于来源：" + explicit[1])
    if not (_topic_terms(quote) & terms(answer)) and not (expected and expected <= actual):
        errors.append("正文片段未表达引用来源的具体内容")
    predicates = _topic_terms(quote) - _topic_terms(question)
    if (
        len(predicates) >= 3
        and not (predicates & terms(answer))
        and not (expected and expected <= actual)
    ):
        errors.append("正文只重复对象，未表达来源支持的具体观察或答复")
    factual = "".join(_factual_clauses(answer))
    content = _topic_terms(factual) - _topic_terms(question)
    if not factual or not content and not re.search(
        r"有|存在|留着|可见|明显|显眼|不知|不清|未知", factual
    ):
        errors.append("正文只有对象、问题或建议，缺少实际回答")
    # Explicit literal values (labels/codes/titles) must survive a paraphrase.
    for label, value in re.findall(r"([^，。；：:\n]{2,16})[：:]([^，。；\n]{1,30})", quote):
        if _topic_terms(label) & _topic_terms(question) and _compact(value) not in _compact(answer):
            errors.append("正文改写了来源的具体内容：" + label + "：" + value)
    return errors


def _prefix_assertion_errors(body, requirements, sources):
    """Check finished factual assertions without demanding future answers/IDs.

    Streaming invokes this on whole sentences. Only a sentence that already
    makes the requested quantitative assertion is checked; an observation-only
    prefix is never rejected for an unanswered later historical question.
    """
    errors = []
    sentences = re.findall(r"[^。！？!?；;\n]+[。！？!?；;\n]", body)
    for index, sentence in enumerate(sentences):
        if not any(_quantities(clause) for clause in _factual_clauses(sentence)):
            continue
        for requirement in requirements:
            if requirement["kind"] != "question" or not re.search(
                r"多少|数量|数目|几(?:本|件|个|把|张|枚|份)", requirement["text"]
            ):
                continue
            candidates = [sources[s] for s in requirement.get("source_ids", []) if s in sources]
            expected = (
                set().union(
                    *(_requested_quantities(s["text"], requirement["text"]) for s in candidates)
                )
                if candidates
                else set()
            )
            units = {unit for _, unit in expected}
            if not units or not any(unit in units for _, unit in _quantities(sentence)):
                continue
            if not (_topic_terms(requirement["text"]) & terms(sentence)):
                continue
            scope = sentence
            if index and requirement.get("speaker"):
                speaker_names = re.split(r"[·・\s]", requirement["speaker"])
                if any(len(name) >= 2 and name in sentences[index - 1] for name in speaker_names):
                    scope = sentences[index - 1] + sentence
            failures = [
                _source_errors(requirement, scope, {"source_quote": s["text"]}, s)
                for s in candidates
            ]
            if failures and all(failures):
                errors.append(
                    {
                        "id": requirement["id"],
                        "requirement": requirement["text"],
                        "reasons": min(failures, key=len),
                    }
                )
    return errors


def _body_assertion_errors(body, requirement, source):
    """Check every relevant factual assertion, independent of mapping copies.

    Repetition and advice do not establish extra answers. A contradictory fact
    anywhere in the body still invalidates a perfectly copied native mapping.
    Context may supply a pronoun's speaker/history, never another value or an
    unknown marker to excuse an incompatible assertion in the current clause.
    """
    topic = _content_topics(requirement)
    support = source["text"]
    expected = _requested_quantities(support, requirement["text"])
    numeric = bool(re.search(r"多少|数量|数目|几(?:本|件|个|把|张|枚|份)", requirement["text"]))
    units = {unit for _, unit in expected} if numeric else set()
    errors = []
    sentences = re.findall(r"[^。；;！？!?\n]+[。；;！？!?\n]*", body)
    for index, sentence in enumerate(sentences):
        for clause in _factual_clauses(sentence):
            actual = _answer_quantities(clause, requirement, source)
            referring = bool(index and re.match(r"\s*(?:这些|这点|上述|以上|这项|它们)", clause))
            inherited = referring and (
                topic & terms(sentences[index - 1])
                or any(unit in units for _, unit in _quantities(sentences[index - 1]))
            )
            related = bool(topic & terms(clause)) or any(unit in units for _, unit in actual)
            related = related or inherited
            if not related:
                continue
            # A topic lead-in ("至于具体书名") is not a positive knowledge claim.
            informative = bool(
                actual or re.search(UNKNOWN, clause) or inherited
                or re.search(
                    r"有|是|为|留着|可见|明显|显眼|打开|关闭|知道|清楚|确认|名称叫|书名叫", clause,
                )
                or (_topic_terms(support) - topic) & terms(clause)
            )
            if not informative:
                continue
            scope = sentence
            speaker = requirement.get("speaker") or source.get("speaker", "")
            aliases = [n for n in re.split(r"[·・\s]", speaker) if len(n) >= 2]
            if index and (
                inherited or re.search(CONDITION, sentences[index - 1])
                or not any(n in scope for n in aliases) and re.match(
                    r"\s*(?:[他她其]|据此|这些|这项|至于|具体|仍|但)", sentence,
                )
            ):
                scope = sentences[index - 1] + sentence
            errors += _polarity_errors(clause, support)
            errors += _observation_binding_errors(clause, requirement, support)
            failures = _source_errors(
                requirement, scope, {"source_quote": support}, source,
            )
            errors += [e for e in failures if e.startswith("正文未保留来源条件")]
            if re.search(r"已证实|确定|确实|准确|正好", clause):
                errors += [e for e in failures if e.startswith((
                    "来源只给估计", "正文把估计升级",
                ))]
            if actual and not actual <= _quantities(support):
                errors.append("正文其他位置的数值没有所选来源支持：" + clause)
            if numeric and any(unit in units for _, unit in actual):
                errors += _source_errors(requirement, scope, {"source_quote": support}, source)
            unknown = _unknown_for_requirement(support, requirement)
            if unknown and re.search(
                r"(?:名称|名字|书名).{0,8}(?:是|为|叫)|(?:知道|清楚|确认).{0,8}(?:名称|名字|书名)|《",
                clause,
            ) and not _unknown_for_requirement(clause, requirement):
                errors.append("正文其他位置把来源未知内容改成已知：" + clause)
            if any(title not in support for title in re.findall(r"《[^》]+》", clause)):
                errors.append("正文其他位置给出了来源未提供的具体名称")
            if requirement.get("historical") or source.get("historical"):
                # Only actual testimony assertions need attribution; a later
                # suggestion about this subject is not another historical fact.
                testimony_fact = bool(actual and numeric or re.search(UNKNOWN, clause))
                if testimony_fact:
                    failures = _source_errors(
                        requirement, scope, {"source_quote": support}, source,
                    )
                    errors += [e for e in failures if e.startswith(
                        ("正文须将证词归属于", "正文实际断言的说话人",
                         "证词须保留转述性质", "旧证词不能")
                    )]
    return list(dict.fromkeys(errors))


def coverage_audit(output, brief, *, prefix=False, partial=False):
    """Accept dict/model output, including raw observed_detail, for call logging."""
    value = output.model_dump() if hasattr(output, "model_dump") else (output or {})
    if not isinstance(value, dict):
        value = {}
    body = value.get("public_narration") or value.get("observed_detail") or ""
    body = body if isinstance(body, str) else ""
    requirements = brief.get("answer_requirements", [])
    audit = {
        "checked": bool(requirements) and not prefix and not partial,
        "complete": None if prefix or partial else not requirements,
        "valid": True,
        "covered": [],
        "missing": [],
        "errors": [],
        "verified": [],
    }
    if not requirements:
        return audit
    sources = {s["id"]: s for s in brief.get("answer_sources", [])}
    assertion_errors = _prefix_assertion_errors(body, requirements, sources)
    if assertion_errors:
        audit["valid"] = False
        audit["errors"].extend(assertion_errors)
    rows = value.get("answer_coverage", []) or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        rows = []
        audit["valid"] = False
        audit["errors"].append({"id": None, "reasons": ["覆盖映射必须是对象列表"]})
    cleaned = []
    for row in rows:
        if (
            not isinstance(row.get("requirement_id"), str)
            or not isinstance(row.get("body_quote"), str)
            or row.get("source_id") is not None
            and not isinstance(row["source_id"], str)
            or not isinstance(row.get("source_quote", ""), str)
        ):
            audit["valid"] = False
            audit["errors"].append({"id": None, "reasons": ["覆盖映射字段类型错误"]})
        else:
            cleaned.append(row)
    rows = cleaned
    known = {r["id"] for r in requirements}
    if any(r.get("requirement_id") not in known for r in rows):
        audit["valid"] = False
        audit["errors"].append({"id": None, "reasons": ["覆盖ID不是本轮需求"]})
    for requirement in requirements:
        rid = requirement["id"]
        matches = [r for r in rows if r.get("requirement_id") == rid]
        reasons = []
        if len(matches) != 1:
            reasons.append("须为需求提供一条正文→来源映射")
        else:
            row = matches[0]
            answer = row.get("body_quote", "")
            if not answer.strip() or answer not in body:
                reasons.append("body_quote必须原样出现在KP正文；附属细节或NPC发言不算回答")
            elif re.search(r"[？?]", answer) or _compact(answer) == _compact(requirement["text"]):
                reasons.append("复述问题不能算作回答")
            elif not requirement.get("generic_scope") and not (
                _topic_terms(requirement["text"]) & terms(answer)
            ):
                reasons.append("正文片段没有回应此需求的具体对象")
            source_id = row.get("source_id")
            source = sources.get(source_id)
            if source_id:
                if not source or source_id not in requirement.get("source_ids", []):
                    reasons.append("引用不属于本轮该需求选定的可见来源")
                else:
                    reasons += _source_errors(requirement, answer, row, source)
                    if not prefix:
                        reasons += _body_assertion_errors(body, requirement, source)
            elif row.get("status") != "unknown" or not re.search(UNKNOWN, answer):
                reasons.append("无可见依据时须具体说明尚未知的内容")
            elif requirement.get("source_ids"):
                reasons.append("本轮存在相关可见来源，须引用并保留已知内容后说明未知项")
            elif not requirement.get("source_ids"):
                # A later 'unknown' sentence cannot excuse an earlier invented
                # answer to this same explicit factual probe.
                reasons += unknown_assertion_errors(body, requirement)
            if row.get("status") == "unknown" and not _unknown_for_requirement(answer, requirement):
                reasons.append("unknown须在正文具体说明未知内容")
        if reasons:
            if matches:
                audit["valid"] = False
            audit["missing"].append(rid)
            audit["errors"].append(
                {
                    "id": rid,
                    "requirement": requirement["text"],
                    "reasons": reasons,
                    "available_sources": [
                        sources[s] for s in requirement.get("source_ids", []) if s in sources
                    ],
                }
            )
        else:
            audit["covered"].append(rid)
            audit["verified"].append(matches[0])
    if not prefix and not partial:
        audit["complete"] = not audit["missing"] and not audit["errors"]
    return audit


def unknown_answer_hints(requirements):
    """Expression hints for missing evidence; never fill a generated body."""
    return [
        {"requirement_id": r["id"],
         "answer": "关于" + re.sub(r"[？?。！!]+$", "", r["text"].strip())
         + "，目前尚未确认。"}
        for r in requirements if "source_ids" in r and not r["source_ids"]
    ]


def coverage_repair_message(audit):
    import json

    return (
        "先写完整正文：把missing中每项requirement的实际答复写进本次正文，"
        "观察、历史问题、估计归属及具体未知项都须在正文中回答；"
        "再从已写正文逐字摘取body_quote，并填写source_id/source_quote/status。"
        "仅在answer_coverage内写答案不算回答。保持已验证片段的含义/来源，"
        "仍输出一段自然KP正文，不限定句数。"
        + json.dumps(
            {"missing": audit["errors"], "preserve": audit["verified"],
             "required_unknown_answers": unknown_answer_hints([
                 {"id": row["id"], "text": row["requirement"], "source_ids": []}
                 for row in audit["errors"]
                 if row.get("requirement") and row.get("available_sources") == []
             ])},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _fact_atoms(text, speaker=""):
    """Small deterministic equivalences; unfamiliar rewrites remain unverified.

    Keep descriptive nouns/values and action direction while removing the
    grammatical scaffolding already enforced by source/epistemic validation.
    This is deliberately not a general semantic similarity score.
    """
    for name in sorted([speaker, *re.split(r"[·・\s]", speaker)], key=len, reverse=True):
        if name:
            text = text.replace(name, "")
    text = QUANTITY.sub(" ", text)
    for pattern, replacement in (
        (r"留有|留着|留下|有着|可见|能看见", "有"),
        (r"灰尘|尘埃", "灰尘"),
        (r"丢失|缺少|短缺|少了|少", "缺少"),
        (r"增加|多了|增多", "增加"),
        (r"不清楚|不知道|未知|不明白", "未知"),
        (r"明显|显眼", "显眼"),
        (r"具体名称|具体名字", "名称"),
    ):
        text = re.sub(pattern, " " + replacement + " ", text)
    text = re.sub(HISTORICAL + "|" + ESTIMATE + "|" + TESTIMONY, " ", text)
    text = re.sub(
        r"据|的|地|得|了|着|有|是|在|上|表面|边沿|仍|并|但|还|也|他|她|我|其|约|具体",
        " ", text,
    )
    return {part for part in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", text.lower()) if part}


def freeze_verified_fact(row, brief):
    from copy import deepcopy

    requirement = next(r for r in brief["answer_requirements"] if r["id"] == row["requirement_id"])
    source = next((s for s in brief["answer_sources"] if s["id"] == row.get("source_id")), None)
    fact_text = _requirement_fact_text(row["body_quote"], requirement)
    return deepcopy({
        "requirement": requirement,
        "source": source,
        "row": row,
        "facts": {
            "text": fact_text,
            "quantities": sorted(_quantities(fact_text)),
            "atoms": sorted(_fact_atoms(
                fact_text, requirement.get("speaker") or (source or {}).get("speaker", ""),
            )),
        },
    })


def retained_fact_errors(output, brief, retained):
    """Revalidate requirement, original authority and facts after a repair."""
    value = output.model_dump() if hasattr(output, "model_dump") else output
    effective, _ = normalize_coverage_spans(value, brief)
    audit = coverage_audit(effective, brief)
    verified = {r["requirement_id"]: r for r in audit["verified"]}
    requirements = {r["id"]: r for r in brief["answer_requirements"]}
    sources = {s["id"]: s for s in brief["answer_sources"]}
    errors = []
    for frozen in retained:
        old = frozen["row"]
        rid = old["requirement_id"]
        current = verified.get(rid)
        reasons = []
        if (
            current is None or current.get("source_id") != old.get("source_id")
            or requirements.get(rid) != frozen["requirement"]
            or sources.get(old.get("source_id")) != frozen["source"]
        ):
            reasons.append("修复未保留原需求及已验证来源")
        else:
            # Keep the object/predicate binding. Another requirement in the
            # same scene source cannot provide the missing or swapped fact.
            answer = _requirement_fact_text(current["body_quote"], frozen["requirement"])
            if not _quantities(frozen["facts"]["text"]) <= _quantities(answer):
                reasons.append("修复丢失已验证数值或单位")
            speaker = frozen["requirement"].get("speaker") or (frozen["source"] or {}).get(
                "speaker", "",
            )
            if not set(frozen["facts"]["atoms"]) <= _fact_atoms(answer, speaker):
                reasons.append("修复未能确定保留原已验证关键事实")
            reasons += _polarity_errors(answer, frozen["facts"]["text"])
            reasons += _condition_errors(current["body_quote"], old["body_quote"])
        if reasons:
            errors.append({"requirement_id": rid, "reasons": reasons, "preserve": frozen})
    return errors


def normalize_coverage_spans(output, brief):
    """Repair copies of already-declared evidence, never body text or authority.

    The model must supply a unique current requirement and its allowed source.
    Consistent independently verified spans are ordered by length then position.
    Every factual assertion is checked even for a correct native mapping, so
    later repetition/advice is harmless while a contradictory fact is rejected.
    """
    from copy import deepcopy

    value = output.model_dump() if hasattr(output, "model_dump") else output
    effective = deepcopy(value) if isinstance(value, dict) else {}
    rows = effective.get("answer_coverage")
    body = effective.get("public_narration") or effective.get("observed_detail")
    if not isinstance(rows, list) or not isinstance(body, str):
        return effective, []
    requirements = {r["id"]: r for r in brief.get("answer_requirements", [])}
    sources = {s["id"]: s for s in brief.get("answer_sources", [])}
    sentences = list(re.finditer(r"[^。！？!?；;\n]+(?:[。！？!?；;\n]+|$)", body))
    changes = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("requirement_id"), str):
            continue
        rid, source_id = row["requirement_id"], row.get("source_id")
        requirement = requirements.get(rid)
        if (
            not requirement
            or not isinstance(source_id, str)
            or source_id not in requirement.get("source_ids", [])
            or source_id not in sources
            or row.get("status") not in {"answered", "unknown"}
            or sum(isinstance(r, dict) and r.get("requirement_id") == rid for r in rows) != 1
        ):
            continue
        source = sources[source_id]
        if not isinstance(source.get("text"), str) or not 0 < len(source["text"]) <= 1200:
            continue
        # Restrict the audit to this declared source; another allowed candidate
        # cannot excuse a conflicting value in the rest of the actual body.
        local = {**brief, "answer_requirements": [{**requirement, "source_ids": [source_id]}]}
        if coverage_audit({**effective, "answer_coverage": [row]}, local)["complete"]:
            continue
        if _body_assertion_errors(body, requirement, source):
            continue
        candidates = []
        for start in range(len(sentences)):
            for end in range(start, len(sentences)):
                quote = body[sentences[start].start() : sentences[end].end()]
                candidate = {**row, "body_quote": quote, "source_quote": source["text"]}
                if coverage_audit({**effective, "answer_coverage": [candidate]}, local)["complete"]:
                    candidates.append((start, end, candidate))
                    break  # Longer spans at this start cannot win the stable order.
        if not candidates:
            continue
        start, end, candidate = min(
            candidates, key=lambda c: (len(c[2]["body_quote"]), sentences[c[0]].start()),
        )
        changes.append(
            {
                "requirement_id": rid,
                "before": deepcopy(row),
                "after": candidate,
                "reason": "shortest_earliest_verified_span_and_selected_source",
                "candidate_count": len(candidates),
                "body_start": sentences[start].start(),
                "body_end": sentences[end].end(),
            }
        )
        rows[index] = candidate
    return effective, changes
