"""Visibility before selection; bounded context without embeddings or hidden reasoning."""

import json
from uuid import uuid4

from sqlalchemy import select

from app.agents.modules import public_module
from app.persistence.agent_models import AgentMemory
from app.rooms.service import Identity, require


def memory_view(memory):
    return {
        key: getattr(memory, key)
        for key in (
            "id",
            "profile_id",
            "kind",
            "scope",
            "content",
            "source_event_ids",
            "salience",
            "supersedes_id",
            "active",
            "coverage_start",
            "coverage_end",
        )
    }


async def memories(session, room_id, profile_id=None, keeper=False, host=False):
    query = select(AgentMemory).where(AgentMemory.room_id == room_id, AgentMemory.active.is_(True))
    if not host:
        query = query.where(
            (AgentMemory.scope == "public")
            | ((AgentMemory.scope == "agent_private") & (AgentMemory.profile_id == profile_id))
            | ((AgentMemory.scope == "keeper_only") if keeper else False)
        )
    return list(await session.scalars(query.order_by(AgentMemory.created_at, AgentMemory.id)))


async def write_memory(session, rooms, room, binding, profile, args):
    keeper = profile.role == "keeper"
    require(
        args.scope != "public" or args.kind == "observation",
        "公开记忆必须来自权威事件，推测请保存在私有范围",
        403,
    )
    require(
        keeper or (args.scope == "agent_private" and args.kind == "belief"),
        "调查员推断只能保存为自己的私有 belief",
        403,
    )
    require(args.kind != "summary", "摘要仅由编排器维护", 403)
    events = await rooms.events(session, room, Identity(binding.member_id, keeper))
    sources = [e for e in events if e["seq"] in args.source_event_ids]
    require(len(sources) == len(set(args.source_event_ids)), "记忆来源不存在或不可见", 403)
    if args.scope == "public":
        require(
            all(e["visibility"] == "public" for e in sources), "私有来源不能发布为公开记忆", 403
        )
    if args.kind == "observation":
        require(
            sources
            and all(
                e["type"]
                in {"clue.revealed", "scene.updated", "check.resolved", "module.completed"}
                for e in sources
            ),
            "世界事实必须来自权威事件",
            422,
        )
        # Model wording is never promoted into a world fact, even with a valid citation.
        content = json.dumps(
            [{"type": e["type"], "payload": e["payload"]} for e in sources], ensure_ascii=False
        )
    else:
        content = args.content
    previous = None
    if args.supersedes_id:
        previous = await session.get(AgentMemory, str(args.supersedes_id))
        require(
            previous is not None
            and previous.room_id == room.id
            and previous.profile_id == binding.profile_id
            and previous.scope == args.scope,
            "不能替代其他 Agent 或范围的记忆",
            403,
        )
        previous.active = False
    memory = AgentMemory(
        id=str(uuid4()),
        room_id=room.id,
        profile_id=binding.profile_id,
        kind=args.kind,
        scope=args.scope,
        content=content,
        source_event_ids=sorted(set(args.source_event_ids)),
        salience=args.salience,
        supersedes_id=previous.id if previous else None,
        active=True,
    )
    session.add(memory)
    return memory


async def build_context(
    service, session, room, binding, profile, cycle, phase=None, additions=None
):
    phase = phase or cycle.state["current_node"]
    narrator = phase == "narrate_publicly"
    keeper = profile.role == "keeper" and not narrator
    identity = Identity(binding.member_id, keeper)
    all_events = await service.rooms.events(session, room, identity)
    if narrator:
        all_events = [e for e in all_events if e["visibility"] == "public"]
    # Credential/administrative events have no gameplay value. Filtering precedes truncation.
    all_events = [
        e
        for e in all_events
        if not e["type"].startswith(("snapshot.", "invite."))
        and (
            not e["type"].startswith("agent.")
            or e["type"] in {"agent.spoke", "agent.action_proposed"}
        )
    ]
    visible_memories = await memories(session, room.id, binding.profile_id, keeper)
    if narrator:
        visible_memories = [m for m in visible_memories if m.scope == "public"]
    module = await service.module(session, room.id)
    slots = await service.rooms.slots(session, room)
    cards = []
    for slot in slots:
        if narrator:
            cards.append({"member_id": slot.member_id, **slot.public_summary})
        elif keeper or slot.member_id == binding.member_id:
            card = slot.character_snapshot
            cards.append(
                {
                    "member_id": slot.member_id,
                    "slot_id": slot.id,
                    **{
                        k: card[k]
                        for k in ("name", "occupation", "effective_attributes", "skill_values")
                    },
                }
            )
    trigger = next((e for e in all_events if e["seq"] == cycle.state["triggering_event_seq"]), None)
    text = str(trigger["payload"] if trigger else "")
    ranked = sorted(
        visible_memories,
        key=lambda m: (
            m.kind in {"goal", "summary"},
            m.salience,
            sum(word in m.content for word in text.split() if len(word) > 1),
            str(m.created_at),
        ),
        reverse=True,
    )
    context = {
        "role": profile.role,
        "profile": {
            k: v for k, v in profile.document.items() if k not in {"id", "created_at", "updated_at"}
        },
        "module": module.document if keeper else public_module(module),
        "public_state": module.state if keeper else public_module(module),
        "characters": cards,
        "triggering_action": trigger,
        "memories": [],
        "events": [],
        "checks": await service.check_views(session, room, identity, cycle.id),
        "phase": phase,
        **(additions or {}),
    }
    if narrator:
        context["profile"] = {"role": "public_narrator"}
        context["checks"] = [c for c in context["checks"] if c["visibility"] == "public"]
    for check in context["checks"]:
        if check.get("dice"):
            check["dice"] = {k: v for k, v in check["dice"].items() if k != "roll_record"}
    # An upper bound in characters is conservative for the configured Chinese context.
    budget = min(
        service.settings.agent_context_chars,
        service.settings.model_context_limit - service.settings.model_output_limit - 1200,
    )
    if budget < 2500:
        budget = 2500
    selected = []
    for memory in ranked:
        candidate = memory_view(memory)
        proposed = {**context, "memories": [*context["memories"], candidate]}
        if len(json.dumps(proposed, ensure_ascii=False)) > budget - 1200:
            continue
        context["memories"].append(candidate)
        selected.append(memory.id)
    window = all_events[-service.settings.agent_event_window :]
    for event in reversed(window):
        proposed = {**context, "events": [event, *context["events"]]}
        if len(json.dumps(proposed, ensure_ascii=False)) <= budget:
            context["events"].insert(0, event)
    require(
        len(json.dumps(context, ensure_ascii=False)) <= budget,
        "当前模组和角色超过上下文预算，请提高上下文限制或减少席位",
        422,
    )
    return await service.sanitize(session, room, context), all_events, selected
