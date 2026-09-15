"""Build a shareable evidence index from local batch-21 artifacts, without tokens."""

import hashlib
from collections import Counter
from xml.etree import ElementTree

from module_package import ROOT, read, write


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def events(directory):
    value = read(directory / "HOST_DEBUG-events.json")
    return value.get("events", []) if isinstance(value, dict) else value


def audit():
    base = ROOT / "data/prepared/changan/batch-21"
    result = dict(evidence_root=str(base.relative_to(ROOT)).replace("\\", "/"))
    result["packages"] = {
        "original_sha256": digest(base.parent / "batch-18/package-approved.json"),
        "new_sha256": digest(base / "package-approved.json"),
        "changed_entities": [c["key"] for c in read(base / "package-diff.json")["changes"]],
    }
    assert (
        result["packages"]["original_sha256"]
        == "b47bf0fb5127e4bc46fdb1c5a97fd9b41fe358eea2595351b5e19d95411e6c87"
    )
    assert result["packages"]["new_sha256"] == read(base / "package-diff.json")["output_sha256"]
    result["model"] = read(base / "short-c2/model-settings.json")
    assert result["model"]["model_name"] == "qwen3:8b"
    assert result["model"]["model_context_limit"] == 8192
    protected = {}
    for name in ["short-c1", "short-c2"]:
        provenance = read(base / name / "fixture-provenance.json")
        source = ROOT / provenance["source"]
        protected[name] = {
            file: digest(source / file) == expected
            for file, expected in provenance["source_db_sha256"].items()
        }
        assert all(protected[name].values()), "Historical source database changed"
    result["historical_databases_unchanged"] = protected
    result["tests"] = {}
    for name in ["all-c1", "final", "final-items"]:
        suite = ElementTree.parse(base / (name + ".xml")).getroot().find("testsuite")
        result["tests"][name] = {
            k: suite.attrib[k] for k in ["tests", "failures", "errors", "skipped", "time"]
        }
    result["browser"] = read(base / "browser-c2/result.json")
    world_ops = {
        "module.interaction",
        "scene.updated",
        "entity.revealed",
        "clue.revealed",
        "check.requested",
        "check.resolved",
        "combat.action_started",
        "module.completed",
    }
    for name in ["short-c1", "short-c2", "new-card-c2"]:
        directory = base / name
        history = events(directory)
        calls = read(directory / "model-calls.json")
        room = read(directory / "room-final.json")
        entry = {
            "model_calls_in_export": len(calls),
            "inventory": room["inventory"],
            "outcome": room["session_state"]["module_runtime"].get("outcome"),
            "recalls": [],
        }
        if name.startswith("short"):
            inherited = len(read(base.parent / "batch-20/long-c39/model-calls.json"))
            entry.update(inherited_model_calls=inherited, new_model_calls=len(calls) - inherited)
        else:
            entry["new_model_calls"] = len(calls)
        for plan in read(directory / "HOST_DEBUG-plans.json"):
            value = plan["document"]["plan"]
            seq = value.get("action_authority", {}).get("source_event_seq", 0)
            if value["parsed_intent"]["type"] != "recall" or seq < (
                1690 if name.startswith("short") else 1
            ):
                continue
            selected = [e for e in history if e["payload"].get("cycle_id") == plan["cycle_id"]]
            operations = [
                dict(seq=e["seq"], type=e["type"]) for e in selected if e["type"] in world_ops
            ]
            entry["recalls"].append(
                dict(
                    action_seq=seq,
                    operations=operations,
                    narration_seqs=[e["seq"] for e in selected if e["type"] == "keeper.narration"],
                )
            )
            assert not operations
        if (directory / "restore-comparison.json").exists():
            entry["restore"] = read(directory / "restore-comparison.json")
            assert all(entry["restore"].values())
            before, after = [
                read(directory / filename)
                for filename in ["before-restart.json", "after-restart.json"]
            ]
            def cards(view):
                return {s["id"]: s["character_snapshot"] for s in view["character_slots"]}
            entry["character_snapshots_preserved"] = cards(before) == cards(after)
            assert entry["character_snapshots_preserved"]
        result[name] = entry
    paper = read(base / "short-c2/load-info.json")["entity_ids"]["newspaper"]
    room = read(base / "short-c2/room-final.json")
    inventory = room["session_state"]["module_runtime"]["inventory"]
    assert Counter(room["session_state"]["module_runtime"]["item_instances"].values())[paper] == 1
    assert paper in inventory
    date_id = read(base / "short-c2/load-info.json")["entity_ids"]["newspaper_date"]
    assert date_id not in {e["id"] for e in room["game"]["public_entities"]}
    new_checks = [
        e["seq"]
        for e in events(base / "short-c2")
        if e["seq"] > 1690 and e["type"] in {"check.requested", "check.resolved"}
    ]
    assert not new_checks
    result["short-c2"].update(
        new_checks=new_checks, newspaper_date_still_hidden=True, newspaper_instance_count=1
    )
    result["inherited_long_test"] = {
        "source": "batch-20/long-c39",
        "active_seconds": 4678.10,
        "summary_rebuilds": 47,
        "stale_event_seq": 1196,
        "scope": "historical conclusion, not rerun or combined",
    }
    write(ROOT / "docs/batch-21-coverage.json", result)
    print("Evidence index written; original databases and recall boundaries verified")


if __name__ == "__main__":
    audit()
