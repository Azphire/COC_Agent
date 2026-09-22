"""Resumable local preparation queue. Never edits source files or room bindings."""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "data/prepared/batch-41"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def isolate(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "data/prepared"):
        raise ValueError("An independent data/prepared directory is required")
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (directory / "game.db").as_posix()
    os.environ["KNOWLEDGE_DB_PATH"] = str(directory / "knowledge.db")
    os.environ["CHECKPOINT_DB_PATH"] = str(directory / "checkpoint.db")
    return directory


def protect(final=False):
    initial = QUEUE / "protected-files-initial.json"
    if final:
        before = read(initial)
        after = {p: digest(ROOT / p) if (ROOT / p).is_file() else None for p in before}
        result = {
            "checked": len(before),
            "changes": [p for p in before if before[p] != after[p]],
            "hashes": after,
        }
        write(QUEUE / "protected-files-final.json", result)
        return result
    if initial.exists():
        return {"reused": True, "files": len(read(initial))}
    old = read(ROOT / "data/prepared/zhuishuren/batch-40/protected-files-initial.json")
    paths = {ROOT / p for p in old}
    paths.update(p for p in (ROOT / "data/modules").rglob("*") if p.is_file())
    base = ROOT / "data/prepared/zhuishuren/batch-40"
    paths.update(base / p for p in ("package-reviewed.json", "source-ir.json", "source-review.md"))
    run = base / "run-20260921"
    paths.update(
        run / p
        for p in (
            "game.db",
            "knowledge.db",
            "checkpoint.db",
            "session-full.md",
            "natural-save.json",
            "load-info.json",
        )
    )
    write(initial, {relative(p): digest(p) if p.is_file() else None for p in sorted(paths)})
    return {"files": len(paths)}


