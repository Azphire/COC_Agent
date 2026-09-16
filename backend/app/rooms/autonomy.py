"""Read investigator autonomy from the authoritative runtime, never combat copies."""

from uuid import UUID


def autonomy_reason(character):
    if character is None:
        return None  # A model without investigator resources is not SAN zero.
    if character.san == 0:
        return "SAN 为 0：永久疯狂，不能自主行动"
    if character.sanity.kind == "permanent":
        return "永久疯狂，不能自主行动；更正 SAN 不会解除永久疯狂"
    if character.sanity.phase == "awaiting_symptom":
        return "等待主机确认疯狂症状，不能自主行动"
    if character.sanity.phase == "bout":
        return "疯狂发作中，不能自主行动"
    return None


def combat_autonomy_reason(state, participant):
    if participant.slot_id:
        character = state.characters.get(UUID(participant.slot_id))
        if character is None:
            return "缺少调查员运行时，不能自主行动"
        return autonomy_reason(character)
    if participant.member_id:
        return "缺少调查员席位，不能自主行动"
    return None


def initialize_zero_san(character):
    """Idempotent derived state only; no encounters, losses, gains or dice."""
    if character.san == 0 and character.sanity.kind != "permanent":
        sanity = character.sanity
        sanity.history.append(
            {
                "event": "runtime_zero_san_initialized",
                "source": "runtime_initialization_or_load",
                "previous_kind": sanity.kind,
                "previous_phase": sanity.phase,
                "reason": "当前 SAN 为 0，补齐永久疯狂运行时状态；未改变原卡、骰点或历史损失",
            }
        )
        sanity.kind, sanity.phase = "permanent", "bout"
        sanity.ends_minute = sanity.bout_end_minute = sanity.bout_end_round = None
    return character
