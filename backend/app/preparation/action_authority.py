"""Server-side action capabilities, frozen before auxiliary method selection.

The lexical checks are conservative operation guards, not a second story parser.
An unknown operation cannot acquire the capability to transfer or throw an item.
"""

import re

from app.preparation.inventory import held_instance

VERBS = {
    "first_aid": (r"急救|包扎|止(?:一下|一止|住)?血|"
                  r"处理(?:一下)?(?:[^，。；！？,;.!?]{1,10}的)?伤口|"
                  r"(?:按|压)(?:住|紧)?[^，。；！？,;.!?]{0,8}伤口|\bfirst aid\b"),
    "medicine": r"(?:医学)?治疗|\bmedical treatment\b",
    "throw": r"扔|抛|投(?:出|向|掷)|掷|甩(?:出|向)|砸向|\bthrow\b|\btoss\b|\bhurl\b",
    "give": (
        r"交(?:还|回)?(?:给|到|出)|归还|还给|递(?:给|到|出)|转交|塞.{0,16}手里"
        r"|\bgive\b|\bhand\b"
    ),
    "place": r"放(?:下|在|到|置)|搁|摆在|\b(?:place|drop|put)\b",
    "take": (
        r"拿(?:起|走|取|好|回|出(?:来)?)|取(?:走|下|回|出)|掏出|抽出|拾|捡|收(?:起|好|进)|带走|装进"
        r"|\b(?:take|pick|collect)\b"
    ),
    "consume": r"吃|喝|吞|用掉|耗用|消耗|\b(?:eat|drink|consume)\b",
    "search": (
        r"寻找|找|搜|查(?:查|一查)|摸索|翻(?:找|查|开|看|过|转|翻)|揭下|检查|查看.{0,8}(?:包|袋|背面|反面|正反面)"
        r"|(?:看看|观察)(?:一下)?(?:(?:我|你|自己)(?:的)?)?(?:口袋|衣袋|随身物|随身东西|背包)"
        r"|摸(?:索|摸)?(?:一下)?(?:自己|我的)?(?:的)?(?:口袋|衣袋|背包)"
        r"|\b(?:search|rummage|find|inspect)\b"
    ),
    "clear": r"清理|搬开|移开|\bclear\b",
    "observe": (r"观察|查看|核对|看(?:清|向|看)|照(?:向|着)|辨认|打量|注视"
                r"|\b(?:look|observe|watch)\b"),
    "light": (
        r"(?:打开|开启|点亮|关掉|关闭).{0,12}(?:灯|照明|手电|屏幕)"
        r"|(?:灯|照明|手电(?:筒)?|屏幕)(?:的?(?:开关|功能))?(?:也|给|都)?(?:关掉|关闭|关上|打开|开启)"
        r"|关灯|开灯"
        r"|(?:调(?:整|节)|改变|降低|提高).{0,6}(?:灯光|照明|亮度)"
        r"|(?:用|使用).{0,16}(?:照明|照亮)|\b(?:light|illuminate)\b"
    ),
    "sound_start": r"(?:打开|开启|放|播|启动|响起).{0,12}(?:铃|音乐|声音|录音)|\b(?:ring|play)\b",
    "sound_stop": r"(?:关闭|关掉|停止|关上).{0,12}(?:铃|音乐|声音|录音)|静音|\bsilence\b",
    "open": (
        r"开(?:锁|门)|解锁|打开.{0,12}(?:门|面板)|插.{0,10}钥匙"
        r"|(?:门|面板)(?:给|再|也)?打开|插入.{0,16}锁孔|转动.{0,8}钥匙|\b(?:unlock|open)\b"
    ),
    "close": r"关(?:门|上|闭)|拉上.{0,8}门|\bclose\b",
    "pass": r"通过|穿过|绕过|潜行|溜过|冲进|冲过|跳|悄悄.{0,8}走|\b(?:sneak|pass)\b",
    "carry": r"背(?:起|上|着|负)|抱起|扶起|\bcarry\b",
    "support": (r"扶稳|靠稳|安置|扶住|(?:调整|变换|固定).{0,10}(?:姿势|体位)"
                r"|(?:协助|帮助|帮).{0,8}稳定(?:身体|姿势)|(?:靠|倚).{0,8}(?:墙|座|椅|壁)"),
    "release": r"挣脱|挣开|摆脱|\bescape\b|\bbreak free\b",
    "control": (
        r"(?:推|拉)(?:动|开|上|下|起|出|入|回|住|向|到|倒|紧|松|过|着|了|一)"
        r"|(?:上|下|前|后|向外|向内|往外|往里)(?:推|拉)"
        r"|(?:我(?:们)?(?:想|要|先|再|来)?|用力|使劲|试着|尝试|继续|轻轻|慢慢)"
        r"(?:推|拉)(?!测|断|理|荐)"
        r"|(?:把|将)[^，。；！？,;.!?]{1,16}(?:推|拉)(?=[，。；！？,;.!?]|$)"
        r"|加速|减速|停车|\b(?:push|pull|accelerate|stop)\b"
    ),
    "converse": r"问|告诉|说|询问|答|\b(?:ask|tell|say)\b",
    "use": r"使用|启用|激活|饮用|喝|吃|服用|\b(?:use|activate|drink|eat)\b",
    "settle": r"确认.{0,12}(?:结束|终幕|结算)|继续结算|结束调查",
    "rest": r"休息|等候|等待|歇|\b(?:rest|wait)\b",
}
NON_ACTION = re.compile(
    r"是否|能否|可否|假如|如果|假设|建议|不如|要不要|(?:你|我们)(?:可以|应该)"
    r"|不要|不用|不必|不敢|不愿|不想|不(?:尝试|试着)|先别|别再|停止|取消|并未|尚未|不曾|没(?!关系|问题)|[?？]"
    r"|\b(?:if|could you|would you|we could|we should|you should)\b",
    re.I,
)


