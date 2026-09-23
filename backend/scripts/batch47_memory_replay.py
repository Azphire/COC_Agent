"""Read frozen batch-46 JSON only; never mutate its database or model state."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from app.memory.recall import select_memory, visible_tasks


async def replay(scoped):
    root = Path(__file__).resolve().parents[2]
    source = root / "data/prepared/batch-46/real-04"
    names = ["task-case.json", "public-events.json"]
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names}
    case, events = [json.loads((source / name).read_text(encoding="utf-8")) for name in names]
    trigger = case["original_submission"]["response_event"]
    events = sorted({e["seq"]: e for e in [*events, *case["events"]]}.values(),
                    key=lambda e: e["seq"])
    member = next(b["member_id"] for b in case["behavior"] if b["pending_requests"])

    class Session:
        async def scalars(self, query):
            return [SimpleNamespace(member_id=b["member_id"], document=b)
                    for b in case["behavior"]]

    tasks = await visible_tasks(Session(), "frozen", events, member_id=member)
    context = case["runs"][0]["context"]
    titles = {t["id"]: t for t in context["current_targets"]}
    targets = [{**titles.get(t["id"], {}), **t} for t in context["public_entities"]
               if t.get("fact_scope") == "current_scene"]
    frozen_target_ids = context["memory_selection_audit"]["reference_resolution"]["target_ids"]
    options = {"current_request_keys": ["242:0"], "task_source_seqs": [242]} if scoped else {}
    rows, audit = select_memory(
        [e for e in events if e["seq"] < trigger["seq"]], trigger["payload"]["text"],
        tasks=tasks, scene_id=context["public_state"]["scene_id"],
        targets=targets, target_ids=frozen_target_ids,
        budget=3000, **options,
    )
    result = {"source_sha256": hashes, "source_unchanged": all(
        hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
        for name, digest in hashes.items()), "scoped": scoped, "query": trigger["payload"]["text"],
        "visible_tasks": tasks, "selected": rows, "audit": audit, "model_calls": 0}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scoped", action="store_true")
    asyncio.run(replay(parser.parse_args().scoped))
