"""Batch 46: reuse the passed real-05 preparation and preserve independent cases.

Only normal local HTTP/model operations are used after a read-only SQLite backup.
The unchanged main question is frozen before the independent window delegation.
An actually executed operation is replayed with its original HTTP request body;
no extra action is submitted merely to obtain a convenient nonzero result.
"""

import argparse
import copy
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from uuid import uuid4

import validate_batch43 as streaming
import validate_batch45 as fixed
from resume_batch45 import ResumeCheck
from validate_batch43 import read, write

OUTPUT = fixed.ROOT / "data/prepared/batch-46"
SOURCE_ROOT = fixed.OUTPUT
streaming.OUTPUT = OUTPUT
fixed.BACKEND_PORT = streaming.BACKEND_PORT = 8146
fixed.FRONTEND_PORT = streaming.FRONTEND_PORT = 5246
BASE_STREAMING_CHECKS = fixed.streaming_checks

# Only this acceptance driver's observer changes. Each real attempt gets one
# generation-time reconnect, so a rejected native attempt cannot stand in for
# the repaired final attempt. The product frontend is unchanged.
OBSERVER = r"""(() => {
  window.streamAudit = {
    armed:false, reconnect:false, observations:[], frames:[], disconnections:[]
  };
  const seen = new Set(), disconnected = new Set();
  const audit = () => {
    const state = window.streamAudit;
    if (!state.armed) return;
    for (const node of document.querySelectorAll('[data-testid="keeper-stream"]')) {
      const text = node.querySelector('p.preserve-lines')?.textContent || '';
      const identity = {
        cycle_id:node.dataset.cycleId, stream_id:node.dataset.streamId,
        attempt:Number(node.dataset.streamAttempt)
      };
      const attemptKey = JSON.stringify(identity);
      const key = attemptKey + ':' + node.dataset.streamIndex + ':' + text;
      if (!seen.has(key)) {
        seen.add(key);
        state.observations.push({at:Date.now()/1000, ...identity,
          index:Number(node.dataset.streamIndex), text, status:node.dataset.streamStatus});
      }
      const socket = window.roomSockets.at(-1);
      if (text && node.dataset.streamStatus === 'responding' && state.reconnect &&
          !disconnected.has(attemptKey) && socket && socket.readyState === 1) {
        disconnected.add(attemptKey);
        const at = Date.now()/1000;
        state.disconnections.push({at, ...identity, visible_text:text});
        state.disconnected_at = at;
        socket.close();
      }
    }
  };
  new MutationObserver(audit).observe(document.body, {
    subtree:true, childList:true, characterData:true, attributes:true,
    attributeFilter:['data-stream-index', 'data-stream-status', 'data-stream-attempt',
      'data-stream-id', 'data-cycle-id']
  });
  const observeSocket = socket => {
    socket.addEventListener('message', event => {
      const frame = JSON.parse(event.data);
      if (window.streamAudit.armed &&
          (frame.type.startsWith('keeper.stream.') || frame.type === 'room.synced')) {
        window.streamAudit.frames.push({at:Date.now()/1000, ...frame});
      }
    });
  };
  for (const socket of window.roomSockets || []) observeSocket(socket);
  const OriginalSocket = window.WebSocket;
  window.WebSocket = class extends OriginalSocket {
    constructor(...args) {
      super(...args);
      if (String(args[0]).includes('/ws/rooms/')) observeSocket(this);
    }
  };
})()"""


def streaming_checks(audits, narration, final, cycle_id):
    selected = copy.deepcopy(audits)
    payload = final["payload"]
    matching = [
        row
        for row in selected[1].get("disconnections", [])
        if row.get("cycle_id") == cycle_id
        and row.get("stream_id") == payload.get("stream_id")
        and row.get("attempt") == payload.get("stream_attempt")
    ]
    disconnect = min(matching, key=lambda row: row["at"]) if matching else None
    selected[1]["disconnected_at"] = disconnect["at"] if disconnect else float("inf")
    result = BASE_STREAMING_CHECKS(selected, narration, final, cycle_id)
    return {
        **result,
        "matched_disconnect": disconnect,
        "all_disconnections": audits[1].get("disconnections", []),
    }