def declared_action(text):
    """An actual inspection may contain an embedded question about its result."""
    if re.search(
        r"假如|如果|假设|建议|不如|要不要|不要|不用|不必|先别|别再|停止|取消|并未|\bif\b",
        text,
        re.I,
    ):
        return False
    return any(
        re.match(
            r"\s*(?:我(?:们)?(?:现在|这就|先|想|要|打算|准备)?)?"
            r"(?:仔细|认真|现在|这就|先|再|然后|接着|继续|试着|尝试|实际|去)*"
            r"(?:(?:靠近|走近|凑近|过去|蹲下|弯腰)[^，。；,.!?;]{0,16}?)?"
            r"(?:(?:透过|隔着|借着|顺着)[^，。；,.!?;]{1,12}?)?"
            r"(?:(?:从|在|沿|伸手|伸出手)[^，。；,.!?;]{0,16}?)?"
            r"(?:急救|包扎|止血|检查|查看|核对|看看|摸|搜索|寻找|搜寻|翻看|翻翻|翻转|翻过|揭下|观察|拿起|取下|使用|潜行|"
            r"(?:把|将).{1,24}(?:翻过|翻转|揭下|拿起|扔|抛|交给|打开)|交给|交还|归还|递给)",
            c,
        )
        for c in re.split(r"(?<=[，。；！？,;.!?\n])", text)
    )


def operative_fragments(text):
    """Original spans of attempts, excluding subordinate purpose/result questions.

    This boundary is shared by freezing and method selection, so quoting just a
    verb inside an excluded purpose cannot regain authority.
    """
    fragments = []
    conditional = False
    for match in re.finditer(r"[^，。；！？,;.!?\n]+[，。；！？,;.!?\n]?", text or ""):
        clause = match[0]
        conditional = conditional or bool(re.match(
            r"\s*(?:如果|假如|假设|建议|不如|(?:等)?(?:找|拿)到.{0,12}(?:之后|以后|后))", clause,
        ))
        if conditional:
            conditional = not bool(re.search(r"[。；！？;.!?\n]$", clause))
            continue
        if NON_ACTION.search(clause) and not declared_action(clause):
            continue
        # Capability modifiers describe a wanted object; intention markers at
        # the start of an independent attempt (我想/准备/试着拿出) stay actionable.
        boundary = re.search(
            r"(?:能|可)(?:够|以)?(?:拿来|用来|用于)|(?:拿来|用来|用于|以便|为了)|"
            r"(?:是否|有没有|是不是|能否|可否|在不在|能不能)|"
            r"(?:想|打算)(?=把|将).*(?:让|以便)|"
            r"(?:找|拿)到(?:以后|之后|后)(?=再|就|把|将)", clause,
        )
        end = boundary.start() if boundary else len(clause)
        actual = clause[:end]
        if actual.strip():
            fragments.append({"text": actual, "start": match.start(),
                              "end": match.start() + end,
                              "purpose": clause[end:] if boundary else ""})
    return fragments


