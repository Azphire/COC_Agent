"""Authorised transfers of prepared item identities, within the current room."""

import re

from app.rooms.service import require


def held_instance(runtime, entity_id, actor, instance_id=None):
    matches = [
        key
        for key, holder in runtime.inventory.items()
        if holder == actor and runtime.item_instances.get(key, key) == entity_id
    ]
    if instance_id is not None:
        return instance_id if instance_id in matches else None
    return matches[0] if len(matches) == 1 else None


def acquisition_error(runtime, eid, actor):
    """First acquisition cannot recreate an instance that moved or was consumed."""
    instances = {key for key, value in runtime.item_instances.items() if value == eid} | {eid}
    if instances & runtime.consumed_items.keys():
        return "物品已经消耗，不能重新取得"
    if instances & runtime.dropped_items.keys():
        return "放下的物品须在实际位置拾回"
    holders = {runtime.inventory[i] for i in instances if i in runtime.inventory}
    if holders - {actor}:
        return "物品已有持有者，需要由持有者实际交接"
    if holders:
        return "你已经持有这件物品"
    return None


def acquire_instance(runtime, eid, actor):
    from app.rooms.service import require

    error = acquisition_error(runtime, eid, actor)
    # Same holder repeating a request is a no-op, never a second instance.
    if held_instance(runtime, eid, actor):
        return
    require(not error, error or "无法取得物品")
    runtime.item_instances[eid] = eid
    runtime.inventory[eid] = actor


def guard_unavailable_transfer(plan, facts, runtime, members):
    """Explain known possession without reopening an unrelated investigation."""
    authority = plan.action_authority
    target = authority.get("target_id")
    entity = facts.approved_entities.get(target, {})
    if (
        authority.get("request_member_id")
        or entity.get("type") != "item"
        or target not in facts.revealed_entity_ids
        or not set(authority.get("kinds", [])) & {"take", "give", "place"}
    ):
        return False
    instances = {i for i, eid in runtime.item_instances.items() if eid == target} | {target}
    actor = facts.actor_member_id
    holders = {runtime.inventory[i] for i in instances if i in runtime.inventory}
    local_drop = any(runtime.dropped_items.get(i) == facts.scene_id for i in instances)
    reason = None
    if "take" in authority["kinds"] and actor in holders and not local_drop:
        reason = f"你已经持有{entity['title']}，无需再次拿取。"
    elif holders and actor not in holders and not local_drop:
        names = "、".join(members.get(h, "其他人") for h in sorted(holders))
        reason = f"{entity['title']}目前由{names}持有，需要持有者实际交接。"
    elif instances & runtime.consumed_items.keys() and not holders and not local_drop:
        reason = f"{entity['title']}已经消耗，不能再次拿取。"
    elif instances & runtime.dropped_items.keys() and not holders and not local_drop:
        reason = f"{entity['title']}放在其他场景，需要回到放下的位置拾取。"
    if not reason:
        return False
    plan.focus.obstacle = reason
    plan.needs_clarification = plan.parsed_intent.requires_clarification = True
    plan.parsed_intent.clarification_question = reason
    plan.proposed_check = None
    plan.proposed_tool_calls = []
    plan.proposed_reveal_entity_ids = []
    plan.proposed_transition_id = None
    authority["rejection_code"] = "inventory_state"
    authority["rejection_message"] = reason
    return True


def light_state(entity, flags):
    """Public device state derived from existing approved operation flags."""
    rules = [
        rule
        for rule in entity.get("interactions", [])
        if "light" in rule.get("action_kinds", []) and any(rule.get("set_flags", {}).values())
    ]
    if not rules:
        return {}
    return {
        "light_active": any(
            all(flags.get(key, False) == value for key, value in rule["set_flags"].items())
            for rule in rules
        )
    }


