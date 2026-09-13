"""Server-side action capabilities, frozen before auxiliary method selection.

The lexical checks are conservative operation guards, not a second story parser.
An unknown operation cannot acquire the capability to transfer or throw an item.
"""

import re

from app.preparation.inventory import held_instance

VERBS = {
    "throw": r"扔|抛|投(?:出|向|掷)|掷|甩(?:出|向)|砸向|\bthrow\b|\btoss\b|\bhurl\b",
    "give": r"交(?:还)?(?:给|到|出)|归还|还给|递(?:给|到|出)|转交|塞.{0,16}手里|\bgive\b|\bhand\b",
    "place": r"放(?:下|在|到|置)|搁|摆在|\b(?:place|drop|put)\b",
    "take": r"拿(?:起|走|取|好|回)|取(?:走|下|回)|拾|捡|收起|带走|\b(?:take|pick|collect)\b",
    "consume": r"吃|喝|吞|用掉|耗用|消耗|\b(?:eat|drink|consume)\b",
    "search": (
        r"寻找|找|搜|翻(?:找|查|开|看)|检查|查看.{0,8}(?:包|袋)"
        r"|\b(?:search|rummage|find|inspect)\b"
    ),
    "clear": r"清理|搬开|移开|\bclear\b",
    "observe": r"观察|看(?:清|向|看)|照(?:向|着)|辨认|打量|注视|\b(?:look|observe|watch)\b",
    "light": r"(?:打开|开启|点亮|关掉|关闭).{0,12}(?:灯|照明|手电)|\b(?:light|illuminate)\b",
    "sound_start": r"(?:打开|开启|放|播|启动|响起).{0,12}(?:铃|音乐|声音|录音)|\b(?:ring|play)\b",
    "sound_stop": r"(?:关闭|关掉|停止|关上).{0,12}(?:铃|音乐|声音|录音)|静音|\bsilence\b",
    "open": (
        r"开(?:锁|门)|解锁|打开.{0,12}(?:门|面板)|插.{0,10}钥匙"
        r"|插入.{0,16}锁孔|转动.{0,8}钥匙|\b(?:unlock|open)\b"
    ),
    "close": r"关(?:门|上|闭)|拉上.{0,8}门|\bclose\b",
    "pass": r"通过|穿过|绕过|潜行|溜过|悄悄.{0,8}走|\b(?:sneak|pass)\b",
    "carry": r"背(?:起|上|着|负)|抱起|扶起|\bcarry\b",
    "release": r"挣脱|挣开|摆脱|\bescape\b|\bbreak free\b",
    "control": r"推|拉|加速|减速|停车|\b(?:push|pull|accelerate|stop)\b",
    "converse": r"问|告诉|说|询问|答|\b(?:ask|tell|say)\b",
}
NON_ACTION = re.compile(
    r"是否|能否|可否|假如|如果|假设|建议|不如|要不要|(?:你|我们)(?:可以|应该)"
    r"|不要|并未|没有|[?？]|\b(?:if|could you|would you|we could|we should|you should)\b",
    re.I,
)


def action_kinds(text):
    if not text or re.search(r"假如|如果|假设|建议|\bif\b", text, re.I):
        return []
    clauses = [c for c in re.split(r"[，。；,;.!\n]", text) if c and not NON_ACTION.search(c)]
    # A completed-state modifier identifies an object; it is not a new attempt
    # to perform that operation (e.g. operating an already-open panel).
    clauses = [re.sub(r"(?:已经|早已|已)[^，。；,;.!\n]{0,16}?的", "", c) for c in clauses]
    return [
        kind for kind, pattern in VERBS.items() if any(re.search(pattern, c, re.I) for c in clauses)
    ]


def aliases(entity):
    return list(dict.fromkeys([entity.get("title", ""), *entity.get("aliases", [])]))


def teammate_request(raw, members, actor):
    """A direct request is speech until the addressee submits their own event."""
    for mid, name in members.items():
        if mid == actor:
            continue
        if re.search(r"^\s*" + re.escape(name) + r"[，,:：]\s*(?:请|你|帮|把|打开|关|拿|用)", raw):
            return mid
        if re.search(r"(?:请|让|叫)" + re.escape(name) + r".{0,8}(?:打开|关|拿|用|帮)", raw):
            return mid
    return None


def freeze_action(plan, raw, actor, seq, scene, entities, runtime, members):
    action = plan.focus.action if plan.focus else ""
    target = plan.focus.action_target_id if plan.focus else plan.parsed_intent.target_id
    request = teammate_request(raw, members, actor)
    kinds = action_kinds(action) if action and action in raw and not request else []
    if plan.parsed_intent.type == "observe":
        kinds = [k for k in kinds if k in {"observe", "light"}]
    held = {eid: instance for eid in entities if (instance := held_instance(runtime, eid, actor))}
    named = {eid for eid, e in entities.items() if any(a and a in action for a in aliases(e))}
    instances = {
        eid: instance
        for eid, instance in held.items()
        if eid in named or not named and eid == target
    }
    return {
        "actor_member_id": actor,
        "source_event_seq": seq,
        "scene_node_id": scene,
        "intent_type": plan.parsed_intent.type,
        "target_id": target,
        "action": action,
        "utterance": raw,
        "clauses": [c.strip() for c in re.split(r"[，。；,;.!\n]", raw) if c.strip()],
        "kinds": kinds,
        "item_instances": instances,
        "held_instances": held,
        "named_item_ids": [eid for eid in named if entities[eid].get("type") == "item"],
        "named_entity_ids": sorted(named),
        "named_recipients": [
            mid
            for mid, name in members.items()
            if mid != actor and name in action and "give" in kinds
        ],
        "request_member_id": request,
    }


def required_kinds(rule):
    if rule.action_kinds:
        return set(rule.action_kinds)
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
    allowed = required_kinds(rule)
    return bool(
        selected
        and selected in authority.get("action", "")
        and (
            not allowed or allowed.intersection(authority.get("kinds", []), action_kinds(selected))
        )
    )


def authority_error(
    authority, rule, runtime, *, actor, seq, scene, item_id=None, recipient=None, check_facts=True
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
    allowed = required_kinds(rule)
    if allowed and not allowed.intersection(authority.get("kinds", [])):
        return "实际动作不授权此操作"
    if (
        rule.inventory_operation in {"initial", "recover"}
        and authority.get("named_item_ids")
        and rule.item_id not in authority["named_item_ids"]
    ):
        return "查找物品与本次原话指定的对象不一致"
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
        if not frozen or held_instance(runtime, eid, actor) != frozen:
            return "所用物品实例与本次实际动作不一致"
    item_ids = set()
    if rule.inventory_operation in {"give", "drop", "consume"}:
        item_ids.add(rule.item_id)
    if rule.encounter_operation == "sound_once" and item_id:
        item_ids.add(item_id)
    for eid in item_ids:
        frozen = authority.get("item_instances", {}).get(eid)
        if not frozen or held_instance(runtime, eid, actor) != frozen:
            return "所用物品实例与本次实际动作不一致"
    if rule.encounter_operation == "sound_once" and not item_id:
        if not rule.allow_worn_sound_item or not re.search(
            r"鞋|衣|帽|\b(?:shoe|coat|hat)\b", authority["action"], re.I
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
