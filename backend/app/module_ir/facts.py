"""A public location projection, derived without exposing unvisited bindings."""

import re

from sqlalchemy import select

from app.module_ir.context import scene_nodes
from app.module_ir.schemas import StructureSnapshot
from app.persistence.module_ir_models import ApprovedStructure
from app.persistence.room_models import RoomEvent

SCOPE_LABELS = {"current_scene": "当前场景", "historical": "先前获知", "unknown": "位置未确认"}
SCOPE_PREFIXES = {
    "historical": "先前获知（当前位置未确认）：",
    "unknown": "已知信息（位置未确认）：",
}


def recalling(text):
    return bool(re.search(r"回顾|回想|先前|之前|此前|旧线索|记得|曾经|早先", text))


def explicit_recall(text):
    return bool(re.match(r"\s*我(?:们)?(?:想|要|只)?(?:回顾|回想|复述)", text)) and not re.search(
        r"进入|前往|移动|离开|尝试|使用|拿起|打开|交谈|询问|掷骰|检定|检查", text
    )


def scoped_statement(entity, text):
    return SCOPE_PREFIXES.get(entity.get("fact_scope"), "") + text


def relevant_public_facts(entities, action, target=None):
    mentioned = {e["id"] for e in entities if e["title"] in action or e["id"] == target}
    current_titles = {
        e["title"] for e in entities if e.get("fact_scope", "current_scene") == "current_scene"
    }
    return [
        e
        for e in entities
        if e.get("fact_scope", "current_scene") == "current_scene"
        or (
            e["id"] in mentioned
            and (e["title"] not in current_titles or recalling(action) or e["id"] == target)
        )
        or (recalling(action) and not mentioned)
    ]


async def public_fact_scopes(agents, session, room_id, entities):
    prepared = await agents.entities.binding(session, room_id)
    current_scene = prepared.current_scene if prepared else None
    current, visited = set(), set()
    nav = await agents.navigation.state(session, room_id)
    if nav and not nav.module_structure_missing:
        record = await session.get(ApprovedStructure, nav.structure_snapshot_id)
        try:
            snapshot = StructureSnapshot.model_validate(record.document) if record else None
        except ValueError:
            snapshot = None
    else:
        snapshot = None
    if snapshot and snapshot.approved and snapshot.source_hash == nav.module_source_hash:
        local = set(scene_nodes(snapshot, nav.current_scene_node_id))
        previous = set()
        visited_nodes = set(nav.visited_scene_node_ids)
        # Public knowledge is monotonic across saves. Older saves can restore the
        # current location, but cannot erase a scene already shown to players.
        events = list(
            await session.scalars(
                select(RoomEvent)
                .where(
                    RoomEvent.room_id == room_id,
                    RoomEvent.type.in_(["scene.updated", "module.scene_transition"]),
                )
                .order_by(RoomEvent.seq)
            )
        )
        for before, event in zip(events, events[1:]):
            if (
                before.type == "scene.updated"
                and before.visibility == "public"
                and event.type == "module.scene_transition"
                and event.seq == before.seq + 1
            ):
                visited_nodes.add(event.payload.get("target"))
        valid_nodes = {n.node_id for n in snapshot.nodes}
        for node_id in visited_nodes & valid_nodes:
            previous.update(scene_nodes(snapshot, node_id))
        current = {b.entity_id for b in snapshot.entity_bindings if b.node_id in local}
        visited = {b.entity_id for b in snapshot.entity_bindings if b.node_id in previous}
    for entity in entities:
        eid = entity["id"]
        # Binding identity, never a matching title, establishes presence. Public
        # disclosure alone cannot establish possession or movement.
        scope = (
            "current_scene"
            if eid in current or eid == current_scene
            else (
                "historical" if eid in visited and entity.get("revealed_event_seq") else "unknown"
            )
        )
        entity.update(fact_scope=scope, scope_label=SCOPE_LABELS[scope])
    return entities
