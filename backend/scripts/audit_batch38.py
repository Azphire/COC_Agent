"""Batch 38 delivery metrics; reuse the existing exports, avoid nested-cycle sums."""

import json
import sqlite3

from scripts.audit_batch36 import stamp
from scripts.audit_batch37 import audit_quality as existing_quality
from scripts.play_batch36 import write


def audit_quality(directory):
    existing_quality(directory)

    def load(name):
        return json.loads((directory / "private-audit" / (name + ".json")).read_text(
            encoding="utf-8",
        ))

    runs, plans, cycles = [load(n) for n in ("agent-runs", "cycle-audits", "cycles")]
    counts = {"full": 0, "partial": 0}
    detail = []
    for row in plans:
        validation = row["document"].get("narration_validation", {})
        if not validation.get("fallback_reason"):
            continue
        run = next((r for r in runs if r["cycle_id"] == row["cycle_id"]
                    and r["graph_node"] == "generate_keeper_narration"), {})
        kept = run.get("context", {}).get("validated_partial", {})
        kind = "partial" if kept.get("public_narration") or kept.get("npc_speech") else "full"
        counts[kind] += 1
        detail.append({"cycle_id": row["cycle_id"], "kind": kind,
                       "npc_missing_question_indices": validation.get(
                           "npc_missing_question_indices", [],
                       )})
    path = directory / "interaction-metrics.json"
    metrics = json.loads(path.read_text(encoding="utf-8"))
    metrics.update(fallback_cycles=counts, fallback_details=detail)
    metrics["teammate_decision_failures"] = sum(
        e["type"] == "agent.teammate_decision"
        and e["payload"].get("task_status") == "generation_failed"
        and not e["payload"].get("deterministically_skipped")
        and bool(e["payload"].get("failure_reason") or e["payload"].get("rejections"))
        for e in load("host-events")
    )
    # The player log also includes their own SAN results. They are visible to
    # this player but are not global-public events in the database export.
    projected_path = directory / "public-events.json"
    if projected_path.exists():
        provenance_path = directory / "fixture-provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8")) \
            if provenance_path.exists() else {}
        projected = [e for e in json.loads(projected_path.read_text(encoding="utf-8"))
                     if e["seq"] > provenance.get("initial_max_seq", 0)]
        metrics["generic_sanity_clarification_event_seqs"] = [
            e["seq"] for e in projected if e["type"] == "chat.message"
            and e["payload"].get("text") == "请补充你这次实际遭遇的情况。"
        ]
        metrics["clarification_messages_total"] = (
            metrics["clarifications"] + len(metrics["generic_sanity_clarification_event_seqs"])
        )
        extra = [e for e in projected if e["visibility"] != "public"]
        metrics["player_visible_nonpublic_events"] = len(extra)
        if extra:
            transcript = directory / "session-full.md"
            lines = [transcript.read_text(encoding="utf-8"),
                     "## 玩家本人可见事件补充", "",
                     "以下来自玩家日志投影，主要为本人 SAN；不是全员公开或 KP 私密事件。"
                     "按原始 seq 可与上文对照，完整玩家视角见 public-events.json。", ""]
            for event in extra:
                payload = event["payload"]
                lines += [f"### seq {event['seq']} · {event['type']}", "",
                          payload.get("text") or payload.get("display_text", ""), "",
                          "```json", json.dumps(event, ensure_ascii=False, indent=2), "```", ""]
            transcript.write_text("\n".join(lines), encoding="utf-8")
    write(path, metrics)

    def union_ms(intervals):
        total, end = 0, None
        for start, stop in sorted(intervals):
            total += max(0, (stop - max(start, end or start)).total_seconds() * 1000)
            end = max(stop, end or stop)
        return round(total)

    intervals = [(stamp(c["created_at"]), stamp(c["finished_at"]))
                 for c in cycles if c.get("finished_at")]
    timing_path = directory / "timing-summary.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    active_ms = union_ms(intervals)
    timing["active_cycle_union_ms"] = active_ms
    timing["non_model_active_ms"] = active_ms - timing["model_ms"]
    timing["non_model_active_definition"] = (
        "Union of active cycle intervals minus measured model execution; includes "
        "application, validation, checkpointing, queueing and roll waits. Nested "
        "parent/child cycles are counted once. This is not application CPU time."
    )
    write(timing_path, timing)
    with sqlite3.connect(f"file:{(directory / 'game.db').as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        from scripts.audit_batch36 import decode

        write(directory / "private-audit" / "behavior-state.json", [
            decode(row) for row in db.execute("select * from agent_behavior_states")
        ])
