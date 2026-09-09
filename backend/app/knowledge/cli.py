"""python -m app.knowledge.cli --database ../.cache/... index --all"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import Settings
from app.knowledge.indexer import KnowledgeIndexer
from app.knowledge.repository import KnowledgeRepository, source_view
from app.knowledge.retriever import KnowledgeRetriever


def protected_versions(game_path):
    # Read-only: absence of a known schema cannot certify that a version is unreferenced.
    if not game_path.is_file() or game_path.stat().st_size == 0:
        return set()
    with sqlite3.connect(game_path.as_uri() + "?mode=ro", uri=True) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        protected = set()
        for table in ("room_knowledge_bindings", "knowledge_save_states", "agent_grounded_claims"):
            if table not in names:
                continue
            for row in db.execute(f"SELECT document FROM {table}"):

                def walk(value):
                    if isinstance(value, dict):
                        if "source_id" in value and "source_hash" in value:
                            protected.add((value["source_id"], value["source_hash"]))
                        for child in value.values():
                            walk(child)
                    elif isinstance(value, list):
                        for child in value:
                            walk(child)

                walk(json.loads(row[0]))
        return protected


def main():
    parser = argparse.ArgumentParser(description="Local versioned knowledge index")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--data-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("--kind", choices=("rules", "modules", "all"), default="all")
    index.add_argument("--all", action="store_true")
    sub.add_parser("status")
    sub.add_parser("verify")
    query = sub.add_parser("query")
    query.add_argument("--kind", choices=("rules", "module"), default="rules")
    query.add_argument("--source-id")
    query.add_argument("--source-hash")
    query.add_argument("text")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--apply", action="store_true")
    cleanup.add_argument("--game-database", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    path = (args.database or settings.knowledge_path).resolve()
    if path == Path(make_url(settings.database_url).database).resolve():
        parser.error("knowledge database must be separate from game database")
    repo = KnowledgeRepository(path)
    if args.command == "index":
        output = []
        for item in KnowledgeIndexer(args.data_dir or settings.data_dir, repo).index(args.kind):
            output.append(
                {**item, "source": source_view(item["source"])} if "source" in item else item
            )
    elif args.command == "status":
        output = [source_view(source) for source in repo.sources()]
    elif args.command == "verify":
        output = repo.verify()
    elif args.command == "query":
        if args.kind == "module" and (not args.source_id or not args.source_hash):
            parser.error("module query requires exact source ID and hash")
        refs = [
            {"source_id": s.source_id, "source_hash": s.source_hash}
            for s in repo.sources(True)
            if (not args.source_id or args.source_id == s.source_id)
            and (not args.source_hash or args.source_hash == s.source_hash)
        ]
        output = KnowledgeRetriever(repo).search(
            args.text, refs=refs, run_id="cli", kind=args.kind, keeper=True
        )
    else:
        output = repo.cleanup(protected_versions(args.game_database.resolve()), args.apply)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
