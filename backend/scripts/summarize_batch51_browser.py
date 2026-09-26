"""Separate formal completion, model visibility and reconnect evidence in one run."""

import argparse
import json
from pathlib import Path

from audit_batch49_metrics import natural_case
from summarize_batch50_browser import event_seconds, positive_before, read

TIMING_FIELDS = (
    "attempt", "token_usage", "latency_ms", "model_elapsed_ms", "queue_wait_ms",
    "queued_at", "semaphore_acquired_at", "on_call_ms", "request_preparation_ms",
    "request_started_at", "first_chunk_at", "terminal_received_at", "terminal_received_ms",
    "adapter_returned_at", "adapter_completed_at", "validation_ms", "stream_callback_ms",
    "model_finished_at", "model_finished_at_semantics", "first_validated_segment_at",
    "first_model_published_at", "first_server_published_at", "first_retained_published_at",
    "stream_segments", "provider_chunks", "answer_origin", "answer_contract_version",
)


def assess_answer(event, validation, call, evidence, started):
    payload, doc = event["payload"], (call or {}).get("document", {})
    cycle_id, seq, text = payload["cycle_id"], event["seq"], payload["text"]
    frames, dom = evidence["frames"], evidence["dom"]
    ends = [frame for frame in frames if frame["type"] == "keeper.stream.end"
            and (frame.get("data") or {}).get("cycle_id") == cycle_id
            and (frame.get("data") or {}).get("event_seq") == seq]
    end = ends[-1] if ends else None
    stream_id = end["data"]["stream_id"] if end else payload.get("stream_id")
    attempt = end["data"].get("attempt") if end else payload.get("stream_attempt")
    call_matches = bool(call and doc.get("attempt") == attempt
                        and doc.get("stream_id") == stream_id)
    if not call_matches:
        doc = {}
    selected = [f for f in frames if stream_id
                and (f.get("data") or {}).get("stream_id") == stream_id
                and (f.get("data") or {}).get("attempt") == attempt]
    deltas = [f for f in selected if f["type"] == "keeper.stream.delta"]
    snapshots = [f for f in selected if f["type"] == "keeper.stream.snapshot"
                 and f["data"].get("status") == "responding"]
    reconnects = [f for f in selected if f["type"] == "observer.forced_reconnect"]
    stream_dom = [(entry["at"] / 1000, row) for entry in dom for row in entry["streams"]
                  if row["stream"] == stream_id and row["status"] == "responding"
                  and str(row.get("attempt")) == str(attempt)
                  and int(row.get("index") or 0) > 0 and row.get("text")]
    bodies = list(dict.fromkeys(row["text"] for _, row in stream_dom))
    final_bubbles = ([row for row in dom[-1]["timeline"] if str(row["seq"]) == str(seq)]
                     if dom else [])
    segments = [s for s in doc.get("stream_segments", []) if s.get("published_at") is not None
                and s.get("stream_id") == stream_id and s.get("attempt") == attempt]
    origins = {source: [s for s in segments if s.get("source") == source]
               for source in ("model", "server", "retained")}
    terminal = doc.get("terminal_received_at")

    def visible_at(segment):
        offset = segment.get("body_end")
        if not isinstance(offset, int):
            return None
        return next((stamp for stamp, row in stream_dom
                     if len(row["text"]) >= offset and text.startswith(row["text"])), None)

    first_dom = {source: visible_at(rows[0]) if rows else None
                 for source, rows in origins.items()}
    model = origins["model"]
    provider_chunks = doc.get("provider_chunks", [])
    terminal_from_provider = any(chunk.get("terminal")
                                 and chunk.get("received_at") == terminal
                                 for chunk in provider_chunks)
    first_model_published = model[0].get("published_at") if model else None
    model_visible = terminal_from_provider and positive_before(first_dom["model"], terminal)
    model_published = terminal_from_provider and positive_before(first_model_published, terminal)
    first_model_end = model[0].get("body_end") if model else None
    reconnect_before_terminal = bool(model_visible and any(
        positive_before(frame["at"] / 1000, terminal) for frame in reconnects
    ))
    reconnect_pairs = []
    if model_visible and isinstance(first_model_end, int):
        for reconnect in reconnects:
            disconnected_at = reconnect["at"] / 1000
            snapshot = next((frame for frame in snapshots if
                             positive_before(disconnected_at, frame["at"] / 1000)
                             and positive_before(frame["at"] / 1000, terminal)
                             and len(frame["data"].get("text", "")) >= first_model_end
                             and text.startswith(frame["data"].get("text", ""))), None)
            if snapshot:
                reconnect_pairs.append({"reconnect_at": disconnected_at,
                                        "snapshot_at": snapshot["at"] / 1000})
    # Both frames already share this stream and attempt. The received snapshot
    # must also follow that disconnection: an earlier snapshot proves no recovery.
    snapshot_before_terminal = bool(reconnect_pairs)
    model_incremental = len(model) >= 2 and all(
        first.get("chunk_index") is not None
        and first.get("chunk_index") != later.get("chunk_index")
        and positive_before(first.get("published_at"), later.get("received_at"))
        and positive_before(visible_at(first), visible_at(later))
        for first, later in zip(model, model[1:])
    )
    formal_complete = bool(validation.get("answer_complete") and validation.get("valid")
                           and payload.get("answer_origin") in {"native", "repaired", "mixed"}
                           and not payload.get("safe_fallback"))
    row = {
        "seq": seq, "cycle_id": cycle_id, "stream_id": stream_id, "attempt": attempt,
        "call_id": call["id"] if call_matches else None,
        "answer_origin": payload.get("answer_origin"), "formal_complete": formal_complete,
        "has_successful_stream_end": end is not None,
        "delta_count": len(deltas), "distinct_stream_bodies": bodies,
        "part_counts_by_source": {source: len(rows) for source, rows in origins.items()},
        "first_dom_at_by_source": first_dom,
        "first_model_published_at": first_model_published,
        "terminal_received_at": terminal,
        "terminal_receipt_verified": terminal_from_provider,
        "adapter_returned_at": doc.get("adapter_returned_at"),
        "legacy_model_finished_at": doc.get("model_finished_at"),
        "model_published_before_terminal": model_published,
        "model_visible_before_terminal": model_visible,
        "model_incremental_append": model_incremental,
        "model_incrementality_evidence": (
            "separate_model_parts_observed" if model_incremental
            else "single_model_part_no_multi_part_claim" if len(model) == 1
            else "not_obtained"
        ),
        "reconnect_during_generation": reconnect_before_terminal,
        "responding_snapshot_during_generation": snapshot_before_terminal,
        "reconnect_snapshot_pairs": reconnect_pairs,
        "reconnect_evidence": "obtained" if reconnect_before_terminal and snapshot_before_terminal
        else "not_obtained",
        "all_stream_bodies_prefix_formal": bool(bodies)
        and all(text.startswith(body) for body in bodies),
        "final_bubble_count": len(final_bubbles),
        "final_matches_history_exactly": len(final_bubbles) == 1
        and final_bubbles[0]["text"] == text,
        "no_remaining_draft": bool(dom)
        and not any(s["cycle"] == cycle_id for s in dom[-1]["streams"]),
        "interrupted": any(f["type"] == "keeper.stream.interrupt" for f in selected),
        "formal_event_seconds": event_seconds(event, started),
    }
    row["same_attempt_formal_passed"] = all(row[key] for key in (
        "formal_complete", "has_successful_stream_end", "all_stream_bodies_prefix_formal",
        "final_matches_history_exactly", "no_remaining_draft",
    )) and not row["interrupted"] and call_matches
    row["same_attempt_generation_visible"] = bool(
        row["same_attempt_formal_passed"] and model_published and model_visible,
    )
    return row