# Narration, advice, action.result summaries and an attempted-but-refused operation
# alone are never counted as a nonzero settlement.
EFFECT_EVENTS = {
    "module.interaction",
    "check.resolved",
    "scene.updated",
    "entity.revealed",
    "clue.revealed",
    "item.transferred",
    "item.used",
    "time.advanced",
    "character.resource_changed",
}


def rows(db, query, parameters=()):
    cursor = db.execute(query, parameters)
    names = [column[0] for column in cursor.description]
    result = []
    for values in cursor:
        row = dict(zip(names, values))
        for key in (
            "document",
            "context",
            "structured_output",
            "tool_results",
            "state",
            "result",
            "session_state",
            "payload",
            "source_event_ids",
            "token_usage",
        ):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except json.JSONDecodeError:
                    pass
        result.append(row)
    return result


def ordered_calls(calls):
    return sorted(
        calls,
        key=lambda call: (
            call.get("request_started_at") or 0,
            call.get("attempt") or 0,
            call.get("schema") or "",
        ),
    )


def generation_assessment(calls, final, validation=None):
    """Report provenance, not answer accuracy inferred from a successful schema."""
    narration = [call for call in ordered_calls(calls) if call.get("schema") == "KeeperNarration"]
    validation = validation or {}
    payload = (final or {}).get("payload", {})
    origin = validation.get("answer_origin") or payload.get("answer_origin")
    fallback = bool(payload.get("safe_fallback") or origin == "server_fallback")
    attempts = [
        {
            key: call.get(key)
            for key in (
                "attempt",
                "raw_output",
                "generated_output",
                "error_category",
                "validation_issues",
                "request_started_at",
                "model_finished_at",
                "latency_ms",
                "validation_ms",
                "token_usage",
                "answer_origin",
                "answer_coverage_audit",
            )
        }
        for call in narration
    ]
    return {
        "outcome": (
            "server_fallback"
            if fallback
            else "missing_final"
            if not final
            else "repaired_model_answer"
            if origin == "repaired" or len(narration) > 1
            else "native_model_answer"
        ),
        "first_output": attempts[0] if attempts else None,
        "attempts": attempts,
        "final_payload": (final or {}).get("payload"),
        "answer_origin": origin,
        "answer_complete": validation.get("answer_complete"),
        "answer_coverage": validation.get("answer_coverage"),
        "server_validation": validation,
        "semantic_accuracy": "requires body-to-selected-source review",
    }


def call_metrics(calls):
    calls = ordered_calls(calls)
    return {
        "actual_call_count": len(calls),
        "actual_usage": {
            key: sum((call.get("token_usage") or {}).get(key, 0) or 0 for call in calls)
            for key in ("input", "output")
        },
        "stages": [
            {
                key: call.get(key)
                for key in (
                    "schema",
                    "attempt",
                    "cycle_id",
                    "latency_ms",
                    "queue_wait_ms",
                    "model_elapsed_ms",
                    "validation_ms",
                    "request_started_at",
                    "first_chunk_at",
                    "model_finished_at",
                    "token_usage",
                )
            }
            for call in calls
        ],
        "performance_claim": "measurement only; no cross-run speedup claim",
    }


def case_cycle_ids(snapshot, cycle_id):
    """Follow persisted parent links without borrowing another player submission."""
    selected = {cycle_id} if cycle_id else set()
    independent = {
        event["payload"].get("cycle_id") for event in snapshot["submission_events"]
    } - selected
    changed = True
    while changed:
        changed = False
        for cycle in snapshot.get("cycles", []):
            if cycle["id"] in selected or cycle["id"] in independent:
                continue
            state = cycle["state"]
            if any(
                state.get(key) in selected
                for key in (
                    "related_player_cycle_id",
                    "parent_cycle_id",
                )
            ):
                selected.add(cycle["id"])
                changed = True
    return selected