def action_kinds(text):
    clauses = [f["text"] for f in operative_fragments(text)]
    # The question describes what is being checked, not additional operations:
    # inspecting whether a door opened cannot acquire an opening capability.
    clauses = [
        re.split(r"是否|有没有|是不是|能否|可否|在不在|能不能", c, maxsplit=1)[0]
        if declared_action(c)
        else c
        for c in clauses
    ]
    # A completed-state modifier identifies an object; it is not a new attempt
    # to perform that operation (e.g. operating an already-open panel).
    clauses = [re.sub(r"(?:已经|早已|已|刚刚|刚)[^，。；,;.!\n]{0,16}?的", "", c) for c in clauses]
    def operation_clauses(kind):
        if kind not in {"open", "close", "use"}:
            return clauses
        # A specific equipment operation consumes its own verb span. Closing
        # a lamp is one operation, not an additional unexecuted door closure.
        specific = "|".join(VERBS[k] for k in ("light", "sound_start", "sound_stop"))
        return [re.sub(specific, "", c, flags=re.I) for c in clauses]

    return [kind for kind, pattern in VERBS.items()
            if any(re.search(pattern, c, re.I) for c in operation_clauses(kind))]


def check_operation_error(name, action):
    """A treatment-gated discovery cannot turn another action into treatment."""
    if name in {"first_aid", "medicine"} and name not in action_kinds(action):
        return "本次实际动作没有医疗操作，不能借用伤情线索的治疗检定"
    return None


def speaker_action(text):
    """A present first-person operation remains owned by its speaker."""
    from app.agents.action_policy import explicit_movement

    quotes = [m.span() for m in re.finditer(r'“[^”]*”|‘[^’]*’|「[^」]*」|"[^"]*"', text)]
    offset = 0
    for clause in re.split(r"(?<=[，。；！？,;.!?\n])|(?<=……)", text):
        start = offset + len(clause) - len(clause.lstrip())
        offset += len(clause)
        if any(left <= start < right for left, right in quotes):
            continue
        if re.match(r"\s*我(?:是|想)?请你|\s*我(?:希望|让|叫)你", clause):
            continue
        if not re.match(r"\s*我(?:们)?", clause) or re.match(
            r"\s*我(?:们)?(?:先|暂时)?(?:不|没|未)", clause
        ):
            continue
        if declared_action(clause) or explicit_movement(clause) or (
            not NON_ACTION.search(clause) and set(action_kinds(clause)) - {"converse"}
        ):
            return clause
    return None


def addressed_request_text(raw, name, members=None):
    """A named request ends before another sentence or the speaker's own act."""
    if members:
        spans = addressed_spans(raw, members)
        return next((raw[start:end] for mid, start, end in spans if members[mid] == name), "")
    match = re.search(r"(?:^|[，,。！？；])\s*" + re.escape(name) + r"[，,:：]([^。！？；]*)", raw)
    if not match:
        return raw
    return re.split(r"[，,]\s*我(?:们)?(?!是请|想请|希望你)", match[1], maxsplit=1)[0]


def address_names(members):
    """Accept an unambiguous given name as a salutation, never a shared prefix."""
    members = public_member_names(members)
    candidates = {
        mid: {name, re.split(r"[·•\s]", name)[0]} for mid, name in members.items()
    }
    patterns = {}
    for mid, names in candidates.items():
        unique = [n for n in sorted(names, key=len, reverse=True)
                  if n and (n == members[mid] or len(n) >= 2)
                  and (sum(n == value for value in members.values()) == 1
                       if n == members[mid] else sum(n in ns for ns in candidates.values()) == 1)]
        patterns[mid] = "(?:" + "|".join(map(re.escape, unique)) + ")" if unique else r"(?!)"
    return patterns


def public_member_names(members):
    """Display seat suffixes are not part of a saved investigator's name."""
    return {mid: re.sub(r"[（(]队友\s*\d+[）)]$", "", name) for mid, name in members.items()}


def unquoted_text(text):
    """Quoted instructions describe content; they do not acquire new authority."""
    return re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|"[^"]*"', lambda m: " " * len(m[0]), text)


