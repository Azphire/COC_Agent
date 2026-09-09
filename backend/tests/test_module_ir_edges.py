import hashlib
import zipfile

import pymupdf
import pytest
from pydantic import ValidationError
from test_module_ir import indexed

from app.module_ir.parser import parse_module
from app.module_ir.schemas import ModuleDocumentIR


def test_markdown_table_and_fenced_headings_preserve_source_order(tmp_path):
    source, indexer, _ = indexed(
        tmp_path,
        {
            "a.md": "# Start\n| Name | Value |\n| --- | --- |\n| One | Two |\n"
            "```text\n# Not a heading\n1. Not a chapter\n```\nAfter.\n"
        },
    )
    ir = parse_module(source, indexer.root)
    assert [n.title for n in ir.nodes[1:]] == ["Start"]
    assert ir.blocks[1].table_structure == [["Name", "Value"], ["One", "Two"]]
    assert ir.blocks[-1].text == "After."
    assert ir.blocks[1].source_position.paragraph_end == 3


def test_docx_outline_body_override_bookmarks_and_page_break(tmp_path):
    folder = tmp_path / "data/modules/synthetic"
    folder.mkdir(parents=True)
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(folder / "a.docx", "w") as doc:
        doc.writestr(
            "word/styles.xml",
            f'<w:styles xmlns:w="{ns}">'
            '<w:style w:styleId="Heading1"><w:name w:val="Heading 1"/>'
            "</w:style></w:styles>",
        )
        doc.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{ns}"><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:outlineLvl w:val="9"/>'
            '<w:pageBreakBefore/></w:pPr><w:bookmarkStart w:name="Reference" w:id="1"/>'
            "<w:r><w:t>Ordinary body.</w:t></w:r></w:p></w:body></w:document>",
        )
    source, indexer, _ = indexed(tmp_path, {})
    ir = parse_module(source, indexer.root)
    assert ir.node_count == 1
    assert ir.blocks[0].page_reference is None
    assert ir.blocks[0].page_break_before
    assert ir.blocks[0].bookmark_names == ["Reference"]


def test_pdf_grid_table_is_one_block_between_paragraphs(tmp_path):
    folder = tmp_path / "data/modules/synthetic"
    folder.mkdir(parents=True)
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), "Before.")
        for x in (50, 150, 250):
            page.draw_line((x, 80), (x, 160))
        for y in (80, 120, 160):
            page.draw_line((50, y), (250, y))
        for x, y, text in (
            (60, 105, "Name"),
            (160, 105, "Value"),
            (60, 145, "One"),
            (160, 145, "Two"),
        ):
            page.insert_text((x, y), text)
        page.insert_text((50, 200), "After.")
        pdf.save(folder / "a.pdf")
    source, indexer, _ = indexed(tmp_path, {})
    ir = parse_module(source, indexer.root)
    assert len(ir.blocks) == 3
    assert ir.blocks[1].table_structure == [["Name", "Value"], ["One", "Two"]]
    assert ir.blocks[0].text == "Before." and ir.blocks[2].text == "After."


def test_source_added_attachment_and_block_corruption_are_detected(tmp_path):
    source, indexer, folder = indexed(tmp_path, {"a.md": "# Start\nBody."})
    ir = parse_module(source, indexer.root)
    bad = ir.model_dump()
    bad["blocks"][0]["text"] = "Tampered"
    assert hashlib.sha256(b"Tampered").hexdigest() != bad["blocks"][0]["content_hash"]
    with pytest.raises(ValidationError):
        ModuleDocumentIR.model_validate(bad)
    (folder / "attachment.bin").write_bytes(b"additional source")
    with pytest.raises(ValueError, match="hash_changed"):
        parse_module(source, indexer.root)
