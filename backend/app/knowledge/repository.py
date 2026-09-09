import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.knowledge.schemas import KnowledgeSource


class KnowledgeRepository:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as db:
            try:
                db.execute("CREATE VIRTUAL TABLE temp.fts_probe USING fts5(text)")
            except sqlite3.OperationalError:
                raise ValueError("fts5_unavailable") from None
            db.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_sources (
                    source_id TEXT NOT NULL, source_hash TEXT NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(source_id, source_hash));
                CREATE TABLE IF NOT EXISTS source_heads (
                    source_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_hash TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL, file_reference TEXT NOT NULL, file_hash TEXT,
                    physical_page INTEGER, page_label TEXT, page_kind TEXT NOT NULL,
                    section TEXT, offset_start INTEGER, offset_end INTEGER,
                    normalized_text TEXT NOT NULL, display_text TEXT NOT NULL,
                    visibility TEXT NOT NULL, module_id TEXT, scene_id TEXT, entity_id TEXT,
                    previous_id TEXT, next_id TEXT);
                CREATE INDEX IF NOT EXISTS chunks_source_version
                    ON knowledge_chunks(source_id, source_hash);
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
                    USING fts5(chunk_id UNINDEXED, terms, tokenize='unicode61');
            """)

    def source(self, source_id, source_hash=None):
        if not self.path.exists():
            return None
        with self.connect() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='knowledge_sources'"
            ).fetchone():
                return None
            if source_hash is None:
                row = db.execute(
                    "SELECT source_hash FROM source_heads WHERE source_id=?", (source_id,)
                ).fetchone()
                if row is None:
                    return None
                source_hash = row[0]
            row = db.execute(
                "SELECT document FROM knowledge_sources WHERE source_id=? AND source_hash=?",
                (source_id, source_hash),
            ).fetchone()
        return KnowledgeSource.model_validate_json(row[0]) if row else None

    def sources(self, versions=False):
        if not self.path.exists():
            return []
        with self.connect() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='knowledge_sources'"
            ).fetchone():
                return []
            query = "SELECT s.document FROM knowledge_sources s"
            if not versions:
                query += " JOIN source_heads h USING(source_id, source_hash)"
            return [
                KnowledgeSource.model_validate_json(row[0])
                for row in db.execute(query + " ORDER BY s.source_id, s.source_hash")
            ]

    def available(self, source_id, source_hash):
        source = self.source(source_id, source_hash)
        if not source or not source.chunk_count:
            return False
        try:
            with self.connect() as db:
                ids = {
                    row[0]
                    for row in db.execute(
                        "SELECT chunk_id FROM knowledge_chunks WHERE source_id=? AND source_hash=?",
                        (source_id, source_hash),
                    )
                }
                indexed = {row[0] for row in db.execute("SELECT chunk_id FROM knowledge_fts")}
            return len(ids) == source.chunk_count and ids <= indexed
        except sqlite3.DatabaseError:
            return False

    def store(self, source, chunks):
        from app.knowledge.text import tokens

        with self.connect() as db:
            # Rebuild a failed version atomically; successful old versions remain immutable.
            db.execute(
                "DELETE FROM knowledge_fts WHERE chunk_id IN "
                "(SELECT chunk_id FROM knowledge_chunks WHERE source_id=? AND source_hash=?)",
                (source.source_id, source.source_hash),
            )
            db.execute(
                "DELETE FROM knowledge_chunks WHERE source_id=? AND source_hash=?",
                (source.source_id, source.source_hash),
            )
            for chunk in chunks:
                columns = ",".join(chunk)
                db.execute(
                    f"INSERT INTO knowledge_chunks ({columns}) "
                    f"VALUES ({','.join('?' for _ in chunk)})",
                    list(chunk.values()),
                )
                db.execute(
                    "INSERT INTO knowledge_fts(chunk_id, terms) VALUES (?,?)",
                    (chunk["chunk_id"], " ".join(tokens(chunk["normalized_text"]))),
                )
            db.execute(
                "INSERT OR REPLACE INTO knowledge_sources VALUES (?,?,?)",
                (source.source_id, source.source_hash, source.model_dump_json()),
            )
            db.execute(
                "INSERT OR REPLACE INTO source_heads VALUES (?,?)",
                (source.source_id, source.source_hash),
            )

    def set_head(self, source):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO source_heads VALUES (?,?)",
                (source.source_id, source.source_hash),
            )

    def verify(self):
        if not self.path.exists():
            return {"ok": False, "error": "knowledge_missing"}
        with self.connect() as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            chunks = db.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
            fts = db.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0]
            chunk_ids = {row[0] for row in db.execute("SELECT chunk_id FROM knowledge_chunks")}
            fts_ids = {row[0] for row in db.execute("SELECT chunk_id FROM knowledge_fts")}
            return {
                "ok": integrity == "ok" and chunks == fts and chunk_ids == fts_ids,
                "integrity": integrity,
                "chunks": chunks,
                "fts_rows": fts,
            }

    def cleanup(self, protected, apply=False):
        """Only non-head, unreferenced derived versions. Never touches original files."""
        with self.connect() as db:
            heads = {(r[0], r[1]) for r in db.execute("SELECT * FROM source_heads")}
            candidates = [
                (s.source_id, s.source_hash)
                for s in self.sources(True)
                if (s.source_id, s.source_hash) not in heads | set(protected)
            ]
            if apply:
                for source_id, source_hash in candidates:
                    db.execute(
                        "DELETE FROM knowledge_fts WHERE chunk_id IN "
                        "(SELECT chunk_id FROM knowledge_chunks "
                        "WHERE source_id=? AND source_hash=?)",
                        (source_id, source_hash),
                    )
                    db.execute(
                        "DELETE FROM knowledge_chunks WHERE source_id=? AND source_hash=?",
                        (source_id, source_hash),
                    )
                    db.execute(
                        "DELETE FROM knowledge_sources WHERE source_id=? AND source_hash=?",
                        (source_id, source_hash),
                    )
            return {"applied": apply, "versions": candidates}


def source_view(source):
    """Safe metadata, including file basenames, never a local path or raw text."""
    value = source.model_dump(exclude={"relative_reference"})
    value["files"] = [
        {k: v for k, v in file.items() if k != "reference"} for file in value["files"]
    ]
    return value