def address_candidates(raw, members):
    """Resolve complete names before aliases; preserve genuinely shared names."""
    members = public_member_names(members)
    owners = {}
    for mid, name in members.items():
        for alias in {name, re.split(r"[·•\s]", name)[0]}:
            if alias and (alias == name or len(alias) >= 2):
                owners.setdefault(alias, set()).add(mid)
    for alias in owners:
        exact = {mid for mid, name in members.items() if name == alias}
        owners[alias] = exact or owners[alias]
    if not owners:
        return []
    name_pattern = "(?:" + "|".join(
        re.escape(n) for n in sorted(owners, key=len, reverse=True)
    ) + ")"
    found = []
    quotes = [m.span() for m in re.finditer(r'“[^”]*”|‘[^’]*’|「[^」]*」|"[^"]*"', raw)]
    for prefix, suffix in (
        (r"(?:^|(?<=[，,。！？；;\n]))\s*(?:(?:我)?(?:请|让|叫|问|向|对))?",
         r"(?:先生|女士)?(?=[，,:：、]|(?:帮|照看|照顾|检查|查看|观察|负责|去|来|说|问|拿|用|打开|关))"),
        (r"(?:^|(?<=[，,。！？；;\n]))\s*我(?:蹲在|站在|坐在|走到|来到|转向|看着|靠近)",
         r"(?:旁边|身旁|面前)?(?:，?)(?:并|然后)?(?:询问|问|说|请教)[：:，,]?"),
    ):
        for match in re.finditer(
            prefix + "(?P<name>" + name_pattern + ")" + suffix, raw,
        ):
            introduction = raw[match.start():match.start("name")]
            report = re.match(r"(?:先生|女士)?说(?!说)", raw[match.end("name"):])
            if report and not re.search(r"请|让|叫|问|向|对", introduction):
                continue  # "林修远说：…" reports him; it does not address him.
            if not any(left <= match.start() < right for left, right in quotes):
                found.append((owners[match["name"]], match.start(), match.end()))
    found.sort(key=lambda item: item[1])
    return found


def mentioned_members(text, members):
    """Complete public names occupy one span; shorter substrings cannot add IDs."""
    members = public_member_names(members)
    names = sorted(set(filter(None, members.values())), key=len, reverse=True)
    if not names:
        return set()
    found = {m[0] for m in re.finditer("|".join(map(re.escape, names)), text)}
    return {mid for mid, name in members.items() if name in found}


def addressed_spans(raw, members):
    """Explicit address boundaries, not a classifier of an utterance's meaning."""
    found = address_candidates(raw, members)
    spans = []
    for i, (owners, start, name_end) in enumerate(found):
        if len(owners) != 1:
            continue
        mid = next(iter(owners))
        end = found[i + 1][1] if i + 1 < len(found) else len(raw)
        # A new first-person declaration ends the request even in the same sentence.
        own = re.search(
            r"(?<=[，,。！？；;])\s*我(?:们)?(?!是请|想请|希望你|想问|没|不|觉得|知道|听)",
            raw[name_end:end],
        )
        if own:
            clause = re.split(r"[，,。！？；;]", raw[name_end + own.start():end])[0]
            if (NON_ACTION.search(clause) and not declared_action(clause)) or re.search(
                r"(?:可以|能|该|要|怎么|如何).*(?:帮|做|配合)|做些?什么", clause
            ):
                own = None  # "我可以帮你做些什么？" remains addressed speech.
        if own:
            end = name_end + own.start()
        if end > name_end:
            spans.append((mid, start, end))
    return spans


def information_question(text):
    """Questions about meaning/advice never grant an operation capability."""
    return bool(
        re.search(
            r"你(?:们)?(?:知道|觉得|认为|看法|怎么看)|有什么(?:用|建议)|"
            r"(?:办法|主意|方案).{0,8}(?:行不行|可不可行|合不合适)|"
            r"我(?:可以|能|该|要|怎么|如何)[^。！？?]{0,16}(?:帮|做|配合)"
            r"[^。！？?]{0,12}(?:什么|如何|怎么|[？?])|"
            r"是(?:做|干|用来).{0,8}(?:什么|啥)|是什么意思|该(?:动|选|用)哪|"
            r"(?:说明|说说|告诉我|解释|回答|讲讲|分析|列出)[^，。；！？,;.!?]{0,32}"
            r"(?:什么|哪些|如何|怎么|是否|下一步|接下来)|"
            r"(?:下一步|接下来)[^，。；！？,;.!?]{0,12}(?:需要|还需|应该|该|要)"
            r"[^，。；！？,;.!?]{0,16}(?:什么|哪些|如何|怎么)|"
            r"(?:请教|想问|问一下)|\b(?:do you know|what do you think)\b",
            text,
            re.I,
        )
    )


