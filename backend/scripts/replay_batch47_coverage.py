"""Frozen real-04 coverage replay: no model, no writes to the source database."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.agents import narration_coverage  # noqa: E402
from app.agents.adjudication_schemas import KeeperNarration  # noqa: E402
from app.agents.runtime import AgentRuntime  # noqa: E402
from app.agents.service import AgentService  # noqa: E402
from app.config import Settings  # noqa: E402
from app.persistence.agent_models import AgentCycle, AgentRun  # noqa: E402
from app.persistence.room_models import GameRoom  # noqa: E402
from app.rooms.service import RoomService  # noqa: E402

BASELINE = "d650badbc0b4ef401ca6dd9969aff9b3be3e1022"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


async def replay():
    directory = ROOT / "data/prepared/batch-46/real-04"
    paths = [directory / name for name in (
        "game.db", "game.db-wal", "game.db-shm", "main-case.json", "fixed-turn-calls.json",
        "member-1-stream.json", "member-2-stream.json", "main-settlement.json",
    )]
    before = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    case = json.loads((directory / "main-case.json").read_text(encoding="utf-8"))
    frozen = next(r for r in case["runs"] if r["graph_node"] == "generate_keeper_narration")
    brief = frozen["context"]["response_brief"]
    old_source = subprocess.check_output(
        ["git", "show", BASELINE + ":backend/app/agents/narration_coverage.py"], cwd=ROOT,
    )
    baseline = {"__name__": "_batch47_baseline_coverage"}
    exec(compile(old_source, BASELINE + ":narration_coverage.py", "exec"), baseline)
    engine = create_async_engine(
        "sqlite+aiosqlite:///file:" + (directory / "game.db").as_posix() + "?mode=ro&uri=true"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    runtime = AgentRuntime(AgentService(
        RoomService(SimpleNamespace(sessions=sessions), Settings()), None,
    ))
    results = []
    async with sessions() as session:
        await session.execute(text("PRAGMA query_only=ON"))
        run = await session.get(AgentRun, frozen["id"])
        run.context = frozen["context"]  # In memory only; no flush/commit.
        cycle = await session.get(AgentCycle, case["cycle_id"])
        room = await session.get(GameRoom, run.room_id)
        for call in case["model_calls"]:
            if call["schema"] != "KeeperNarration" or call["cycle_id"] != case["cycle_id"]:
                continue
            raw = call["raw_output"]
            old, old_changes = baseline["normalize_coverage_spans"](raw, brief)
            effective, changes = narration_coverage.normalize_coverage_spans(raw, brief)
            record = {
                "attempt": call["attempt"], "raw_output": raw,
                "raw_audit": narration_coverage.coverage_audit(raw, brief),
                "baseline_effective": old, "baseline_changes": old_changes,
                "baseline_audit": baseline["coverage_audit"](old, brief),
                "effective": effective, "normalizations": changes,
                "audit": narration_coverage.coverage_audit(effective, brief),
                "body_unchanged": effective["public_narration"] == raw["public_narration"],
            }
            try:
                record["runtime_validation"] = await runtime.validate_narration_output(
                    session, room, cycle, run, KeeperNarration.model_validate(effective),
                )
                record["runtime_valid"] = True
            except Exception as error:
                record.update(runtime_valid=False, runtime_error=repr(error))
            results.append(record)
    await engine.dispose()
    after = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    return {
        "baseline": BASELINE, "baseline_code_sha256": hashlib.sha256(old_source).hexdigest(),
        "current_code_sha256": digest(ROOT / "backend/app/agents/narration_coverage.py"),
        "cycle_id": case["cycle_id"], "run_id": frozen["id"],
        "brief": brief, "results": results,
        "read_only": {"sqlite_mode": "ro", "query_only": True, "autoflush": False,
                      "model_calls": 0, "initialize_calls": 0, "commits": 0},
        "limits": "Offline frozen full-body validation; no live stream or formal publication.",
        "source_hashes_before": before, "source_hashes_after": after,
        "source_files_unchanged": before == after,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(replay())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "source_files_unchanged": result["source_files_unchanged"],
        "results": [{"attempt": r["attempt"], "baseline_complete": r["baseline_audit"]["complete"],
                     "complete": r["audit"]["complete"], "runtime_valid": r["runtime_valid"]}
                    for r in result["results"]],
    }, ensure_ascii=False))
