import json
import sqlite3

from app.module_ir.schemas import ModuleDocumentIR


class StructureRepository:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    def initialize(self):
        with self.knowledge.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS module_structure_versions (
                    structure_version TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS module_nodes (
                    structure_version TEXT NOT NULL, node_id TEXT NOT NULL,
                    source_order INTEGER NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(structure_version, node_id));
                CREATE TABLE IF NOT EXISTS module_blocks (
                    structure_version TEXT NOT NULL, block_id TEXT NOT NULL,
                    node_id TEXT NOT NULL, source_order INTEGER NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(structure_version, block_id));
                CREATE INDEX IF NOT EXISTS module_blocks_node
                    ON module_blocks(structure_version, node_id, source_order);
            """)

    def get(self, version):
        if not self.knowledge.path.exists():
            return None
        try:
            with self.knowledge.connect() as db:
                row = db.execute(
                    "SELECT document FROM module_structure_versions WHERE structure_version=?",
                    (version,),
                ).fetchone()
                if not row:
                    return None
                data = json.loads(row[0])
                data["nodes"] = [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT document FROM module_nodes WHERE structure_version=? "
                        "ORDER BY source_order, rowid",
                        (version,),
                    )
                ]
                data["blocks"] = [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT document FROM module_blocks WHERE structure_version=? "
                        "ORDER BY source_order",
                        (version,),
                    )
                ]
                return ModuleDocumentIR.model_validate(data)
        except (sqlite3.DatabaseError, ValueError):
            return None

    def store(self, ir):
        ir = ModuleDocumentIR.model_validate(ir)
        self.initialize()
        existing = self.get(ir.structure_version)
        if existing:
            return existing
        with self.knowledge.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO module_structure_versions VALUES (?,?,?,?)",
                (
                    ir.structure_version,
                    ir.source_id,
                    ir.source_hash,
                    ir.model_dump_json(exclude={"nodes", "blocks"}),
                ),
            )
            db.execute(
                "DELETE FROM module_nodes WHERE structure_version=?", (ir.structure_version,)
            )
            db.execute(
                "DELETE FROM module_blocks WHERE structure_version=?", (ir.structure_version,)
            )
            db.executemany(
                "INSERT INTO module_nodes VALUES (?,?,?,?)",
                [
                    (ir.structure_version, n.node_id, n.source_order, n.model_dump_json())
                    for n in ir.nodes
                ],
            )
            db.executemany(
                "INSERT INTO module_blocks VALUES (?,?,?,?,?)",
                [
                    (
                        ir.structure_version,
                        b.block_id,
                        b.node_id,
                        b.source_order,
                        b.model_dump_json(),
                    )
                    for b in ir.blocks
                ],
            )
        return ir
