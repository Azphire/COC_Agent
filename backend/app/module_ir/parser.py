"""Loss-aware extraction. No model, OCR, document mutation or semantic scene inference."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf

from app.knowledge.extraction import extract
from app.knowledge.indexer import IGNORED_DIRS, safe_path, sha256
from app.knowledge.text import normalize
from app.module_ir.schemas import ModuleBlock, ModuleDocumentIR, ModuleNode, Position

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def stable_id(prefix, *parts):
    return prefix + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


def numbered_heading(text):
    # Deliberately conservative: a short complete numbered title, not prose or a bold sentence.
    if len(text) > 70 or re.search(r"[。！？!?；;]", text):
        return None
    if re.match(r"^第[零一二三四五六七八九十百千0-9]+[章节部篇卷]\s*\S+", text):
        return 1
    if re.match(r"^[一二三四五六七八九十百]+[、．.]\s*\S+", text):
        return 1
    match = re.match(r"^(\d+(?:\.\d+)+)[.、\s]\s*\S+", text)
    if match:
        return min(8, len(match[1].split(".")))
    if re.match(r"^\d+[、.)．]\s*\S+", text):
        return 2
    if re.match(r"^[<＜【][^<>＜＞【】]{1,30}[>＞】][^。！？!?]{0,25}$", text):
        return 1
    if re.match(r"^(?:END|Ending|Appendix)\s+[A-Z0-9]+\b.{0,30}$", text, re.I):
        return 1
    return None


def xml_text(element):
    return "".join(
        n.text or "" if n.tag == W + "t" else "\t" if n.tag == W + "tab" else "\n"
        for n in element.iter()
        if n.tag in {W + "t", W + "tab", W + "br"}
    )


def docx_blocks(path):
    with zipfile.ZipFile(path) as archive:

        def read(name):
            if name not in archive.namelist():
                return None
            if archive.getinfo(name).file_size > 30_000_000:
                raise ValueError("document_too_large")
            return ET.fromstring(archive.read(name))

        root, styles_xml = read("word/document.xml"), read("word/styles.xml")
    styles = {}
    if styles_xml is not None:
        for style in styles_xml.findall(W + "style"):
            name, parent = style.find(W + "name"), style.find(W + "basedOn")
            outline = style.find(f"{W}pPr/{W}outlineLvl")
            styles[style.get(W + "styleId")] = {
                "name": name.get(W + "val") if name is not None else "",
                "parent": parent.get(W + "val") if parent is not None else None,
                "outline": int(outline.get(W + "val")) if outline is not None else None,
            }

    def style_outline(style_id, seen=None):
        seen = seen or set()
        if style_id in seen or style_id not in styles:
            return None
        seen.add(style_id)
        entry = styles[style_id]
        return (
            entry["outline"]
            if entry["outline"] is not None
            else style_outline(entry["parent"], seen)
        )

    result, paragraph = [], 0
    body = root.find(W + "body")
    if body is None:
        raise ValueError("document_body_missing")
    for child in body:
        if child.tag == W + "tbl":
            table = [
                [
                    "\n".join(xml_text(p) for p in cell.findall(W + "p"))
                    for cell in row.findall(W + "tc")
                ]
                for row in child.findall(W + "tr")
            ]
            count = len(list(child.iter(W + "p")))
            result.append(
                dict(
                    text="\n".join("\t".join(row) for row in table),
                    block_type="table",
                    table_structure=table,
                    paragraph=paragraph,
                    paragraph_end=paragraph + max(0, count - 1),
                )
            )
            paragraph += count
        elif child.tag == W + "p":
            properties = child.find(W + "pPr")
            style_node = child.find(f"{W}pPr/{W}pStyle")
            style_id = style_node.get(W + "val") if style_node is not None else ""
            style_name = styles.get(style_id, {}).get("name", style_id)
            outline = child.find(f"{W}pPr/{W}outlineLvl")
            level = int(outline.get(W + "val")) if outline is not None else style_outline(style_id)
            numbering = child.find(f"{W}pPr/{W}numPr")
            text = xml_text(child)
            block = dict(
                text=text,
                paragraph=paragraph,
                paragraph_end=paragraph,
                style_name=style_name or None,
                outline_level=level,
                block_type="list" if numbering is not None else "paragraph",
            )
            semantic_style = style_name.lower().replace(" ", "_")
            if semantic_style in {"read_aloud", "keeper_note", "stat_block"}:
                block["block_type"] = semantic_style
            if numbering is not None:
                block["numbering"] = ET.tostring(numbering, encoding="unicode")
            if level is not None and level < 9:
                block.update(
                    heading_level=level + 1, detection_source="outline_level", confidence=1
                )
            elif level is None and (
                match := re.match(r"^(?:heading|标题)\s*(\d+)$", style_name, re.I)
            ):
                block.update(
                    heading_level=int(match[1]), detection_source="word_style", confidence=1
                )
            elif re.match(r"^(?:toc|目录)\s*\d+", style_name, re.I):
                block.update(heading_level=1, detection_source="toc", confidence=0.65)
            # Explicit breaks are known positions, not reliable physical page numbers.
            if properties is not None and properties.find(W + "pageBreakBefore") is not None:
                block["page_break"] = True
            block["bookmark_names"] = [
                n.get(W + "name")
                for n in child.iter(W + "bookmarkStart")
                if n.get(W + "name") and n.get(W + "name") != "_GoBack"
            ]
            result.append(block)
            paragraph += 1
    return result, ["docx_physical_pagination_unavailable"]


def raw_blocks(path):
    if path.suffix.lower() == ".docx":
        blocks, warnings = docx_blocks(path)
        return blocks, "docx_xml", warnings
    if path.suffix.lower() == ".doc":
        blocks = extract(path, structure=True)
        return blocks, "word_com_readonly_structure", []
    if path.suffix.lower() == ".pdf":
        result, warnings = [], []
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise ValueError("encrypted_pdf")
            toc = doc.get_toc()
            for page_index, page in enumerate(doc, 1):
                entries = [(level, title) for level, title, p in toc if p == page_index]
                paragraphs = page.get_text("blocks", sort=False)
                tables = page.find_tables().tables
                emitted_tables = set()
                if not paragraphs:
                    warnings.append(f"pdf_no_text_page:{page_index}")
                for index, block in enumerate(paragraphs):
                    if len(block) > 6 and block[6] != 0:
                        continue
                    text = block[4].strip()
                    item = dict(
                        text=text,
                        paragraph=len(result),
                        physical_page=page_index,
                        page_label=page.get_label() or None,
                    )
                    table_index = next(
                        (
                            i
                            for i, t in enumerate(tables)
                            if pymupdf.Rect(t.bbox).intersects(pymupdf.Rect(block[:4]))
                        ),
                        None,
                    )
                    if table_index is not None:
                        if table_index in emitted_tables:
                            continue
                        emitted_tables.add(table_index)
                        rows = [
                            [value or "" for value in row] for row in tables[table_index].extract()
                        ]
                        item.update(
                            block_type="table",
                            table_structure=rows,
                            text="\n".join("\t".join(row) for row in rows),
                        )
                        result.append(item)
                        continue
                    matching = next(
                        (
                            (level, title)
                            for level, title in entries
                            if normalize(title) == normalize(text)
                        ),
                        None,
                    )
                    if not matching and index == 0 and entries:
                        matching = entries[0]
                        item["heading_title"] = matching[1]
                    if matching:
                        item.update(
                            heading_level=matching[0], detection_source="bookmark", confidence=1
                        )
                    result.append(item)
        return result, "pymupdf_text_outline", warnings
    text = extract(path)[0]["text"]
    result, offset, fenced, table_until = [], 0, False, -1
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        value = line.rstrip("\r\n")
        item = dict(text=value, paragraph=index, offset_start=offset, offset_end=offset + len(line))
        offset += len(line)
        if index <= table_until:
            continue
        if path.suffix.lower() == ".md":
            if re.match(r"^\s*(```|~~~)", value):
                fenced = not fenced
                item["block_type"] = "unknown"
            elif fenced:
                item["block_type"] = "unknown"
            elif (
                "|" in value
                and index + 1 < len(lines)
                and re.match(r"^\s*\|?\s*:?-+:?\s*\|[| :\-]+$", lines[index + 1].strip())
            ):
                end = index + 2
                while end < len(lines) and "|" in lines[end] and lines[end].strip():
                    end += 1
                table_until = end - 1
                item.update(
                    block_type="table",
                    text="".join(lines[index:end]).rstrip(),
                    table_structure=[
                        [cell.strip() for cell in row.strip().strip("|").split("|")]
                        for row in [lines[index], *lines[index + 2 : end]]
                    ],
                    paragraph_end=table_until,
                    offset_end=item["offset_start"] + sum(len(row) for row in lines[index:end]),
                )
            elif match := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", value):
                item.update(
                    heading_level=len(match[1]),
                    heading_title=match[2],
                    detection_source="format_heuristic",
                    confidence=1,
                )
            elif re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", value):
                item.update(block_type="list", numbering=value.split()[0])
        result.append(item)
    return result, "markdown" if path.suffix.lower() == ".md" else "text", []


def validate_source(source, data_dir):
    """Verify the complete original inventory without extracting or changing documents."""
    root = safe_path(Path(data_dir), source.relative_reference)
    original_files = {f["reference"]: f["sha256"] for f in source.files if f.get("sha256")}
    current_files = {}
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        reference = candidate.relative_to(root).as_posix()
        if candidate.name.startswith(("~$", ".")) or any(
            part.lower() in IGNORED_DIRS for part in Path(reference).parts[:-1]
        ):
            continue
        current_files[reference] = sha256(safe_path(root, reference))
    if current_files != original_files:
        raise ValueError("source_hash_changed_reindex_required")
    return root


def parse_module(source, data_dir):
    """Parse only the selected indexed source, validating every original file hash."""
    root = validate_source(source, data_dir)
    selected = [f for f in source.files if f["status"] in {"indexed", "partial"}]
    if not selected:
        raise ValueError("module_text_missing")
    nodes, blocks, warnings, methods = [], [], [], []
    root_id = stable_id("node_", source.source_id, source.source_hash, "root")
    root_node = ModuleNode(
        node_id=root_id,
        source_order=0,
        depth=0,
        title=source.title[:240],
        normalized_title=normalize(source.title),
        heading_path=[source.title[:240]],
        detected_type="document",
        detection_source="format_heuristic",
        confidence=1,
        source_position=Position(file_reference="", paragraph_start=0, paragraph_end=0),
    )
    nodes.append(root_node)
    for file in selected:
        path = safe_path(root, file["reference"])
        if sha256(path) != file["sha256"]:
            raise ValueError("source_hash_changed_reindex_required")
        raw, method, file_warnings = raw_blocks(path)
        methods.append(method)
        warnings.extend(f"{file['reference']}:{w}" for w in file_warnings)
        file_root = root_node
        if len(selected) > 1:
            file_root = ModuleNode(
                node_id=stable_id("node_", source.source_hash, file["reference"], "root"),
                parent_node_id=root_id,
                source_order=len(blocks),
                depth=1,
                title=path.name[:240],
                normalized_title=normalize(path.name),
                heading_path=[root_node.title, path.name[:240]],
                detected_type="document",
                detection_source="format_heuristic",
                confidence=1,
                source_position=Position(
                    file_reference=file["reference"], paragraph_start=0, paragraph_end=0
                ),
            )
            nodes.append(file_root)
            root_node.child_ids.append(file_root.node_id)
        stack = [(0, file_root)]
        heading_count = 0
        for item in raw:
            text = item.get("text", "")
            if not text.strip() and item.get("block_type") != "table":
                continue
            order = len(blocks)
            position = Position(
                file_reference=file["reference"],
                paragraph_start=item["paragraph"],
                paragraph_end=item.get("paragraph_end", item["paragraph"]),
                physical_page=item.get("physical_page"),
                page_end=item.get("page_end"),
                page_label=item.get("page_label"),
                offset_start=item.get("offset_start"),
                offset_end=item.get("offset_end"),
            )
            level = item.get("heading_level")
            if not level and item.get("block_type") not in {"table", "list", "unknown"}:
                level = numbered_heading(text.strip())
                if level:
                    item.update(detection_source="numbering", confidence=0.55)
                elif (
                    len(text.strip()) <= 35
                    and not re.search(r"[。！？!?，,；;：:]", text)
                    and (
                        14 <= item.get("font_size", 0) <= 96
                        or item.get("bold")
                        and (item.get("alignment") == 1 or item.get("space_before", 0) > 0)
                    )
                ):
                    level = 1
                    item.update(detection_source="format_heuristic", confidence=0.45)
            if level and len(text.strip()) <= 240 or level and item.get("heading_title"):
                heading_count += 1
                while len(stack) > 1 and stack[-1][0] >= level:
                    stack.pop()
                parent = stack[-1][1]
                title = item.get("heading_title", text.strip())[:240]
                node = ModuleNode(
                    node_id=stable_id(
                        "node_", source.source_id, source.source_hash, file["reference"], order
                    ),
                    parent_node_id=parent.node_id,
                    source_order=order,
                    depth=parent.depth + 1,
                    title=title,
                    normalized_title=normalize(title),
                    heading_path=[*parent.heading_path, title],
                    detected_type="chapter" if level == 1 else "section",
                    detection_source=item["detection_source"],
                    confidence=item["confidence"],
                    source_position=position.model_copy(deep=True),
                )
                nodes.append(node)
                parent.child_ids.append(node.node_id)
                stack.append((level, node))
                if node.confidence < 0.8:
                    warnings.append(f"host_review_required:{node.node_id}")
            owner = stack[-1][1]
            content_hash = hashlib.sha256(text.encode()).hexdigest()
            blocks.append(
                ModuleBlock(
                    block_id=stable_id(
                        "block_",
                        source.source_id,
                        source.source_hash,
                        file["reference"],
                        order,
                        content_hash,
                    ),
                    node_id=owner.node_id,
                    source_order=order,
                    block_type="heading" if level else item.get("block_type", "paragraph"),
                    text=text,
                    table_structure=item.get("table_structure"),
                    style_name=item.get("style_name"),
                    outline_level=item.get("outline_level"),
                    numbering=item.get("numbering"),
                    page_break_before=bool(item.get("page_break")),
                    bookmark_names=item.get("bookmark_names", []),
                    page_reference=item.get("physical_page"),
                    source_position=position,
                    content_hash=content_hash,
                )
            )
            for _, ancestor in stack:
                ancestor.source_position.paragraph_end = position.paragraph_end
                ancestor.source_position.page_end = position.page_end or position.physical_page
        if not heading_count:
            warnings.append(f"{file['reference']}:single_node_fallback_host_marking_required")
    return ModuleDocumentIR(
        module_id=source.module_id,
        source_id=source.source_id,
        source_hash=source.source_hash,
        structure_version=stable_id("ir1_", source.source_id, source.source_hash),
        display_title=source.title,
        source_format="+".join(dict.fromkeys(f["type"] for f in selected)),
        extraction_method="+".join(dict.fromkeys(methods)),
        root_node_id=root_id,
        nodes=nodes,
        blocks=blocks,
        node_count=len(nodes),
        block_count=len(blocks),
        warnings=warnings,
    )