def instance_light_state(entity_id, instance, runtime, definitions):
    """Disambiguate shared rule flags using actual existing operation receipts."""
    entity = definitions.get(entity_id, {})
    state = light_state(entity, runtime.get("flags", {}))
    same = {
        key
        for key in {*runtime.get("inventory", {}), *runtime.get("dropped_items", {})}
        if runtime.get("item_instances", {}).get(key, key) == entity_id
    }
    if not state.get("light_active") or len(same) <= 1:
        return state
    active = {}
    seen = False
    for receipt in sorted(
        runtime.get("receipts", {}).values(), key=lambda r: r.get("source_event_seq") or 0
    ):
        eid = receipt.get("entity_id")
        rule = next(
            (
                r
                for r in definitions.get(eid, {}).get("interactions", [])
                if r["id"] == receipt.get("interaction_id")
            ),
            {},
        )
        if "light" not in rule.get("action_kinds", []) or rule.get("check_name"):
            continue
        seen = seen or eid == entity_id
        ruling = receipt.get("kp_ruling") or {}
        selected = ruling.get("action_authority", {}).get("item_instances", {}).get(eid)
        held = [
            key
            for key, actor in receipt.get("inventory", {}).items()
            if actor == receipt.get("actor_member_id")
            and runtime.get("item_instances", {}).get(key, key) == eid
        ]
        selected = selected or (held[0] if len(held) == 1 else None)
        for flag, value in rule.get("set_flags", {}).items():
            if not value:
                active[flag] = set()
            elif selected:
                active.setdefault(flag, set()).add(selected)
    if not seen:
        return {"light_active": None}
    return {
        "light_active": any(
            instance in active.get(flag, set())
            for rule in entity.get("interactions", [])
            if "light" in rule.get("action_kinds", [])
            for flag, value in rule.get("set_flags", {}).items()
            if value
        )
    }


async def public_inventory(agents, session, room):
    runtime = room.session_state.get("module_runtime", {})
    visible = {e["id"]: e for e in await agents.entities.public(session, room.id)}
    members = {m.id: m.display_name for m in await agents.rooms.members(session, room)}
    definitions = {
        e.source_entity_id: e.snapshot for e in await agents.entities.rows(session, room.id)
    }
    return [
        {
            "instance_id": eid,
            "item_id": runtime.get("item_instances", {}).get(eid, eid),
            "title": visible[runtime.get("item_instances", {}).get(eid, eid)]["title"],
            "holder_id": holder,
            "holder_name": members.get(holder, "未知持有者"),
            **instance_light_state(
                runtime.get("item_instances", {}).get(eid, eid),
                eid,
                runtime,
                definitions,
            ),
            "remaining_uses": runtime.get("item_uses", {}).get(
                eid,
                definitions.get(runtime.get("item_instances", {}).get(eid, eid), {}).get(
                    "item_uses"
                ),
            ),
        }
        for eid, holder in runtime.get("inventory", {}).items()
        if runtime.get("item_instances", {}).get(eid, eid) in visible
    ]


async def inventory_context(agents, session, room, question=""):
    """Explicit emptiness and settled belongings, with the same public visibility."""
    from app.preparation.dialogue import public_npc_name

    runtime = room.session_state.get("module_runtime", {})
    held = await public_inventory(agents, session, room)
    visible = {e["id"] for e in await agents.entities.public(session, room.id)}
    initial = runtime.get("initial_belongings", {})
    chosen = {v.get("chosen_item_id") for v in initial.values()}
    definitions = await agents.entities.rows(session, room.id)
    snapshots = {r.source_entity_id: r.snapshot for r in definitions}
    items = []
    for row in definitions:
        e = row.snapshot
        if row.entity_type != "item":
            continue
        names = [e["title"], *e.get("aliases", [])]
        # Named searches may discuss an item, but do not reveal hidden contents.
        if row.source_entity_id not in visible | chosen and not any(n in question for n in names):
            continue
        items.append({"id": row.source_entity_id, "names": names})
    members = []
    for slot in await agents.rooms.slots(session, room):
        if not slot.member_id:
            continue
        start = initial.get(slot.member_id)
        members.append(
            {
                "id": slot.member_id,
                "name": slot.public_summary.get("name", ""),
                "held": [h["instance_id"] for h in held if h["holder_id"] == slot.member_id],
                "starting_belongings": "not_retained"
                if start and not start.get("item_ids")
                else "settled"
                if start
                else "undetermined",
                **({"source_event_seq": start.get("source_event_seq")} if start else {}),
                **(
                    {
                        "starting_items": [
                            snapshots[e]["title"]
                            for e in start.get("item_ids", [])
                            if e in snapshots
                        ]
                    }
                    if start and set(start.get("item_ids", [])) <= visible
                    else {}
                ),
            }
        )
    return {
        "complete": True,
        "holders": held,
        "members": members,
        "known_items": items,
        "other_actors": [
            {
                "id": row.source_entity_id,
                "names": [
                    row.snapshot["title"],
                    *row.snapshot.get("aliases", []),
                    *([name] if name else []),
                ],
            }
            for row in definitions
            if row.entity_type == "npc"
            for name in [public_npc_name(row.snapshot, room.session_state.get("scene_summary", ""))]
            if row.source_entity_id in visible or name
        ],
        "dropped_items": [
            {
                "instance_id": instance,
                "item_id": runtime.get("item_instances", {}).get(instance, instance),
                "title": snapshots[runtime.get("item_instances", {}).get(instance, instance)][
                    "title"
                ],
                "scene_node_id": node,
                "remaining_uses": runtime.get("item_uses", {}).get(instance),
                **instance_light_state(
                    runtime.get("item_instances", {}).get(instance, instance),
                    instance,
                    runtime,
                    snapshots,
                ),
            }
            for instance, node in runtime.get("dropped_items", {}).items()
            if runtime.get("item_instances", {}).get(instance, instance) in visible
        ],
        "rule": "held为空即当前未持有登记道具；已知、未确定均不等于持有。穿戴物按批准交互处理。",
    }