def inventory():
    from app.knowledge.indexer import IGNORED_DIRS, stable_id
    from app.preparation.packages import audit

    old_path = QUEUE / "preparation-inventory.json"
    old = {m["source_reference"]: m for m in read(old_path)["modules"]} if old_path.exists() else {}
    packages = []
    # Top-level batch deliveries only; do not traverse historical games/saves.
    for module in (ROOT / "data/prepared").iterdir():
        if not module.is_dir() or module == QUEUE:
            continue
        for batch in module.glob("batch-*"):
            paths = list(batch.glob("package*.json"))
            if batch.name == "batch-41":
                paths.extend(batch.glob("*/package-reviewed.json"))
            for path in paths:
                try:
                    package = read(path)
                    if package.get("format_version") != 1:
                        continue
                    packages.append((path, package))
                except (ValueError, OSError):
                    continue
    modules = []
    for folder in sorted((ROOT / "data/modules").iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        files, h = [], hashlib.sha256()
        for p in sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).as_posix()):
            if not p.is_file():
                continue
            ref = p.relative_to(folder).as_posix()
            ignored = p.name.startswith((".", "~$")) or any(
                part.lower() in IGNORED_DIRS for part in Path(ref).parts[:-1]
            )
            file_hash = digest(p)
            files.append(
                {
                    "path": relative(p),
                    "reference": ref,
                    "sha256": file_hash,
                    "bytes": p.stat().st_size,
                    "type": p.suffix.lower(),
                    "ignored": ignored,
                }
            )
            if not ignored:
                h.update(
                    json.dumps([ref, file_hash], ensure_ascii=False, separators=(",", ":")).encode()
                )
                h.update(b"\n")
        source_ref = folder.relative_to(ROOT / "data").as_posix()
        source_hash = h.hexdigest()
        source_id = stable_id("module", source_ref)
        for name in ("manifest.json", "manifest.yaml", "manifest.yml"):
            if (folder / name).exists():
                import yaml

                source_id = (
                    yaml.safe_load((folder / name).read_text("utf-8-sig")).get("source_id")
                    or source_id
                )
                break
        slug = {
            "常暗之厢": "changan",
            "追书人": "zhuishuren",
            "希普拉": "xipula",
            "吱乎鲁的呼唤": "zhihulu",
            "妖灵会馆守则-禁止接触": "jinzhijiechu",
            "木星噩梦": "muxingemeng",
            "被煮沸的钢琴": "zhufeigangqin",
            "长明灯": "changmingdeng",
        }.get(folder.name, source_id)
        existing = []
        for path, package in packages:
            source = package["knowledge"]["source"]
            if source["source_id"] != source_id and source["relative_reference"] != source_ref:
                continue
            item = {
                "path": relative(path),
                "sha256": digest(path),
                "source_hash": source["source_hash"],
            }
            try:
                item["audit"] = audit(package, ROOT / "data")
                item["valid"] = source["source_hash"] == source_hash
            except Exception as error:
                item.update(valid=False, error=str(error))
            existing.append(item)
        valid = [p for p in existing if p["valid"]]
        previous = old.get(source_ref, {})
        entry = {
            "module_id": slug,
            "title": folder.name,
            "source_id": source_id,
            "source_reference": source_ref,
            "source_hash": source_hash,
            "files": files,
            "existing_products": existing,
            "initial_status": "existing_candidate"
            if valid
            else "source_changed"
            if existing
            else "unprepared",
            "decision": "verify_existing"
            if valid
            else "prepare_new_version"
            if existing
            else "prepare",
            "reason": "须核对全文范围、校对及批准记录" if valid else "未发现匹配来源的全文交付包",
            "output_directory": f"data/prepared/{slug}/batch-41/{source_hash[:16]}",
            "missing": [],
            "runtime_limits": [],
            "status": "pending",
        }
        if previous.get("source_hash") == source_hash:
            for key in (
                "decision",
                "reason",
                "missing",
                "runtime_limits",
                "status",
                "review",
                "result",
                "initial_status",
                "source_scope",
                "delivery",
            ):
                if key in previous:
                    entry[key] = previous[key]
        modules.append(entry)
    result = {
        "batch": 41,
        "updated_at": stamp(),
        "modules": modules,
        "unassigned_root_files": [
            {"path": relative(p), "sha256": digest(p)}
            for p in (ROOT / "data/modules").iterdir()
            if p.is_file()
        ],
        "counts": {
            "source_directories": len(modules),
            "actual_modules_verified": all(m.get("source_scope") for m in modules),
            "independent_complete_modules": sum(
                m.get("source_scope", {}).get("independent_complete_module_count", 0)
                for m in modules
            ),
            "source_files": sum(len(m["files"]) for m in modules),
            "existing_valid_skipped": sum(m["decision"] == "skip_valid" for m in modules),
            "new_source_packages": sum(
                m["decision"] != "skip_valid"
                and m.get("review", {}).get("source_review_complete", False)
                for m in modules
            ),
            "new_reviewed_physical_pages": sum(
                m.get("source_scope", {}).get("physical_pages", 0)
                for m in modules
                if m["decision"] != "skip_valid"
                and m.get("review", {}).get("source_review_complete")
            ),
            "additional_scenario_seeds_not_complete_modules": sum(
                m.get("source_scope", {}).get("seed_count", 0) for m in modules
            ),
            "full_source_character_short_validation_passed": sum(
                m.get("delivery", {}).get("short_validation_status") == "passed"
                for m in modules
            ),
            "public_probe_only": sum(
                m.get("delivery", {}).get("short_validation_status")
                == "passed_public_probe_with_character_ruling"
                for m in modules
            ),
            "native_rules_blocked": sum(
                m.get("delivery", {}).get("short_validation_status") == "blocked_native_rules"
                for m in modules
            ),
            "pending_source_preparation": sum(
                m["decision"] != "skip_valid"
                and not m.get("review", {}).get("source_review_complete")
                for m in modules
            ),
        },
    }
    write(old_path, result)
    return result


