"""Recheck only the fixed window delegation from a retained batch-46/47 run.

The source's main/task history and actual observations remain in the read-only
SQLite backup. This is a new-history task recheck, never a combined main pass.
"""

import argparse
import base64
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import validate_batch47 as batch

previous = batch.previous
fixed, streaming = previous.fixed, previous.streaming
OUTPUT = fixed.ROOT / "data/prepared/batch-47"


def backup_sqlite(source, destination):
    """Include committed WAL pages without writing to the original database."""
    assert not destination.exists(), "Backup destination must be a new file"
    before = {str(path): batch.digest(path) for path in (source, Path(str(source) + "-wal"))}
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as old:
        with sqlite3.connect(destination) as new:
            old.backup(new)
    after = {path: batch.digest(Path(path)) for path in before}
    assert before == after, "Read-only backup changed a source database or WAL"
    return {"source_sha256": before, "source_unchanged": True,
            "method": "SQLite mode=ro backup including WAL contents"}


def first_dom(audit, capture, started):
    scope = set(capture["case_cycle_ids"])
    seqs = {event["seq"] for event in capture["events"]
            if event["type"] in {"keeper.narration", "npc.spoke", "agent.spoke"}}
    streamed = [row["at"] for row in audit.get("observations", [])
                if row.get("cycle_id") in scope and row.get("text") and row["at"] >= started]
    formal = [row["at"] for row in audit.get("formal_observations", [])
              if row["seq"] in seqs and row["at"] >= started]
    return {"first_dom_seconds": min(streamed) - started if streamed else None,
            "first_public_dom_seconds": min(streamed + formal) - started
            if streamed or formal else None}


