"""Shared story selection. Input must already be filtered for the reader's permissions."""

import re
from collections import Counter


def published_incidental_details(details, text):
    """Extract short verbatim quotes; a draft field is never publication."""
    return list(
        dict.fromkeys(
            d.strip()
            for d in details
            if isinstance(d, str) and 0 < len(d.strip()) <= 160 and d.strip() in text
        )
    )[:2]


def incidental_records(event):
    payload = event["payload"]
    if (
        event.get("visibility") != "public"
        or event["type"] not in {"keeper.narration", "npc.spoke"}
        or payload.get("safe_fallback")
        or payload.get("incidental_source") != "kp_improvisation"
    ):
        return []
    return [
        {
            "text": detail,
            "source": "kp_improvisation",
            "source_event_seq": event["seq"],
            "speaker_id": payload.get("entity_id") or event.get("actor_member_id"),
            "speaker": payload.get("actor_name") or "KP",
            "scene_id": payload.get("scene_id"),
        }
        for detail in published_incidental_details(
            payload.get("incidental_details", []), payload.get("text", "")
        )
        if not re.search(
            r"写着|写道|写有|上写|文字是|内容是|"
            r"(?:没有|看不到|未见|没任何).{0,10}(?:文字|字迹|记号)|"
            r"(?:背面|纸面).{0,10}空白|(?:纸条|便签|信件|报纸).{0,10}(?:内容|号码|数字)",
            detail,
        )
    ]


def relevant_incidental_memories(visible_events, query, scene_id, *, limit=6, budget=1400):
    """Retrieve public quotes on the active story branch, independently of summaries."""
    events, _ = story_events(visible_events)
    from app.memory.facts import fact_records
    from app.module_ir.facts import nonlocal_source_claims, recalling
    from app.preparation.action_authority import action_kinds, declared_action

    observing_here = bool(
        declared_action(query) and set(action_kinds(query)) & {"observe", "search"}
        and not recalling(query)
    )
    sources = [
        r for r in fact_records(visible_events)
        if r["kind"] == "source_text" and r.get("source_type") in {"clue", "location"}
        and r.get("scene_id")
    ]

    def terms(text):
        # Chinese questions rarely contain spaces. Bigrams also find short
        # follow-ups without introducing an embedding model or another store.
        tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
        return {t for t in tokens if len(t) > 1} | {
            t[i : i + 2] for t in tokens for i in range(len(t) - 1)
        }

    wanted = terms(query)
    ranked = []
    for event in events:
        for record in incidental_records(event):
            current = bool(scene_id and record["scene_id"] == scene_id)
            if observing_here and not current:
                continue
            located = [
                {"id": source["id"], "type": source["source_type"],
                 "public_summary": source["text"],
                 "fact_scope": "current_scene" if source["scene_id"] == record["scene_id"]
                 else "historical"}
                for source in sources if source["source_event_seq"] < event["seq"]
            ] if record["scene_id"] else []
            if event["type"] == "keeper.narration" and nonlocal_source_claims(
                event["payload"].get("text", ""), located
            ):
                continue  # Rebuild the projection without rewriting the false original log.
            score = len(wanted & terms(record["text"] + record["speaker"]))
            if not current and not score:
                continue
            record["fact_scope"] = "current_scene" if current else "historical"
            ranked.append((score, current, event["seq"], record))
    selected, used = [], 0
    import json

    for _, _, _, record in sorted(ranked, key=lambda r: r[:3], reverse=True):
        size = len(json.dumps(record, ensure_ascii=False))
        if used + size > budget:
            continue
        selected.append(record)
        used += size
        if len(selected) >= limit:
            break
    return selected


STORY_TYPES = {
    "module.interaction",
    "combat.started",
    "combat.ended",
    "combat.action_created",
    "combat.action_submitted",
    "combat.resolved",
    "combat.receipt",
    "combat.damage_confirmed",
    "check.choice_made",
    "check.rolled",
    "check.luck_spent",
    "check.push_requested",
    "check.push_reviewed",
    "check.consequence_pending",
    "check.consequence_applied",
    "sanity.progressed",
    "sanity.involuntary",
    "sanity.state_changed",
    "action.submitted",
    "action.clarification_requested",
    "keeper.narration",
    "npc.spoke",
    "agent.spoke",
    "agent.action_proposed",
    "agent.needs_host_ruling",
    "chat.message",
    "check.requested",
    "check.resolved",
    "check.cancelled",
    "dice.rolled",
    "entity.revealed",
    "entity.corrected",
    "clue.revealed",
    "scene.updated",
    "module.completed",
}
INITIAL_TYPES = {"entity.revealed", "entity.corrected", "clue.revealed", "scene.updated"}


