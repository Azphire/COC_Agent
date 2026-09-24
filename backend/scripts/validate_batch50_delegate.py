"""One original delegated turn in a fresh isolated room, using the normal APIs.

Preparation/setup are model-free. Only --case submits the unchanged original
request. Offline completion must be established by the operator first. The
driver refuses to overwrite an existing attempt or begin after a prior reveal.
"""

import argparse
import sys
from pathlib import Path

import validate_batch49_natural as previous
from audit_batch49_metrics import integrity, natural_case

ROOT = previous.ROOT
BASE = ROOT / "data/prepared/batch-50"
PORT = 8150
ORIGINAL_REQUEST = (
    "林修远·猎人，请用手检查门上便签正面的边缘，看看纸张有没有夹层或折叠，"
    "再把实际发现告诉大家。"
)
ORIGINAL_REQUEST_CALL = previous.request


def request(client, method, path, body=None, *, token=previous.TEST_HOST):
    if method == "POST" and path == "/api/rooms":
        body = {**body, "name": "第50批原委派复验"}
    return ORIGINAL_REQUEST_CALL(client, method, path, body, token=token)


def configure(directory):
    directory = directory.resolve()
    assert directory.is_relative_to(BASE.resolve()) and directory != BASE.resolve()
    previous.DIRECTORY = directory
    previous.PORT = PORT
    previous.request = request
    return directory


def preflight(directory):
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
    previous.write(directory / "batch50-preflight.json", proof)
    return proof


def export(directory):
    case = previous.read(directory / "delegate-case.json")
    metric = natural_case(directory / "delegate-case.json")
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
    previous.write(directory / "batch50-metrics.json", metric)
    previous.write(directory / "batch50-source-integrity.json", integrity(directory))
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
    for name in ("prepare", "serve", "setup", "preflight", "case", "export"):
        group.add_argument("--" + name, action="store_true")
    args = parser.parse_args()
    directory = configure(args.directory)
    if args.prepare:
        previous.prepare()
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
        previous.case("delegate")
        export(directory)
    else:
        export(directory)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