def requested_action_kinds(text):
    """A requestee does not inherit a separate first-person requester action."""
    # A report/advice clause can contain operation words without delegating
    # them. Scope this exclusion to its original clause: an adjacent explicit
    # instruction still gives the peer a separate choice to act or decline.
    clauses = []
    text = unquoted_text(text)
    for part in request_clauses(text):
        clause = part["text"]
        if (information_question(clause) or speaker_action(clause)
                or re.match(r"\s*我(?:们)?(?:来|去|先|要|会|再|接着|随后|自己)", clause)):
            continue
        polite = bool(re.search(
            r"(?:能不能|可不可以|能否|可否|可以|能)(?:请|帮|把|将|检查|查看|核对|交|递|拿|用|打开|关闭)"
            r"|(?:请|麻烦|劳驾|帮我).{0,20}(?:检查|查看|核对|找|观察|拿|递|交|开|关|照看)",
            clause,
        ))
        if re.search(r"[？?]", clause) and not polite:
            continue
        # A polite request remains non-executable for its speaker. Only an
        # explicit imperative can lose its question punctuation for the peer.
        clause = re.sub(
            r"(?:能不能|可不可以|能否|可否|可以|能)(?=把|将|请|帮|交|递|拿|用|检查|查看|打开|关闭)",
            "", clause,
        )
        if polite:
            clause = clause.rstrip("？?") + ("。" if clause.endswith(("？", "?")) else "")
        clause = re.sub(r"^\s*(?:然后|接着|同时|并且|并|现在|这就)*", "", clause)
        clause = re.sub(r"^\s*(?:你|您)(?:先|再|现在|这就)?", "", clause)
        clause = re.sub(r"^\s*(?:(?:请|麻烦|劳驾)(?:你|您)?|帮我|帮忙)+", "", clause)
        clauses.append(clause)
    # Preserve the original sentence delimiters so a conditional does not
    # accidentally authorize the dependent clause after its comma.
    return [kind for kind in action_kinds("".join(clauses)) if kind != "converse"]


def request_clauses(text):
    """Literal clauses, including an explicit new request after a conjunction."""
    boundaries = {0, len(text)}
    boundaries.update(m.end() for m in re.finditer(r"[，。；！？,;.!?\n]", text))
    boundaries.update(m.start() for m in re.finditer(
        r"(?:然后|接着|同时|并且|并)(?=请|麻烦|现在|这就|告诉我|说明|解释|说说)", text,
    ))
    ordered = sorted(boundaries)
    return [{"text": text[start:end], "start": start, "end": end}
            for start, end in zip(ordered, ordered[1:]) if start < end]


def requested_search_attempt(text):
    """Retain the target of a search the teammate explicitly agreed to attempt."""
    if re.search(r"如果|假如|假设|不要|不想|不必|不需要|先不|别找|别搜|别检查", text):
        return None
    clauses = [
        c
        for c in re.split(r"[，。；？！,;.!?\n]", text)
        if c.strip() and not re.match(r"\s*我(?:们)?(?:来|去|先|要|会|再|接着|随后|自己)", c)
    ]
    for i, clause in enumerate(clauses):
        match = re.search(VERBS["search"], clause)
        if match:
            tail = re.sub(r"(?:吗|呢)\s*$", "", clause[match.end() :])
            return "我搜索" + "，".join([tail, *clauses[i + 1 :]]) + "。"
    return None


def aliases(entity):
    return list(dict.fromkeys([entity.get("title", ""), *entity.get("aliases", [])]))


def mentions_alias(text, name):
    """Match a local name with ordinary possessive particles, preserving its words."""

    def compact(value):
        return re.sub(r"[的\s]", "", value)

    return bool(name and compact(name) and compact(name) in compact(text))


def teammate_request(raw, members, actor, *, action=None):
    """A direct request is speech until the addressee submits their own event."""
    if action and action in raw and speaker_action(action):
        return None  # This selected action belongs to the speaker, even with a separate request.
    for mid, start, end in addressed_spans(raw, members):
        if mid == actor:
            continue
        request = raw[start:end]
        if requested_action_kinds(request) or re.search(
            r"[?？]|请教|询问|想问|(?:我是|我想|我希望)?请你|麻烦你|劳驾|"
            r"(?:^|[，,:：])\s*(?:请|帮|照看|照顾|留意|负责|拿着)", request,
        ):
            return mid
    return None