def summarize(directory):
    capture = read(directory / "delegate-case.json")
    metric = natural_case(directory / "delegate-case.json")
    ids, audit = set(metric["cycle_ids"]), capture["audit"]
    runs = {row["id"]: row for row in audit["agent_runs"] if row["cycle_id"] in ids}
    calls = [row for row in audit["agent_model_calls"] if row["run_id"] in runs]
    events = [row for row in audit["room_events"] if row["type"] == "keeper.narration"
              and row["payload"].get("cycle_id") in ids]
    validations = {row["payload"]["cycle_id"]: row["payload"] for row in audit["room_events"]
                   if row["type"] == "agent.narration_validated"
                   and row["payload"].get("cycle_id") in ids}
    result = {
        "request": capture["request"], "cycle_ids": sorted(ids),
        "model_call_count": metric["model_call_count"], "usage": metric["usage"],
        "elapsed_seconds": metric["elapsed_seconds"], "browsers": {},
        "timing_basis": "provider terminal receipt precedes its body callback; "
        "adapter return includes callbacks. An earlier awaited callback can delay receipt "
        "of a later frame: receipt order does not prove the provider's internal compute interval.",
        "assessment": "Model generation quality, mixed formal completion, visibility and reconnect "
        "are separate findings from this run and attempt only. Missing times remain null.",
        "calls": [{"id": row["id"], "run_id": row["run_id"],
                   "cycle_id": runs[row["run_id"]]["cycle_id"],
                   "graph_node": runs[row["run_id"]]["graph_node"],
                   **{key: row["document"].get(key) for key in TIMING_FIELDS}} for row in calls],
    }
    for name in ("player-live", "player-reconnect"):
        observed = read(directory / "browser-observer" / name / "observation.json")
        answers = []
        for event in events:
            matches = [call for call in calls if runs[call["run_id"]]["cycle_id"]
                       == event["payload"]["cycle_id"]
                       and call["document"].get("stream_id") == event["payload"].get("stream_id")
                       and call["document"].get("attempt")
                       == event["payload"].get("stream_attempt")]
            answers.append(assess_answer(
                event, validations.get(event["payload"]["cycle_id"], {}),
                matches[0] if len(matches) == 1 else None,
                observed["evidence"], capture["started_at"],
            ))
        result["browsers"][name] = {"privacy": observed.get("privacy"), "formal_answers": answers}
    for condition in ("same_attempt_formal_passed", "same_attempt_generation_visible"):
        identities = [{(row["cycle_id"], row["stream_id"], row["attempt"], row["seq"])
                       for row in browser["formal_answers"] if row[condition]}
                      for browser in result["browsers"].values()]
        result[condition + "_in_both_windows"] = bool(set.intersection(*identities))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = summarize(args.directory.resolve())
    (args.directory / "browser-observer/batch51-browser-summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