def extract_source(entry):
    import pymupdf

    from app.knowledge.extraction import extract
    from app.knowledge.indexer import KnowledgeIndexer
    from app.knowledge.repository import KnowledgeRepository
    from app.module_ir.parser import parse_module

    out = ROOT / entry["output_directory"]
    out.mkdir(parents=True, exist_ok=True)
    repository = KnowledgeRepository(out / "source-index.db")
    repository.initialize()
    source, _ = KnowledgeIndexer(ROOT / "data", repository).index_source(
        entry["source_reference"], "module"
    )
    if source.source_hash != entry["source_hash"]:
        raise ValueError("Source changed; rerun inventory")
    write(out / "indexed-source.json", source.model_dump(mode="json"))
    ir_path = out / "source-ir.json"
    if not ir_path.exists():
        write(ir_path, parse_module(source, ROOT / "data").model_dump(mode="json"))
    reports = []
    for f in entry["files"]:
        path = ROOT / f["path"]
        if f["type"] not in {".pdf", ".docx", ".doc", ".txt", ".md"}:
            reports.append({**f, "role": "attachment_pending_visual_review"})
            continue
        target = out / "extraction" / (hashlib.sha256(f["reference"].encode()).hexdigest()[:12])
        target.mkdir(parents=True, exist_ok=True)
        if (target / "pages.json").exists():
            pages = read(target / "pages.json")
        else:
            pages = extract(path)
            write(target / "pages.json", pages)
        (target / "full-text.txt").write_text(
            "\n\n".join(
                f"=== {f['reference']} | physical_page={p['physical_page']} ===\n{p['text']}"
                for p in pages
            ),
            "utf-8",
        )
        page_stats = []
        if f["type"] == ".pdf":
            with pymupdf.open(path) as pdf:
                for number, page in enumerate(pdf, 1):
                    image_path = target / f"page-{number:03}.png"
                    if not image_path.exists():
                        page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(image_path)
                    page_stats.append(
                        {
                            "physical_page": number,
                            "text_chars": len(pages[number - 1]["text"]),
                            "images": len(page.get_images()),
                            "render": relative(image_path),
                            "review_status": "pending",
                        }
                    )
        reports.append(
            {
                **f,
                "extraction": relative(target),
                "page_count": len(pages) if f["type"] == ".pdf" else None,
                "text_chars": sum(len(p["text"]) for p in pages),
                "pages": page_stats,
                "review_status": "pending",
            }
        )
    manifest_path = out / "source-manifest.json"
    prior = read(manifest_path) if manifest_path.exists() else {}
    if not prior.get("review_complete") or prior.get("source_hash") != source.source_hash:
        write(
            manifest_path,
            {
                "source_id": source.source_id,
                "source_hash": source.source_hash,
                "files": reports,
                "review_complete": False,
            },
        )
    return {"stage": "extracted", "source_hash": source.source_hash, "output": relative(out)}


