import hashlib
import zipfile

import pymupdf
import pytest
from pydantic import ValidationError

from app.knowledge.indexer import KnowledgeIndexer
from app.knowledge.repository import KnowledgeRepository
from app.module_ir.parser import parse_module
from app.module_ir.schemas import ModuleDocumentIR


def indexed(tmp_path, files):
    folder = tmp_path / "data/modules/synthetic"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / name).write_text(content, encoding="utf-8")
    repository = KnowledgeRepository(tmp_path / "knowledge.db")
    repository.initialize()
    indexer = KnowledgeIndexer(tmp_path / "data", repository)
    source, _ = indexer.index_source("modules/synthetic", "module")
    return source, indexer, folder


def test_markdown_order_stable_ids_duplicate_headings_and_new_version(tmp_path):
    source, indexer, folder = indexed(
        tmp_path, {"chapter.md": "# A\nfirst\n## B\n- one\n- two\n## B\nlast\n"}
    )
    first = parse_module(source, indexer.root)
    second = parse_module(source, indexer.root)
    assert [n.node_id for n in first.nodes] == [n.node_id for n in second.nodes]
    assert [b.block_id for b in first.blocks] == [b.block_id for b in second.blocks]
    assert len({n.node_id for n in first.nodes if n.title == "B"}) == 2
    assert [b.text for b in first.blocks] == [
        "# A",
        "first",
        "## B",
        "- one",
        "- two",
        "## B",
        "last",
    ]
    assert [b.block_type for b in first.blocks][3:5] == ["list", "list"]
    (folder / "chapter.md").write_text("# A\nchanged", encoding="utf-8")
    with pytest.raises(ValueError, match="hash_changed"):
        parse_module(source, indexer.root)
    new_source, _ = indexer.index_source("modules/synthetic", "module")
    assert parse_module(new_source, indexer.root).structure_version != first.structure_version


def test_text_single_node_fallback_and_generic_numbering(tmp_path):
    source, indexer, folder = indexed(tmp_path, {"text.txt": "plain\nordinary body\n"})
    ir = parse_module(source, indexer.root)
    assert ir.node_count == 1
    assert "single_node_fallback" in ir.warnings[0]
    (folder / "text.txt").write_text("第一章 开始\n正文。\n1.1 走廊\n下一段。", encoding="utf-8")
    source, _ = indexer.index_source("modules/synthetic", "module")
    ir = parse_module(source, indexer.root)
    assert ir.node_count == 3
    assert all(n.confidence < 0.8 for n in ir.nodes[1:])
    assert sum("host_review_required" in w for w in ir.warnings) == 2


def test_multifile_isolation_and_schema_rejects_reordering(tmp_path):
    source, indexer, _ = indexed(tmp_path, {"a.md": "# One\nA", "b.md": "## Two\nB"})
    ir = parse_module(source, indexer.root)
    nodes = {n.node_id: n for n in ir.nodes}
    for n in ir.nodes:
        if n.title in {"One", "Two"}:
            assert nodes[n.parent_node_id].detected_type == "document"
    bad = ir.model_dump()
    bad["blocks"] = list(reversed(bad["blocks"]))
    with pytest.raises(ValidationError, match="order_changed"):
        ModuleDocumentIR.model_validate(bad)


def test_docx_explicit_outline_style_table_and_list_order(tmp_path):
    folder = tmp_path / "data/modules/synthetic"
    folder.mkdir(parents=True)
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(folder / "word.docx", "w") as z:
        z.writestr(
            "word/styles.xml",
            f'<w:styles xmlns:w="{namespace}"><w:style w:styleId="Heading1">'
            '<w:name w:val="Heading 1"/></w:style></w:styles>',
        )
        z.writestr(
            "word/document.xml",
            f'''<w:document xmlns:w="{namespace}"><w:body>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Heading</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/><w:outlineLvl w:val="2"/></w:pPr>
        <w:r><w:t>Outline wins</w:t></w:r></w:p>
        <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Bold ordinary prose.</w:t></w:r></w:p>
        <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
        <w:r><w:t>list one</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:p><w:r><w:t>after table</w:t></w:r></w:p></w:body></w:document>''',
        )
    source, indexer, _ = indexed(tmp_path, {})
    ir = parse_module(source, indexer.root)
    assert ir.nodes[1].detection_source == "word_style"
    assert ir.nodes[2].detection_source == "outline_level"
    assert ir.blocks[1].outline_level == 2
    assert ir.blocks[2].block_type == "paragraph"
    assert ir.blocks[3].block_type == "list"
    assert ir.blocks[4].table_structure == [["A", "B"]]
    assert ir.blocks[5].text == "after table"
    assert all(b.page_reference is None for b in ir.blocks)


def test_pdf_outline_pages_and_source_hash(tmp_path):
    folder = tmp_path / "data/modules/synthetic"
    folder.mkdir(parents=True)
    with pymupdf.open() as pdf:
        for text in ("Opening", "Later"):
            page = pdf.new_page()
            page.insert_text((50, 50), text)
            page.insert_text((50, 100), "Body text.")
        pdf.set_toc([[1, "Opening", 1], [1, "Later", 2]])
        pdf.save(folder / "pages.pdf")
    source, indexer, _ = indexed(tmp_path, {})
    ir = parse_module(source, indexer.root)
    assert [n.title for n in ir.nodes[1:]] == ["Opening", "Later"]
    assert all(n.detection_source == "bookmark" for n in ir.nodes[1:])
    assert [n.source_position.physical_page for n in ir.nodes[1:]] == [1, 2]
    assert ir.source_hash == source.source_hash


def test_repository_validates_missing_blocks_and_keeps_old_versions(tmp_path):
    from app.module_ir.repository import StructureRepository

    source, indexer, _ = indexed(tmp_path, {"a.md": "# One\nbody"})
    ir = parse_module(source, indexer.root)
    repository = StructureRepository(indexer.repository)
    stored = repository.store(ir)
    assert stored.structure_version == repository.get(ir.structure_version).structure_version
    with indexer.repository.connect() as db:
        db.execute("DELETE FROM module_blocks WHERE block_id=?", (ir.blocks[0].block_id,))
    assert repository.get(ir.structure_version) is None
    assert repository.store(ir).block_count == 2


def test_raw_word_path_never_changes_source_and_preserves_metadata(tmp_path, monkeypatch):
    from app.module_ir import parser

    source, indexer, folder = indexed(tmp_path, {"fixture.doc": "synthetic"})
    source.files[0]["status"] = "indexed"
    before = hashlib.sha256((folder / "fixture.doc").read_bytes()).hexdigest()
    monkeypatch.setattr(
        parser,
        "extract",
        lambda path, structure: [
            dict(
                paragraph=0,
                text="First",
                physical_page=1,
                heading_level=1,
                detection_source="outline_level",
                confidence=1.0,
                outline_level=0,
                style_name="Custom Heading",
                block_type="heading",
            )
        ],
    )
    ir = parse_module(source, indexer.root)
    assert ir.blocks[0].page_reference == 1
    assert ir.blocks[0].style_name == "Custom Heading"
    assert hashlib.sha256((folder / "fixture.doc").read_bytes()).hexdigest() == before
    assert "word_com" in ir.extraction_method
