"""Frozen batch-4 cases plus independently worded holdout; no source text is exported."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import evaluate_knowledge as original  # noqa: E402

HOLDOUT = [
    ("创建人物时，力量和体质分别投什么骰子？", "rulebook", [24, 25], "creation"),
    ("调查员变老以后怎样提高教育属性？", "rulebook", [27, 38], "creation"),
    ("不用随机掷骰，能否用460点分配属性？", "rulebook", [39], "creation"),
    ("本职技能的点数与个人兴趣点数分别怎么计算？", "rulebook", [30, 31], "creation"),
    ("检定的常规、困难、极难三个难度如何比较？", "rulebook", [72, 73], "difficulty"),
    ("一次检定什么时候算大成功，什么时候算大失败？", "rulebook", [77], "critical"),
    ("获得奖励骰时，额外骰子如何影响检定？", "rulebook", [79], "bonus"),
    ("调查员手册里，1D100百分骰要怎样读取？", "investigator_handbook", [9], "overlap"),
    ("反物质曲率护盾的星际燃料效率公式", None, [], "no_answer"),
]


def evaluate(repo, cases):
    frozen = original.CASES
    try:
        original.CASES = cases
        return original.evaluate(repo)
    finally:
        original.CASES = frozen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = original.KnowledgeRepository(args.database)
    frozen = list(original.CASES)
    report = {
        "original_sha256": hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest(),
        "holdout_sha256": hashlib.sha256(
            json.dumps(HOLDOUT, ensure_ascii=False).encode()
        ).hexdigest(),
        "original": evaluate(repo, frozen),
        "holdout": evaluate(repo, HOLDOUT),
        "combined": evaluate(repo, frozen + HOLDOUT),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k]["metrics"] for k in ("original", "holdout", "combined")}))


if __name__ == "__main__":
    main()
