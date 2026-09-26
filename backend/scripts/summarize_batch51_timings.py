"""Summarize one run's phase spans; nested durations must not be added together."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from audit_batch49_metrics import natural_case


def aggregate(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get(key) or "unattributed"].append(row)
    result = {}
    for name, items in groups.items():
        known = [row["elapsed_ms"] for row in items if row.get("elapsed_ms") is not None]
        result[name] = {
            "count": len(items), "measured_count": len(known),
            "failed_count": sum(row["status"] == "failed" for row in items),
            "unfinished_count": sum(row["status"] == "started" for row in items),
            "total_ms": round(sum(known), 3) if known else None,
            "max_ms": max(known) if known else None,
        }
    return result


def summarize(directory):
    path = directory / "stage-timings.jsonl"
    if not path.exists():
        return {"available": False, "reason": "stage-timings.jsonl missing", "phases": {}}
    case = json.loads((directory / "delegate-case.json").read_text(encoding="utf-8"))
    setup = json.loads((directory / "setup-state.json").read_text(encoding="utf-8"))
    metric = natural_case(directory / "delegate-case.json")
    cycle_ids = set(metric["cycle_ids"])
    run_ids = {row["id"] for row in case["audit"]["agent_runs"] if row["cycle_id"] in cycle_ids}
    started = case["started_at"]
    finished = (started + case["elapsed_seconds"]
                if case.get("elapsed_seconds") is not None else None)
    latest, points = {}, []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("room_id") != setup["room_id"]:
            continue
        begins = row.get("started_at") or row.get("at")
        at = row.get("finished_at") or begins
        if begins is None or begins < started or (finished is not None and at > finished):
            continue
        if row.get("cycle_id") is not None and row["cycle_id"] not in cycle_ids:
            continue
        if row.get("run_id") is not None and row["run_id"] not in run_ids:
            continue
        if row["status"] == "observed":
            points.append(row)
        else:
            latest[row["span_id"]] = row
    spans = [row for row in latest.values() if row.get("cycle_id") in cycle_ids
             or row.get("run_id") in run_ids]
    unattributed = [row for row in latest.values() if row not in spans]
    points = [row for row in points if row.get("cycle_id") in cycle_ids
              or row.get("run_id") in run_ids]

    def descends(row, parent):
        seen = set()
        while row.get("parent_span_id") in latest:
            identity = row["parent_span_id"]
            if identity == parent:
                return True
            if identity in seen:
                break
            seen.add(identity)
            row = latest[identity]
        return False

    audit_snapshots = []
    for row in spans:
        if row["phase"] not in {"call.audit_pipeline", "model.call_audit"}:
            continue
        snapshots = [item for item in spans if item["phase"] == "snapshot.build"
                     and descends(item, row["span_id"])]
        durations = [item["elapsed_ms"] for item in snapshots if item["elapsed_ms"] is not None]
        audit_snapshots.append({
            "span_id": row["span_id"], "phase": row["phase"], "run_id": row.get("run_id"),
            "elapsed_ms": row["elapsed_ms"], "snapshot_count": len(snapshots),
            "snapshot_ms": round(sum(durations), 3) if durations else None,
        })
    publications = []
    for row in points:
        if row["phase"] != "formal.committed":
            continue
        enqueues = [item for item in spans if item["phase"] == "formal.publish_enqueue"
                    and item.get("event_seq") == row.get("event_seq")]
        publications.append({
            "event_seq": row.get("event_seq"), "cycle_id": row.get("cycle_id"),
            "committed_at": row["at"], "connections": [{
                "member_id": item.get("member_id"), "enqueue_at": item["started_at"],
                "commit_to_enqueue_ms": round((item["started_at"] - row["at"]) * 1000, 3),
                "enqueue_ms": item["elapsed_ms"], "status": item["status"],
            } for item in enqueues],
        })
    return {
        "available": True, "room_id": setup["room_id"], "cycle_ids": sorted(cycle_ids),
        "run_ids": sorted(run_ids), "started_at": started, "finished_at": finished,
        "phases": aggregate(spans, "phase"),
        "callbacks": aggregate([row for row in spans if row["phase"] == "mutate"], "callback"),
        "audit_snapshots": audit_snapshots, "formal_publications": publications,
        "failed_spans": [row for row in spans if row["status"] == "failed"],
        "unclosed_spans": [row for row in spans if row["status"] == "started"],
        "spans": spans, "points": points,
        "unattributed_room_phases": aggregate(unattributed, "phase"),
        "unattributed_room_spans": unattributed,
        "interpretation": "Intervals are nested and overlapping. Sum only within a phase; "
        "never subtract their combined sum from model/workflow wall time. Missing is unknown. "
        "Model latency boundaries come from each call's own fields; on_call and audit may overlap. "
        "Enqueue is not WebSocket send or browser DOM visibility. "
        "Unattributed room spans are separate.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = summarize(args.directory)
    (args.directory / "stage-timings-summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps({"available": result["available"], "phases": result["phases"]}, indent=2))