def initial_inventory_summary(name, titles):
    return (
        name
        + "的起始随身物检查已结算，"
        + ("实际保留：" + "、".join(titles) + "。" if titles else "没有保留下随身物品。")
    )


def recalled_inventory(view, question):
    # A quoted note or NPC statement about an item is not a question about
    # possession. Resolve each clause before reserving current/initial facts.
    clauses = re.split(r"[?？;；，,。]", question)
    initial = r"(?:起始|起初|最初|醒来|开场|一开始|最开始)"
    inventory_clauses = [
        clause
        for clause in clauses
        if inventory_probe(clause)
        or re.search(r"物品|持有|库存|背包|身上|随身物", clause)
        or (
            re.search(
                r"(?:^|回顾)(?:我|我们|两人|大家).{0,8}(?:是否|有没有).{0,6}"
                r"(?:拿到|取得|获得|得到)",
                clause.strip(),
            )
            and not re.search(r"台词|原文|原话|写|说", clause)
        )
        or re.search(initial + r".{0,20}(?:保留|留下)", clause)
        or (
            re.search(
                initial + r".{0,20}(?:各有|都有|人手|带着|带了|携带|备有|拿着)",
                clause,
            )
            and not re.search(r"便签|纸条|线索|文字|台词|原文|写|说", clause)
        )
        or (
            any(n in clause for item in view.get("known_items", []) for n in item["names"])
            and re.search(
                r"谁|手里|手中|保管|拿着|带着|带了|有没有|还有|获得|得到|交接|交给|递给|转交",
                clause,
            )
        )
    ]
    device_question = re.search(r"照明|屏幕|开关|亮着|开启|打开|关掉|关闭", question)
    if not inventory_clauses and not device_question:
        return []
    descriptions = []
    for member in view.get("members", []):
        held = [
            h["title"]
            + (
                "（照明实例状态尚未确定）"
                if h["light_active"] is None
                else "（照明已开启）"
                if h["light_active"]
                else "（照明未开启）"
            )
            if "light_active" in h
            else h["title"]
            for h in view["holders"]
            if h["holder_id"] == member["id"]
        ]
        descriptions.append(
            member["name"] + ("当前持有：" + "、".join(held) if held else "当前没有登记的持有物")
        )
    records = [
        dict(
            id="state:inventory",
            kind="current_state",
            scope="current",
            source_event_seq=None,
            source="current_inventory_projection",
            text="；".join(descriptions) + "。",
        )
    ]
    if re.search(initial + r"|口袋|随身物|保留|留下", "，".join(inventory_clauses)):
        records += [
            dict(
                id="state:initial:" + m["id"],
                kind="result",
                scope="historical",
                source_event_seq=None,
                action_event_seq=m.get("source_event_seq"),
                source="settled_initial_belongings_projection",
                text=initial_inventory_summary(m["name"], m["starting_items"]),
            )
            for m in view.get("members", [])
            if "starting_items" in m
        ]
    return records


