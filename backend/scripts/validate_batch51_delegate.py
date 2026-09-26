"""One original delegated turn in a fresh isolated room, using the normal APIs.

Preparation/setup are model-free. Only --case submits the unchanged original
request. Offline completion must be established by the operator first. The
driver refuses to overwrite an existing attempt or begin after a prior reveal.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import validate_batch49_natural as previous
from audit_batch49_metrics import integrity, natural_case

ROOT = previous.ROOT
BASE = ROOT / "data/prepared/batch-51"
PORT = 8151
ORIGINAL_REQUEST = (
    "林修远·猎人，请用手检查门上便签正面的边缘，看看纸张有没有夹层或折叠，"
    "再把实际发现告诉大家。"
)
ORIGINAL_REQUEST_CALL = previous.request
CONFIG_KEYS = (
    "model_provider", "model_name", "model_context_limit", "model_output_limit",
    "agent_max_calls", "agent_context_chars", "model_timeout_seconds",
)


def static_check():
    """Inspect source/configuration without HTTP, startup or any provider request."""
    from app.config import Settings

    expected = previous.read(ROOT / "data/prepared/batch-49/real-10/effective-config.json")
    settings = Settings()
    actual = {key: getattr(settings, key) for key in CONFIG_KEYS}
    assert actual == expected, "Current environment differs from the approved model configuration"
    assert actual["model_provider"] == "ollama" and actual["model_name"] == "qwen3:8b"
    assert actual["model_context_limit"] == 16384 and actual["model_output_limit"] == 900
    assert actual["agent_context_chars"] == 12000
    required = [previous.SOURCE / name for name in (
        "game.db", "checkpoint.db", "knowledge.db", "delegation-state-01.json",
    )]
    required.extend(ROOT / name for name in (
        "data/prepared/changan/batch-21/package-approved.json",
        "backend/tests/fixtures/batch50/real-09-delegate-failure.json",
        "backend/tests/fixtures/batch50/real-10-delegate-failure.json",
        "backend/tests/fixtures/batch50/real-01-current-failure.json",
    ))
    assert all(path.is_file() for path in required), "Required source or frozen evidence missing"
    original = previous.read(previous.SOURCE / "delegation-state-01.json")
    event = next(row for row in original["room_events"]
                 if row["room_id"] == previous.OLD_ROOM and row["seq"] == 84)
    assert event["payload"]["text"] == ORIGINAL_REQUEST
    protected = set(required)
    for directory in (
        previous.SOURCE, ROOT / "data/prepared/batch-49/real-09",
        ROOT / "data/prepared/batch-49/real-10", ROOT / "data/prepared/batch-50/real-01",
        ROOT / "backend/tests/fixtures/batch50",
    ):
        for folder, subdirs, filenames in os.walk(directory):
            subdirs[:] = [name for name in subdirs if name != "profile"]
            protected.update(Path(folder) / name for name in filenames
                             if not name.endswith("-shm"))
    protected.update(ROOT / name for name in (
        ".env", "data/game.db", "data/game.db-wal", "data/host-model-settings.json",
    ))
    hashes = {str(path): previous.digest(path) for path in sorted(protected)}
    return {
        "effective_config": actual, "request": ORIGINAL_REQUEST,
        "source": str(previous.SOURCE), "source_room": previous.OLD_ROOM,
        "protected_sha256": hashes, "model_calls": 0,
        "hash_exclusions": ["browser profile caches", "SQLite transient shared-memory files"],
        "scope": "Static file/config inspection only; no service or model request",
    }


def check_protection(directory):
    proof = previous.read(directory / "batch51-static-preflight.json")
    matches = {path: previous.digest(Path(path)) == expected
               for path, expected in proof["protected_sha256"].items()}
    assert all(matches.values()), "Protected prior evidence or configuration changed"
    return {"files": matches, "all_match": all(matches.values())}


def prepare(directory):
    proof = static_check()
    previous.prepare()
    previous.write(directory / "batch51-static-preflight.json", proof)
    # Extend the established before/after source protection with all frozen logs.
    provenance = previous.read(directory / "clone-provenance.json")
    provenance["source_sha256"].update(proof["protected_sha256"])
    previous.write(directory / "clone-provenance.json", provenance)
    check_protection(directory)


def request(client, method, path, body=None, *, token=previous.TEST_HOST):
    if method == "POST" and path == "/api/rooms":
        body = {**body, "name": "第51批原委派复验"}
    return ORIGINAL_REQUEST_CALL(client, method, path, body, token=token)


def configure(directory):
    directory = directory.resolve()
    assert directory.is_relative_to(BASE.resolve()) and directory != BASE.resolve()
    previous.DIRECTORY = directory
    previous.PORT = PORT
    previous.request = request
    return directory


def preflight(directory):
    check_protection(directory)
    proof = integrity(directory)
    assert proof["all_protected_hashes_match"]
    assert all(row["equal"] for row in proof["retained"].values())
    assert len(proof["copied_cards"]) == 3
    assert all(row["same_source_character"] and row["full_snapshot_equal"]
               for row in proof["copied_cards"])
    assert all(row["same_profile_id"] for row in proof["copied_profiles"])
    assert proof["same_preparation_binding"] and proof["source_entity_snapshots_equal"]
    assert proof["note_back_hidden_before_turn"]
    expected = previous.read(ROOT / "data/prepared/batch-49/real-10/effective-config.json")
    actual = previous.read(directory / "effective-config.json")
    assert actual == expected, "Do not change model, window, output budget or runtime configuration"
    assert actual["model_name"] == "qwen3:8b" and actual["model_context_limit"] == 16384
    assert actual["model_output_limit"] == 900 and actual["agent_context_chars"] == 12000
    original = previous.read(previous.SOURCE / "delegation-state-01.json")
    event = next(row for row in original["room_events"]
                 if row["room_id"] == previous.OLD_ROOM and row["seq"] == 84)
    assert event["payload"]["text"] == ORIGINAL_REQUEST
    setup = previous.read(directory / "setup-state.json")
    with previous.connection() as db:
        entities = previous.rows(db, "SELECT * FROM room_entity_states WHERE room_id=?",
                                 (setup["room_id"],))
        backs = [row for row in entities if row["snapshot"].get("title") == "便签背面"]
        assert backs and all(row["state"] == "hidden" for row in backs)
        members = previous.rows(db, "SELECT * FROM room_members WHERE room_id=? AND active=1",
                                (setup["room_id"],))
        proof["retained_member_names"] = [row["display_name"] for row in members]
    proof.update(effective_config=actual, request=ORIGINAL_REQUEST, new_model_calls=0)
    previous.write(directory / "batch51-preflight.json", proof)
    return proof


def export(directory):
    from summarize_batch51_timings import summarize

    protection = check_protection(directory)
    case = previous.read(directory / "delegate-case.json")
    metric = natural_case(directory / "delegate-case.json")
    raw_calls = {row["id"]: row["document"] for row in case["audit"]["agent_model_calls"]}
    for call in metric["calls"]:
        call["queue_wait_ms"] = raw_calls[call["id"]].get("queue_wait_ms")
    elapsed = [call["model_elapsed_ms"] for call in metric["calls"]]
    metric["model_elapsed_ms"] = (sum(elapsed)
                                  if all(value is not None for value in elapsed) else None)
    metric["timing_note"] = "Missing call timings remain unknown; phase intervals may overlap."
    ids = set(metric["cycle_ids"])
    runs = [row for row in case["audit"]["agent_runs"] if row["cycle_id"] in ids]
    narration_runs = {row["id"] for row in runs
                      if row["graph_node"] == "generate_keeper_narration"}
    calls = [row for row in case["audit"]["agent_model_calls"]
             if row["run_id"] in narration_runs]
    previous.write(directory / "model-originals.json", calls)
    previous.write(directory / "server-narration-audit.json", {
        "runs": [row for row in runs if row["id"] in narration_runs],
        "events": [row for row in case["audit"]["room_events"]
                   if row["payload"].get("cycle_id") in ids],
        "task_states": metric["task_states"],
    })
    previous.write(directory / "derived-formal-bodies.json", [
        {"seq": row["seq"], **row["payload"]}
        for row in case["audit"]["room_events"]
        if row["type"] == "keeper.narration" and row["payload"].get("cycle_id") in ids
    ])
    previous.write(directory / "batch51-metrics.json", metric)
    previous.write(directory / "batch51-source-integrity.json", integrity(directory))
    previous.write(directory / "batch51-frozen-evidence-protection.json", protection)
    previous.write(directory / "stage-timings-summary.json", summarize(directory))
    setup = previous.read(directory / "setup-state.json")
    with previous.connection() as db:
        previous.write(directory / "memory-and-receipts.json", {
            table: previous.rows(db, f'SELECT * FROM "{table}" WHERE room_id=?',
                                 (setup["room_id"],))
            for table in ("agent_memories", "agent_tool_receipts", "room_entity_states")
        })
    return metric


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=BASE / "real-01")
    group = parser.add_mutually_exclusive_group(required=True)
    for name in ("static_check", "prepare", "serve", "setup", "preflight", "case", "export"):
        group.add_argument("--" + name.replace("_", "-"), action="store_true")
    args = parser.parse_args()
    directory = configure(args.directory)
    if args.static_check:
        proof = static_check()
        BASE.mkdir(parents=True, exist_ok=True)
        previous.write(BASE / "static-preflight.json", proof)
        print(json.dumps({"effective_config": proof["effective_config"],
                          "protected_file_count": len(proof["protected_sha256"]),
                          "model_calls": 0}, ensure_ascii=False))
    elif args.prepare:
        prepare(directory)
    elif args.serve:
        previous.serve()
    elif args.setup:
        previous.setup()
        preflight(directory)
    elif args.preflight:
        preflight(directory)
    elif args.case:
        preflight(directory)
        ready = directory / "browser-observer/browser-observer-ready.json"
        assert ready.exists(), "Both actual player browsers must be observing before submission"
        observer = previous.read(ready)
        setup = previous.read(directory / "setup-state.json")
        assert observer.get("room_id") == setup["room_id"] and observer.get("browsers") == 2
        assert observer.get("mode") == "player-only"
        # Never reuse a half-failed submission or race a second operator/process.
        with (directory / "delegate-submission-claimed.json").open("x", encoding="utf-8") as claim:
            json.dump({"room_id": setup["room_id"], "request": ORIGINAL_REQUEST,
                       "claimed_at": time.time()}, claim, ensure_ascii=False, indent=2)
        previous.case("delegate")
        export(directory)
    else:
        export(directory)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
