"""Derive the three reviewed, player-safe HO texts without changing batch 41."""

import hashlib
import json
from pathlib import Path

from app.domain.handouts import HandoutAdjustments, PreparedHandout

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e"
OUTPUT = ROOT / "data/prepared/zhihulu/batch-42"


def read(path):
    return json.loads(path.read_text("utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")


def build():
    package = read(SOURCE / "package-reviewed.json")
    manifest = read(SOURCE / "source-manifest.json")
    originals = {}
    for file in manifest["files"]:
        actual = hashlib.sha256((ROOT / file["path"]).read_bytes()).hexdigest()
        assert actual == file["sha256"], f"Original source changed: {file['path']}"
        originals[file["path"]] = actual
    previous = read(SOURCE / "private-handouts.json")
    handouts, exclusions = [], []
    order = (
        "先按普通建卡校验基础属性、年龄扣减及职业信用范围；再加HO属性，"
        "重新计算属性基底技能、HP/MP/SAN/MOV/DB/体格和点数；"
        "最后在正常已分配技能上增加HO技能/信用。模组未另定属性上限；"
        "规则书1907物理26页列力量/敏捷99为人类极限，意志特例可超过100，"
        "因此力量/敏捷上限99，意志不加模组未声明的上限；技能沿用原规则99。"
        "超限报错不截断；HO2最终信用不得超过5。"
    )
    for number, resume_heading in [(1, "关于系统："), (2, "关于弹幕："), (3, "关于HO1：")]:
        key = f"HO{number}"
        raw = previous[key]
        text = raw["text"]
        start = text.index(f"关于HO{number} 的注意事项：")
        end = text.index(resume_heading, start)
        # The intervening section addresses KP and discloses facts unavailable to PC.
        excluded = text[start:end].strip()
        player_text = (text[:start].rstrip() + "\n\n" + text[end:].strip()).strip()
        adjustments = HandoutAdjustments(
            required_age=19,
            required_occupation="student",
            required_era="modern",
            requirements_note=(
                f"原文要求19岁、学生、{'男' if number == 3 else '女'}；性别由背景描述记录。"
            ),
            attribute_points=30 if number == 2 else 0,
            attribute_choices=["str", "pow", "dex"] if number == 2 else [],
            attribute_maxima={"str": 99, "dex": 99} if number == 2 else {},
            skill_bonuses=(
                {"credit_rating": 30, "psychology" if number == 1 else "stealth": 30}
                if number != 2
                else {}
            ),
            credit_maximum=5 if number == 2 else None,
            order_note=order,
        )
        pages = raw["pages"]
        blocks = [
            b["block_id"]
            for b in package["ir"]["blocks"]
            if b["source_position"]["physical_page"] in pages
            and b["source_position"]["file_reference"].endswith(".pdf")
        ]
        handouts.append(
            PreparedHandout(
                id=key,
                title=f"吱乎鲁的呼唤 · {key}",
                text=player_text,
                source_hash=package["ir"]["source_hash"],
                source_pages=pages,
                source_block_ids=blocks,
                adjustments=adjustments,
            ).model_dump(mode="json")
        )
        exclusions.append({"id": key, "pages": pages, "keeper_only_excluded_text": excluded})
    package["handouts"] = handouts
    write(OUTPUT / "package-reviewed.json", package)
    write(
        OUTPUT / "handout-source-review.json",
        {
            "original_files_verified": originals,
            "basis": [
                "source-review.md",
                "source-effective-character-sheets.json",
                "private-handouts.json",
            ],
            "visual_review": [5, 7, 9],
            "source_hash": package["ir"]["source_hash"],
            "excluded_keeper_notes": exclusions,
            "order_note": order,
            "prior_effective_table_correction": (
                "第41批HO2示例DEX60→90时闪避仍50；保持原20点分配，本批重算闪避基底30→45，最终应65。"
            ),
            "scope": "事前文本HO及建卡调整；未实施天眼/弹幕能力、定时追加或时间回退",
        },
    )
    return package


if __name__ == "__main__":
    result = build()
    print(f"Built {len(result['handouts'])} source-reviewed handouts in {OUTPUT}")
