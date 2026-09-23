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
        r"什么|多少|是否|知道|具体|早先|估计|请|回答|观察|查看|检查|环顾|"
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
            )
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
    if not demands and brief.get("ordinary_observation") and brief.get("attempt"):
        subject = brief.get("observation_subject") or {}
        demands = [("observation", subject.get("title") or brief["attempt"])]
    # Formatting attached to a factual question never deletes that question.
    for question in brief.get("questions", []):
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
                **({"generic_scope": True} if generic else {}),
                **({"speaker": speaker} if speaker else {}),
                **({"historical": True} if historical else {}),
            }
        )
        if speaker:
            last_speaker = speaker
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
    clauses = [s.strip() for s in re.split(r"[，,。；;！？!?\n]", text) if s.strip()]
    for index, clause in enumerate(clauses):
        if not re.search(UNKNOWN, clause):
            continue
        if topics & terms(clause):
            return True
        # "至于具体名称，他仍不知道" is one scoped statement across a comma.
        if (
            index
            and topics & terms(clauses[index - 1])
            and re.match(
                r"^(?:但|而|[他她我]|仍|还|尚|目前|暂时|并)*(?:不知|不清|不明|未确认|无法确认)",
                clause,
            )
        ):
            return True
    return False


def _polarity_errors(answer, quote):
    def negative(clause):
        # Unknown epistemic state is separate from denying an observable fact.
        clause = re.sub(UNKNOWN, "", clause)
        return bool(re.search(r"没有|并无|不存在|未见|没见|不曾|从未|不是|并非|不在|并不", clause))

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


def _source_errors(requirement, answer, row, source):
    """Check a selected source against the actual KP span, never auxiliary prose."""
    errors = []
    question, quote = requirement["text"], row.get("source_quote", "")
    source_text = source["text"]
    if not quote.strip() or quote not in source_text:
        return ["source_quote须为本轮所选可见来源中的原样片段"]
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
    # A model cannot cite an uninformative slice to discard the value/qualifier.
    relevant = [
        s for s in re.split(r"[。；;！？!?\n]", source_text) if _topic_terms(question) & terms(s)
    ]
    support = "。".join(relevant) or quote
    quantity_question = bool(
        re.search(
            r"多少|几(?:本|件|个|把|张|枚|份|支|瓶|年|月|日)|数量|数目|金额|日期|何时", question
        )
    )
    expected = _requested_quantities(support, question) if quantity_question else set()
    actual = _quantities(answer)
    if expected and not expected <= actual:
        errors.append("正文未保留来源的具体数值/单位：" + support)
    if actual and not actual <= _quantities(source_text):
        errors.append("正文数值没有该可见来源支持")
    estimate = expected and re.search(ESTIMATE, support + question)
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
    if not (_topic_terms(quote) & terms(answer)) and not (expected and expected <= actual):
        errors.append("正文片段未表达引用来源的具体内容")
    predicates = _topic_terms(quote) - _topic_terms(question)
    if (
        len(predicates) >= 3
        and not (predicates & terms(answer))
        and not (expected and expected <= actual)
    ):
        errors.append("正文只重复对象，未表达来源支持的具体观察或答复")
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
        if not _quantities(sentence):
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
            elif row.get("status") != "unknown" or not re.search(UNKNOWN, answer):
                reasons.append("无可见依据时须具体说明尚未知的内容")
            elif requirement.get("source_ids"):
                reasons.append("本轮存在相关可见来源，须引用并保留已知内容后说明未知项")
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


def coverage_repair_message(audit):
    import json

    return (
        "先写完整正文：把missing中每项requirement的实际答复写进本次正文，"
        "观察、历史问题、估计归属及具体未知项都须在正文中回答；"
        "再从已写正文逐字摘取body_quote，并填写source_id/source_quote/status。"
        "仅在answer_coverage内写答案不算回答。保持已验证片段的含义/来源，"
        "仍输出一段自然KP正文，不限定句数。"
        + json.dumps(
            {"missing": audit["errors"], "preserve": audit["verified"]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def normalize_coverage_spans(output, brief):
    """Repair copies of already-declared evidence, never body text or authority.

    The model must supply a unique current requirement and its allowed source.
    Only one minimal, fully valid body span may replace its copied quote. Other
    relevant assertions must also fit that span, so a good sentence cannot hide
    a contradictory or ambiguous answer elsewhere in the same body.
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
        candidates = []
        for start in range(len(sentences)):
            for end in range(start, min(start + 2, len(sentences))):
                quote = body[sentences[start].start() : sentences[end].end()]
                candidate = {**row, "body_quote": quote, "source_quote": source["text"]}
                if coverage_audit({**effective, "answer_coverage": [candidate]}, local)["complete"]:
                    candidates.append((start, end, candidate))
        minimal = [
            c
            for c in candidates
            if not any(
                c[0] <= other[0] <= other[1] <= c[1] and c[:2] != other[:2] for other in candidates
            )
        ]
        if len(minimal) != 1:
            continue
        start, end, candidate = minimal[0]
        topic = _content_topics(
            {**requirement, "text": re.sub(r"[吗呢？?]", "", requirement["text"])}
        )
        numeric = bool(re.search(r"多少|数量|数目|几(?:本|件|个|把|张|枚|份)", requirement["text"]))
        units = {unit for _, unit in _requested_quantities(source["text"], requirement["text"])}
        related = [
            i
            for i, sentence in enumerate(sentences)
            if (
                len(topic & terms(sentence[0])) >= min(2, len(topic))
                and topic
                or numeric
                and any(unit in units for _, unit in _quantities(sentence[0]))
            )
        ]
        if any(i < start or i > end for i in related):
            continue
        # Adjacent sentences may carry speaker/history context. They may not
        # supply a correct unknown/value sentence to mask a conflicting one.
        semantic_errors = [
            error
            for i in related
            for error in _source_errors(
                requirement,
                sentences[i][0],
                candidate,
                source,
            )
            if not error.startswith(("正文须将证词归属于", "证词须保留转述性质", "旧证词不能"))
        ]
        if semantic_errors:
            continue
        changes.append(
            {
                "requirement_id": rid,
                "before": deepcopy(row),
                "after": candidate,
                "reason": "unique_body_span_and_selected_source",
            }
        )
        rows[index] = candidate
    return effective, changes