def freeze_action(plan, raw, actor, seq, scene, entities, runtime, members):
    action = plan.focus.action if plan.focus else ""
    target = plan.focus.action_target_id if plan.focus else plan.parsed_intent.target_id
    if plan.focus and plan.focus.requests:
        request = (
            next((r.addressee_id for r in plan.focus.requests if r.addressee_id in members), None)
            if not action
            else None
        )
    else:
        request = teammate_request(raw, members, actor, action=action)
    kinds = action_kinds(action) if action and action in raw and not request else []
    from app.memory.facts import readonly_recall

    conversation = bool(
        action and action in raw and not request and plan.focus
        and action == plan.focus.question and plan.focus.addressee_id == target
        and entities.get(target, {}).get("type") == "npc"
        and not readonly_recall(raw)
    )
    if conversation:
        kinds = ["converse"]
    operative = "".join(f["text"] for f in operative_fragments(action))
    from app.preparation.inventory import validate_resource_claims
    from app.rooms.service import RoomError

    resource_error = None
    try:
        validate_resource_claims(operative, {"known_items": [
            {"names": aliases(e)} for e in entities.values() if e.get("type") == "item"
        ]}, actual=True)
    except RoomError as error:
        resource_error = error.message
    explicit = [iid for iid in {*runtime.inventory, *runtime.dropped_items} if iid in operative]
    held = {}
    for eid in entities:
        named_instances = [iid for iid in explicit if runtime.item_instances.get(iid, iid) == eid]
        if len(named_instances) > 1:
            continue
        instance = held_instance(
            runtime, eid, actor, named_instances[0] if named_instances else None
        )
        if instance:
            held[eid] = instance
    named = {
        eid for eid, e in entities.items() if any(mentions_alias(operative, a) for a in aliases(e))
    }
    named.update(runtime.item_instances.get(iid, iid) for iid in explicit)
    named.intersection_update(entities)
    instances = {
        eid: instance
        for eid, instance in held.items()
        if eid in named or not named and eid == target
    }
    if "take" in kinds:
        for eid in named:
            dropped = [
                iid
                for iid in explicit
                if runtime.item_instances.get(iid, iid) == eid
                and runtime.dropped_items.get(iid) == scene
            ]
            if len(dropped) == 1:
                instances[eid] = dropped[0]
    fragments, operation_items, previous_items = [], {}, set()
    for fragment in operative_fragments(action):
        operations = action_kinds(fragment["text"])
        operands = {
            eid for eid, entity in entities.items() if (entity.get("type") == "item" or eid in held)
            and any(mentions_alias(fragment["text"], name) for name in aliases(entity))
        }
        if target in operands and target in held:
            # "camera's light" is a component of the named held device, not
            # a second standalone item that happens to share that word.
            parents = aliases(entities[target])
            for eid in list(operands - {target}):
                mentions = [m for name in aliases(entities[eid]) if name
                            for m in re.finditer(re.escape(name), fragment["text"])]
                if mentions and all(any(
                    re.search(re.escape(parent) + r"(?:上)?的$", fragment["text"][:m.start()])
                    for parent in parents if parent
                ) for m in mentions):
                    operands.remove(eid)
        if operations and not operands and re.search(
            r"把它|将它|(?:把|将)(?:这|那)(?:个|件|些)(?:东西|物品)?|\b(?:it|them)\b",
            fragment["text"], re.I,
        ):
            operands = previous_items or ({target} if target in held else set())
        if operations:
            for operation in operations:
                operation_items.setdefault(operation, set()).update(operands)
            previous_items = operands
        else:
            # Background possession/need cannot donate an operand to an action.
            previous_items = set()
        fragments.append({**fragment, "actor_member_id": actor, "target_id": target,
                          "operations": operations, "item_ids": sorted(operands)})
    return {
        "actor_member_id": actor,
        "source_event_seq": seq,
        "scene_node_id": scene,
        "intent_type": plan.parsed_intent.type,
        "target_id": target,
        "action": action,
        "utterance": raw,
        "clauses": [c.strip() for c in re.split(r"[，。；！？,;.!?\n]", raw) if c.strip()],
        "kinds": kinds,
        "conversation_target_id": target if conversation else None,
        "resource_error": resource_error,
        "actual_fragments": fragments if kinds else [],
        "operation_item_ids": {op: sorted(ids) for op, ids in operation_items.items()},
        "item_instances": instances,
        "held_instances": held,
        "named_item_ids": [eid for eid in named if entities[eid].get("type") == "item"],
        "named_entity_ids": sorted(named),
        "named_recipients": [
            mid
            for mid in mentioned_members(action, members)
            if mid != actor and "give" in kinds
        ],
        "request_member_id": request,
        "explicit_instance_ids": explicit,
        "named_members": sorted(mentioned_members(action, members)),
    }


