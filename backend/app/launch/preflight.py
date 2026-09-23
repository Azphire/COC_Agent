"""Shared start gate for the wizard and existing Agent rooms."""

from app.persistence.agent_models import ProfileRecord
from app.persistence.preparation_models import ModulePreparation
from app.rooms.service import RoomError, require


def issue(code, message, repair):
    return {"code": code, "message": message, "repair": repair}


async def agent_start_issues(agents, session, room, *, check_ready=True):
    issues = []
    module = await agents.module(session, room.id)
    if not module or not module.enabled:
        return issues
    try:
        bindings = await agents.ensure_config(session, room)
        for binding in bindings:
            profile = await session.get(ProfileRecord, binding.profile_id)
            expected = "keeper" if binding.member_id == room.host_member_id else "investigator"
            require(profile and profile.role == expected, "Agent 档案不存在或类型错误")
    except RoomError as error:
        issues.append(issue("agent_config", error.message, "team"))
    config = getattr(agents.rooms, "model_configuration", None)
    if config and (not config.ready() or config.verification == "failed"):
        issues.append(issue("model", config.error or "当前模型未配置或不可用", "model"))
    prepared = await agents.entities.binding(session, room.id)
    if prepared:
        prep = await session.get(ModulePreparation, prepared.preparation_id)
        if (
            not prep
            or prep.status != "approved"
            or (
                prep.version != prepared.preparation_version
                or prep.source_hash != prepared.source_hash
            )
        ):
            issues.append(issue("preparation", "已绑定准备版本不再有效，请核对批准版本", "module"))
    knowledge = await agents.knowledge.binding(session, room.id)
    rules = (knowledge or {}).get("rules", [])
    valid_rules = bool(knowledge and knowledge.get("enabled") and rules)
    for ref in rules:
        source = agents.knowledge.repository.source(**ref)
        if not (
            source
            and source.kind != "module"
            and source.visibility == "public_rules"
            and source.edition == "coc7"
            and source.chunk_count
            and agents.knowledge.repository.available(**ref)
        ):
            valid_rules = False
    if not valid_rules:
        issues.append(issue("rules", "请选择有效的第七版规则来源", "rules"))
    members = [
        m for m in await agents.rooms.members(session, room) if m.active and m.role == "player"
    ]
    slots = await agents.rooms.slots(session, room)
    assignments = room.session_state.get("handout_assignments", [])
    if not members:
        issues.append(issue("players", "至少需要一名调查员", "team"))
    for member in members:
        slot = next((s for s in slots if s.member_id == member.id), None)
        if not slot or slot.character_snapshot.get("status") != "finalized":
            issues.append(issue("character", f"{member.display_name} 缺少已确认角色", "team"))
        elif slot.character_snapshot.get("module_handout"):
            ho = slot.character_snapshot["module_handout"]
            if not slot.character_snapshot.get("module_handout_approval") or not any(
                a["member_id"] == member.id
                and a["slot_id"] == slot.id
                and a["handout_id"] == ho["handout_id"]
                and a["preparation_id"] == ho["preparation_id"]
                for a in assignments
            ):
                issues.append(
                    issue("handout", f"{member.display_name} 的 HO 尚未确认并分配", "team")
                )
        if check_ready and not member.ready:
            issues.append(
                issue(
                    "ready",
                    f"{member.display_name} 尚未准备",
                    "invite" if member.access_type == "remote" else "team",
                )
            )
    return issues


async def require_agent_start(agents, session, room):
    issues = await agent_start_issues(agents, session, room)
    require(not issues, "；".join(i["message"] for i in issues), 422)
