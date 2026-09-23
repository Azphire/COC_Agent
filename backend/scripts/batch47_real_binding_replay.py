"""Rebind a frozen real request against all its public identities and DB aliases."""

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents.task_receipts import bind_task_operands, current_task_targets


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


async def main():
    root = Path(__file__).resolve().parents[2]
    source = root / "data/prepared/batch-47/task-recheck-01"
    paths = [source / name for name in (
        "game.db", "game.db-wal", "checkpoint.db", "checkpoint.db-wal", "knowledge.db",
        "knowledge.db-wal", "preflight.json", "task-case.json", "effective-config.json",
        "source-main/main-case.json", "source-main/main-settlement.json",
        "source-main/main-captured-result.json",
    )]
    before = {str(path): digest(path) for path in paths}
    case = json.loads((source / "task-case.json").read_text(encoding="utf-8"))
    preflight = json.loads((source / "preflight.json").read_text(encoding="utf-8"))
    run = next(row for row in case["runs"] if row["graph_node"] == "decide_teammates")
    context = run["context"]
    public = preflight["public_entities"]
    assert {row["id"] for row in public} == {
        row["id"] for row in context["public_entities"]
    }, "The complete frozen public sets differ; do not substitute a reduced target fixture"
    request = context["addressed_requests"][0]
    raw = case["original_submission"]["body"]["text"]
    assert request["text"] == raw[request["source_start"]:request["source_end"]]
    inventory = context["inventory_state"]
    frozen = deepcopy((public, request, inventory))
    room_id = run["room_id"]
    database = source / "game.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:{database.as_posix()}?mode=ro&uri=true",
    )
    try:
        async with async_sessionmaker(engine, autoflush=False)() as session:
            await session.execute(text("PRAGMA query_only=ON"))
            targets = await current_task_targets(session, room_id, public)
    finally:
        await engine.dispose()
    binding = bind_task_operands(
        request, inventory, run["actor_member_id"],
        case["original_submission"]["response_event"]["actor_member_id"], targets=targets,
    )
    expected = next(target for target in targets if target["title"] == "松动的窗锁")
    checks = {
        "all_frozen_public_entities_used": len(public) == len(context["public_entities"]),
        "more_than_one_current_target": len(targets) > 1,
        "known_source_person_still_present": any(
            target["title"] == "托马斯·金博尔" for target in targets),
        "approved_window_alias_present": "窗锁" in expected.get("aliases", []),
        "unique_window_bound": binding.get("target_id") == expected["id"]
        and not binding.get("target_candidates"),
        "source_and_request_bound": binding.get("target_source", {}).get("target_id")
        == expected["id"] and binding.get("target_source", {}).get("request_key") == request["key"]
        and binding.get("target_source", {}).get("request_source_event_seq")
        == request["source_event_seq"],
        "original_request_unchanged": binding["text"] == request["text"],
        "frozen_inputs_unchanged": (public, request, inventory) == frozen,
        "source_files_unchanged": before == {str(path): digest(path) for path in paths},
    }
    result = {
        "mode": "readonly_full_real_input_binding_replay", "model_calls": 0,
        "source_directory": str(source), "source_run_id": run["id"],
        "raw_fixed_task": raw, "frozen_request": request,
        "public_entities": public, "current_targets_with_approved_aliases": targets,
        "inventory": inventory, "effective_binding": binding, "checks": checks,
        "source_sha256_before": before,
        "source_sha256_after": {str(path): digest(path) for path in paths},
        "status": "passed" if all(checks.values()) else "failed",
        "limitation": "Deterministic operand replay only; "
                      "no real task execution or new observation",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert all(checks.values()), checks


if __name__ == "__main__":
    asyncio.run(main())
