import json
import sqlite3
import zipfile
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError

from app.knowledge.extraction import extract
from app.knowledge.indexer import KnowledgeIndexer, safe_path, sha256
from app.knowledge.repository import KnowledgeRepository, source_view
from app.knowledge.retriever import KnowledgeRetriever
from app.knowledge.schemas import KnowledgeSource, SearchArgs
from app.knowledge.service import KnowledgeContextBuilder
from app.knowledge.text import clean_pages, normalize, split_page, tokens


@pytest.fixture
def knowledge(tmp_path):
    data = tmp_path / "data"
    (data / "rules").mkdir(parents=True)
    (data / "modules" / "甲模组").mkdir(parents=True)
    (data / "modules" / "乙模组").mkdir(parents=True)
    (data / "rules" / "练习.txt").write_text(
        "# 原创练习规则\n技能检定使用百分骰。困难成功是一个难度标签。奖励骰增加一个候选结果。\n"
        "Power 60 requires 1D100. A 25% example.",
        encoding="utf-8",
    )
    for title, secret in [("甲模组", "紫色月轮只在甲出现"), ("乙模组", "红色星门只在乙出现")]:
        (data / "modules" / title / "开场.md").write_text(
            f"# 开场\n共同词灯塔\n{secret}", encoding="utf-8"
        )
    repo = KnowledgeRepository(tmp_path / "knowledge.db")
    indexer = KnowledgeIndexer(data, repo)
    indexer.index()
    return data, repo, indexer


def refs(repo, title=None):
    return [
        {"source_id": s.source_id, "source_hash": s.source_hash}
        for s in repo.sources()
        if title is None or s.title == title
    ]


def test_partial_missing_index_and_scene_filter(knowledge):
    _, repo, _ = knowledge
    source = next(s for s in repo.sources() if s.title == "甲模组")
    ref = {"source_id": source.source_id, "source_hash": source.source_hash}
    assert repo.available(**ref)
    with repo.connect() as db:
        db.execute(
            "UPDATE knowledge_chunks SET scene_id='other' WHERE source_id=?", (source.source_id,)
        )
    assert not KnowledgeRetriever(repo).search(
        "共同词灯塔", refs=[ref], run_id="test", kind="module", keeper=True, scene_id="opening"
    )
    with repo.connect() as db:
        db.execute(
            "DELETE FROM knowledge_fts WHERE chunk_id IN "
            "(SELECT chunk_id FROM knowledge_chunks WHERE source_id=?)",
            (source.source_id,),
        )
    assert not repo.available(**ref)


def test_public_claim_candidates_preserve_extract_and_scene_identity():
    context = {
        "triggering_action": {"payload": {"text": "奖励骰规则"}},
        "RULE_EVIDENCE": [{"excerpt": "奖励骰增加一个候选结果。", "evidence_id": "ev_example"}],
        "module": {
            "id": "different-module",
            "scene": {"id": "opening", "public_description": "已经公开的开场。"},
        },
    }
    options = KnowledgeContextBuilder.public_claim_options(context)
    assert options[0]["statement"] == "奖励骰增加一个候选结果。"
    assert options[0]["evidence_ids"] == ["ev_example"]
    assert options[-1]["entity_ids"] == ["opening"]


@pytest.mark.parametrize(
    "reference",
    [
        "../secret.txt",
        "rules/../../secret.txt",
        "/etc/passwd",
        "C:/Windows/system.ini",
        "C:secret",
        "rules/test.txt:stream",
    ],
)
def test_path_boundary(tmp_path, reference):
    with pytest.raises(ValueError):
        safe_path(tmp_path, reference)


def test_symlink_boundary(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(ValueError):
        safe_path(root, "escape/secret.txt")


def test_pdf_pages_labels_blank_and_hash(tmp_path):
    (tmp_path / "rules").mkdir()
    path = tmp_path / "rules" / "original.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Original synthetic skill test. Power 60.")
        doc.new_page()
        doc.set_page_labels([{"startpage": 0, "prefix": "R-", "style": "D", "firstpagenum": 7}])
        doc.save(path)
    pages = extract(path)
    assert pages[0]["physical_page"] == 1 and pages[0]["page_label"] == "R-7"
    repo = KnowledgeRepository(tmp_path / "knowledge.db")
    indexer = KnowledgeIndexer(tmp_path, repo)
    source = indexer.index("rules")[0]["source"]
    assert (source.page_count, source.extracted_pages, source.extraction_status) == (
        2,
        1,
        "partial",
    )
    assert source.files[0]["failed_pages"] == [2]
    assert source.source_hash == sha256(path)
    with repo.connect() as db:
        chunk = db.execute("SELECT * FROM knowledge_chunks").fetchone()
        assert chunk["physical_page"] == 1 and chunk["page_label"] == "R-7"


