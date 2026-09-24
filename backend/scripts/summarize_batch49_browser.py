"""Correlate browser DOM/stream receipts with this exact natural run's history."""

import json
import sys
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(directory):
    summary = {"source": "same-run browser observations and natural driver audit", "cases": {}}
    for case in ("read", "delegate"):
        path = directory / f"{case}-case.json"
        if not path.exists():
            continue
        capture = read(path)
        if capture["status"] != "captured":
            continue
        cycles = {capture["cycle_id"]}
        cycles.update(c["id"] for c in capture["audit"]["agent_cycles"]
                      if c["state"].get("related_player_cycle_id") == capture["cycle_id"])
        events = [e for e in capture["audit"]["room_events"]
                  if e["type"] == "keeper.narration" and e["payload"].get("cycle_id") in cycles]
        case_summary = {"started_at": capture["started_at"], "browsers": {}}
        for name in ("player-live", "player-reconnect"):
            evidence = read(directory / "browser-observer" / name / "observation.json")
            frames = evidence["evidence"]["frames"]
            dom = evidence["evidence"]["dom"]
            narrative_dom = [entry for entry in dom if any(
                s["cycle"] in cycles and int(s.get("index") or 0) > 0
                and s["status"] == "responding" for s in entry["streams"]
            )]
            result = {"privacy": evidence.get("privacy"), "formal_answers": [],
                      "first_narrative_dom_seconds": round(
                          narrative_dom[0]["at"] / 1000 - capture["started_at"], 3,
                      ) if narrative_dom else None}
            streams = {}
            for frame in frames:
                data = frame.get("data") or {}
                if data.get("cycle_id") not in cycles or not data.get("stream_id"):
                    continue
                item = streams.setdefault(data["stream_id"], {
                    "cycle_id": data["cycle_id"], "attempt": data.get("attempt"),
                    "delta_frames": 0, "interrupted": False, "ended_event_seq": None,
                    "forced_reconnect": False, "responding_snapshot": False,
                })
                if frame["type"] == "keeper.stream.delta":
                    item["delta_frames"] += 1
                if frame["type"] == "keeper.stream.interrupt":
                    item["interrupted"] = True
                if frame["type"] == "keeper.stream.end":
                    item["ended_event_seq"] = data.get("event_seq")
                if frame["type"] == "observer.forced_reconnect":
                    item["forced_reconnect"] = True
                if frame["type"] == "keeper.stream.snapshot" and data.get("status") == "responding":
                    item["responding_snapshot"] = True
            result["observed_streams"] = streams
            for event in events:
                cid, seq = event["payload"]["cycle_id"], event["seq"]
                ends = [f for f in frames if f["type"] == "keeper.stream.end"
                        and (f.get("data") or {}).get("event_seq") == seq]
                stream = ends[-1]["data"]["stream_id"] if ends else None
                selected = [f for f in frames if stream
                            and (f.get("data") or {}).get("stream_id") == stream]
                deltas = [f for f in selected if f["type"] == "keeper.stream.delta"]
                stream_dom = [entry for entry in dom if any(
                    s["stream"] == stream and int(s.get("index") or 0) > 0
                    and s["status"] == "responding" for s in entry["streams"]
                )]
                formal_dom = [entry for entry in dom if any(
                    str(e["seq"]) == str(seq) for e in entry["timeline"]
                )]
                last = dom[-1]
                bubbles = [e for e in last["timeline"] if str(e["seq"]) == str(seq)]
                restored = [f for f in selected if f["type"] == "keeper.stream.snapshot"
                            and f["data"].get("status") == "responding"]
                forced = [f for f in selected if f["type"] == "observer.forced_reconnect"]
                result["formal_answers"].append({
                    "cycle_id": cid, "seq": seq, "stream_id": stream,
                    "attempt": ends[-1]["data"].get("attempt") if ends else None,
                    "actual_delta_frames": len(deltas),
                    "delta_offsets": [f["data"]["offset"] for f in deltas],
                    "same_stream_reconnect_snapshot": bool(forced and restored and ends),
                    "successful_stream_first_dom_seconds": round(
                        stream_dom[0]["at"] / 1000 - capture["started_at"], 3,
                    ) if stream_dom else None,
                    "formal_first_dom_seconds": round(
                        formal_dom[0]["at"] / 1000 - capture["started_at"], 3,
                    ) if formal_dom else None,
                    "final_bubble_count": len(bubbles),
                    "final_matches_history": bool(
                        len(bubbles) == 1 and event["payload"]["text"] in bubbles[0]["text"],
                    ),
                    "remaining_draft_for_cycle": any(s["cycle"] == cid for s in last["streams"]),
                    "safe_fallback": event["payload"].get("safe_fallback"),
                    "answer_origin": event["payload"].get("answer_origin"),
                })
            case_summary["browsers"][name] = result
        summary["cases"][case] = case_summary
    return summary


if __name__ == "__main__":
    directory = Path(sys.argv[1]).resolve()
    result = summarize(directory)
    (directory / "browser-observer/browser-summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
