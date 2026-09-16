"""Initialize card equipment once, then use the existing inventory and combat reducers."""

from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.character import utc_now
from app.persistence.preparation_models import RoomEntityState
from app.preparation.runtime_schemas import ModuleInteraction
from app.rooms.combat_schemas import Weapon, unarmed
from app.rooms.schemas import SessionStateV1
from app.rules.equipment import equipment_weapon


async def initialize_equipment(agents, session, room):
    state = SessionStateV1.model_validate(room.session_state)
    entities = await agents.entities.rows(session, room.id)
    has_initial_rules = any(
        r.get("inventory_operation") == "initial"
        for e in entities
        for r in e.snapshot.get("interactions", [])
    )
    for slot in await agents.rooms.slots(session, room):
        if not slot.member_id:
            continue
        card = state.characters[UUID(slot.id)]
        if card.equipment_settlement != "unsettled":
            continue
        if has_initial_rules:
            # Prepared starting loss/choice rules remain authoritative. Card
            # gear is a biography, never an alternative way to obtain items.
            card.equipment_settlement = "module_pending"
            continue
        card.equipment_settlement = "retained"
        state.module_runtime.equipment_slots[slot.member_id] = slot.id
        for entry in slot.character_snapshot.get("equipment", []):
            eid = str(uuid5(NAMESPACE_URL, f"{room.id}:{slot.id}:equipment:{entry['id']}"))
            source = f"character:{slot.source_character_id}:{entry['id']}"
            rules = []
            for op, kind, description in [
                ("give", "give", "交给指定队友"),
                ("drop", "place", "放在当前场景"),
                ("pickup", "take", "拾回放在当前场景的物品"),
            ]:
                rules.append(
                    ModuleInteraction(
                        id=op,
                        instruction=description,
                        source_block_ids=[source],
                        kp_enabled=True,
                        action_kinds=[kind],
                        inventory_operation=op,
                        item_id=eid,
                        public_result=entry["name"] + "已" + description + "。",
                    ).model_dump()
                )
            snapshot = dict(
                id=eid,
                type="item",
                title=entry["name"],
                aliases=[entry["name"]],
                public_summary=entry["name"],
                keeper_summary=entry["notes"],
                source_block_ids=[source],
                source_references=[],
                generated_by="character_equipment",
                status="approved",
                initial_visibility="revealed",
                reveal_conditions=dict(
                    access_policy="automatic",
                    scene_id=None,
                    required_entity_ids=[],
                    successful_check=None,
                    note="",
                ),
                interactions=rules,
                sanity_effects=[],
                tags=["character_equipment"],
            )
            session.add(
                RoomEntityState(
                    room_id=room.id,
                    source_entity_id=eid,
                    entity_type="item",
                    snapshot=snapshot,
                    state="revealed",
                    frozen_public_summary=entry["name"],
                    frozen_source_references=[],
                    revealed_by=slot.member_id,
                    revealed_time=utc_now(),
                )
            )
            for index in range(entry["quantity"]):
                iid = str(uuid5(NAMESPACE_URL, f"{eid}:{index}"))
                state.module_runtime.item_instances[iid] = eid
                state.module_runtime.inventory[iid] = slot.member_id
                weapon = equipment_weapon(
                    entry.get("catalog_id"),
                    iid,
                    ammo=entry.get("initial_ammo", 0),
                    reserve=entry.get("initial_reserve", 0),
                )
                if weapon:
                    state.module_runtime.equipment_weapons[iid] = weapon.model_dump()
        agents.rooms.append(
            session,
            room,
            "character.equipment_initialized",
            slot.member_id,
            {"slot_id": slot.id, "text": "角色原有装备已结算为实际持有物。"},
            "actor_and_host",
        )
    sync_equipment_weapons(state)
    room.session_state = state.model_dump(mode="json")


def sync_equipment_weapons(state):
    """Preserve ammunition from combat, project weapons by current instance holder."""
    runtime = state.module_runtime
    if not runtime.equipment_weapons:
        return
    # Combat holds the newest shot/reload result while a reducer is active.
    for c in [*state.characters.values(), *state.combat.participants.values()]:
        for weapon in c.weapons:
            if weapon.id in runtime.equipment_weapons:
                runtime.equipment_weapons[weapon.id] = weapon.model_dump()
    for member, slot in runtime.equipment_slots.items():
        card = state.characters.get(UUID(slot))
        if not card:
            continue
        card.weapons = [w for w in card.weapons if w.id not in runtime.equipment_weapons]
        card.weapons += [
            Weapon.model_validate(w)
            for iid, w in runtime.equipment_weapons.items()
            if runtime.inventory.get(iid) == member
        ]
        if not any(w.id == "unarmed" for w in card.weapons):
            card.weapons.insert(0, unarmed())
        for participant in state.combat.participants.values():
            if participant.slot_id == slot:
                participant.weapons = [w.model_copy(deep=True) for w in card.weapons]
