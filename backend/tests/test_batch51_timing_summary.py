import importlib
import json
from pathlib import Path


def test_timing_summary_keeps_run_boundaries_and_unknown_durations(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("summarize_batch51_timings")
    monkeypatch.setattr(module, "natural_case", lambda _: {"cycle_ids": ["cycle"]})
    for name, value in {
        "setup-state.json": {"room_id": "room"},
        "delegate-case.json": {"started_at": 100, "elapsed_seconds": 10,
                               "audit": {"agent_runs": [{"id": "run", "cycle_id": "cycle"}]}},
    }.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    common = {"room_id": "room", "cycle_id": "cycle", "run_id": "run",
              "callback": "operation", "parent_span_id": None}
    audit = {**common, "span_id": "audit", "phase": "call.audit_pipeline",
             "started_at": 101, "finished_at": None, "elapsed_ms": None, "status": "started"}
    snapshot = {**common, "span_id": "snapshot", "parent_span_id": "audit",
                "phase": "snapshot.build", "started_at": 102, "finished_at": 104,
                "elapsed_ms": 2000, "status": "completed"}
    unfinished = {**audit, "span_id": "unfinished", "phase": "prepare", "started_at": 109}
    rows = [audit, snapshot, {**audit, "finished_at": 105, "elapsed_ms": 4000,
                             "status": "completed"}, unfinished]
    rows.extend({**snapshot, "span_id": name, **overrides} for name, overrides in (
        ("earlier", {"started_at": 90, "finished_at": 91}),
        ("earlier-overlap", {"started_at": 99, "finished_at": 104}),
        ("later", {"started_at": 111, "finished_at": 112}),
        ("other-cycle", {"cycle_id": "other"}), ("other-run", {"run_id": "other"}),
        ("other-room", {"room_id": "other"}),
        ("unattributed", {"cycle_id": None, "run_id": None}),
    ))
    rows.extend([
        {**common, "phase": "formal.committed", "status": "observed", "event_seq": 42,
         "at": 106, "elapsed_ms": None},
        {**snapshot, "span_id": "enqueue", "phase": "formal.publish_enqueue", "event_seq": 42,
         "started_at": 107, "finished_at": 107, "elapsed_ms": 0},
    ])
    (tmp_path / "stage-timings.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8",
    )
    result = module.summarize(tmp_path)
    assert result["phases"]["call.audit_pipeline"]["count"] == 1
    assert result["phases"]["snapshot.build"]["total_ms"] == 2000
    assert result["phases"]["prepare"]["total_ms"] is None
    assert len(result["unclosed_spans"]) == 1
    assert len(result["unattributed_room_spans"]) == 1
    assert result["audit_snapshots"][0]["snapshot_count"] == 1
    assert result["formal_publications"][0]["connections"][0]["commit_to_enqueue_ms"] == 1000
