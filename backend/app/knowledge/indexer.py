import hashlib
import json
import mimetypes
from pathlib import Path

import yaml

from app.domain.character import utc_now
from app.knowledge.extraction import extract
from app.knowledge.schemas import KnowledgeSource, SourceManifest
from app.knowledge.text import clean_pages, normalize, split_page

SUPPORTED = {".pdf", ".md", ".txt", ".doc", ".docx"}
IGNORED_DIRS = {
    ".cache",
    "cache",
    "caches",
    "output",
    "outputs",
    ".git",
    "__pycache__",
    "node_modules",
    "knowledge",
}
MANIFESTS = ("manifest.json", "manifest.yaml", "manifest.yml")


def safe_path(root, reference):
    path = Path(reference)
    if path.is_absolute() or path.drive or ".." in path.parts or ":" in reference:
        raise ValueError("path_outside_source")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("path_outside_source")
    return resolved


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def stable_id(kind, reference):
    return kind + "-" + hashlib.sha256(reference.encode("utf-8")).hexdigest()[:24]


class KnowledgeIndexer:
    def __init__(self, data_dir, repository):
        self.root, self.repository = Path(data_dir).resolve(), repository

    def discover(self, kind="all"):
        found = []
        rules = self.root / "rules"
        if kind in {"rules", "all"} and rules.exists():
            for file in sorted(rules.rglob("*")):
                if file.is_file() and file.name != ".gitkeep":
                    ref = file.relative_to(self.root).as_posix()
                    found.append(
                        (
                            ref,
                            "investigator_handbook"
                            if "调查员" in file.name or "handbook" in file.name.lower()
                            else "rulebook",
                        )
                    )
        modules = self.root / "modules"
        if kind in {"modules", "all"} and modules.exists():
            for folder in sorted(modules.iterdir()):
                if folder.is_dir() and not folder.name.startswith("."):
                    found.append((folder.relative_to(self.root).as_posix(), "module"))
        return found

    def index(self, kind="all"):
        self.repository.initialize()
        results, ids = [], set()
        for reference, source_kind in self.discover(kind):
            try:
                source, changed = self.index_source(reference, source_kind, ids)
                ids.add(source.source_id)
                results.append({"source": source, "changed": changed})
            except (ValueError, OSError):
                results.append(
                    {
                        "reference_name": Path(reference).name,
                        "status": "extraction_failed",
                        "error": "invalid_source_or_manifest",
                    }
                )
        return results

    def index_source(self, reference, kind, occupied_ids=()):
        root = safe_path(self.root, reference)
        manifest = SourceManifest()
        if kind == "module":
            if not root.is_dir():
                raise ValueError("module_folder_required")
            for name in MANIFESTS:
                if (root / name).exists():
                    path = safe_path(root, name)
                    manifest = SourceManifest.model_validate(
                        yaml.safe_load(path.read_text(encoding="utf-8-sig"))
                    )
                    break
            paths = sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
        else:
            paths = [root]
        source_id = manifest.source_id or stable_id(kind, reference)
        if source_id in occupied_ids:
            raise ValueError("duplicate_source_id")
        old = self.repository.source(source_id)
        if old and old.relative_reference != reference:
            raise ValueError("source_id_boundary_changed")
        files, digest = [], hashlib.sha256()
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix() if kind == "module" else path.name
            safe_root = root if kind == "module" else self.root
            if not path.resolve().is_relative_to(safe_root):
                files.append(
                    dict(
                        name=path.name,
                        reference=relative,
                        status="path_outside_source",
                        type=path.suffix.lower(),
                    )
                )
                continue
            if any(
                part.lower() in IGNORED_DIRS for part in Path(relative).parts[:-1]
            ) or path.name.startswith(("~$", ".")):
                files.append(
                    dict(
                        name=path.name,
                        reference=relative,
                        status="skipped_cache_or_output",
                        type=path.suffix.lower(),
                    )
                )
                continue
            file_hash = sha256(path)
            # Include all original files, even unsupported attachments and manifest metadata.
            digest.update(
                json.dumps(
                    [relative, file_hash], ensure_ascii=False, separators=(",", ":")
                ).encode()
            )
            digest.update(b"\n")
            files.append(
                dict(
                    name=path.name,
                    reference=relative,
                    sha256=file_hash,
                    type=path.suffix.lower(),
                    status="pending",
                )
            )
        source_hash = digest.hexdigest() if kind == "module" else sha256(root)
        existing = self.repository.source(source_id, source_hash)
        if (
            existing
            and existing.extraction_status in {"indexed", "partial", "unsupported", "ocr_required"}
            and all(f["status"] != "pending" for f in existing.files)
            and (not existing.chunk_count or self.repository.available(source_id, source_hash))
        ):
            self.repository.set_head(existing)
            return existing, False
        source = KnowledgeSource(
            **{
                **manifest.model_dump(),
                "source_id": source_id,
                "title": manifest.title or root.name,
                "module_id": (manifest.module_id or source_id) if kind == "module" else None,
            },
            kind=kind,
            visibility="keeper_only" if kind == "module" else "public_rules",
            relative_reference=reference,
            source_hash=source_hash,
            files=files,
            mime_type="inode/directory"
            if kind == "module"
            else mimetypes.guess_type(root.name)[0] or "application/octet-stream",
        )
        priority = {".docx": 0, ".doc": 1, ".pdf": 2}
        preferred = {}
        for file in files:
            if file["type"] in priority and file["status"] == "pending":
                stem = str(Path(file["reference"]).with_suffix("")).casefold()
                previous = preferred.get(stem)
                if previous is None or priority[file["type"]] < priority[previous["type"]]:
                    preferred[stem] = file
        chunks = []
        for file in files:
            if file["status"] != "pending":
                continue
            extension = file["type"]
            if extension not in SUPPORTED:
                file["status"] = "manifest" if file["name"] in MANIFESTS else "unsupported"
                continue
            if (
                extension in priority
                and preferred[str(Path(file["reference"]).with_suffix("")).casefold()] is not file
            ):
                file["status"] = "skipped_preferred_word"
                continue
            try:
                path = safe_path(root, file["reference"]) if kind == "module" else root
                pages = extract(path)
                count = sum(bool(normalize(page["text"])) for page in pages)
                physical = sum(page["physical_page"] is not None for page in pages)
                file.update(
                    page_count=physical,
                    extracted_pages=sum(
                        bool(normalize(p["text"])) for p in pages if p["physical_page"] is not None
                    ),
                    text_units=len(pages),
                    text_units_extracted=count,
                    failed_pages=[p["physical_page"] for p in pages if not normalize(p["text"])],
                    status=("indexed" if count == len(pages) else "partial")
                    if count
                    else "ocr_required"
                    if extension == ".pdf"
                    else "extraction_failed",
                )
                source.page_count += physical
                source.extracted_pages += file["extracted_pages"]
                section = None
                for page in clean_pages(pages):
                    lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
                    if lines and len(lines[0]) < 90:
                        section = lines[0].lstrip("# ")[:80]
                    for start, end, text in split_page(page["text"]):
                        index = len(chunks)
                        chunk_id = hashlib.sha256(
                            f"{source_id}:{source_hash}:{file['reference']}:{page['physical_page']}:{start}".encode()
                        ).hexdigest()
                        chunks.append(
                            dict(
                                chunk_id=chunk_id,
                                source_id=source_id,
                                source_hash=source_hash,
                                chunk_index=index,
                                file_reference=file["reference"],
                                file_hash=file["sha256"],
                                physical_page=page["physical_page"],
                                page_label=page["page_label"],
                                page_kind=page["page_kind"],
                                section=section,
                                offset_start=start,
                                offset_end=end,
                                normalized_text=normalize(text),
                                display_text=text,
                                visibility=source.visibility,
                                module_id=source.module_id,
                                scene_id=None,
                                entity_id=None,
                                previous_id=None,
                                next_id=None,
                            )
                        )
                # Detect edits during extraction rather than publish a mixed version.
                if sha256(path) != file["sha256"]:
                    raise ValueError("source_changed_during_extraction")
            except Exception as error:
                file["status"] = "extraction_failed"
                file["error"] = (
                    str(error)
                    if isinstance(error, ValueError)
                    and str(error)
                    in {
                        "word_required",
                        "word_extraction_failed",
                        "encrypted_pdf",
                        "source_changed_during_extraction",
                    }
                    else "text_extraction_failed"
                )
                chunks = [c for c in chunks if c["file_reference"] != file["reference"]]
        for i, chunk in enumerate(chunks):
            chunk["chunk_index"] = i
            for key, delta in (("previous_id", -1), ("next_id", 1)):
                neighbor = i + delta
                if (
                    0 <= neighbor < len(chunks)
                    and chunks[neighbor]["file_reference"] == chunk["file_reference"]
                ):
                    chunk[key] = chunks[neighbor]["chunk_id"]
        failures = [
            f
            for f in files
            if f["status"]
            in {"partial", "ocr_required", "extraction_failed", "path_outside_source"}
        ]
        source.files = files
        source.chunk_count, source.indexed_at = len(chunks), utc_now().isoformat()
        source.extraction_status = (
            ("partial" if failures else "indexed")
            if chunks
            else "ocr_required"
            if any(f["status"] == "ocr_required" for f in files)
            else "extraction_failed"
            if failures
            else "unsupported"
        )
        source.error_summary = "部分文件或页面无法提取，请查看文件状态" if failures else None
        self.repository.store(source, chunks)
        return source, True
