"""Read-only evidence audit; never call a model, mutate SQLite, or overwrite reports.

Usage: python scripts/audit_batch47.py REAL_DIRECTORY [--output NEW_DIRECTORY]
An optional --expected-target supplies a reviewed entity ID, not a guessed target.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

OUTPUTS = ("formal-result-consistency.json", "metrics-audit.json", "task-execution-audit.json")
RESULT_EVENTS = {"module.interaction", "combat.resolved", "scene.updated", "check.resolved"}


def read(directory, name, default=None):
    path = directory / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def text_paths(value, text, path="$"):
    """Find literal full bodies, also inside JSON-encoded transmitted messages."""
    if not text:
        return []
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(text_paths(child, text, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(text_paths(child, text, f"{path}[{index}]"))
    elif isinstance(value, str):
        if text in value:
            found.append({"path": path, "exact_value": value == text})
        if value.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                pass
            else:
                found.extend(text_paths(decoded, text, path + "<json>"))
    return found


def scope_ids(snapshot, root):
    selected = {root} if root else set()
    independent = {e["payload"].get("cycle_id") for e in snapshot.get("submission_events", [])}
    independent -= selected
    while True:
        related = {c["id"] for c in snapshot.get("cycles", []) if c["id"] not in independent
                   and any(c.get("state", {}).get(k) in selected
                           for k in ("parent_cycle_id", "related_player_cycle_id"))}
        if related <= selected:
            return selected
        selected |= related


def database_view(directory, room_id, cycle_ids):
    path = directory / "game.db"
    if not path.exists():
        return {"available": False}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        plans = {cid: json.loads(doc) for cid, doc in db.execute(
            "SELECT cycle_id,document FROM agent_action_plans WHERE room_id=?", (room_id,),
        ) if cid in cycle_ids}
        events = [{"seq": seq, "type": kind, "visibility": visibility,
                   "actor_member_id": actor, "payload": json.loads(payload)}
                  for seq, kind, visibility, actor, payload in db.execute(
                      "SELECT seq,type,visibility,actor_member_id,payload FROM room_events "
                      "WHERE room_id=? ORDER BY seq", (room_id,),
                  )]
        memories = [{"id": mid, "kind": kind, "scope": scope, "content": content,
                     "source_event_ids": json.loads(sources)}
                    for mid, kind, scope, content, sources in db.execute(
                        "SELECT id,kind,scope,content,source_event_ids FROM agent_memories "
                        "WHERE room_id=? ORDER BY created_at", (room_id,),
                    )]
    return {"available": True, "plans": plans, "events": events, "memories": memories}


def call_id(call):
    return tuple(call.get(k) for k in (
        "cycle_id", "request_id", "schema", "attempt", "request_started_at",
    ))


def call_metrics(calls):
    ordered = sorted(calls, key=lambda c: (c.get("request_started_at") or 0,
                                         c.get("attempt") or 0))
    return {
        "actual_calls": len(ordered),
        "known_usage": {key: sum((c.get("token_usage") or {}).get(key) or 0 for c in ordered)
                        for key in ("input", "output")},
        "calls_missing_usage": [list(call_id(c)) for c in ordered
                                if any((c.get("token_usage") or {}).get(k) is None
                                       for k in ("input", "output"))],
        "model_elapsed_ms_sum": sum(c.get("model_elapsed_ms") or 0 for c in ordered),
        "stages": [{k: c.get(k) for k in (
            "cycle_id", "schema", "attempt", "request_started_at", "first_chunk_at",
            "model_finished_at", "latency_ms", "queue_wait_ms", "model_elapsed_ms",
            "validation_ms", "token_usage", "answer_origin", "error_category",
        )} for c in ordered],
    }


def body_from_call(call):
    raw = call.get("raw_output") or call.get("generated_output") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return (raw.get("public_narration") or raw.get("observed_detail")) if isinstance(
        raw, dict,
    ) else None


def formal_consistency(main, task, members, db):
    final = main.get("final") or {}
    payload = final.get("payload", {})
    body, cid = payload.get("text"), main.get("cycle_id")
    plans = [p["document"] for p in main.get("validated_plans", []) if p["cycle_id"] == cid]
    documents = [p.get("narration", {}).get("public_narration") for p in plans]
    contexts = [run.get("context", {}) for run in task.get("runs", [])]
    messages = [call.get("transmitted_messages", []) for call in task.get("model_calls", [])]
    public_memories = [m for m in db.get("memories", []) if m["scope"] == "public"]
    rejected = []
    for call in main.get("model_calls", []):
        if call.get("schema") != "KeeperNarration" or call.get("cycle_id") != cid:
            continue
        raw = body_from_call(call)
        failed = bool(call.get("error_category") or call.get("validation_issues")
                      or (call.get("answer_coverage_audit") or {}).get("valid") is False)
        if raw and failed:
            rejected.append({
                "attempt": call.get("attempt"), "raw_body": raw,
                "same_as_official_body": raw == body,
                "context_paths": text_paths(contexts, raw),
                "message_paths": text_paths(messages, raw),
                "public_memory_paths": text_paths(public_memories, raw),
            })
    db_events = [e for e in db.get("events", []) if e["type"] == "keeper.narration"
                 and e["payload"].get("cycle_id") == cid]
    db_plan = db.get("plans", {}).get(cid)
    return {
        "cycle_id": cid, "official_event_seq": final.get("seq"), "official_body": body,
        "official_origin": payload.get("answer_origin"),
        "official_answer_complete": (main.get("generation") or {}).get("answer_complete"),
        "captured_adjudication_bodies": documents,
        "adjudication_matches_event": bool(body and documents) and all(
            v == body for v in documents),
        "database_available": db["available"],
        "database_official_events": db_events,
        "database_event_matches_capture": (len(db_events) == 1
                                           and db_events[0].get("seq") == final.get("seq")
                                           and db_events[0]["payload"].get("text") == body)
        if db["available"] else None,
        "database_adjudication_matches_event": db_plan.get("narration", {}).get(
            "public_narration") == body if db_plan else None,
        "members": [{"member": index + 1, "captured": bool(member),
                     "final_text": member.get("final_text"),
                     "final_bubbles": member.get("final_bubbles"),
                     "matches_official_once": bool(body) and member.get("final_text") == body
                     and member.get("final_bubbles") == 1} for index, member in enumerate(members)],
        "later_task_context_official_body_paths": text_paths(contexts, body),
        "later_task_transmitted_official_body_paths": text_paths(messages, body),
        "persistent_public_memory_official_body_paths": text_paths(public_memories, body),
        "rejected_full_bodies": rejected,
        "limits": (
            "Exact full-body presence only; shared verified facts or paraphrases require review. "
            "Failed diagnostic outputs are not public memory. Absence of a persistent whole-body "
            "memory is not a failure: the official event/recent-dialogue projection may carry it."
        ),
        "semantic_accuracy": "not inferred; review original requirements and selected sources",
    }


def metrics_audit(directory, main, task, acceptance, main_scope, task_scope):
    main_calls = [c for c in main.get("model_calls", []) if c.get("cycle_id") in main_scope]
    task_calls = [c for c in task.get("model_calls", []) if c.get("cycle_id") in task_scope]
    union = {call_id(c): c for c in [*main_calls, *task_calls]}
    frozen = acceptance.get("main_evidence_sha256", {})
    hashes = {name: {"expected": expected, "actual": digest(directory / name),
                     "unchanged": digest(directory / name) == expected}
              for name, expected in frozen.items()}
    browser = main.get("browser_result") or read(directory, "main-captured-result.json", {})
    return {
        "main": {"cycle_ids": sorted(main_scope), **call_metrics(main_calls)},
        "independent_task": {"cycle_ids": sorted(task_scope), **call_metrics(task_calls)},
        "batch_unique_calls": call_metrics(list(union.values())),
        "cross_case_cycle_overlap": sorted(main_scope & task_scope),
        "cross_case_call_overlap": [list(key) for key in
                                    {call_id(c) for c in main_calls} &
                                    {call_id(c) for c in task_calls}],
        "main_submitted_at": main.get("submitted_at"),
        "main_captured_at": main.get("captured_at"),
        "first_dom_seconds": browser.get("first_dom_seconds"),
        "first_public_dom_seconds": browser.get("first_public_dom_seconds"),
        "dom_metric_scope": "Whole main turn, including rejected prefixes; not final-attempt proof",
        "stream_assessment": read(directory, "stream-assessment.json", {}),
        "main_frozen_files": hashes,
        "main_frozen_hashes_unchanged": all(
            h["unchanged"] for h in hashes.values()) if hashes else None,
        "performance_claim": "Measurements only; no same-condition speedup comparison was made",
    }


def task_only_reports(directory, task, task_scope, db):
    """Audit this recheck without treating copied source-main files as a new run."""
    events = [e for e in task.get("events", []) if e["type"] == "keeper.narration"
              and e["payload"].get("cycle_id") in task_scope]
    plans = {p["cycle_id"]: p["document"] for p in task.get("validated_plans", [])
             if p["cycle_id"] in task_scope}
    members = [read(directory, f"task-member-{i}-stream.json", {}) for i in (1, 2)]
    cycles = []
    for cid in sorted(task_scope):
        formal = [e for e in events if e["payload"].get("cycle_id") == cid]
        body = formal[0]["payload"].get("text") if len(formal) == 1 else None
        document = plans.get(cid, {})
        narration = document.get("narration") or {}
        db_events = [e for e in db.get("events", []) if e["type"] == "keeper.narration"
                     and e["payload"].get("cycle_id") == cid]
        db_plan = db.get("plans", {}).get(cid)
        cycles.append({
            "cycle_id": cid, "official_events": formal,
            "captured_adjudication_body": narration.get("public_narration"),
            "adjudication_matches_event": bool(body) and narration.get("public_narration") == body,
            "database_official_events": db_events,
            "database_event_matches_capture": (bool(body) and len(db_events) == 1
                                                and db_events[0].get("seq") == formal[0]["seq"]
                                                and db_events[0]["payload"].get("text") == body)
            if db["available"] else None,
            "database_adjudication_matches_event": bool(body) and (db_plan.get(
                "narration") or {}).get("public_narration") == body if db_plan else None,
            "members": [{"member": index + 1, "captured": bool(member),
                         "final_records": [row for row in member.get("final_records", [])
                                           if len(formal) == 1
                                           and row.get("seq") == formal[0]["seq"]],
                         "matches_official_once": bool(body) and len([
                             row for row in member.get("final_records", [])
                             if row.get("seq") == formal[0]["seq"]
                         ]) == 1 and any(row.get("seq") == formal[0]["seq"]
                                         and row.get("text") == body and row.get("bubbles") == 1
                                         for row in member.get("final_records", []))}
                        for index, member in enumerate(members)],
        })
    common = {"scope": "task_only_recheck", "main_not_rerun": True,
              "new_main_evidence_collected": False, "source_main_included": False}
    formal = {
        **common, "database_available": db["available"], "cycles": cycles,
        "main_consistency": "not reassessed; original main evidence remains a separate run",
        "dom_consistency_scope": "This task and its descendants only; no new main DOM evidence",
        "semantic_accuracy": "not inferred; review original requirements and selected sources",
    }
    task_calls = [c for c in task.get("model_calls", []) if c.get("cycle_id") in task_scope]
    metrics = {
        **common, "independent_task": {"cycle_ids": sorted(task_scope),
                                       **call_metrics(task_calls)},
        "task_submitted_at": task.get("submitted_at"),
        "task_captured_at": task.get("captured_at"),
        "task_timing": read(directory, "task-timing.json", {}),
        "dom_metric_scope": "No new main turn or main DOM measurement was collected",
        "performance_claim": "Measurements only; no same-condition speedup comparison was made",
    }
    return formal, metrics


def task_audit(task, snapshot, expected_target=None):
    cid = task.get("cycle_id")
    scope = scope_ids(snapshot, cid)
    root_submission = (task.get("original_submission") or {}).get("response_event", {})
    request_seq = root_submission.get("seq")
    children = [c for c in snapshot.get("cycles", []) if c["id"] in scope - {cid}
                and c.get("state", {}).get("origin") == "teammate"]
    frozen_keys = {key for child in children for key in child["state"].get("request_keys", [])}
    requests, results = [], []
    for behavior in task.get("behavior", []):
        for ledger in ("pending_requests", "request_history"):
            for request in behavior.get(ledger, []):
                if ((request_seq is not None and request.get("source_event_seq") == request_seq)
                        or request.get("key") in frozen_keys):
                    requests.append({"member_id": behavior["member_id"], "ledger": ledger,
                                     "task_status": behavior.get("task_status"), **request})
        for kind in ("last_result", "last_attempt_result"):
            result = behavior.get(kind) or {}
            if result.get("cycle_id") in scope:
                results.append({"member_id": behavior["member_id"], "field": kind, **result})
    events = {e["seq"]: e for e in [*task.get("events", []), *task.get("ledger_events", [])]}
    plans = {p["cycle_id"]: p["document"] for p in task.get("validated_plans", [])}
    audits = []
    for child in children:
        child_id, state = child["id"], child["state"]
        doc = plans.get(child_id, {})
        plan = doc.get("plan", {})
        target = (plan.get("focus") or {}).get("action_target_id") or (
            plan.get("parsed_intent") or {}).get("target_id")
        target = target or state.get("teammate_attempt", {}).get("target_id")
        validation = doc.get("narration_validation") or {}
        text = (doc.get("narration") or {}).get("public_narration")
        formal = [e for e in events.values() if e["type"] == "keeper.narration"
                  and e["payload"].get("cycle_id") == child_id]
        validated = bool(text and validation.get("valid") is True
                         and validation.get("answer_complete") is not False
                         and len(formal) == 1 and formal[0]["payload"].get("text") == text
                         and not formal[0]["payload"].get("safe_fallback"))
        bound = state.get("request_operands", {})
        target_matches = {key: request.get("target_id") == target if request.get("target_id")
                          else None for key, request in bound.items()}
        facts = []
        seen = set()
        for result in results:
            for fact in result.get("result_facts", []):
                signature = json.dumps(fact, sort_keys=True, ensure_ascii=False)
                if fact.get("cycle_id") != child_id or signature in seen:
                    continue
                seen.add(signature)
                event = events.get(fact.get("source_event_seq"))
                same_cycle = bool(event and event["payload"].get("cycle_id") == child_id)
                current = bool(event and request_seq is not None and event["seq"] > request_seq)
                allowed_event = bool(event and (event["type"] in RESULT_EVENTS
                                     or event["type"] == "keeper.narration" and validated))
                same_actor = fact.get("actor_id") == state.get("triggering_member_id")
                same_target = (fact.get("action_target_id") or fact.get("target_id")) == target
                event_payload = event["payload"] if event else {}
                formal_observation = bool(event and event["type"] == "keeper.narration"
                                          and fact.get("operation") == "observe" and validated)
                event_success = formal_observation or bool(
                    event_payload.get("operation") == fact.get("operation")
                    and (event_payload.get("passed") is True
                         or event_payload.get("status") == "success"
                         or (event_payload.get("rolls", {}).get("treatment", {}).get(
                             "result") or {}).get("passed") is True))
                facts.append({"fact": fact, "source_event": event, "same_child_cycle": same_cycle,
                              "after_original_request": current,
                              "source_kind_allowed": allowed_event,
                              "source_confirms_operation_success": event_success,
                              "same_executor": same_actor, "same_target": same_target,
                              "current_success_receipt_supported": bool(
                                  fact.get("status") == "success"
                                  and fact.get("operation") != "check"
                                  and target and same_cycle and current and allowed_event
                                  and same_actor and same_target and event_success)})
        audits.append({
            "cycle_id": child_id, "status": child.get("status"), "executor": state.get(
                "triggering_member_id"), "request_keys": state.get("request_keys", []),
            "entered_execution": child.get("status") not in {
                None, "queued", "queued_action", "cancelled",
            },
            "frozen_request_operands": bound, "attempt": state.get("teammate_attempt"),
            "plan_target_id": target, "target_matches_frozen_request": target_matches,
            "matches_reviewed_expected_target": (
                target == expected_target if expected_target else None),
            "formal_events": formal, "narration_validation": validation,
            "has_current_validated_formal_body": validated, "result_facts": facts,
            "safe_error": state.get("safe_error"),
        })
    receipts = []
    for receipt in task.get("case_tool_receipts", []):
        result = receipt.get("result") or {}
        data = result.get("data") or {}
        seq = data.get("event_seq")
        receipts.append({**receipt, "already_revealed": data.get("already_revealed"),
                         "source_precedes_original_request": seq <= request_seq
                         if isinstance(seq, int) and isinstance(request_seq, int) else None,
                         "counts_as_current_success_by_itself": False})
    return {
        "root_cycle_id": cid, "case_cycle_ids": sorted(scope), "request_event_seq": request_seq,
        "original_request": task.get("request") or (
            task.get("original_submission") or {}).get("body"),
        "reviewed_expected_target_id": expected_target, "child_created": bool(children),
        "entered_actual_child": any(a["entered_execution"] for a in audits),
        "current_requests": requests, "current_result_records": results, "children": audits,
        "tool_receipts": receipts,
        "budget_or_generation_failures": [e for e in events.values() if e["type"] in {
            "memory.context_blocked", "agent.teammate_generation_failed", "agent.failed",
        }],
        "child_has_supported_current_success_receipt": any(
            f["current_success_receipt_supported"] for a in audits for f in a["result_facts"]),
        "semantic_accuracy": "not inferred; review target, observation content and ledger status",
        "rule": ("Proposals, old reveals and failed fallback bodies are never "
                 "current success receipts"),
    }


def audit(directory, output, expected_target=None):
    directory, output = directory.resolve(), output.resolve()
    # Check every filename first; a previous successful report is never replaced.
    if any((output / name).exists() for name in OUTPUTS):
        raise FileExistsError("Audit output exists; choose a fresh --output directory")
    acceptance = read(directory, "acceptance.json", {})
    task_only = (acceptance.get("task_only_recheck") is True or any(
        acceptance.get(key) == "task_only_recheck" for key in ("mode", "scope")))
    main = {} if task_only else read(directory, "main-case.json", {})
    task = read(directory, "task-case.json", {})
    if task_only and not task:
        raise ValueError("task-case.json is required for task_only_recheck")
    if not task_only and (not main or not task):
        raise ValueError("Both main-case.json and task-case.json are required")
    task_snapshot = read(directory, "task-settlement.json", {})
    task_scope = scope_ids(task_snapshot, task["cycle_id"])
    if task_only:
        db = database_view(directory, acceptance.get("room_id"), task_scope)
        formal, metrics = task_only_reports(directory, task, task_scope, db)
    else:
        main_snapshot = read(directory, "main-settlement.json", {})
        main_scope = scope_ids(main_snapshot, main["cycle_id"])
        members = [read(directory, f"member-{i}-stream.json", {}) for i in (1, 2)]
        db = database_view(directory, acceptance.get("room_id"), main_scope | task_scope)
        formal = formal_consistency(main, task, members, db)
        metrics = metrics_audit(directory, main, task, acceptance, main_scope, task_scope)
    reports = [formal, metrics, task_audit(task, task_snapshot, expected_target)]
    inputs = {p.name: digest(p) for p in directory.glob("*.json") if p.name not in OUTPUTS}
    output.mkdir(parents=True, exist_ok=True)
    for name, report in zip(OUTPUTS, reports):
        report["audit_provenance"] = {"input_directory": str(directory), "input_sha256": inputs,
                                      "script_sha256": digest(Path(__file__))}
        with (output / name).open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return [str(output / name) for name in OUTPUTS]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-target")
    args = parser.parse_args()
    paths = audit(args.directory, args.output or args.directory, args.expected_target)
    print(json.dumps({"created": paths}, ensure_ascii=False))


if __name__ == "__main__":
    main()
