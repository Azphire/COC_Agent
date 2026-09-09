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
                in {
                    "clue.revealed",
                    "scene.updated",
                    "check.resolved",
                    "module.completed",
                    "entity.revealed",
                    "entity.corrected",
                }
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
    service, session, room, binding, profile, cycle, phase=None, additions=None, run_id=None
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
    prepared = await service.entities.binding(session, room.id)
    if prepared:
        public_entities = await service.entities.public(session, room.id)
        context["public_entities"] = public_entities
        context["prepared_module"] = True
        context["public_state"] = {"scene_id": prepared.current_scene}
        if cycle.state.get("review_result"):
            context["review_result"] = {"status": cycle.state["review_result"]["status"]}
        if keeper:
            actor_id = (trigger or {}).get("actor_member_id")
            context["characters"] = [
                card
                if card["member_id"] == actor_id
                else {
                    k: v
                    for k, v in card.items()
                    if k not in {"effective_attributes", "skill_values"}
                }
                for card in cards
            ]
            entities = await service.entities.host(session, room.id)
            # The same frozen summaries are already in approved_entities. Avoid
            # repeating their source metadata in the keeper's bounded context.
            context["public_entities"] = [
                {"id": e["id"], "state": e["state"]} for e in public_entities
            ]
            context["module"] = {
                "id": module.document["id"],
                "title": module.document["title"],
                "approved_entities": [
                    {
                        k: e[k]
                        for k in (
                            "id",
                            "type",
                            "title",
                            "keeper_summary",
                            "public_summary",
                            "state",
                        )
                    }
                    for e in entities
                ],
                "approved_relations": [
                    {
                        k: r[k]
                        for k in (
                            "source_entity_id",
                            "target_entity_id",
                            "relation_type",
                            "keeper_note",
                        )
                    }
                    for r in prepared.relations
                ],
            }
            for item, entity in zip(context["module"]["approved_entities"], entities, strict=True):
                conditions = {k: v for k, v in entity["reveal_conditions"].items() if v}
                if conditions:
                    item["reveal_conditions"] = conditions
                if entity["suggested_checks"]:
                    item["suggested_checks"] = entity["suggested_checks"]
            context["review_result"] = cycle.state.get("review_result")
        else:
            visible = public_module(module)
            context["module"] = {k: visible[k] for k in ("id", "title", "scene")}
            context["public_state"] = {"scene_id": prepared.current_scene}
    if narrator:
        context["profile"] = {"role": "public_narrator"}
        context["checks"] = [c for c in context["checks"] if c["visibility"] == "public"]
    for check in context["checks"]:
        if check.get("dice"):
            check["dice"] = {k: v for k, v in check["dice"].items() if k != "roll_record"}
    if prepared:
        context["checks"] = [
            {
                k: v
                for k, v in check.items()
                if k
                in {
                    "id",
                    "target_member_id",
                    "kind",
                    "name",
                    "difficulty",
                    "value",
                    "visibility",
                    "status",
                    "dice",
                    "result",
                    "clue_id",
                }
            }
            for check in context["checks"]
        ]
    # An upper bound in characters is conservative for the configured Chinese context.
    budget = min(
        service.settings.agent_context_chars,
        service.settings.model_context_limit - service.settings.model_output_limit - 1200,
    )
    if budget < 2500:
        budget = 2500
    navigation = await service.navigation.state(session, room.id)
    if navigation:
        base_size = len(
            json.dumps(
                {k: v for k, v in context.items() if k not in {"module", "public_entities"}},
                ensure_ascii=False,
            )
        )
        resolved = await service.module_context.resolve(
            session,
            room,
            "keeper" if keeper else "investigator",
            recent_action=(trigger or {}).get("payload", {}).get("text", ""),
            budget=max(700, budget - base_size - 1300),
            cycle=cycle if keeper else None,
        )
        context.update(resolved)
        if keeper:
            service.rooms.append(
                session,
                room,
                "module.context_selected",
                binding.member_id,
                {"cycle_id": cycle.id, **resolved["module_context_audit"]},
                "host_only",
            )
    if run_id:
        configured = await service.knowledge.binding(session, room.id)
        if configured and configured["enabled"]:
            evidence, _ = await service.knowledge.pre_context(
                session,
                room,
                binding,
                profile,
                run_id,
                context,
                min(
                    int(budget * 0.3),
                    max(0, budget - len(json.dumps(context, ensure_ascii=False)) - 100),
                ),
            )
            context["knowledge_enabled"] = True
            context["RULE_EVIDENCE"] = [e for e in evidence if e["source_kind"] != "module"]
            context["MODULE_EVIDENCE"] = [e for e in evidence if e["source_kind"] == "module"]
            if keeper and context.get("module_context_audit"):
                cycle.state = {
                    **cycle.state,
                    "module_fallback_mode": context["module_context_audit"]["context_mode"],
                }
            if narrator:
                from app.knowledge.service import KnowledgeContextBuilder

                options = KnowledgeContextBuilder.public_claim_options(context)
                for option in options:
                    proposed = {
                        **context,
                        "PUBLIC_CLAIM_OPTIONS": [*context.get("PUBLIC_CLAIM_OPTIONS", []), option],
                    }
                    if len(json.dumps(proposed, ensure_ascii=False)) <= budget - 400:
                        context = proposed
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
    # Stable prompt order: identity/cards/public scene/module, memories/summary/events,
    # evidence, then the current action. JSON remains explicitly reference data.
    ordered = {
        key: context[key]
        for key in (
            "role",
            "profile",
            "characters",
            "public_state",
            "module",
            "memories",
            "events",
            "checks",
            "RULE_EVIDENCE",
            "MODULE_EVIDENCE",
        )
        if key in context
    }
    ordered.update(
        {k: v for k, v in context.items() if k not in ordered and k != "triggering_action"}
    )
    ordered["triggering_action"] = context["triggering_action"]
    return await service.sanitize(session, room, ordered), all_events, selected