def inventory_reply(view, actor):
    """Answer a failed possession proposal from current state, without acting."""
    held = [h["title"] for h in view.get("holders", []) if h["holder_id"] == actor]
    if held:
        return "我目前持有：" + "、".join(held) + "。其他物品不能当作已经拿到。"
    member = next((m for m in view.get("members", []) if m["id"] == actor), {})
    if member.get("starting_belongings") == "undetermined":
        return "我的随身物还没有确认，现在不能把它当作已经保留下来了。"
    return "我目前没有已确认可用的随身物品，需要寻找其他办法。"


def inventory_probe(text):
    """Recognize checking one's belongings, not a search of a dropped world object."""
    if re.search(
        r"(?:有什么|有哪些|带着什么|带了什么).{0,8}(?:物品|道具|东西)",
        text,
    ) and not re.search(r"办法|建议|想法|计划|如果|假如", text):
        return True
    if re.search(r"地上|散落|遗落|失落|丢在", text) and not re.search(r"自己|我的|你的", text):
        return False
    return bool(
        re.search(r"口袋|衣袋|随身物|随身东西|(?:我|你|自己).{0,5}(?:身上|背包)", text)
        and re.search(
            r"检查|查看|看看|观察|确认|摸|翻|有什么|还有|找到|发现|拿出|取出|带着|带了", text
        )
    )


def requested_handover(text, view, actor, requester):
    """Bind an agreed handover to one named held instance and an actual recipient."""
    from app.preparation.action_authority import mentions_alias, requested_action_kinds

    clauses = [c for c in re.split(r"[，。；,;.\n]", text) if "give" in requested_action_kinds(c)]
    request = "，".join(clauses)
    recipients = {
        m["id"]
        for m in view.get("members", [])
        if m["id"] != actor
        and m.get("name")
        and re.search(r"(?:给|到)" + re.escape(m["name"]), request)
    }
    if requester and requester != actor and re.search(r"(?:给|到)我", request):
        recipients.add(requester)
    named = {
        i["id"]
        for i in view.get("known_items", [])
        if any(mentions_alias(request, n) for n in i["names"])
    }
    held = [h for h in view.get("holders", []) if h["holder_id"] == actor and h["item_id"] in named]
    names = {m["id"]: m["name"] for m in view.get("members", [])}
    if len(recipients) != 1 or len(held) != 1:
        return None
    recipient = next(iter(recipients))
    if recipient not in names:
        return None
    return {**held[0], "action_text": f"我把{held[0]['title']}交给{names[recipient]}。"}


def asserted_item_uses(text, view, actor):
    """Bind concrete possession/use clauses, while allowing questions and plans to search."""
    uses = []
    # Questions may discuss a named person's item without asserting ownership.
    text = re.sub(r"[^，。；！？,;!?\n]*[？?]", "", text)
    for clause in re.split(r"[，。；！？,;!?\n]|但是|不过|然而|可是|但", text):
        if re.search(
            r"没有|没带|未持有|不在|尚未|找不到|不见了|丢了|遗失|如果|假如|要是|能否|有没有|是否"
            r"|在哪|哪里|如何|怎样|为什么|吗|呢",
            clause,
        ):
            continue
        holder = next(
            (
                m["id"]
                for m in [*view.get("members", []), *view.get("other_actors", [])]
                for name in m.get("names", [m.get("name")])
                if name and clause.lstrip().startswith(name)
            ),
            actor,
        )
        for item in view.get("known_items", []):
            for name in item["names"]:
                n = re.escape(name)
                owner = next(
                    (
                        person["id"]
                        for person in [*view.get("members", []), *view.get("other_actors", [])]
                        for person_name in person.get("names", [person.get("name")])
                        if person_name and re.search(re.escape(person_name) + rf"的{n}", clause)
                    ),
                    None,
                )
                if owner:
                    if re.search(r"提到|提及|说起|讨论|谈论|关于|询问|想问", clause):
                        break
                    uses.append((item["id"], owner))
                    break
                if (
                    re.search(
                        rf"^\s*(?:那)?我(?:现在|这就|先)?(?:把|将).{{0,8}}{n}.{{0,8}}(?:交给|递给|交出|交到|递到|还给|归还)",
                        clause,
                    )
                    or re.search(
                        rf"(?:用|使用|举起|举着|拿着|掏出|拿出|取出|打开|点亮|接过|递给|交出|交给|带了|带着|持有|我的|手里的).{{0,8}}{n}|{n}.{{0,8}}(?:照亮|照明|照射|照向|光束|灯光|亮起|在手|在身上)",
                        clause,
                    )
                    or re.search(
                        rf"{n}.{{0,8}}(?:(?:在|装在|放在|留在)(?:我|自己|我的|自己的)?"
                        r"(?:口袋|背包|衣袋|身上|手里|包里)|(?:还在|仍在|没丢|没弄丢)$)",
                        clause,
                    )
                    or re.search(
                        rf"(?:我|自己)(?:身上|口袋里|包里)?(?:还|仍)?(?:有|留着|带着)"
                        rf"(?:一(?:个|部|支|盏)|个|部|支|盏)?{n}",
                        clause,
                    )
                ):
                    uses.append((item["id"], holder))
                    break
    return list(dict.fromkeys(uses))


