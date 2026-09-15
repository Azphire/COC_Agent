"""Add sourced portable-object operations to a NEW package, retaining old bytes."""

import copy
import hashlib

from module_package import ROOT, read, write

from app.preparation.schemas import EntityFields

OUTPUT = ROOT / "data/prepared/changan/batch-21"
SOURCE = ROOT / "data/prepared/changan/batch-18/package-approved.json"
EXPECTED = "b47bf0fb5127e4bc46fdb1c5a97fd9b41fe358eea2595351b5e19d95411e6c87"


def build():
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == EXPECTED
    original = read(SOURCE)
    package = copy.deepcopy(original)
    fields = {e["key"]: e["fields"] for e in package["entities"]}
    # The note is physical; its back remains a separate gated clue. Merely
    # carrying it does not grant reading its back or interpreting its meaning.
    fields["note_front"]["type"] = "item"
    for key, names in [("newspaper", ["报纸", "那份报纸"]), ("note_front", ["便签", "纸条"])]:
        f = fields[key]
        f["aliases"] = names
        f.setdefault("interactions", [])
        for op, kind, instruction, result in [
            (
                "take",
                "take",
                "拿取已经发现、当前可接触且没有持有者的实物，不调查内容",
                "你收起了" + f["title"] + "。",
            ),
            ("give", "give", "把本人实际持有的物品交给指定队友", "物品已交给指定队友。"),
            ("drop", "place", "把本人实际持有的物品放下在当前场景", "物品已放在这里。"),
            ("pickup", "take", "拾回实际放在当前场景的同一件物品", "你拾回了物品。"),
        ]:
            f["interactions"].append(
                dict(
                    id=op,
                    instruction=instruction,
                    situation=instruction,
                    source_block_ids=f["source_block_ids"],
                    kp_enabled=True,
                    action_kinds=[kind],
                    public_result=result,
                    **(
                        {"acquire_item_ids": [key]}
                        if op == "take"
                        else {"inventory_operation": op, "item_id": key}
                    ),
                )
            )
        EntityFields.model_validate(f)
    assert package["numeric_supplement"] == original["numeric_supplement"]
    for old, new in zip(original["entities"], package["entities"]):
        for name in ("combat_template", "check_stats", "source_block_ids", "reveal_conditions"):
            assert old["fields"].get(name) == new["fields"].get(name)
    diff = [
        dict(key=e["key"], before=old["fields"], after=e["fields"])
        for old, e in zip(original["entities"], package["entities"])
        if old != e
    ]
    package["batch21"] = dict(
        source_package=str(SOURCE.relative_to(ROOT)),
        source_sha256=EXPECTED,
        basis="原稿PDF/Word页2–4的便签、报纸实物；仅补日常携带操作",
    )
    OUTPUT.mkdir(exist_ok=True, parents=True)
    write(OUTPUT / "package-approved.json", package)
    write(
        OUTPUT / "package-diff.json",
        dict(
            source_sha256=EXPECTED,
            output_sha256=hashlib.sha256(
                (OUTPUT / "package-approved.json").read_bytes()
            ).hexdigest(),
            changes=diff,
            retained_special_rules=["phone", "torch", "keys"],
            nonportable=["train_map", "luggage", "cab_door", "panel"],
            note="示意图写在门旁；散落行李是障碍集合，未虚构其中具体物品；钥匙保管异议不变。",
        ),
    )
    print("Wrote new package and explicit diff:", OUTPUT)


if __name__ == "__main__":
    build()