def nonzero_evidence(snapshot, cycle_id):
    scope = case_cycle_ids(snapshot, cycle_id)
    submitted_seq = next(
        (
            event["seq"]
            for event in snapshot["submission_events"]
            if event["payload"].get("cycle_id") == cycle_id
        ),
        None,
    )
    if submitted_seq is None:
        return []
    evidence = [
        event
        for event in snapshot["effect_events"]
        if event["payload"].get("cycle_id") in scope and event["seq"] > submitted_seq
    ]
    # Reveal tools may store the owning cycle on the run, while the event itself
    # uses entity provenance. Require both a successful durable tool receipt and
    # the corresponding new effect event; old visible clues do not qualify.
    events_by_seq = {event["seq"]: event for event in snapshot["effect_events"]}
    for receipt in snapshot["tool_receipts"]:
        if receipt["cycle_id"] not in scope:
            continue
        result = receipt["result"] or {}
        data = result.get("data") or {}
        event = events_by_seq.get(data.get("event_seq"))
        if result.get("ok") and event and event["seq"] > submitted_seq and event not in evidence:
            evidence.append(event)
    return evidence


class Batch46Check(ResumeCheck):
    def __init__(self, name, source):
        super().__init__(name, source)
        self.submissions = {}
        self.case_results = {}
        self.frozen_main = {}
        case = read(self.directory / "fixed-case.json")
        case.update(
            batch=46,
            setup_actions_executed=[],
            history_padding_executed=False,
            compression_preparation_executed=False,
            source_preflight=str(source / "preflight.json"),
            case_order=["main", "independent_window_delegation", "same_request_replay"],
            local_ports={"backend": fixed.BACKEND_PORT, "frontend": fixed.FRONTEND_PORT},
        )
        write(self.directory / "fixed-case.json", case)

    def start(self, command, cwd, name):
        if name == "backend":
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--serve",
                str(self.directory),
                "--backend-port",
                str(fixed.BACKEND_PORT),
                "--frontend-port",
                str(fixed.FRONTEND_PORT),
            ]
        return super().start(command, cwd, name)

    def run(self):
        previous_observer, previous_checks = fixed.OBSERVER, fixed.streaming_checks
        fixed.OBSERVER, fixed.streaming_checks = OBSERVER, streaming_checks
        try:
            return super().run()
        finally:
            fixed.OBSERVER, fixed.streaming_checks = previous_observer, previous_checks

    def model_calls(self):
        # UUID row ordering is unrelated to native/repair chronology.
        return ordered_calls(super().model_calls())

    def act(self, text, *, cycle_field=None):
        body = {"text": text, "client_request_id": str(uuid4())}
        started = time.time()
        response = self.request("POST", self.prefix + "/actions", body, actor="player")
        cycle_id = response["event"]["payload"]["cycle_id"]
        if cycle_field:
            self.result[cycle_field] = cycle_id
        self.submissions[cycle_id] = {
            "method": "POST",
            "path": self.prefix + "/actions",
            "actor": "player",
            "body": body,
            "response_event": response["event"],
            "submitted_at": started,
        }
        write(self.directory / "original-submissions.json", self.submissions)
        self.settle()
        return response

    def settle(self, timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = super().settle(timeout=max(1, deadline - time.monotonic()))
            if not self.submissions:
                return status
            root = next(reversed(self.submissions))
            snapshot = self.settlement_snapshot()
            scope = case_cycle_ids(snapshot, root)
            owned = [cycle for cycle in snapshot["cycles"] if cycle["id"] in scope]
            failed = [
                cycle
                for cycle in owned
                if cycle["status"]
                in {
                    "failed",
                    "waiting_for_review",
                }
            ]
            assert not failed, failed
            if not any(
                cycle["status"]
                in {
                    "running",
                    "queued",
                    "queued_action",
                    "waiting_for_roll",
                    "suspended",
                }
                for cycle in owned
            ):
                return status
            time.sleep(0.1)
        raise TimeoutError("The original request or a related child cycle did not settle")

    def settlement_snapshot(self):
        with sqlite3.connect(
            f"file:{(self.directory / 'game.db').as_posix()}?mode=ro", uri=True
        ) as db:
            room_id = self.result["room_id"]
            return {
                "session": rows(db, "SELECT session_state FROM game_rooms WHERE id=?", (room_id,)),
                "module": rows(
                    db,
                    "SELECT id,state FROM module_snapshots WHERE room_id=? ORDER BY id",
                    (room_id,),
                ),
                "navigation": rows(
                    db,
                    "SELECT document FROM room_module_navigation_states WHERE room_id=?",
                    (room_id,),
                ),
                "tool_receipts": rows(
                    db,
                    "SELECT t.id,t.run_id,t.result,r.cycle_id FROM agent_tool_receipts t "
                    "JOIN agent_runs r ON t.run_id=r.id WHERE t.room_id=? ORDER BY t.id",
                    (room_id,),
                ),
                "checks": rows(
                    db,
                    "SELECT id,cycle_id,status,document FROM pending_checks "
                    "WHERE room_id=? ORDER BY id",
                    (room_id,),
                ),
                "behavior": rows(
                    db,
                    "SELECT member_id,document FROM agent_behavior_states "
                    "WHERE room_id=? ORDER BY member_id",
                    (room_id,),
                ),
                "cycles": rows(
                    db,
                    "SELECT id,status,state FROM agent_cycles WHERE room_id=? ORDER BY id",
                    (room_id,),
                ),
                "model_call_ids": rows(
                    db,
                    "SELECT c.id,r.cycle_id FROM agent_model_calls c "
                    "JOIN agent_runs r ON c.run_id=r.id "
                    "WHERE r.room_id=? ORDER BY c.id",
                    (room_id,),
                ),
                "effect_events": [
                    event
                    for event in rows(
                        db,
                        "SELECT seq,type,actor_member_id,payload,client_request_id "
                        "FROM room_events "
                        "WHERE room_id=? ORDER BY seq",
                        (room_id,),
                    )
                    if event["type"] in EFFECT_EVENTS
                ],
                "submission_events": rows(
                    db,
                    "SELECT seq,payload,client_request_id FROM room_events "
                    "WHERE room_id=? AND type='action.submitted' ORDER BY seq",
                    (room_id,),
                ),
            }

    def capture_case(self, name, cycle_id, start, error=None):
        public = self.request("GET", self.prefix + "/logs", actor="player")
        snapshot = self.settlement_snapshot()
        scope = case_cycle_ids(snapshot, cycle_id)
        calls = [call for call in self.model_calls() if call.get("cycle_id") in scope]
        root_calls = [call for call in calls if call.get("cycle_id") == cycle_id]
        final = self.persisted(cycle_id) if cycle_id else None
        parameters = tuple(sorted(scope))
        placeholders = ",".join("?" for _ in parameters) or "NULL"
        with sqlite3.connect(
            f"file:{(self.directory / 'game.db').as_posix()}?mode=ro", uri=True
        ) as db:
            runs = rows(
                db,
                f"SELECT * FROM agent_runs WHERE cycle_id IN ({placeholders}) ORDER BY created_at",
                parameters,
            )
            plans = rows(
                db,
                "SELECT cycle_id,document FROM agent_action_plans "
                f"WHERE cycle_id IN ({placeholders})",
                parameters,
            )
            memories = rows(
                db,
                "SELECT id,kind,content,source_event_ids FROM agent_memories "
                "WHERE room_id=? ORDER BY created_at",
                (self.result["room_id"],),
            )
            validation_events = rows(
                db,
                "SELECT seq,payload FROM room_events "
                "WHERE room_id=? AND type='agent.narration_validated' ORDER BY seq",
                (self.result["room_id"],),
            )
            ledger_events = rows(
                db,
                "SELECT seq,type,actor_member_id,visibility,payload FROM room_events "
                "WHERE room_id=? ORDER BY seq",
                (self.result["room_id"],),
            )
        validation_events = [
            event for event in validation_events if event["payload"].get("cycle_id") in scope
        ]
        root_validations = [
            event for event in validation_events if event["payload"].get("cycle_id") == cycle_id
        ]
        validation = root_validations[-1]["payload"] if root_validations else {}
        if not validation:
            validation = next(
                (
                    plan["document"].get("narration_validation", {})
                    for plan in plans
                    if plan["cycle_id"] == cycle_id and plan["document"].get("narration_validation")
                ),
                {},
            )
        effects = nonzero_evidence(snapshot, cycle_id)
        effect_seqs = {event["seq"] for event in effects}
        capture = {
            "case": name,
            "cycle_id": cycle_id,
            "case_cycle_ids": sorted(scope),
            "case_cycles": [cycle for cycle in snapshot["cycles"] if cycle["id"] in scope],
            "submitted_at": start,
            "captured_at": time.time(),
            "status": "failed" if error else "captured",
            "error": str(error) if error else None,
            "original_submission": self.submissions.get(cycle_id),
            "final": final,
            "generation": generation_assessment(root_calls, final, validation),
            "descendant_narrations": [
                narration
                for child in sorted(scope - {cycle_id})
                if (narration := self.persisted(child)) is not None
            ],
            "narration_validation_events": validation_events,
            "selected_answer_sources": [
                {
                    key: run["context"].get(key)
                    for key in (
                        "response_brief",
                        "memory_evidence",
                        "fact_evidence",
                        "result_facts",
                    )
                }
                for run in runs
                if run["graph_node"] == "generate_keeper_narration" and run["cycle_id"] == cycle_id
            ],
            "model_calls": calls,
            "metrics": call_metrics(calls),
            "runs": runs,
            "validated_plans": plans,
            "events": [
                event
                for event in public
                if event["payload"].get("cycle_id") in scope or event["seq"] in effect_seqs
            ],
            "ledger_events": [
                event
                for event in ledger_events
                if event["payload"].get("cycle_id") in scope or event["seq"] in effect_seqs
            ],
            "behavior": self.request("GET", self.prefix + "/teammate-behavior"),
            "case_tool_receipts": [
                receipt for receipt in snapshot["tool_receipts"] if receipt["cycle_id"] in scope
            ],
            "nonzero_effects": effects,
            "semantic_assessment": "pending source/target/receipt review; no automatic success",
        }
        if name == "main":
            capture["browser_result"] = copy.deepcopy(self.result)
            capture["browser_result"].get("checks", {}).pop("multiple_body_sentences", None)
        write(self.directory / (name + "-case.json"), capture)
        write(self.directory / (name + "-settlement.json"), snapshot)
        write(self.directory / (name + "-memories.json"), memories)
        self.case_results[name] = capture
        return capture

    def task_case(self):
        # The inherited main driver has already captured both windows and latency.
        # Freeze all main evidence before a new request, including its database view.
        self.capture_case("main", self.result["cycle_id"], self.result["action_requested_at"])
        for name in ("main-captured-result.json", "main-case.json", "main-settlement.json"):
            self.frozen_main[name] = hashlib.sha256(
                (self.directory / name).read_bytes()
            ).hexdigest()
        main_result = copy.deepcopy(self.result)
        start, error = time.time(), None
        try:
            self.act(fixed.TASK_ACTION, cycle_field="task_cycle_id")
        except Exception as caught:
            error = caught
        task_cycle = self.result.get("task_cycle_id")
        task = self.capture_case("task", task_cycle, start, error)
        task["request"] = fixed.TASK_ACTION
        write(self.directory / "task-case.json", task)
        # A failed task may leave an active cycle; replay must never silently cancel
        # or replace it. Record the failed state and mark the replay inapplicable.
        if error:
            replay = {
                "status": "not_run",
                "reason": "independent task did not settle",
                "error": str(error),
            }
        else:
            replay = self.replay_nonzero()
        write(self.directory / "idempotency.json", replay)
        assert all(
            hashlib.sha256((self.directory / name).read_bytes()).hexdigest() == digest
            for name, digest in self.frozen_main.items()
        ), "An independent task or replay modified frozen main evidence"
        batch_scope = set().union(
            *[set(case.get("case_cycle_ids", [])) for case in self.case_results.values()]
        )
        batch_calls = [call for call in self.model_calls() if call.get("cycle_id") in batch_scope]
        self.result = {
            **main_result,
            "status": "captured_requires_semantic_review",
            "main_case": "main-case.json",
            "task_case": "task-case.json",
            "task_status": task["status"],
            "task_cycle_id": task_cycle,
            "idempotency_status": replay["status"],
            "main_evidence_sha256": self.frozen_main,
            "main_preserved_after_task": True,
            "batch_cycle_ids": sorted(batch_scope),
            "batch_metrics": call_metrics(batch_calls),
        }
        write(
            self.directory / "batch-model-calls.json",
            batch_calls,
        )

    def replay_nonzero(self):
        before = self.settlement_snapshot()
        selected = next(
            (
                cycle_id
                for cycle_id in reversed(self.submissions)
                if nonzero_evidence(before, cycle_id)
            ),
            None,
        )
        if not selected:
            return {
                "status": "not_proven",
                "reason": "Neither original case produced a nonzero operation",
            }
        original = self.submissions[selected]
        result = {
            "status": "failed",
            "cycle_id": selected,
            "original_request": original,
            "nonzero_evidence": nonzero_evidence(before, selected),
            "pause_resume_scope": (
                "settled original operation; ordinary room pause/resume, then identical request"
            ),
            "replays": [],
        }
        write(self.directory / "idempotency-before.json", before)
        for label in ("normal_same_request", "same_request_after_pause_resume"):
            try:
                if label == "same_request_after_pause_resume":
                    self.request("POST", self.prefix + "/pause")
                    self.request("POST", self.prefix + "/resume")
                response = self.request(
                    original["method"],
                    original["path"],
                    copy.deepcopy(original["body"]),
                    actor=original["actor"],
                )
                self.settle()
                after = self.settlement_snapshot()
                checks = {key: before[key] == after[key] for key in before}
                checks["original_event_returned"] = response["event"] == original["response_event"]
                entry = {"path": label, "response_event": response["event"], "checks": checks}
                result["replays"].append(entry)
                write(self.directory / ("idempotency-" + label + ".json"), after)
                if not all(checks.values()):
                    break
            except Exception as error:
                result["replays"].append({"path": label, "error": str(error)})
                break
        if len(result["replays"]) == 2 and all(
            row.get("checks") and all(row["checks"].values()) for row in result["replays"]
        ):
            result["status"] = "passed"
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name")
    parser.add_argument("--source", default="real-05")
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--backend-port", type=int, default=8146)
    parser.add_argument("--frontend-port", type=int, default=5246)
    args = parser.parse_args()
    fixed.BACKEND_PORT = streaming.BACKEND_PORT = args.backend_port
    fixed.FRONTEND_PORT = streaming.FRONTEND_PORT = args.frontend_port
    if args.serve:
        fixed.serve(args.serve)
        return
    if not args.name:
        parser.error("--name is required")
    check = Batch46Check(args.name, SOURCE_ROOT / args.source)
    try:
        check.run()
    except Exception as error:
        check.result.update(status="failed", error=str(error))
        try:
            check.capture_failure()
            if check.result.get("cycle_id") and "main" not in check.case_results:
                check.capture_case(
                    "main", check.result["cycle_id"], check.result["action_requested_at"], error
                )
        except Exception as capture_error:
            check.result["capture_error"] = str(capture_error)
        raise
    finally:
        check.close()


if __name__ == "__main__":
    main()
