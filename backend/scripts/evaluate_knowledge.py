"""Small reproducible local evaluation. Expected pages are evaluation data, not ranking rules."""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge.repository import KnowledgeRepository  # noqa: E402
from app.knowledge.retriever import KnowledgeRetriever  # noqa: E402

CASES = [
    ("力量体质敏捷外貌意志属性如何生成", "rulebook", [24, 25], "creation"),
    ("年龄教育增强检定", "rulebook", [27, 38], "creation"),
    ("购点法460点", "rulebook", [39], "creation"),
    ("职业技能点兴趣技能点", "rulebook", [30, 31], "creation"),
    ("技能检定和属性检定", "rulebook", [72, 73], "check"),
    ("普通成功困难成功极难成功", "rulebook", [72, 73], "difficulty"),
    ("常规难度困难难度极难难度", "rulebook", [72, 73], "difficulty"),
    ("大成功和大失败", "rulebook", [77], "critical"),
    ("奖励骰与惩罚骰", "rulebook", [79], "bonus"),
    ("奖励骰应该怎样掷", "rulebook", [79], "bonus"),
    ("惩罚骰如何判定", "rulebook", [79], "bonus"),
    ("百分骰1D100", "investigator_handbook", [9], "overlap"),
    ("超光速量子跃迁引擎热寂修正", None, [], "no_answer"),
]


def evaluate(repo):
    refs = [
        {"source_id": s.source_id, "source_hash": s.source_hash}
        for s in repo.sources()
        if s.kind != "module"
    ]
    retriever = KnowledgeRetriever(repo)
    records = []
    for query, kind, pages, category in CASES:
        start = time.perf_counter()
        results = retriever.search(query, refs=refs, run_id="evaluation", top_k=5)
        elapsed = (time.perf_counter() - start) * 1000
        rank = next(
            (
                i
                for i, e in enumerate(results, 1)
                if e["source_kind"] == kind and e["physical_page"] in pages
            ),
            None,
        )
        records.append(
            {
                "query": query,
                "category": category,
                "expected_kind": kind,
                "expected_pages": pages,
                "rank": rank,
                "latency_ms": round(elapsed, 2),
                "no_answer_correct": not results if kind is None else None,
                "results": [
                    {
                        k: e[k]
                        for k in (
                            "evidence_id",
                            "source_id",
                            "source_hash",
                            "source_title",
                            "physical_page",
                            "rank",
                            "score",
                        )
                    }
                    for e in results
                ],
            }
        )
    answered = [r for r in records if r["expected_kind"]]
    metrics = {
        f"Hit@{k}": sum(r["rank"] is not None and r["rank"] <= k for r in answered) / len(answered)
        for k in (1, 3, 5)
    }
    metrics["MRR"] = sum(1 / r["rank"] if r["rank"] else 0 for r in answered) / len(answered)
    metrics["mean_latency_ms"] = statistics.mean(r["latency_ms"] for r in records)
    return {
        "metrics": metrics,
        "cases": records,
        "answerable_count": len(answered),
        "no_answer_correct": all(
            r["no_answer_correct"] for r in records if r["expected_kind"] is None
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(KnowledgeRepository(args.database))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=True))
