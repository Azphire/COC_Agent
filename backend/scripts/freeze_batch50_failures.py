"""Freeze actual batch 49 failures verbatim; never transform coverage into prose."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "backend/tests/fixtures/batch50"
BASELINE = "42401ab511e8823e8a0082b5e0ca91385f660648"
SOURCES = {
    "real-09": "1de3e6bf63a1f55cb22a97889ea5468d8a96656d4d9b63a5117822dd96cac3eb",
    "real-10": "5c79ea046ce7a6a9eb7bf8c6c5fe7b8b712113e09b67354f509090e4f9b207cb",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def freeze():
    DESTINATION.mkdir(parents=True, exist_ok=False)
    manifest = {
        "baseline": BASELINE,
        "actual_branch_at_freeze": "main",
        "user_baseline_label": "repair",
        "method": "Unmodified JSON subtrees from original context, calls and formal events",
        "model_calls": 0,
        "files": {},
    }
    for label, expected in SOURCES.items():
        source = ROOT / "data/prepared/batch-49" / label / "delegate-case.json"
        original = source.read_bytes()
        assert digest(original) == expected, f"Changed original: {source}"
        case = json.loads(original.decode("utf-8"))
        audit = case["audit"]
        runs = [run for run in audit["agent_runs"]
                if run["graph_node"] == "generate_keeper_narration"]
        assert len(runs) == 1
        run = runs[0]
        calls = [row for row in audit["agent_model_calls"] if row["run_id"] == run["id"]]
        assert len(calls) == 2
        frozen = {
            "source": source.relative_to(ROOT).as_posix(),
            "source_sha256": expected,
            "request": case["request"],
            "root_cycle_id": case["cycle_id"],
            "run": run,
            "calls": calls,
            "formal_events": [event for event in audit["room_events"]
                              if event["type"] in {"keeper.narration", "agent.answer_incomplete"}
                              and event["payload"].get("cycle_id") == run["cycle_id"]],
        }
        name = f"{label}-delegate-failure.json"
        data = json.dumps(frozen, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        with (DESTINATION / name).open("xb") as stream:
            stream.write(data)
        manifest["files"][name] = {
            "sha256": digest(data),
            "source_sha256": expected,
            "context_sha256": digest(canonical(run["context"])),
            "call_document_sha256": [digest(canonical(row["document"])) for row in calls],
        }
        assert source.read_bytes() == original
    with (DESTINATION / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return manifest


def freeze_current_failure():
    """Keep the first actual new-protocol failure independently of old fixtures."""
    source = ROOT / "data/prepared/batch-50/real-01/delegate-case.json"
    original = source.read_bytes()
    expected = "e3a1e4b9cdfd88a9e94f2832cfcca4097dfdff2c3bfdfb2f889e5138e81de1a2"
    assert digest(original) == expected
    case = json.loads(original.decode("utf-8"))
    runs = [run for run in case["audit"]["agent_runs"]
            if run["graph_node"] == "generate_keeper_narration"]
    assert len(runs) == 1
    run = runs[0]
    calls = [row for row in case["audit"]["agent_model_calls"] if row["run_id"] == run["id"]]
    assert len(calls) == 2
    frozen = {
        "source": source.relative_to(ROOT).as_posix(), "source_sha256": expected,
        "request": case["request"], "root_cycle_id": case["cycle_id"], "run": run,
        "calls": calls,
        "formal_events": [event for event in case["audit"]["room_events"]
                          if event["type"] in {"keeper.narration", "agent.narration_validated"}
                          and event["payload"].get("cycle_id") == run["cycle_id"]],
    }
    name = "real-01-current-failure.json"
    data = json.dumps(frozen, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    with (DESTINATION / name).open("xb") as stream:
        stream.write(data)
    manifest = {
        "file": name, "sha256": digest(data), "source_sha256": expected,
        "context_sha256": digest(canonical(run["context"])),
        "call_document_sha256": [digest(canonical(row["document"])) for row in calls],
        "new_model_calls": 0,
    }
    with (DESTINATION / "real-01-current-manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    assert source.read_bytes() == original
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-01", action="store_true")
    args = parser.parse_args()
    print(json.dumps(freeze_current_failure() if args.real_01 else freeze(),
                     ensure_ascii=True, indent=2))