def bind_item_prose(text, view, actor):
    for clause in re.split(r"[，。；！？,;!?\n]", text):
        if re.search(r"没有|不用|不要|未持有|如果|假如|能否|是否|有没有", clause):
            continue
        instrument = re.search(
            r"(?:用|使用|借助|拿着|拿出|取出|举着)([^，。；！？,;!?]{1,16}?)"
            r"(?:撬|照明|照亮|敲|砸|切|剪|捆|划|点火|开锁|打开|修理)",
            clause,
        )
        generic = re.search(
            r"(?:用|使用|借助|拿着|拿出|举着).{0,8}(?:工具|道具|器具|照明设备)", clause
        )
        if instrument and re.fullmatch(
            r"(?:我|自己|的|一只|双)?(?:手|脚|拳头|肩膀|身体|肘部|衣服|衣袖|鞋|力)(?:的|布条)?",
            instrument[1],
        ):
            continue
        if instrument or generic:
            named = asserted_item_uses(clause, view, actor)
            require(
                any(
                    h["item_id"] == eid and h["holder_id"] == holder
                    for eid, holder in named
                    for h in view.get("holders", [])
                ),
                "使用工具前必须指定实际持有的物品，不能凭空补出工具",
                422,
            )
    instances = []
    for eid, holder in asserted_item_uses(text, view, actor):
        owned = [
            h["instance_id"]
            for h in view.get("holders", [])
            if h["item_id"] == eid and h["holder_id"] == holder
        ]
        explicit = [i for i in owned if i in text]
        require(len(explicit or owned) == 1, "叙述或行动使用了未持有或未指定实例的物品", 422)
        instances.extend(explicit or owned)
    return list(dict.fromkeys(instances))


def held_item_acknowledgement(text, view, actor):
    """A receipt acknowledgement of an already-held item creates no new operation."""
    from app.preparation.action_authority import action_kinds

    # Past receipt verbs do not authorize another transfer. Remove only the
    # completed verb, so a following independent operation still remains.
    completed_view = re.sub(r"(?:已经|已)(?:安全)?(?:交给|收到|接过|保管|收好)", "", text)
    if (
        re.search(r"接过|收到|保管|收好", text)
        and not re.search(r"没有|尚未|并未|如果|假如|不想|不愿", text)
        and not action_kinds(completed_view)
    ):
        named = {
            item["id"]
            for item in view.get("known_items", [])
            if any(name and name in text for name in item["names"])
        }
        if named and all(
            len(
                [
                    h
                    for h in view.get("holders", [])
                    if h["item_id"] == eid and h["holder_id"] == actor
                ]
            )
            == 1
            for eid in named
        ):
            return True
    clauses = re.split(r"[，。；,;\n]", text)
    completed = bool(
        re.search(r"(?:确认|知道|明白).{0,30}(?:已|已经)(?:交给|收到|接过)", clauses[0])
    )
    if completed and not action_kinds("，".join(clauses[1:])):
        owned = {h["item_id"] for h in view.get("holders", []) if h["holder_id"] == actor}
        return any(
            item["id"] in owned and any(name and name in text for name in item["names"])
            for item in view.get("known_items", [])
        )
    if not re.search(r"接过|收到|保管|收好", text) or action_kinds(text):
        return False
    from app.rooms.service import RoomError

    try:
        return bool(bind_item_prose(text, view, actor))
    except RoomError:
        return False