def test_scanned_pdf_requires_ocr(tmp_path):
    (tmp_path / "rules").mkdir()
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(tmp_path / "rules/scan.pdf")
    source = KnowledgeIndexer(tmp_path, KnowledgeRepository(tmp_path / "k.db")).index()[0]["source"]
    assert source.extraction_status == "ocr_required" and source.chunk_count == 0


def test_docx_and_word_preference(knowledge):
    data, repo, indexer = knowledge
    directory = data / "modules/甲模组"
    with zipfile.ZipFile(directory / "同名.docx", "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>原创优先文本</w:t></w:r></w:p></w:body></w:document>',
        )
    (directory / "同名.doc").write_bytes(b"not opened because docx is preferred")
    (directory / "同名.pdf").write_bytes(b"not opened because docx is preferred")
    item = next(x["source"] for x in indexer.index("modules") if x["source"].title == "甲模组")
    files = {f["name"]: f for f in item.files}
    assert files["同名.docx"]["status"] == "indexed"
    assert files["同名.doc"]["status"] == files["同名.pdf"]["status"] == "skipped_preferred_word"
    assert "原创优先文本" in extract(directory / "同名.docx")[0]["text"]


def test_unsupported_cached_outputs_hash_and_manifest(knowledge):
    data, repo, indexer = knowledge
    directory = data / "modules/甲模组"
    (directory / "image.png").write_bytes(b"original image")
    (directory / "cache").mkdir()
    (directory / "cache/out.txt").write_text("must never be indexed")
    (directory / "manifest.json").write_text(
        json.dumps(
            {"source_id": "stable-original", "title": "显示标题", "module_id": "stopped-clock"}
        ),
        encoding="utf-8",
    )
    source = indexer.index_source("modules/甲模组", "module")[0]
    assert source.source_id == "stable-original" and source.module_id == "stopped-clock"
    assert source.title == "显示标题" and source.visibility == "keeper_only"
    assert {f["status"] for f in source.files} >= {
        "manifest",
        "unsupported",
        "skipped_cache_or_output",
        "indexed",
    }
    unchanged = indexer.index_source("modules/甲模组", "module")
    assert not unchanged[1]
    (directory / "cache/out.txt").write_text("different output")
    assert indexer.index_source("modules/甲模组", "module")[0].source_hash == source.source_hash
    (directory / "image.png").write_bytes(b"changed original image")
    assert indexer.index_source("modules/甲模组", "module")[0].source_hash != source.source_hash
    assert repo.source(source.source_id, source.source_hash)


def test_stable_order_and_versions(knowledge):
    data, repo, indexer = knowledge
    original = refs(repo, "甲模组")[0]
    (data / "modules/AAA").mkdir()
    indexer.index("modules")
    assert refs(repo, "甲模组")[0] == original
    (data / "modules/甲模组/开场.md").write_text("新的原创蓝色门扉", encoding="utf-8")
    indexer.index("modules")
    assert refs(repo, "甲模组")[0]["source_hash"] != original["source_hash"]
    assert repo.source(**original).chunk_count
    old = KnowledgeRetriever(repo).search(
        "紫色月轮", refs=[original], kind="module", keeper=True, run_id="r"
    )
    assert old and old[0]["source_hash"] == original["source_hash"]


@pytest.mark.parametrize("query", ["技能检定", "困难成功", "奖励骰", "Power", "1D100", "25%"])
def test_chinese_english_numeric_retrieval(knowledge, query):
    _, repo, _ = knowledge
    result = KnowledgeRetriever(repo).search(query, refs=refs(repo), run_id="run")
    assert result and result[0]["source_kind"] == "rulebook"
    assert len(result[0]["excerpt"]) <= 420


def test_module_boundary_roles_versions_and_stable_evidence(knowledge):
    _, repo, _ = knowledge
    retriever = KnowledgeRetriever(repo)
    selected = refs(repo, "甲模组")
    with pytest.raises(PermissionError):
        retriever.search("共同词", refs=selected, run_id="r", kind="module")
    result = retriever.search("共同词", refs=selected, run_id="r", kind="module", keeper=True)
    assert result and all(e["source_title"] == "甲模组" for e in result)
    assert result == retriever.search(
        "共同词", refs=selected, run_id="r", kind="module", keeper=True
    )
    assert (
        result[0]["evidence_id"]
        != retriever.search("共同词", refs=selected, run_id="other", kind="module", keeper=True)[0][
            "evidence_id"
        ]
    )
    assert not retriever.search("共同词", refs=[], run_id="r", kind="module", keeper=True)
    assert not retriever.search(
        "共同词", refs=selected, run_id="r", kind="module", keeper=True, edition="coc6"
    )
    assert not retriever.search(
        "共同词",
        refs=[{**selected[0], "source_hash": "0" * 64}],
        run_id="r",
        kind="module",
        keeper=True,
    )
    assert not retriever.search("qzxv星际跃迁量子加速", refs=refs(repo), run_id="r")


