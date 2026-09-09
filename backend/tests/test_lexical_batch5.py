from pathlib import Path

import pytest
from test_knowledge import knowledge, refs  # noqa: F401

from app.knowledge.lexical import decompose, query_channels, weighted_rrf
from app.knowledge.retriever import KnowledgeRetriever
from scripts.evaluate_batch5 import HOLDOUT
from scripts.evaluate_knowledge import CASES


@pytest.mark.parametrize(
    "query,required",
    [
        ("力量、体质和敏捷", {"力量", "体质", "敏捷"}),
        ("奖励骰与惩罚骰", {"奖励骰", "惩罚骰"}),
        ("普通成功困难成功极难成功", {"普通成功", "困难成功", "极难成功", "常规难度"}),
        ("spot_hidden检定1D100", {"spot_hidden", "1d100"}),
    ],
)
def test_deterministic_decomposition_preserves_concepts(query, required):
    assert required <= set(decompose(query)[1])


def test_multiple_lexical_channels_and_weighted_fusion():
    channels = query_channels("奖励骰与惩罚骰")
    assert set(channels) == {"bigram", "trigram", "phrase", "title"}
    assert "奖励" in channels["bigram"] and "奖励骰" in channels["trigram"]
    assert "奖励骰" in channels["phrase"] and "惩罚骰" in channels["title"]
    scores = weighted_rrf({"a": ["x", "y"], "b": ["y", "z"]}, {"a": 1, "b": 2})
    assert scores["y"] > scores["z"] > scores["x"]


def test_independent_holdout_and_no_query_page_mapping():
    assert len(HOLDOUT) >= 8 and len({c[0] for c in HOLDOUT}) == len(HOLDOUT)
    assert not {c[0] for c in HOLDOUT} & {c[0] for c in CASES}
    implementation = "".join(
        p.read_text(encoding="utf-8")
        for p in (Path(__file__).parents[1] / "app/knowledge").glob("*.py")
    )
    assert "expected_pages" not in implementation
    assert all(query not in implementation for query, *_ in CASES + HOLDOUT)


def test_no_answer_and_module_boundary_after_fusion(knowledge):  # noqa: F811
    _, repo, _ = knowledge
    retriever = KnowledgeRetriever(repo)
    assert not retriever.search("反物质曲率护盾的星际燃料效率公式", refs=refs(repo), run_id="none")
    result = retriever.search(
        "灯塔紫色月轮", refs=refs(repo, "甲模组"), run_id="module", kind="module", keeper=True
    )
    assert result and all(e["source_title"] == "甲模组" for e in result)
    assert all("红色星门" not in e["excerpt"] for e in result)


def test_cross_book_and_multi_concept_coverage(knowledge):  # noqa: F811
    data, repo, indexer = knowledge
    (data / "rules/调查员手册.txt").write_text(
        "奖励骰：候选骰规则示例。\n惩罚骰：另一候选骰规则示例。", encoding="utf-8"
    )
    (data / "rules/规则书.txt").write_text(
        "奖励骰：一次公开规则示例。\n" + "奖励骰增加一个候选结果。" * 180, encoding="utf-8"
    )
    indexer.index("rules")
    result = KnowledgeRetriever(repo).search(
        "奖励骰与惩罚骰", refs=refs(repo), run_id="coverage", top_k=5
    )
    assert len({e["source_id"] for e in result}) >= 2
    assert any("惩罚骰" in e["excerpt"] for e in result)


def test_same_page_dedup(knowledge):  # noqa: F811
    _, repo, _ = knowledge
    with repo.connect() as db:
        db.execute("UPDATE knowledge_chunks SET physical_page=1 WHERE visibility='public_rules'")
    result = KnowledgeRetriever(repo).search(
        "技能检定奖励骰", refs=refs(repo), run_id="dedup", top_k=6
    )
    pages = [(e["source_id"], e["file_name"], e["physical_page"]) for e in result]
    assert len(pages) == len(set(pages))