async def generate(entry):
    from app.config import Settings
    from app.knowledge.repository import KnowledgeRepository
    from app.knowledge.schemas import KnowledgeSource
    from app.main import create_app
    from app.module_ir.schemas import ModuleDocumentIR
    from app.preparation.schemas import PreparationInput

    out = ROOT / entry["output_directory"]
    progress_path = out / "generation-progress.json"
    saved = read(progress_path) if progress_path.exists() else {}
    if (
        saved.get("source_hash") == entry["source_hash"]
        and saved.get("preparation", {}).get("status") == "review_ready"
    ):
        return {
            "stage": "generated_unreviewed",
            "preparation_id": saved["preparation_id"],
            "reused": True,
        }
    isolate(out / "draft")
    app = create_app(Settings(host_admin_token="batch41-local-review"))
    async with app.router.lifespan_context(app):
        svc = app.state.agent_service
        svc.knowledge.repository.initialize()
        source = KnowledgeSource.model_validate(read(out / "indexed-source.json"))
        repo = KnowledgeRepository(out / "source-index.db")
        with repo.connect() as db:
            chunks = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM knowledge_chunks WHERE source_id=? AND source_hash=? "
                    "ORDER BY chunk_index",
                    (source.source_id, source.source_hash),
                )
            ]
        ir = read(out / "source-ir.json")
        spec_path = out / "reviewed-spec.json"
        if spec_path.exists() and read(spec_path).get("ocr"):
            package = read(out / "package-reviewed.json")
            source = KnowledgeSource.model_validate(package["knowledge"]["source"])
            chunks = package["knowledge"]["chunks"]
            ir = package["ir"]
        svc.knowledge.repository.store(source, chunks)
        svc.structure.repository.store(ModuleDocumentIR.model_validate(ir))
        # Keep a concrete, scrubbed local failure reason if the service rejects a
        # schema-valid output after model validation. Never bypass its validation.
        save_output = svc.preparation.save_output

        async def traced_save(*args, **kwargs):
            try:
                return await save_output(*args, **kwargs)
            except Exception as error:
                write(
                    out / "generation-validation-error.json",
                    {
                        "time": stamp(),
                        "type": type(error).__name__,
                        "reason": svc.preparation.safe(getattr(error, "message", str(error))),
                    },
                )
                raise

        svc.preparation.save_output = traced_save
        progress = read(progress_path) if progress_path.exists() else {}
        if progress.get("source_hash") != entry["source_hash"]:
            prep = await svc.preparation.create(
                PreparationInput(
                    source_id=source.source_id,
                    source_hash=source.source_hash,
                    display_title=entry["title"] + " 第41批全文草稿",
                )
            )
            progress = {"preparation_id": prep["id"], "source_hash": entry["source_hash"]}
            write(progress_path, progress)
        prep_id = progress["preparation_id"]
        await svc.preparation.start_generation(prep_id)
        while svc.preparation.tasks:
            await asyncio.sleep(2)
            async with svc.rooms.database.sessions() as session:
                prep = await svc.preparation.get(session, prep_id)
                view = await svc.preparation.view(session, prep)
            write(progress_path, {**progress, "updated_at": stamp(), "preparation": view})
        async with svc.rooms.database.sessions() as session:
            prep = await svc.preparation.get(session, prep_id)
            view = await svc.preparation.view(session, prep)
            from app.preparation.service import entity_view, row_view

            write(
                out / "generated-draft.json",
                {
                    "preparation": view,
                    "entities": [
                        entity_view(e) for e in await svc.preparation.entities(session, prep_id)
                    ],
                    "relations": [
                        row_view(r) for r in await svc.preparation.relations(session, prep_id)
                    ],
                },
            )
        write(progress_path, {**progress, "updated_at": stamp(), "preparation": view})
        if view["status"] == "failed":
            write(
                out / "generation-failure-details.json",
                {
                    "preparation_id": prep_id,
                    "completed_batches": view["completed_batches"],
                    "total_batches": view["total_batches"],
                    "calls": [
                        {
                            k: call.get(k)
                            for k in (
                                "error_category",
                                "validation_issues",
                                "token_usage",
                                "latency_ms",
                            )
                        }
                        for run in view["runs"]
                        if run["status"] == "failed"
                        for call in run["calls"]
                    ],
                    "recovery": "resume skips completed evidence IDs",
                },
            )
            raise ValueError(view.get("safe_error", "generation failed"))
        return {"stage": "generated_unreviewed", "preparation_id": prep_id}


