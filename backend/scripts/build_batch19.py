"""Byte-preserving approved package and separate, explicitly authored effect example."""

import hashlib
import shutil

from module_package import ROOT, audit, read, write

from app.knowledge.indexer import KnowledgeIndexer
from app.knowledge.repository import KnowledgeRepository
from app.module_ir.parser import parse_module
from app.preparation.runtime_schemas import ModuleInteraction
from app.preparation.schemas import EntityFields

OUTPUT = ROOT / "data/prepared/changan/batch-19"
EXPECTED = "b47bf0fb5127e4bc46fdb1c5a97fd9b41fe358eea2595351b5e19d95411e6c87"
EXAMPLE = """# 工作间
独立效果示例，不属于《常暗之厢》，不代表正式法术。这里没有危险和隐藏真相。
桌上有一只三次使用的共鸣器、两剂装的一瓶补剂和一张说明卡。大家可以直接拿取和交接。
共鸣器：普通激活消耗4MP和1次，指示灯闪亮；校准激活也消耗4MP和1次，需要POW检定，
失败仍消耗但灯不亮。资源不足不允许尝试。补剂：饮用一剂恢复5MP，不能超过角色上限。
以上数值是本批独立示例自定义规则，非CoC正式法术或《常暗之厢》道具。
说明卡：西边的休息室可以休息。正常使用和交接无需主机审批。
# 休息室
一间安全的小休息室，有座椅和一只钟。可返回工作间。
明确休息半小时才经过30游戏分钟；聊天、页面刷新、真实等待均不推进游戏时间。
MP自然恢复使用本地CoC7规则书PDF148页：每游戏小时1点，上限为角色派生MP。
"""


