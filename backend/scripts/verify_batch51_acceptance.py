"""Verify a captured batch51 run without serving, submitting or calling a model."""

import argparse
import json
from pathlib import Path

from check_character_creation import ROOT

from app.memory.facts import fact_records


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(directory):
    case = read(directory / "delegate-case.json")
    metrics = read(directory / "batch51-metrics.json")
    browser = read(directory / "browser-observer/batch51-browser-summary.json")
    integrity = read(directory / "batch51-source-integrity.json")
    protection = read(directory / "batch51-frozen-evidence-protection.json")
    audit = case["audit"]
    ids = set(metrics["cycle_ids"])
    events = [e for e in audit["room_events"] if e["payload"].get("cycle_id") in ids]
    submissions = [e for e in events if e["type"] == "action.submitted"
                   and e["payload"].get("text") == case["request"]]
    assert len(submissions) == 1
    formal = [e for e in events if e["type"] == "keeper.narration"]
    assert len(formal) == 1
    report = formal[0]
    payload = report["payload"]
    assert payload["answer_origin"] == "mixed" and not payload["safe_fallback"]
    validation = next(e["payload"] for e in events
                      if e["type"] == "agent.narration_validated"
                      and e["payload"]["cycle_id"] == payload["cycle_id"])
    assert validation["valid"] and validation["answer_complete"]
    assert validation["answer_contract_version"] == "kp-answer-parts-v2-mixed"
    reveals = [e for e in events if e["type"] == "entity.revealed"]
    searches = [e for e in events if e["type"] == "module.interaction"
                and "search" in e["payload"].get("operations", [])]
    assert len(reveals) == len(searches) == 1
    assert reveals[0]["payload"]["public_summary"] in payload["text"]
    unknown = validation["server_parts"]
    assert unknown and all(p["text"] in payload["text"] for p in unknown)
    behavior = next(b["document"] for b in audit["agent_behavior_states"]
                    if b["document"].get("task_cycle_id") == payload["cycle_id"])
    request = next(r for r in behavior["request_history"] if r["key"] == "25:7")
    assert request["status"] == "completed"
    assert request["result_cycle_id"] == payload["cycle_id"]
    assert not any(r["key"] == "25:7" for r in behavior["pending_requests"])
    properties = behavior["last_result"]["unknown_properties"]
    assert properties and all(p["status"] == "unknown" for p in properties)
    facts = fact_records([e for e in audit["room_events"] if e["visibility"] == "public"])
    assert not any(f["source_event_seq"] == report["seq"] for f in facts)
    memories = read(directory / "memory-and-receipts.json")["agent_memories"]
    assert all(report["seq"] not in r["source_event_ids"] for r in memories)
    assert all("夹层" not in json.dumps(r["content"], ensure_ascii=False) for r in memories)
    assert browser["same_attempt_formal_passed_in_both_windows"]
    assert integrity["all_protected_hashes_match"] and protection["all_match"]
    assert all(r["equal"] for r in integrity["retained"].values())
    assert all(r["full_snapshot_equal"] for r in integrity["copied_cards"])
    return {
        "room_id": report["room_id"], "cycle_id": payload["cycle_id"],
        "stream_id": payload["stream_id"], "attempt": payload["stream_attempt"],
        "formal_event_seq": report["seq"], "single_original_submission": True,
        "reveal_event_seqs": [e["seq"] for e in reveals],
        "search_event_seqs": [e["seq"] for e in searches],
        "formal_text": payload["text"], "server_parts": unknown,
        "settled_request": request, "unknown_properties": properties,
        "formal_not_a_fact_or_memory_source": True,
        "persisted_memories_contain_no_layer_claim": True,
        "both_windows_exact_formal": True, "protected_evidence_and_cards_unchanged": True,
        "actual_model_calls": metrics["model_call_count"], "usage": metrics["usage"],
        "generation_visibility": browser["same_attempt_generation_visible_in_both_windows"],
        "limitations": "Visibility and reconnect findings are in the same-run browser summary; "
        "offline recovery and two-model-part tests are separate evidence.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory.resolve()
    assert directory.is_relative_to((ROOT / "data/prepared/batch-51").resolve())
    proof = verify(directory)
    (directory / "batch51-acceptance.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps({"formal_complete": True, "task_settled_property_unknown": True,
                      "actual_model_calls": proof["actual_model_calls"],
                      "generation_visibility": proof["generation_visibility"]}))
