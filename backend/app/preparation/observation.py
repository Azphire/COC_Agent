"""Complete a source-configured visual method when its missing choice is supplied."""

import re

from app.preparation.action_authority import action_kinds
from app.rooms.combat_service import load_state


def validate_lighting_prose(text, entities, runtime, scene):
    """A named prepared light cannot appear in prose without its actual state."""
    from app.preparation.action_authority import aliases
    from app.rooms.service import require

    for entity in entities:
        if entity.get("type") != "item":
            continue
        lights = [
            r
            for r in entity.get("interactions", [])
            if "light" in r.get("action_kinds", []) and any(r.get("set_flags", {}).values())
        ]
        if not lights:
            continue
        claims = [
            clause
            for clause in re.split(r"[，。；,;\n]", text)
            if any(name and name in clause for name in aliases(entity))
            and re.search(r"光束|灯光|亮着|照亮|点亮|(?:打开|开启).*(?:灯|手电)", clause)
            and not re.search(r"没有|未|不亮|关闭|关掉|如果|假设|打算|准备|可以|[?？]", clause)
        ]
        if not claims:
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