def test_top_k_page_limit_exact_phrase_adjacent(knowledge):
    data, repo, indexer = knowledge
    text = "".join(f"这是第{i}段原创场景记录。" * 40 + "\n\n" for i in range(5))
    (data / "rules/long.txt").write_text(text + "精确短语定位。" * 5, encoding="utf-8")
    indexer.index("rules")
    result = KnowledgeRetriever(repo).search("精确短语定位", refs=refs(repo), run_id="r", top_k=100)
    assert result and "精确短语定位" in result[0]["excerpt"]
    assert len(result) <= 6 and len({e["chunk_id"] for e in result}) == len(result)


def test_normalization_chunk_overlap_and_repeated_headers():
    assert normalize("ＰＯＷ ６０％\n奖 励 骰 hy-\nphen") == "pow 60% 奖励骰 hyphen"
    assert {"奖励", "奖励骰", "1d100"} <= set(tokens("奖励骰 1D100"))
    text = "段落甲" * 180 + "\n\n" + "段落乙" * 230
    pieces = list(split_page(text))
    assert all(end - start <= 800 for start, end, _ in pieces)
    assert pieces[1][0] == pieces[0][1] - 100
    pages = list(clean_pages([{"text": f"共同页眉\n第{i}页正文\n共同页脚"} for i in range(5)]))
    assert all("共同页眉" not in page["text"] and "正文" in page["text"] for page in pages)


def test_fts_probe_failure_clear(monkeypatch, tmp_path):
    class NoFTS:
        def execute(self, *args):
            raise sqlite3.OperationalError("no such module: fts5")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: NoFTS())
    with pytest.raises(ValueError, match="fts5_unavailable"):
        KnowledgeRepository(tmp_path / "bad.db").initialize()


def test_cleanup_only_unreferenced_versions(knowledge):
    data, repo, indexer = knowledge
    original = refs(repo, "练习.txt")[0]
    file = data / "rules/练习.txt"
    file.write_text("修改后的原创规则。", encoding="utf-8")
    indexer.index("rules")
    pair = (original["source_id"], original["source_hash"])
    assert pair in repo.cleanup(set())["versions"]
    assert not repo.cleanup({pair}, apply=True)["versions"]
    assert repo.source(**original)
    repo.cleanup(set(), apply=True)
    assert repo.source(**original) is None and file.is_file()
    assert repo.verify()["ok"]


def test_context_permissions_before_budget_and_diversity(knowledge):
    _, repo, _ = knowledge
    retriever = KnowledgeRetriever(repo)
    hidden = retriever.search("共同词", refs=refs(repo), run_id="r", kind="module", keeper=True)
    rules = retriever.search("技能检定", refs=refs(repo), run_id="r")
    selected = KnowledgeContextBuilder.select(hidden + rules, 1800, public_only=True)
    assert selected and all(e["visibility"] == "public_rules" for e in selected)
    assert len(json.dumps(selected, ensure_ascii=False)) <= 1800
    selected = KnowledgeContextBuilder.select(
        rules + [dict(rules[0], evidence_id="other")] + hidden, 2800, public_only=False
    )
    assert len({e["source_id"] for e in selected}) >= 2
    crowded = [
        dict(rules[0], excerpt="规则文本" * 100, score=100),
        dict(rules[0], source_id="another-rule", evidence_id="another", score=90),
    ]
    selected = KnowledgeContextBuilder.select(crowded + hidden[:1], 1800, public_only=False)
    assert {e["source_kind"] for e in selected} == {"rulebook", "module"}
    assert len(json.dumps(selected, ensure_ascii=False)) <= 1800


def test_source_and_query_validation_and_safe_views(knowledge):
    _, repo, _ = knowledge
    with pytest.raises(ValidationError):
        SearchArgs(query="x", top_k=7)
    with pytest.raises(ValidationError):
        KnowledgeSource(source_id="x", title="x", source_hash="bad")
    for source in repo.sources():
        assert "relative_reference" not in source_view(source)
        assert str(Path.cwd()) not in json.dumps(source_view(source))
