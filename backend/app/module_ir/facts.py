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
    from app.memory.facts import recall_question

    return recall_question(text) or bool(re.search(r"先前|之前|此前|旧线索|曾经|早先", text))


def explicit_recall(text):
    from app.memory.facts import readonly_recall

    return readonly_recall(text)


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


def nonlocal_source_claims(text, entities):
    """Detect a distinctive published clue copied into current observation prose.

    This does not certify all prose. It protects existing located evidence;
    ordinary unsourced detail and explicitly attributed recollection remain free.
    """
    from app.knowledge.text import normalize

    current = "\n".join(
        normalize(e.get("public_summary", "")).replace(" ", "")
        for e in entities if e.get("fact_scope", "current_scene") == "current_scene"
    )
    clauses = []
    for sentence in re.split(r"[。；;\n]", text):
        if re.match(
            r"\s*(?:先前|之前|此前|当时|刚才|曾经|回忆|"
            r"(?:你们?|我)(?:向.{1,20})?(?:想起|回想|记得|转述|引用|复述|念出))", sentence
        ):
            continue
        clauses += [
            normalize(c).replace(" ", "") for c in re.split(r"[，,]", sentence)
            if not re.search(r"没有|未见|看不到|并非|不是", c)
        ]
    conflicts = []
    for entity in entities:
        if entity.get("type") not in {"clue", "location"} or entity.get("fact_scope") not in {
            "historical", "unknown"
        }:
            continue
        spans = {
            phrase[i:i + 8]
            for raw in re.split(r"[，。；：！？,.!?;:\n]", entity.get("public_summary", ""))
            for phrase in [normalize(raw).replace(" ", "")]
            for i in range(len(phrase) - 7)
            if phrase[i:i + 8] not in current
        }
        if any(span in clause for span in spans for clause in clauses):
            conflicts.append(entity["id"])
    return conflicts


async def public_fact_scopes(agents, session, room_id, entities):
    from app.memory.events import story_events

    events = list(
        await session.scalars(
            select(RoomEvent)
            .where(
                RoomEvent.room_id == room_id,
                RoomEvent.type.in_([
                    "scene.updated", "module.scene_transition", "snapshot.loaded",
                    "entity.revealed", "entity.corrected", "clue.revealed",
                ]),
            )
            .order_by(RoomEvent.seq)
        )
    )
    active, _ = story_events(
        [dict(seq=e.seq, type=e.type, payload=e.payload, visibility=e.visibility) for e in events],
        include_initial_reveals=True,
    )
    abandoned = {e.seq for e in events} - {e["seq"] for e in active}
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
        transitions = [
            e for e in events if e.type in {"scene.updated", "module.scene_transition"}
        ]
        for before, event in zip(transitions, transitions[1:]):
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
        from app.preparation.runtime import current_entity_ids

        room = await agents.rooms.room(session, room_id)
        current = current_entity_ids(snapshot, local, room.session_state.get("module_runtime", {}))
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
        # A load preserves public knowledge, but a disclosure from an abandoned
        # future is not a current observation, even when bound to this scene.
        if entity.get("revealed_event_seq") in abandoned:
            scope = "historical"
        entity.update(fact_scope=scope, scope_label=SCOPE_LABELS[scope])
    return entities