def process(entry, operation):
    """Stages have separate receipts; an early-stage rerun never erases later evidence."""
    from scripts.build_batch41 import build
    from scripts.validate_batch41 import validate, verify_existing

    out = ROOT / entry["output_directory"]
    if entry["decision"] in {"verify_existing", "skip_valid"}:
        record = verify_existing(entry)
        entry.update(
            decision="skip_valid",
            reason="来源/全文包审计/正常API批准及结构绑定记录吻合",
            status="valid_existing",
        )
        return {"stage": "valid_existing", "records": len(record["records"])}
    stages_path = out / "stages.json"
    stages = read(stages_path) if stages_path.exists() else {"source_hash": entry["source_hash"]}
    if stages["source_hash"] != entry["source_hash"]:
        raise ValueError("Stage source version mismatch; rerun inventory")

    def save(stage, result):
        stages[stage] = {"updated_at": stamp(), "result": result}
        write(stages_path, stages)

    if operation in {"extract", "generate", "process", "resume", "retry"}:
        if not (out / "indexed-source.json").exists():
            save("extraction", extract_source(entry))
        if operation == "extract":
            return {"stage": "extracted"}
        saved = out / "generation-progress.json"
        generated = (
            saved.exists()
            and read(saved).get("source_hash") == entry["source_hash"]
            and read(saved)["preparation"].get("status") == "review_ready"
        )
        if not generated:
            save("generation", asyncio.run(generate(entry)))
        if operation == "generate":
            return {"stage": "generated_unreviewed"}
    spec = out / "reviewed-spec.json"
    if not spec.exists() or not read(spec).get("source_review_complete"):
        return {
            "stage": "awaiting_source_review",
            "missing": "Complete reviewed-spec and file manifest",
        }
    if operation in {"build", "process", "resume", "retry", "validate"}:
        save("package", build(out))
    if operation == "build":
        return {"stage": "audited_source_package"}
    save("validation", validate(entry))
    return {
        "stage": "import_validated",
        "preparation_id": stages["validation"]["result"]["preparation_id"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=[
            "inventory",
            "extract",
            "generate",
            "process",
            "resume",
            "retry",
            "build",
            "validate",
            "publish",
            "protect",
            "verify-protected",
        ],
    )
    parser.add_argument("--module", action="append", default=[])
    args = parser.parse_args()
    if args.operation in {"protect", "verify-protected"}:
        print(json.dumps(protect(args.operation == "verify-protected"), ensure_ascii=False))
        return
    if args.operation == "publish":
        from scripts.validate_batch41 import publish

        queue = read(QUEUE / "preparation-inventory.json")
        selected = [e for e in queue["modules"] if not args.module or e["module_id"] in args.module]
        print(json.dumps(publish(selected), ensure_ascii=False, indent=2))
        return
    isolate(QUEUE / "bootstrap")
    queue = inventory()
    if args.operation != "inventory":
        for entry in queue["modules"]:
            if args.module and entry["module_id"] not in args.module:
                continue
            if entry["decision"] == "skip_valid" and args.operation != "validate":
                continue
            if args.operation == "retry" and entry["status"] != "failed":
                continue
            try:
                print(entry["module_id"], args.operation, flush=True)
                result = process(entry, args.operation)
                # Source preparation, model generation and runtime acceptance are
                # independent. Preserve the richer delivery status on stage-only commands.
                if entry["status"] not in {"delivered", "prepared_runtime_blocked"}:
                    entry["status"] = result["stage"]
                entry["result"] = {**entry.get("result", {}), args.operation: result}
            except Exception as error:
                import traceback

                entry.update(status="failed", result={"error": str(error), "time": stamp()})
                out = ROOT / entry["output_directory"]
                out.mkdir(parents=True, exist_ok=True)
                with (out / "failures.log").open("a", encoding="utf-8") as log:
                    log.write(stamp() + "\n" + traceback.format_exc() + "\n")
                print(entry["module_id"], "failed", str(error), flush=True)
            write(QUEUE / "preparation-inventory.json", queue)
    print(
        json.dumps(
            {
                "modules": [
                    {"id": e["module_id"], "status": e["status"], "decision": e["decision"]}
                    for e in queue["modules"]
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
