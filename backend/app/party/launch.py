"""Atomically associate generated batches with their versioned launch intent."""

from sqlalchemy import update

from app.domain.character import utc_now
from app.party.requirements import resolved_requirements
from app.persistence.launch_models import LaunchDraft
from app.persistence.preparation_models import ModulePreparation
from app.rooms.service import require


def check_count(requirements, count, reserved_humans=0, own=True):
    if requirements.get("players_verified"):
        maximum = requirements["maximum_players"]
        require(count + reserved_humans + int(own) <= maximum,
                f"已核准模组最多 {maximum} 名调查员；请减少 AI 或预留真人席位后生成", 422)


async def check_launch_batch(session, document, expected_version=None):
    link = document.get("launch")
    if not link:
        check_count(document["requirements"], document["count"], own=False)
        return None
    row = await session.get(LaunchDraft, link["draft_id"])
    require(row and not row.room_id, "开团草稿不存在或队伍已采用", 409)
    require(expected_version is None or row.version == expected_version,
            "开团草稿已更新，请刷新后继续生成", 409)
    require(row.document["preparation_id"] == document["preparation_id"]
            and row.document["preparation_version"] == document["preparation_version"]
            and row.document["source_hash"] == document["preparation_source_hash"],
            "批次与开团准备版本不一致", 409)
    require(row.document.get("era", document["era"]) == document["era"],
            "批次与开团选择年代不一致", 409)
    prep = await session.get(ModulePreparation, document["preparation_id"])
    requirements = await resolved_requirements(session, prep)
    require(not requirements.get("era_verified") or document["era"] == requirements["era"],
            "所选年代不符合已核准模组来源", 422)
    count = document["count"] if link["role"] == "party" else row.document.get("ai_count", 0)
    check_count(requirements, count or 0, row.document.get("reserved_humans", 0))
    if link["role"] == "self":
        require(document["count"] == 1, "本人快速生成仅允许一名调查员", 422)
    key = "party_batch_id" if link["role"] == "party" else "own_batch_id"
    require(not row.document.get(key) or row.document[key] == document["id"],
            "此开团草稿已有可恢复批次，请继续原批次", 409)
    return row


async def sync_launch_batch(session, document, *, expected_version=None):
    row = await check_launch_batch(session, document, expected_version)
    if row is None:
        return
    role = document["launch"]["role"]
    changes = {"party_batch_id" if role == "party" else "own_batch_id": document["id"],
               "era": document["era"]}
    if role == "party":
        changes["ai_count"] = document["count"]
    else:
        handout = document["members"][0]["character"].get("module_handout")
        changes["own_handout"] = handout["handout_id"] if handout else None
        if document.get("character_ids"):
            changes["character_id"] = document["character_ids"][0]
    version = row.version
    result = await session.execute(update(LaunchDraft).where(
        LaunchDraft.id == row.id, LaunchDraft.version == version,
    ).values(document={**row.document, **changes}, version=version + 1,
             updated_at=utc_now(), steps={**row.steps, "issues": []}))
    require(result.rowcount == 1, "开团草稿已更新，请刷新后继续", 409)