async def apply_inventory(agents, session, room, state, rule, args, actor, node, seq, checks):
    runtime, op = state.module_runtime, rule.inventory_operation
    if not op:
        return
    eid = rule.item_id or args.entity_id
    item = await agents.entities.entity(session, room.id, eid)
    require(
        item.entity_type == "item" and (item.state != "hidden" or op in {"initial", "recover"}),
        "物品尚未实际发现",
    )
    instance = held_instance(runtime, eid, actor, args.item_instance_id)
    if op in {"give", "drop", "consume"}:
        require(instance, "交出、放下或消耗只能来自本次行动者持有物")
    if op == "give":
        recipients = {s.member_id for s in await agents.rooms.slots(session, room) if s.member_id}
        members = {m.id for m in await agents.rooms.members(session, room) if m.active}
        require(
            args.recipient_member_id in recipients & members and args.recipient_member_id != actor,
            "接收人必须是本房间当前调查员",
        )
        runtime.inventory[instance] = args.recipient_member_id
        if instance in runtime.sounds:
            runtime.sounds[instance]["actor_id"] = args.recipient_member_id
    elif op == "drop":
        runtime.inventory.pop(instance)
        runtime.dropped_items[instance] = node
        if instance in runtime.sounds:
            runtime.sounds[instance]["actor_id"] = None
            runtime.sounds[instance]["scene_node_id"] = node
    elif op == "pickup":
        dropped = [
            key
            for key, location in runtime.dropped_items.items()
            if location == node
            and runtime.item_instances.get(key, key) == eid
            and (not args.item_instance_id or key == args.item_instance_id)
        ]
        require(
            len(dropped) == 1,
            "物品未放在当前场景或已有持有者",
        )
        runtime.dropped_items.pop(dropped[0])
        runtime.inventory[dropped[0]] = actor
        if dropped[0] in runtime.sounds:
            runtime.sounds[dropped[0]]["actor_id"] = actor
    elif op == "consume":
        require(rule.consume_amount == 1, "此独立物品只有一件可消耗")
        runtime.inventory.pop(instance)
        runtime.consumed_items[instance] = runtime.consumed_items.get(instance, 0) + 1
        if instance in runtime.sounds:
            runtime.sounds[instance]["active"] = False
            from app.preparation.encounters import end_transient_sound

            end_transient_sound(state, node)
    elif op == "recover":
        initial = runtime.initial_belongings.get(actor, {})
        require(
            initial.get("chosen_item_id") == eid and not initial.get("item_ids"),
            "只能找回起始检定中确实遗失的本人随身物",
        )
        require(checks, "找回随身物需要本次实际幸运检定")
        instance = eid + ":" + actor
        require(instance not in runtime.item_instances, "该随身物已经找回，不能复制")
        runtime.item_instances[instance] = eid
        runtime.inventory[instance] = actor
        initial["recovered_item_id"] = instance
    elif op == "initial":
        require(actor not in runtime.initial_belongings, "起始随身物已经确定，不能重骰或重新选择")
        require(checks, "起始随身物需要实际幸运检定")
        instance = eid + ":" + actor
        require(
            instance not in runtime.inventory and instance not in runtime.consumed_items,
            "此物品已经分配",
        )
        runtime.initial_belongings[actor] = {
            "chosen_item_id": eid,
            "item_ids": [eid] if rule.check_passed else [],
            "check_ids": checks,
            "source_event_seq": seq,
        }
        from uuid import UUID

        for slot in await agents.rooms.slots(session, room):
            if slot.member_id == actor:
                state.characters[UUID(slot.id)].equipment_settlement = "module_settled"
        if rule.check_passed:
            runtime.item_instances[instance] = eid
            runtime.inventory[instance] = actor