def required_kinds(rule):
    if rule.use_effect:
        return {"use", "consume"}
    if rule.action_kinds:
        kinds = set(rule.action_kinds)
        # Older approved search methods also list "observe" as a synonym.
        # A timed search still requires a search attempt: looking at public
        # features cannot authorize its elapsed time or discovery effects.
        if rule.elapsed_minutes and "search" in kinds:
            kinds.discard("observe")
        return kinds
    if rule.inventory_operation:
        return {
            "give": {"give"},
            "drop": {"place"},
            "pickup": {"take"},
            "consume": {"consume"},
            "initial": {"search", "take"},
            "recover": {"search", "take"},
        }[rule.inventory_operation]
    if rule.encounter_operation:
        return {
            "sound_once": {"throw"},
            "sound_start": {"sound_start"},
            "sound_stop": {"sound_stop"},
            "continuous_lure": {"pass"},
            "open_door": {"open"},
            "close_door": {"close"},
            "grab": {"pass", "control"},
            "release": {"release"},
            "carry": {"carry"},
            "put_down": {"place"},
            "carry_check": {"carry", "pass"},
        }[rule.encounter_operation]
    if rule.acquire_item_ids:
        return {"take"}
    # Legacy observation-only revelations remain valid. State-changing new
    # package methods carry explicit kinds, rather than mining their prose.
    return set()


def selected_action_matches(authority, selected, rule):
    """A method can cite the operative clause without absorbing its purpose.

    Both the frozen primary action and the selected clause must authorize the
    operation; an observation clause cannot borrow a different clause's throw.
    """
    if authority.get("resource_error") or operation_item_error(authority, rule):
        return False
    allowed = required_kinds(rule)
    if (authority.get("conversation_target_id") and "converse" in allowed
            and not rule.outcome and not rule.inventory_operation and not rule.use_effect
            and not rule.encounter_operation and not rule.elapsed_minutes):
        return bool(selected and selected in authority.get("action", ""))
    terminal = authority.get("terminal_confirmation", {})
    if (
        rule.outcome
        and terminal.get("outcome") == rule.outcome
        and terminal.get("interaction_id") == rule.id
    ):
        return bool(selected and selected in authority.get("action", ""))
    return bool(
        selected
        and selected in authority.get("action", "")
        and any(
            (not allowed or allowed.intersection(action_kinds(f["text"])))
            and (selected in f["text"] or f["text"] in selected)
            for f in operative_fragments(authority.get("action", ""))
        )
        and (
            not allowed or allowed.intersection(authority.get("kinds", []), action_kinds(selected))
        )
    )


def operation_item_ids(authority, operations):
    if "operation_item_ids" not in authority:
        return set(authority.get("named_item_ids", authority.get("item_instances", {})))
    return set().union(*(set(authority["operation_item_ids"].get(op, [])) for op in operations))


def operation_item_error(authority, rule):
    # Operating carried equipment needs an actual reference to that equipment.
    # Possession alone cannot turn an environmental light into the held phone.
    # Other operations may legitimately require unmentioned prerequisite keys.
    if "light" in required_kinds(rule) and rule.required_item_ids and not set(
        rule.required_item_ids
    ) <= operation_item_ids(authority, {"light"}):
        return "原话没有指向该照明设备，不能用持有的其他物品替代操作目标"
    return None


