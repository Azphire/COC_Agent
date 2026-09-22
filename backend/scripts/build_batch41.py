"""Build the existing package format from explicitly source-reviewed batch notes."""

import hashlib
import json
from pathlib import Path

from scripts.prepare_batch41 import ROOT, read, write

REVIEWER = "Codex source review, batch-41 (not human/user approval)"


def build(directory):
    from app.knowledge.schemas import KnowledgeSource
    from app.knowledge.text import clean_pages, normalize, split_page
    from app.module_ir.parser import parse_module, stable_id
    from app.module_ir.schemas import ModuleBlock, ModuleDocumentIR, ModuleNode, Position
    from app.preparation.packages import audit
    from app.preparation.schemas import EntityFields

    out = Path(directory)
    spec = read(out / "reviewed-spec.json")
    if not spec.get("source_review_complete"):
        raise ValueError("Complete source review is required before building an approved package")
    manifest = read(out / "source-manifest.json")
    if not manifest.get("review_complete"):
        raise ValueError("The complete file manifest must be reviewed")
    source = KnowledgeSource.model_validate(read(out / "indexed-source.json"))
    if manifest.get("source_hash") != source.source_hash:
        raise ValueError("Reviewed manifest belongs to a different source version")
    source.edition = spec["edition"]
    file = next(f for f in source.files if f["reference"] == spec["primary_file"])
    pages = read(out / spec["pages_path"])
    if [p["physical_page"] for p in pages] != list(range(1, len(pages) + 1)):
        raise ValueError("Every physical page must be accounted for")
    source.page_count = len(pages)
    source.extracted_pages = sum(bool(p["text"].strip()) for p in pages)
    source.mime_type = "inode/directory"
    for f in source.files:
        f["status"] = "indexed" if f is file else "reviewed_alternative_or_attachment"
    source.extraction_status = "indexed" if source.extracted_pages == len(pages) else "partial"
    ir_path = out / "primary-source-ir.json"
    if ir_path.exists():
        raw = ModuleDocumentIR.model_validate(read(ir_path))
    elif spec.get("ocr"):
        raw = ModuleDocumentIR.model_validate(read(out / "source-ir.json"))
        root = raw.nodes[0].model_copy(deep=True)
        root.child_ids = []
        blocks = []
        for page in pages:
            for _, _, text in split_page(page["text"]):
                order = len(blocks)
                blocks.append(
                    ModuleBlock(
                        block_id=stable_id(
                            "block_",
                            source.source_hash,
                            file["reference"],
                            page["physical_page"],
                            order,
                            text,
                        ),
                        node_id=root.node_id,
                        source_order=order,
                        block_type="paragraph",
                        text=text,
                        page_reference=page["physical_page"],
                        source_position=Position(
                            file_reference=file["reference"],
                            paragraph_start=order,
                            paragraph_end=order,
                            physical_page=page["physical_page"],
                        ),
                        content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    )
                )
        raw = raw.model_copy(
            update={
                "nodes": [root],
                "node_count": 1,
                "blocks": blocks,
                "block_count": len(blocks),
                "extraction_method": spec.get(
                    "extraction_method", "Windows OCR + Codex visual correction"
                ),
            }
        )
        write(ir_path, raw.model_dump(mode="json"))
    else:
        raw = parse_module(source, ROOT / "data")
        write(ir_path, raw.model_dump(mode="json"))
    ir = raw.model_copy(deep=True)
    root = ir.nodes[0].model_copy(deep=True)
    root.child_ids = []
    scenes = spec["scenes"]
    ids, anchors, nodes = {}, {}, [root]
    for scene in scenes:
        candidates = [b for b in ir.blocks if b.page_reference == scene["page"]]
        anchor = next(
            (b for b in candidates if scene.get("anchor", scene["title"]) in b.text), candidates[0]
        )
        nid = stable_id("node_", source.source_hash, "batch41-reviewed-scene", scene["key"])
        ids[scene["key"]], anchors[scene["key"]] = nid, anchor
        nodes.append(
            ModuleNode(
                node_id=nid,
                parent_node_id=root.node_id,
                source_order=anchor.source_order,
                depth=1,
                title=scene["title"],
                normalized_title=normalize(scene["title"]),
                heading_path=[root.title, scene["title"]],
                detected_type="scene",
                detection_source="host",
                created_at=raw.generated_at,
                updated_at=raw.generated_at,
                source_position=anchor.source_position.model_copy(deep=True),
            )
        )
    nodes[1:] = sorted(nodes[1:], key=lambda n: n.source_order)
    root.child_ids = [n.node_id for n in nodes[1:]]
    for b in ir.blocks:
        previous = [n for n in nodes[1:] if n.source_order <= b.source_order]
        b.node_id = previous[-1].node_id if previous else root.node_id
    ir.nodes, ir.node_count = nodes, len(nodes)
    ir = ModuleDocumentIR.model_validate(ir.model_dump())

    def refs(entry):
        candidates = [b for b in ir.blocks if b.page_reference in entry["pages"]]
        needles = entry.get("anchors", [])
        chosen = (
            [b for b in candidates if any(n in b.text for n in needles)] if needles else candidates
        )
        if not chosen:
            raise ValueError(f"No source anchor for {entry['key']}")
        if len(chosen) > 60:
            raise ValueError(f"Narrow the source anchors for {entry['key']}")
        return [b.block_id for b in chosen]

    definitions = [
        {
            "key": s["key"],
            "type": "scene",
            "title": s["title"],
            "public": s["public"],
            "private": s["private"],
            "pages": [s["page"]],
            "scenes": [s["key"]],
            "anchors": [s.get("anchor", s["title"])],
            "initial": s["key"] == spec["initial"],
        }
        for s in scenes
    ] + spec["entities"]
    entities = []
    for e in definitions:
        block_ids = refs(e)
        fields = {
            "type": e["type"],
            "title": e["title"],
            "public_summary": e["public"],
            "keeper_summary": e["private"],
            "source_block_ids": block_ids,
            "source_pages": e["pages"],
            "initial_visibility": "revealed" if e.get("initial") else "hidden",
            "reviewed_by": REVIEWER,
            "review_basis": (
                f"逐项核对 {file['reference']} 物理页 {e['pages']}；"
                "全文及附件说明见 source-review.md。"
            ),
            "reveal_conditions": {
                "access_policy": e.get("policy", "automatic"),
                "note": e.get("condition", "仅在实际到场观察或询问后公开，不提前公布未来遭遇。"),
            },
            **e.get("fields", {}),
        }
        if fields["reveal_conditions"].get("access_policy") == "requires_check":
            checks = fields.get("suggested_checks", [])
            if len(checks) != 1:
                raise ValueError("Alternative checks need explicit separate interactions")
            fields["reveal_conditions"]["successful_check"] = checks[0]
        for effect in fields.get("sanity_effects", []):
            effect.setdefault("source", "module:" + source.source_hash)
        fields["interactions"] = []
        for interaction in e.get("interactions", []):
            r = {
                "source_block_ids": block_ids[:20],
                "scene_node_ids": [ids[k] for k in e["scenes"]],
                "kp_enabled": True,
                **interaction,
            }
            fields["interactions"].append(r)
        entities.append(
            {
                "key": e["key"],
                "fields": EntityFields.model_validate(fields).model_dump(mode="json"),
                "node_ids": [ids[k] for k in e["scenes"]],
            }
        )
    transitions = []
    for edge in spec["edges"]:
        a, b = edge[0:2]
        transitions.append(
            {
                "source_scene_node_id": ids[a],
                "target_scene_node_id": ids[b],
                "approved": True,
                "condition_summary": edge[2],
                "source_evidence": list(dict.fromkeys([anchors[a].block_id, anchors[b].block_id])),
                **(edge[3] if len(edge) > 3 else {}),
            }
        )
    coverage = []
    for page in pages:
        number = page["physical_page"]
        blocks = [b for b in ir.blocks if b.page_reference == number]
        related = [e["key"] for e in definitions if number in e["pages"]]
        note = spec["page_review"][str(number)]
        coverage.append(
            {
                "id": f"pdf-{number}",
                "source_orders": [b.source_order for b in blocks],
                "entity_keys": related,
                "node_ids": list(dict.fromkeys(b.node_id for b in blocks)),
                "status": note["status"],
                "gaps": note.get("gaps", []),
                "note": note["note"],
            }
        )
    chunks = []
    for page in clean_pages(
        [{**p, "page_kind": "pdf", "page_label": p.get("page_label")} for p in pages]
    ):
        for start, end, text in split_page(page["text"]):
            chunk_id = hashlib.sha256(
                f"{source.source_id}:{source.source_hash}:{file['reference']}:{page['physical_page']}:{start}".encode()
            ).hexdigest()
            chunks.append(
                dict(
                    chunk_id=chunk_id,
                    source_id=source.source_id,
                    source_hash=source.source_hash,
                    chunk_index=len(chunks),
                    file_reference=file["reference"],
                    file_hash=file["sha256"],
                    physical_page=page["physical_page"],
                    page_label=page.get("page_label"),
                    page_kind="pdf",
                    section=f"物理页{page['physical_page']}",
                    offset_start=start,
                    offset_end=end,
                    normalized_text=normalize(text),
                    display_text=text,
                    visibility="keeper_only",
                    module_id=source.module_id,
                    scene_id=None,
                    entity_id=None,
                    previous_id=None,
                    next_id=None,
                )
            )
    for i, c in enumerate(chunks):
        c["previous_id"] = chunks[i - 1]["chunk_id"] if i else None
        c["next_id"] = chunks[i + 1]["chunk_id"] if i + 1 < len(chunks) else None
    source.chunk_count = len(chunks)
    package = {
        "format_version": 1,
        "title": spec["title"],
        "reviewer": REVIEWER,
        "review_basis": spec["review_basis"],
        "knowledge": {"source": source.model_dump(mode="json"), "chunks": chunks},
        "ir": ir.model_dump(mode="json"),
        "entities": entities,
        "nodes": [
            {"node_id": root.node_id, "patch": {"approved_type": "document", "included": True}}
        ]
        + [
            {
                "node_id": ids[s["key"]],
                "patch": {
                    "approved_type": "scene",
                    "included": True,
                    "public_title": s["title"],
                    "public_summary": s["public"],
                    "keeper_summary": s["private"],
                    "initial_scene": s["key"] == spec["initial"],
                },
            }
            for s in scenes
        ],
        "transitions": transitions,
        "initial_node_id": ids[spec["initial"]],
        "initial_entity_key": spec["initial"],
        "required_entity_keys": spec["required"],
        "coverage": coverage,
        "required_npc_operations": {},
        "original_choices": spec["original_choices"],
        "known_execution_gaps": spec["limits"],
    }
    result = audit(package, ROOT / "data")
    write(out / "package-reviewed.json", package)
    write(out / "package-audit.json", result)
    write(out / "unsupported-mechanisms.json", spec["limits"])
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(build(Path(sys.argv[1])), ensure_ascii=False, indent=2))
