"""Complete a source-configured visual method when its missing choice is supplied."""

import re

from app.preparation.action_authority import action_kinds
from app.rooms.combat_service import load_state


def validate_lighting_prose(text, entities, runtime, scene):
    """A named prepared light cannot appear in prose without its actual state."""
    from app.preparation.action_authority import aliases
    from app.rooms.service import require

    items = [entity for entity in entities if entity.get("type") == "item"]
    claims = set()
    for sentence in re.split(r"[。；;\n]", text):
        subject = None
        hypothetical = False
        for clause in re.split(r"[，,]", sentence):
            hypothetical = hypothetical or bool(re.search(r"如果|假如|假设|打算|准备|可以", clause))
            named = [
                (len(name), entity["id"])
                for entity in items for name in aliases(entity)
                if name and name in clause
            ]
            if named:
                longest = max(size for size, _ in named)
                ids = {eid for size, eid in named if size == longest}
                subject = next(iter(ids)) if len(ids) == 1 else None
            elif subject is None and "屏幕" in clause:
                screens = {
                    entity["id"] for entity in items
                    if any(re.search(r"手机|平板|电脑|屏幕|终端", name) for name in aliases(entity))
                }
                subject = next(iter(screens)) if len(screens) == 1 else None
            if (
                subject
                and not hypothetical
                and re.search(
                    r"光束|灯光|亮着|亮起|发光|照亮|照明|点亮|"
                    r"(?:打开|开启).{0,12}(?:手机|屏幕|灯|手电)", clause
                )
                and not re.search(
                    r"没有|未|不亮|关闭|关掉|如果|假设|打算|准备|可以|能否|是否|[?？]", clause
                )
            ):
                claims.add(subject)
    for entity in items:
        if entity["id"] not in claims:
            continue
        lights = [
            r
            for r in entity.get("interactions", [])
            if "light" in r.get("action_kinds", []) and any(r.get("set_flags", {}).values())
        ]
        if not lights:
            continue
        eid = entity["id"]
        present = any(runtime.item_instances.get(i, i) == eid for i in runtime.inventory)
        present = present or any(
            runtime.item_instances.get(i, i) == eid and node == scene
            for i, node in runtime.dropped_items.items()
        )
        require(
            present
            and any(
                all(runtime.flags.get(k, False) == v for k, v in r["set_flags"].items())
                for r in lights
            ),
            "所描述的照明物品尚未实际在场并开启",
            422,
        )


async def approved_visual_result(service, session, room, entity_id, effect, raw):
    if effect.perception != "visual" or "observe" not in action_kinds(raw):
        return None
    nav = await service.navigation.state(session, room.id)
    if not nav:
        return None
    snapshot, _ = await service.navigation.snapshot(session, nav)
    local = {
        b.entity_id for b in snapshot.entity_bindings if b.node_id == nav.current_scene_node_id
    }
    rows = await service.entities.rows(session, room.id)
    visible = {e.source_entity_id for e in rows if e.state != "hidden"}
    flags = load_state(room).module_runtime.flags
    for row in rows:
        if row.source_entity_id not in local & visible:
            continue
        for rule in row.snapshot.get("interactions", []):
            if (
                rule.get("observation_entity_id") != entity_id
                or rule.get("observation_effect_id") != effect.id
                or not rule.get("kp_enabled")
                or rule.get("check_name")
                or rule.get("required_item_ids")
                or not all(
                    flags.get(k, False) == v for k, v in rule.get("required_flags", {}).items()
                )
                or not set(rule.get("required_entity_ids", [])) <= visible
                or not any(flags.get(k) for k in rule.get("visibility_any_flags", []))
                or rule.get("scene_node_ids")
                and nav.current_scene_node_id not in rule["scene_node_ids"]
            ):
                continue
            missing = set(rule.get("required_facts", []))
            if missing - {"visual_target_in_phone_light"}:
                continue
            if "visual_target_in_phone_light" in missing and not re.search(
                r"靠近|走近|近处|面前|脚边|贴近", raw
            ):
                continue
            # This branch cannot supply a roll, a light, or an unknown position.
            # It executes only the observation portion of the approved method.
            return {
                "text": rule["public_result"],
                "source_block_ids": rule["source_block_ids"],
                "method_entity_id": row.source_entity_id,
                "interaction_id": rule["id"],
                "evidence_quote": raw,
                "origin": "approved_observation",
            }
    return None