def authority_error(
    authority,
    rule,
    runtime,
    *,
    actor,
    seq,
    scene,
    item_id=None,
    recipient=None,
    instance_id=None,
    target=None,
    check_facts=True,
):
    item_id = rule.sound_item_id or item_id
    if not authority or any(
        authority.get(k) != v
        for k, v in {
            "actor_member_id": actor,
            "source_event_seq": seq,
            "scene_node_id": scene,
        }.items()
    ):
        return "行动授权与本次事件不一致"
    if authority.get("request_member_id"):
        return "队友请求须由队友自己的行动事件执行"
    if authority.get("resource_error"):
        return authority["resource_error"]
    if error := operation_item_error(authority, rule):
        return error
    if (
        rule.use_effect
        and target
        and target != actor
        and target not in authority.get("named_members", [])
    ):
        return "效果目标与本次实际行动不一致"
    allowed = required_kinds(rule)
    terminal = authority.get("terminal_confirmation", {})
    settling = bool(
        rule.outcome
        and rule.outcome == runtime.pending_outcome
        and terminal.get("outcome") == rule.outcome
        and terminal.get("interaction_id") == rule.id
    )
    if allowed and not settling and not allowed.intersection(authority.get("kinds", [])):
        return "实际动作不授权此操作"
    if (
        rule.inventory_operation in {"initial", "recover"}
        and authority.get("named_item_ids")
        and rule.item_id not in authority["named_item_ids"]
    ):
        return "查找物品与本次原话指定的对象不一致"
    if rule.acquire_item_ids and authority.get("named_item_ids") and not set(
        rule.acquire_item_ids
    ).intersection(authority["named_item_ids"]):
        return "取得物品与本次原话指定的对象不一致"
    if (
        rule.inventory_operation == "give"
        and recipient
        and authority.get("named_recipients")
        and authority["named_recipients"] != [recipient]
    ):
        return "接收者与本次原话指定的人不一致"
    for eid in rule.required_item_ids:
        # A possession prerequisite (e.g. keys while operating an unlocked
        # console) does not require reciting the item name on every action.
        frozen = authority.get("held_instances", authority.get("item_instances", {})).get(eid)
        if not frozen or held_instance(runtime, eid, actor, frozen) != frozen:
            return "所用物品实例与本次实际动作不一致"
    item_ids = set()
    if rule.inventory_operation in {"give", "drop", "consume"}:
        item_ids.add(rule.item_id)
    if rule.use_effect:
        item_ids.add(rule.item_id)
    if rule.encounter_operation == "sound_once" and item_id:
        item_ids.add(item_id)
    for eid in item_ids:
        if "operation_item_ids" in authority and eid not in operation_item_ids(authority, allowed):
            return "该物品只在其他片段出现，未获本次操作授权"
        frozen = authority.get("item_instances", {}).get(eid)
        if (
            not frozen
            or held_instance(runtime, eid, actor, frozen) != frozen
            or instance_id is not None
            and instance_id != frozen
        ):
            return "所用物品实例与本次实际动作不一致"
    if rule.inventory_operation == "pickup":
        frozen = authority.get("item_instances", {}).get(rule.item_id)
        if frozen and (
            runtime.dropped_items.get(frozen) != scene
            or instance_id is not None
            and instance_id != frozen
        ):
            return "拾回实例与本次指定的物品不一致"
    if rule.encounter_operation == "sound_once" and not item_id:
        throw_text = "".join(f["text"] for f in authority.get("actual_fragments", [])
                             if "throw" in f.get("operations", []))
        if not rule.allow_worn_sound_item or not re.search(
            r"鞋|衣|帽|\b(?:shoe|coat|hat)\b", throw_text or authority["action"], re.I
        ):
            return "未确定本次实际投出的物品"
    for key in rule.required_facts if check_facts else []:
        fact = runtime.scene_facts.get(key, {})
        if not (
            fact.get("established")
            and fact.get("scene_node_id") == scene
            and fact.get("actor_member_id") == actor
            and fact.get("source_event_seq")
            and fact.get("origin") in {"scene_adjudication", "interaction_receipt"}
        ):
            return "尚缺已裁定的场景事实：" + key
    return None


def establish_scene_facts(runtime, authority, proposed):
    """KP relations require current positional evidence; rule prose is excluded."""
    for key, quote in proposed.items():
        if not quote or quote not in authority.get("action", "") or NON_ACTION.search(quote):
            continue
        if key == "sound_distance_over_half_car":
            supported = bool(
                re.search(
                    r"(?:相距|距离|隔着|间隔).{0,8}(?:超过|大于|多于).{0,3}半.{0,2}车厢",
                    quote,
                )
                and re.search(r"站|位于|在.{0,10}(?:前|后)门", quote)
            )
        elif key == "visual_target_in_phone_light":
            supported = bool(
                re.search(r"靠近|走近|近处|面前|脚边|贴近", quote)
                and "observe" in authority.get("kinds", [])
                and runtime.flags.get("phone_light")
            )
        else:
            supported = False
        if supported:
            runtime.scene_facts[key] = {
                "established": True,
                "actor_member_id": authority["actor_member_id"],
                "source_event_seq": authority["source_event_seq"],
                "scene_node_id": authority["scene_node_id"],
                "origin": "scene_adjudication",
                "evidence_quote": quote,
            }