class TaskRecheck(batch.Batch47Check):
    def __init__(self, name, source):
        source = source.resolve()
        allowed = [fixed.ROOT / "data/prepared" / name for name in ("batch-46", "batch-47")]
        assert any(source.is_relative_to(root.resolve()) and source != root.resolve()
                   for root in allowed), "Source must be an isolated batch-46 or batch-47 run"
        original = previous.read(source / "acceptance.json")
        assert original["preparation_status"] == "passed"
        assert original["question"] == fixed.FIXED_ACTION
        original_task = previous.read(source / "task-case.json")
        assert original_task.get("request") == fixed.TASK_ACTION
        with sqlite3.connect(f"file:{(source / 'game.db').as_posix()}?mode=ro", uri=True) as db:
            assert not db.execute(
                "SELECT 1 FROM agent_cycles WHERE status IN "
                "('running','queued','queued_action','suspended','waiting_for_roll',"
                "'waiting_for_review') LIMIT 1"
            ).fetchone(), "The source must have no executing or waiting cycle"
            assert not db.execute(
                "SELECT 1 FROM room_events WHERE type LIKE 'handout.%' LIMIT 1"
            ).fetchone(), "Normal member replacement requires no private handout history"
        self.source, self.original = source, original
        self.protected_before = batch.protected_files(source)
        self.retained_before = batch.retained_documents(source)
        self.source_main = {path.name: batch.digest(path) for path in source.glob("main-*.json")}
        assert {"main-case.json", "main-settlement.json", "main-captured-result.json"} <= set(
            self.source_main
        )
        self.protected_before.update({str(source / name): digest
                                      for name, digest in self.source_main.items()})
        # The inherited ResumeCheck constructor requires the old pre-main failed
        # real-05 fixture. Initialize only its unchanged directory/process base.
        fixed.FixedCheck.__init__(self, name)
        self.submissions, self.case_results, self.frozen_main = {}, {}, {}
        provenance = {"source_directory": str(source), "task_only_recheck": True,
                      "databases": {}}
        for filename in ("game.db", "checkpoint.db", "knowledge.db"):
            provenance["databases"][filename] = backup_sqlite(
                source / filename, self.directory / filename,
            )
        if (source / "model-settings.json").exists():
            shutil.copy2(source / "model-settings.json", self.directory / "model-settings.json")
        frozen = self.directory / "source-main"
        frozen.mkdir()
        for filename in self.source_main:
            shutil.copy2(source / filename, frozen / filename)
        self.history_difference = {
            "source_directory": str(source), "source_main_cycle_id": original.get("cycle_id"),
            "prior_task_cycle_ids": original_task.get("case_cycle_ids", []),
            "prior_task_request": original_task["request"],
            "prior_task_observations": original_task.get("descendant_narrations", []),
            "prior_task_effects": original_task.get("nonzero_effects", []),
            "retained_history": True,
            "limitation": "Starts after the prior task and any recorded outcomes; no rewind and "
                          "no claim of identical history or a newly revalidated main case",
        }
        previous.write(self.directory / "clone-provenance.json", provenance)
        previous.write(self.directory / "source-history-difference.json", self.history_difference)
        previous.write(self.directory / "fixed-case.json", {
            "batch": 47, "task_only_recheck": True, "question": fixed.TASK_ACTION,
            "case_order": ["independent_window_delegation"], "main_submitted": False,
            "setup_actions_executed": [], "history_padding_executed": False,
            "compression_preparation_executed": False, "source_directory": str(source),
            "source_main_sha256": self.source_main,
            "local_ports": {"backend": fixed.BACKEND_PORT, "frontend": fixed.FRONTEND_PORT},
        })
        self.result.update(
            question=fixed.TASK_ACTION, task_only_recheck=True, scope="task_only_recheck",
            main_submitted=False, setup_actions=[], fixture_origin=str(source),
            history_difference=self.history_difference,
            submission_mode="fresh fixed delegation after retained original outcomes",
            main_evidence_sha256=self.source_main, answer_accuracy="requires task receipt review",
            criteria={"task": "fixed delegation enters a correctly bound actual child cycle",
                      "result": "settle from this child formal observation or actual receipt",
                      "history": "both members show this recheck's persisted body once"},
        )

    def request(self, method, path, body=None, actor="host"):
        if method == "POST" and path == self.prefix + "/agent-cycle/cancel":
            room = super().request("GET", self.prefix)
            cycle = (room.get("game") or {}).get("cycle") or {}
            eligible = bool(cycle and cycle.get("status") not in {"completed", "cancelled"})
            self.result["setup_cancel_check"] = {
                "cycle_id": cycle.get("id"), "status": cycle.get("status"),
                "cancel_submitted": eligible,
            }
            if not eligible:
                return {"skipped": True, "reason": "no cancellable active cycle"}
        return super().request(method, path, body, actor)

    def setup(self):
        previous.ResumeCheck.setup(self)
        preflight = previous.read(self.directory / "preflight.json")
        preflight.update(task_scope="Fixed independent delegation only; main not submitted",
                         task_only_recheck=True, history_difference=self.history_difference)
        previous.write(self.directory / "preflight.json", preflight)

    def launch(self):
        for port in (fixed.BACKEND_PORT, fixed.FRONTEND_PORT):
            fixed.port_free(port)
        self.start([sys.executable], fixed.BACKEND, "backend")
        fixed.wait_for(lambda: self.http.get(
            f"http://127.0.0.1:{fixed.BACKEND_PORT}/api/health").is_success, 90)
        vite = fixed.ROOT / "frontend/node_modules/vite/bin/vite.js"
        self.start([shutil.which("node"), str(vite),
                    "--host", "127.0.0.1"], fixed.ROOT / "frontend", "frontend")
        fixed.wait_for(lambda: self.http.get(self.frontend_url).is_success, 45)

    def capture_windows(self, capture, started):
        finals = [f for f in [capture.get("final"), *capture["descendant_narrations"]] if f]
        audits = []
        for index, page in enumerate(self.pages):
            fixed.wait_for(page.connected, 30)
            records = []
            for final in finals:
                selector = f'[data-event-seq="{final["seq"]}"] p.preserve-lines'
                fixed.wait_for(lambda: page.text_at(selector) == final["payload"]["text"], 30)
                records.append({"seq": final["seq"], "text": page.text_at(selector),
                                "expected": final["payload"]["text"], "bubbles": page.evaluate(
                                    f"document.querySelectorAll('[data-event-seq=\"{final['seq']}\"]').length"
                                )})
            audit = page.evaluate("window.streamAudit") or {}
            audit.update(formal_observations=page.evaluate("window.publicDomAudit") or [],
                         final_records=records)
            audit.update(first_dom(audit, capture, started))
            audits.append(audit)
            previous.write(self.directory / f"task-member-{index + 1}-stream.json", audit)
            screenshot = page.command("Page.captureScreenshot", {"format": "png"})
            (self.directory / f"task-member-{index + 1}-final.png").write_bytes(
                base64.b64decode(screenshot["data"]))
        self.result.update(
            **(first_dom(audits[0], capture, started) if audits else {}),
            final_history_equal=bool(finals) and len(audits) == 2 and all(
                row["text"] == row["expected"] and row["bubbles"] == 1
                for audit in audits for row in audit["final_records"]),
            browser_errors=[e for page in self.pages for e in page.exceptions],
        )
        previous.write(self.directory / "task-dom-assessment.json", {
            "case_cycle_ids": capture["case_cycle_ids"],
            "members": [{k: audit[k] for k in ("first_dom_seconds", "first_public_dom_seconds",
                                                "final_records")} for audit in audits],
            "final_history_equal": self.result["final_history_equal"],
        })

    def run(self):
        old_observer = fixed.OBSERVER
        fixed.OBSERVER = previous.OBSERVER
        try:
            self.launch()
            self.setup()
            for index, page in enumerate(self.pages):
                page.evaluate("window.streamAudit.armed=true;window.streamAudit.reconnect="
                              + ("true" if index else "false"))
            started, error = time.time(), None
            self.result["action_requested_at"] = started
            try:
                self.act(fixed.TASK_ACTION, cycle_field="task_cycle_id")
            except Exception as caught:
                error = caught
            cycle_id = self.result.get("task_cycle_id")
            self.result["cycle_id"] = cycle_id
            capture = self.capture_case("task", cycle_id, started, error)
            capture.update(request=fixed.TASK_ACTION, task_only_recheck=True,
                           history_difference=self.history_difference)
            previous.write(self.directory / "task-case.json", capture)
            previous.write(self.directory / "batch-model-calls.json", capture["model_calls"])
            previous.write(self.directory / "model-calls.json", self.model_calls())
            self.capture_windows(capture, started)
            previous.write(self.directory / "public-events.json",
                           self.request("GET", self.prefix + "/logs", actor="player"))
            self.result.update(status="failed" if error else "captured_requires_semantic_review",
                               error=str(error) if error else None, task_case="task-case.json",
                               task_status=capture["status"], batch_metrics=capture["metrics"],
                               batch_cycle_ids=capture["case_cycle_ids"],
                               main_result="not rerun; source evidence retained separately")
            previous.write(self.directory / "task-timing.json", {
                "task_only_recheck": True, **capture["metrics"],
                "task_wall_seconds": capture["captured_at"] - started,
                "first_dom_seconds": self.result.get("first_dom_seconds"),
                "first_public_dom_seconds": self.result.get("first_public_dom_seconds"),
            })
            print(json.dumps({key: self.result.get(key) for key in (
                "scope", "status", "task_cycle_id", "first_public_dom_seconds", "batch_metrics",
            )}, ensure_ascii=False), flush=True)
        finally:
            fixed.OBSERVER = old_observer

    def close(self):
        # Batch47Check.close expects its protected-file inventory. Source main
        # evidence is separately verified here and never treated as a new pass.
        main_audit = {name: {"before": digest, "source_after": batch.digest(self.source / name),
                            "copied": batch.digest(self.directory / "source-main" / name)}
                      for name, digest in self.source_main.items()}
        self.result["source_main_unchanged"] = all(
            entry["before"] == entry["source_after"] == entry["copied"]
            for entry in main_audit.values())
        previous.write(self.directory / "source-main-audit.json", main_audit)
        # The base inventory deliberately contains only files that its own
        # protected_files() enumerates; the complete main audit is above.
        self.protected_before = {path: value for path, value in self.protected_before.items()
                                 if path in batch.protected_files(self.source)}
        super().close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="task-recheck-01")
    parser.add_argument("--source", default="../batch-46/real-04")
    parser.add_argument("--backend-port", type=int, default=8147)
    parser.add_argument("--frontend-port", type=int, default=5247)
    args = parser.parse_args()
    streaming.OUTPUT = OUTPUT
    fixed.BACKEND_PORT = streaming.BACKEND_PORT = args.backend_port
    fixed.FRONTEND_PORT = streaming.FRONTEND_PORT = args.frontend_port
    check = TaskRecheck(args.name, OUTPUT / args.source)
    try:
        check.run()
    except Exception as error:
        check.result.update(status="failed", error=str(error))
        try:
            check.capture_failure()
        except Exception as capture_error:
            check.result["capture_error"] = str(capture_error)
        raise
    finally:
        check.close()


if __name__ == "__main__":
    main()
