"""Judge only the same successful delegation attempt, using exact body text."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from audit_batch49_metrics import natural_case


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def positive_before(first, last):
    return isinstance(first, (float, int)) and isinstance(last, (float, int)) and first < last


def event_seconds(event, started):
    value = event.get("occurred_at")
    if not value:
        return None
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return round(timestamp.timestamp() - started, 3)


def summarize(directory):
    capture = read(directory / "delegate-case.json")
    metric = natural_case(directory / "delegate-case.json")
    ids = set(metric["cycle_ids"])
    audit = capture["audit"]
    runs = {row["id"]: row for row in audit["agent_runs"]
            if row["cycle_id"] in ids and row["graph_node"] == "generate_keeper_narration"}
    all_runs = {row["id"]: row for row in audit["agent_runs"] if row["cycle_id"] in ids}
    calls_in_case = [row for row in audit["agent_model_calls"] if row["run_id"] in all_runs]
    events = [row for row in audit["room_events"]
              if row["type"] == "keeper.narration" and row["payload"].get("cycle_id") in ids]
    validations = {row["payload"]["cycle_id"]: row["payload"]
                   for row in audit["room_events"]
                   if row["type"] == "agent.narration_validated"
                   and row["payload"].get("cycle_id") in ids}
    result = {
        "request": capture["request"], "cycle_ids": sorted(ids),
        "model_call_count": metric["model_call_count"], "usage": metric["usage"],
        "elapsed_seconds": metric["elapsed_seconds"], "browsers": {},
        "model_latency_ms": sum(row["document"].get("latency_ms", 0) for row in calls_in_case),
        "calls": [{
            "id": row["id"], "run_id": row["run_id"],
            "cycle_id": all_runs[row["run_id"]]["cycle_id"],
            "graph_node": all_runs[row["run_id"]]["graph_node"],
            **{key: row["document"].get(key) for key in (
                "attempt", "token_usage", "latency_ms", "model_elapsed_ms", "queue_wait_ms",
                "request_started_at", "first_chunk_at", "model_finished_at",
                "first_validated_segment_at",
            )},
        } for row in calls_in_case],
        "assessment": "Only exact current delegation cycle/stream/attempt is eligible; "
        "failures and earlier read successes never supply evidence.",
    }
    for name in ("player-live", "player-reconnect"):
        evidence = read(directory / "browser-observer" / name / "observation.json")
        frames, dom = evidence["evidence"]["frames"], evidence["evidence"]["dom"]
        observed = {}
        for frame in frames:
            data = frame.get("data") or {}
            if data.get("cycle_id") not in ids or not data.get("stream_id"):
                continue
            stream = observed.setdefault(data["stream_id"], {
                "cycle_id": data["cycle_id"], "attempt": data.get("attempt"),
                "delta_count": 0, "interrupt": False, "end_event_seq": None,
            })
            if frame["type"] == "keeper.stream.delta":
                stream["delta_count"] += 1
            elif frame["type"] == "keeper.stream.interrupt":
                stream["interrupt"] = True
            elif frame["type"] == "keeper.stream.end":
                stream["end_event_seq"] = data.get("event_seq")
        answers = []
        for event in events:
            payload = event["payload"]
            cid, seq, text = payload["cycle_id"], event["seq"], payload["text"]
            ends = [f for f in frames if f["type"] == "keeper.stream.end"
                    and (f.get("data") or {}).get("cycle_id") == cid
                    and (f.get("data") or {}).get("event_seq") == seq]
            end = ends[-1] if ends else None
            stream_id = end["data"]["stream_id"] if end else payload.get("stream_id")
            attempt = end["data"].get("attempt") if end else payload.get("stream_attempt")
            selected = [f for f in frames if stream_id
                        and (f.get("data") or {}).get("stream_id") == stream_id]
            deltas = [f for f in selected if f["type"] == "keeper.stream.delta"]
            snapshots = [f for f in selected if f["type"] == "keeper.stream.snapshot"
                         and f["data"].get("status") == "responding"]
            reconnects = [f for f in selected if f["type"] == "observer.forced_reconnect"]
            stream_dom = [(entry["at"] / 1000, row) for entry in dom for row in entry["streams"]
                          if row["stream"] == stream_id and row["status"] == "responding"
                          and int(row.get("index") or 0) > 0 and row.get("text")]
            formal_dom = [entry for entry in dom
                          if any(str(row["seq"]) == str(seq) for row in entry["timeline"])]
            final_bubbles = [row for row in dom[-1]["timeline"] if str(row["seq"]) == str(seq)]
            calls = [row for row in audit["agent_model_calls"]
                     if row["run_id"] in runs and runs[row["run_id"]]["cycle_id"] == cid
                     and row["document"].get("attempt") == attempt]
            doc = calls[0]["document"] if len(calls) == 1 else {}
            model_end = doc.get("model_finished_at")
            first_dom = stream_dom[0][0] if stream_dom else None
            bodies = list(dict.fromkeys(row["text"] for _, row in stream_dom))
            validation = validations.get(cid, {})
            formal_ok = bool(validation.get("answer_complete") and validation.get("valid")
                             and payload.get("answer_origin") in {"native", "repaired"}
                             and not payload.get("safe_fallback"))
            row = {
                "seq": seq, "cycle_id": cid, "stream_id": stream_id, "attempt": attempt,
                "has_successful_stream_end": end is not None,
                "answer_origin": payload.get("answer_origin"), "formal_complete": formal_ok,
                "call_id": calls[0]["id"] if len(calls) == 1 else None,
                "delta_count": len(deltas), "distinct_stream_bodies": bodies,
                "delta_offsets": [(frame.get("data") or {}).get("offset") for frame in deltas],
                "delta_seconds": [round(frame["at"] / 1000 - capture["started_at"], 3)
                                  for frame in deltas],
                "request_started_at": doc.get("request_started_at"),
                "first_model_chunk_at": doc.get("first_chunk_at"),
                "first_validated_segment_at": doc.get("first_validated_segment_at"),
                "model_finished_at": model_end, "first_dom_at": first_dom,
                "first_dom_seconds": round(first_dom - capture["started_at"], 3)
                if first_dom else None,
                "formal_dom_seconds": round(formal_dom[0]["at"] / 1000 - capture["started_at"], 3)
                if formal_dom else None,
                "formal_event_seconds": event_seconds(event, capture["started_at"]),
                "server_segment_before_model_finished": positive_before(
                    doc.get("first_validated_segment_at"), model_end,
                ),
                "visible_before_model_finished": positive_before(first_dom, model_end),
                "later_parts_append": len(bodies) >= 2 and all(
                    later.startswith(earlier) and len(later) > len(earlier)
                    for earlier, later in zip(bodies, bodies[1:])
                ),
                "all_stream_bodies_prefix_formal": bool(bodies)
                and all(text.startswith(body) for body in bodies),
                "reconnect_during_generation": any(positive_before(f["at"] / 1000, model_end)
                                                     for f in reconnects),
                "responding_snapshot_during_generation": any(
                    positive_before(f["at"] / 1000, model_end) for f in snapshots
                ),
                "final_bubble_count": len(final_bubbles),
                "final_matches_history_exactly": len(final_bubbles) == 1
                and final_bubbles[0]["text"] == text,
                "no_remaining_draft": not any(s["cycle"] == cid for s in dom[-1]["streams"]),
                "interrupted": any(f["type"] == "keeper.stream.interrupt" for f in selected),
            }
            row["same_attempt_stream_passed"] = all(row[key] for key in (
                "formal_complete", "has_successful_stream_end",
                "server_segment_before_model_finished",
                "visible_before_model_finished", "later_parts_append",
                "all_stream_bodies_prefix_formal", "final_matches_history_exactly",
                "no_remaining_draft",
            )) and not row["interrupted"] and len(deltas) >= 2
            if name == "player-reconnect":
                row["same_attempt_stream_passed"] = row["same_attempt_stream_passed"] and (
                    row["reconnect_during_generation"]
                    and row["responding_snapshot_during_generation"]
                )
            answers.append(row)
        result["browsers"][name] = {
            "privacy": evidence.get("privacy"), "observed_streams": observed,
            "formal_answers": answers,
        }
    success_sets = [
        {(row["cycle_id"], row["stream_id"], row["attempt"], row["seq"])
         for row in browser["formal_answers"] if row["same_attempt_stream_passed"]}
        for browser in result["browsers"].values()
    ]
    result["same_successful_attempt_in_both_windows"] = bool(set.intersection(*success_sets))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    output = summarize(args.directory.resolve())
    (args.directory / "browser-observer/batch50-browser-summary.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=True, indent=2))
