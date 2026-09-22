"""Source-reviewed private handouts; callers cannot approve invented bonuses by label."""

from app.domain.handouts import PreparedHandout
from app.rooms.service import require


def validate_handouts(raw, source_hash, ir=None):
    from app.preparation.packages import content_digest, evidence_registry

    require(isinstance(raw, list) and len(raw) <= 20, "HO 必须为至多20项的列表", 422)
    handouts = [PreparedHandout.model_validate(value) for value in raw]
    require(len({h.id for h in handouts}) == len(handouts), "HO ID 不能重复", 422)
    registered = evidence_registry().get("source_handouts", {}).get(source_hash, {})
    blocks = {b.block_id: b for b in ir.blocks} if ir else None
    for handout in handouts:
        require(handout.source_hash == source_hash, "HO 来源 hash 与准备包不一致", 422)
        require(
            registered.get(handout.id) == content_digest(handout.model_dump(mode="json")),
            f"HO {handout.id} 未登记来源校对，或文本/数值/限制已变更；请先校对原稿",
            422,
        )
        if blocks is not None:
            selected = set(handout.source_block_ids)
            require(selected and selected <= blocks.keys(), "HO 来源块缺失", 422)
            pages = {blocks[b].source_position.physical_page for b in selected}
            require(pages == set(handout.source_pages), "HO 页码与来源块不一致", 422)
    return handouts
