"""One-time belief conversion as part of an approved SAN encounter settlement."""

from app.rules.mythos import initial_belief


def conversion_cost(character, snapshot, progress, check_id, mythos, minute, day, voluntary=False):
    if initial_belief(snapshot) != "unbeliever":
        return 0
    sanity = character.sanity
    if sanity.belief is None:
        sanity.belief = "unbeliever"
    if sanity.belief != "unbeliever" or sanity.belief_conversion:
        return 0
    evidence = progress.effect.mythos_evidence
    if not voluntary and (not evidence or (evidence == "direct" and progress.loss <= 0)):
        return 0
    receipt = {
        "check_id": check_id,
        "source_event_seq": progress.source_event_seq,
        "entity_id": progress.entity_id,
        "effect_id": progress.effect.id,
        "evidence": "voluntary" if voluntary else evidence,
        "basis": progress.effect.basis if voluntary else progress.effect.mythos_evidence_basis,
        "mythos_before_insanity": mythos,
        "cost": mythos,
        "encounter_loss": progress.loss,
        "minute": minute,
        "day": day,
        "source": "规则书1907 PDF151／印刷150：成为相信者",
    }
    sanity.belief = "believer"
    sanity.belief_conversion = progress.belief_conversion = receipt
    return mythos


async def voluntarily_believe(service, session, room, identity, body):
    """A player's explicit choice under RB150, never a model-proposed state edit."""
    from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

    from app.agents.schemas import PendingCheck
    from app.persistence.agent_models import AgentCycle, CheckRecord
    from app.rooms.autonomy import autonomy_reason
    from app.rooms.sanity_schemas import SanityEffect, SanityProgress
    from app.rooms.sanity_service import runtime_character
    from app.rooms.service import require

    slot = await service.slot(session, room, identity.member_id)
    require(initial_belief(slot.character_snapshot) == "unbeliever", "此卡不适用不信者转换", 422)
    check_id = str(uuid5(NAMESPACE_URL, f"{room.id}:{slot.id}:voluntary-belief"))
    old = await session.get(CheckRecord, check_id)
    if old and not old.document.get("sanity_rewound"):
        return old
    require(room.status == "running", "请先恢复游戏")
    require(body.expected_revision == room.revision, "房间已更新，请刷新")
    require(body.reason.strip(), "请填写玩家自愿相信的理由", 422)
    require(not await service.agents.cycle(session, room.id, active=True), "请先完成当前回合")
    state, character = runtime_character(room, slot)
    require(not autonomy_reason(character), "当前不能自主作出相信者选择")
    require(character.sanity.belief == "unbeliever", "角色已经是相信者")
    source = service.rooms.append(
        session,
        room,
        "sanity.belief_chosen",
        identity.member_id,
        {"reason": body.reason, "slot_id": slot.id},
        "actor_and_host",
    )
    cycle_id = str(uuid4())
    cycle = AgentCycle(
        id=cycle_id,
        room_id=room.id,
        status="waiting_for_roll",
        state={
            "cycle_id": cycle_id,
            "room_id": room.id,
            "request_category": "san_encounter",
            "triggering_event_seq": source.seq,
            "triggering_member_id": identity.member_id,
            "current_node": "wait_for_human_roll",
            "status": "waiting_for_roll",
            "pending_check_id": check_id,
            "wait_reason": "human_roll",
            "call_count": 0,
            "tool_count": 0,
            "tool_results": [],
            "completed_teammate_ids": [],
        },
    )
    session.add(cycle)
    effect = SanityEffect(
        id="voluntary-belief",
        encounter="玩家自愿成为相信者",
        success_loss="0",
        failure_loss="0",
        source="规则书1907",
        page=151,
        basis=body.reason,
        mythos=True,
    )
    progress = SanityProgress(
        origin="voluntary",
        effect=effect,
        source_event_seq=source.seq,
        entity_id="voluntary-belief",
        before=character.san,
        loss=0,
    )
    check = PendingCheck(
        id=UUID(check_id),
        room_id=UUID(room.id),
        slot_id=UUID(slot.id),
        target_member_id=UUID(identity.member_id),
        requester=UUID(identity.member_id),
        agent_run_id=uuid4(),
        name="SAN",
        kind="attribute",
        value=character.san,
        display_name="自愿相信",
        reason="玩家明确选择，按当前神话值结算",
        visibility="actor_and_host",
    )
    record = old or CheckRecord(
        id=check_id,
        room_id=room.id,
        target_member_id=identity.member_id,
        agent_run_id=str(check.agent_run_id),
    )
    record.cycle_id, record.status = cycle.id, "pending"
    if not old:
        session.add(record)
    if character.sanity.day_start_san is None:
        character.sanity.day_start_san = character.san
    service.settle_loss(room, slot, state, character, record, progress, voluntary=True)
    room.session_state = state.model_dump(mode="json")
    await service.finish(session, room, record, check, progress)
    service.agents.cycle_event(session, room, cycle)
    return record