def build():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    approved = ROOT / "data/prepared/changan/batch-18/package-approved.json"
    assert hashlib.sha256(approved.read_bytes()).hexdigest() == EXPECTED
    target = OUTPUT / "package-approved.json"
    if target.exists():
        assert target.read_bytes() == approved.read_bytes()
    else:
        shutil.copyfile(approved, target)
    for name in ("approval.json", "npc-supplement.json"):
        source = approved.parent / name
        if source.exists() and not (OUTPUT / name).exists():
            shutil.copyfile(source, OUTPUT / name)
    write(
        OUTPUT / "provenance.json",
        {
            "source_package": str(approved.relative_to(ROOT)),
            "byte_sha256": EXPECTED,
            "formal_package_unchanged": True,
            "approval": read(approved).get("numeric_supplement"),
            "example_is_original_module": False,
        },
    )
    source_dir = OUTPUT / "example-source"
    source_dir.mkdir(exist_ok=True)
    manuscript = source_dir / "example.md"
    if manuscript.exists():
        assert manuscript.read_text(encoding="utf8") == EXAMPLE
    else:
        manuscript.write_text(EXAMPLE, encoding="utf8")
    repository = KnowledgeRepository(OUTPUT / "example-index.db")
    repository.initialize()
    source, _ = KnowledgeIndexer(ROOT / "data", repository).index_source(
        source_dir.relative_to(ROOT / "data").as_posix(), "module"
    )
    ir = parse_module(source, ROOT / "data")
    with repository.connect() as db:
        chunks = [dict(r) for r in db.execute("SELECT * FROM knowledge_chunks")]
    nodes = {n.title: n.node_id for n in ir.nodes}
    workshop, lounge = nodes["工作间"], nodes["休息室"]
    blocks = [b.block_id for b in ir.blocks]
    reviewer = "batch19-independent-example-author"

    def interaction(key, text, result, **kw):
        return ModuleInteraction(
            id=key,
            instruction=text,
            public_result=result,
            source_block_ids=blocks,
            kp_enabled=True,
            **kw,
        ).model_dump()

    def entity(key, kind, title, summary, where, **kw):
        return {
            "key": key,
            "node_ids": where,
            "fields": EntityFields(
                type=kind,
                title=title,
                public_summary=summary,
                keeper_summary=summary,
                source_block_ids=blocks,
                reviewed_by=reviewer,
                review_basis="自定义独立效果示例；源文与数值一起提供，不是正式模组规则",
                initial_visibility="revealed",
                tags=["host_authored_test", "package:" + key],
                **kw,
            ).model_dump(),
        }

    def portable(key, title, uses, effects):
        return entity(
            key,
            "item",
            title,
            f"{title}：独立示例，初始{uses}次，可拿取、交接和使用。",
            [workshop],
            item_uses=uses,
            aliases=[title],
            interactions=[
                interaction("take", f"拿起{title}", f"拿起了{title}。", acquire_item_ids=[key]),
                interaction(
                    "give",
                    f"将{title}交给指定同伴",
                    f"{title}已交给指定同伴。",
                    inventory_operation="give",
                    item_id=key,
                ),
                *effects,
            ],
        )

    def pulse(check):
        return interaction(
            "calibrate" if check else "activate",
            "使用共鸣器校准激活" if check else "使用共鸣器普通激活",
            "共鸣器的指示灯闪亮。",
            item_id="resonator",
            check_name="pow" if check else None,
            use_effect={
                "id": "calibrate" if check else "activate",
                "basis": "独立示例说明卡：成本4MP/1次；校准POW失败仍消耗",
                "mp_cost": 4,
                "uses": 1,
                "consume_on_failure": True,
            },
        )

    package = {
        "title": "第十九批 · 独立物品效果示例",
        "reviewer": reviewer,
        "knowledge": {"source": source.model_dump(mode="json"), "chunks": chunks},
        "ir": ir.model_dump(mode="json"),
        "initial_node_id": workshop,
        "initial_entity_key": "workshop",
        "required_entity_keys": ["resonator", "tonic", "instructions"],
        "entities": [
            entity("workshop", "scene", "工作间", EXAMPLE.split("# 休息室")[0][6:], [workshop]),
            entity(
                "lounge",
                "scene",
                "休息室",
                "安全的休息室，有座椅和钟。",
                [lounge],
                interactions=[
                    interaction(
                        "rest",
                        "明确休息半小时",
                        "你们在休息室休息了半小时。",
                        elapsed_minutes=30,
                        action_kinds=["rest"],
                    )
                ],
            ),
            portable("resonator", "共鸣器", 3, [pulse(False), pulse(True)]),
            portable(
                "tonic",
                "补剂",
                2,
                [
                    interaction(
                        "drink",
                        "饮用一剂补剂",
                        "你饮用了一剂补剂。",
                        item_id="tonic",
                        use_effect={
                            "id": "restore_mp",
                            "basis": "独立示例：每剂恢复5MP，上限不变",
                            "mp_restore": 5,
                            "uses": 1,
                        },
                    )
                ],
            ),
            entity("instructions", "clue", "说明卡", EXAMPLE.split("# 休息室")[0][6:], [workshop]),
        ],
        "nodes": [
            {
                "node_id": node,
                "patch": {
                    "approved_type": "scene",
                    "public_title": name,
                    "public_summary": "独立效果示例：" + name,
                    "initial_scene": node == workshop,
                },
            }
            for name, node in (("工作间", workshop), ("休息室", lounge))
        ],
        "transitions": [
            {
                "source_scene_node_id": a,
                "target_scene_node_id": b,
                "source_evidence": blocks,
                "condition_summary": "可直接通行",
                "transition_type": "normal",
                "approved": True,
            }
            for a, b, eid in ((workshop, lounge, "lounge"), (lounge, workshop, "workshop"))
        ],
        "coverage": [
            {
                "id": "example",
                "source_orders": [b.source_order for b in ir.blocks],
                "node_ids": [workshop, lounge],
                "entity_keys": ["workshop", "lounge", "resonator", "tonic", "instructions"],
                "status": "prepared",
                "gaps": [],
                "description": "独立自定义效果示例",
            }
        ],
    }
    write(OUTPUT / "example-package.json", package)
    write(OUTPUT / "example-audit.json", audit(package))
    print("Prepared byte-identical formal package and separately labelled example")


if __name__ == "__main__":
    build()