def public_accounts(events, member_id, names, entities=()):
    """A bounded, attributed projection. Never rewrite the original event log."""
    actions = {}
    rows = []
    latest = {}
    for event in events:
        p, kind = event["payload"], event["type"]
        actor = p.get("actor_id") or p.get("target_member_id") or event.get("actor_member_id")
        if kind in {"action.submitted", "agent.action_proposed"}:
            actions[p.get("cycle_id")] = actor
        if kind not in {"action.submitted", "agent.action_proposed", "agent.spoke", "npc.spoke",
                        "check.resolved", "combat.resolved", "module.interaction",
                        "entity.corrected"}:
            continue
        if kind in {"check.resolved", "combat.resolved", "module.interaction"}:
            actor = (p.get("actor_id") or p.get("target_member_id")
                     or actions.get(p.get("cycle_id"), actor))
        if kind == "npc.spoke":
            actor = p.get("entity_id", actor)
        text = p.get("text") or p.get("display_text") or p.get("summary") or p.get("public_summary")
        if not text:
            continue
        category = "attempt" if kind in {"action.submitted", "agent.action_proposed"} else (
            "judgment" if kind == "agent.spoke"
            else "testimony" if kind == "npc.spoke" else "result"
        )
        topics = {e["id"] for e in entities if any(
            n and n in text for n in [e.get("title"), *e.get("aliases", [])]
        )}
        row = {"seq": event["seq"], "actor_id": actor,
               "speaker": p.get("actor_name") or names.get(actor, actor),
               "ownership": "self" if actor == member_id else "other",
               "kind": category, "text": text[:500], "status": "current"}
        if category == "judgment":
            row["status"] = "unverified_opinion"
        for old, old_topics in rows:
            if topics & old_topics and category in {"testimony", "result"} and (
                old["kind"] == "judgment"
                or old["kind"] == "testimony" and actor == old["actor_id"]
            ):
                old.update(status="historical_superseded", superseded_by=event["seq"])
        if category == "testimony":
            latest[actor] = row
        rows.append((row, topics))
    # Keep recent receipts/attempts plus the latest NPC testimony even when the
    # current request is for a teammate; it can correct that teammate's opinion.
    selected = [r for r, _ in rows[-8:]]
    selected += [r for r in latest.values() if r not in selected]
    return selected[-10:]


def epistemic_event(event):
    kind = event["type"]
    return {
        **event,
        **({"incidental_sources": incidental_records(event)} if incidental_records(event) else {}),
        "epistemic_status": (
            "intent_not_result"
            if kind in {"action.submitted", "agent.action_proposed"}
            else "attributed_testimony"
            if kind in {"npc.spoke", "agent.spoke", "chat.message"}
            else "narration_not_evidence"
            if kind == "keeper.narration"
            else "authoritative_result"
        ),
    }


def story_events(visible_events, *, include_initial_reveals=False):
    selected, excluded = [], Counter()
    # A later load can select a snapshot made before an intervening rewind.
    # Save each requested original prefix; filtering the current branch cannot
    # bring back records removed by an earlier snapshot load.
    sources = sorted(
        {
            e["payload"]["source_seq"]
            for e in visible_events
            if e["type"] == "snapshot.loaded"
            and isinstance(e["payload"].get("source_seq"), int)
            and e["payload"]["source_seq"] < e["seq"]
        }
    )
    prefixes, source_index = {}, 0
    started = False
    previous_type = None
    for event in visible_events:
        while source_index < len(sources) and sources[source_index] < event["seq"]:
            prefixes[sources[source_index]] = list(selected)
            source_index += 1
        kind, payload = event["type"], event["payload"]
        if kind == "game.started" or kind == "action.submitted" or payload.get("cycle_id"):
            started = True
        if kind == "snapshot.loaded":
            source = payload.get("source_seq")
            if isinstance(source, int):
                retained = list(prefixes.get(source, [e for e in selected if e["seq"] <= source]))
                retained_ids = {e["seq"] for e in retained}
                excluded["abandoned_after_snapshot"] += sum(
                    e["seq"] not in retained_ids for e in selected
                )
                selected = retained
        if kind not in STORY_TYPES:
            excluded[kind] += 1
        elif (
            kind in INITIAL_TYPES
            and not (
                include_initial_reveals
                and kind in {"entity.revealed", "entity.corrected", "clue.revealed"}
            )
            and (
                not started
                or payload.get("initialization")
                or kind == "scene.updated"
                and previous_type == "module.bound"
            )
        ):
            excluded["initialization:" + kind] += 1
        else:
            selected.append(event)
        previous_type = kind
    from app.preparation.inventory import inventory_probe

    projected, checking_inventory = [], False
    for event in selected:
        if event["type"] in {"action.submitted", "agent.action_proposed"}:
            checking_inventory = inventory_probe(event["payload"].get("text", ""))
        if checking_inventory and event["type"] == "keeper.narration":
            # Possession comes from checks/receipts/current inventory. A past
            # pocket-search narration cannot establish an invented new item.
            excluded["inventory_narration_replaced_by_receipts"] += 1
        else:
            projected.append(event)
    selected = projected
    return selected, {
        "policy": "story_v1",
        "visible_count": len(visible_events),
        "candidate_count": len(selected),
        "excluded_count": sum(excluded.values()),
        "excluded_by_type": dict(excluded),
        "coverage_basis": "visible eligible story events; original seq; gaps are excluded events",
    }


async def current_participants(service, session, room):
    # Public roster and actual assignments only. Published but unassigned cards
    # have no member and are deliberately absent from assignments.
    members = await service.rooms.members(session, room)
    return {
        "members": {m.id: m.display_name for m in members if m.active},
        "assignments": {
            s.member_id: s.public_summary.get("name", "")
            for s in await service.rooms.slots(session, room)
            if s.member_id
        },
    }
