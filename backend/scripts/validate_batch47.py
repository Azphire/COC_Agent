"""Batch 47: unchanged main question and independent delegation, isolated locally.

Reuse Batch 46's HTTP, SQLite backup and per-attempt browser observer. Previously
passed settled-operation replays are not submitted again in this batch.
"""

import argparse
import hashlib
import json
import sqlite3

import validate_batch46 as previous


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def protected_files(source):
    paths = [
        previous.fixed.PACKAGE, previous.fixed.ROOT / ".env",
        previous.fixed.ROOT / "frontend/.env",
        previous.fixed.ROOT / "data/host-model-settings.json",
    ]
    paths.extend(
        source / name
        for name in (
            "game.db", "game.db-wal", "checkpoint.db", "checkpoint.db-wal",
            "knowledge.db", "knowledge.db-wal", "effective-config.json", "model-settings.json",
        )
    )
    return {str(path): digest(path) for path in paths}


def retained_documents(directory):
    queries = {
        "characters": "SELECT id,document FROM character_drafts ORDER BY id",
        "profiles": "SELECT id,document FROM agent_profiles ORDER BY id",
        "saves": "SELECT * FROM room_snapshots ORDER BY id",
        "preparations": "SELECT * FROM module_preparations ORDER BY id",
        "character_snapshots": (
            "SELECT id,character_snapshot FROM room_character_slots ORDER BY id"
        ),
    }
    result = {}
    with sqlite3.connect(f"file:{(directory / 'game.db').as_posix()}?mode=ro", uri=True) as db:
        for key, query in queries.items():
            values = db.execute(query).fetchall()
            encoded = json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")
            result[key] = {"rows": len(values), "sha256": hashlib.sha256(encoded).hexdigest()}
    return result


class Batch47Check(previous.Batch46Check):
    def __init__(self, name, source):
        self.protected_before = protected_files(source)
        super().__init__(name, source)
        self.retained_before = retained_documents(source)
        case = previous.read(self.directory / "fixed-case.json")
        case.update(
            batch=47,
            case_order=["main", "independent_window_delegation"],
            history_padding="not executed; retained original preparation",
            compression_preparation="not executed; retained original preparation",
        )
        previous.write(self.directory / "fixed-case.json", case)

    def capture_case(self, name, cycle_id, start, error=None):
        capture = super().capture_case(name, cycle_id, start, error)
        previous.write(self.directory / (name + "-coverage-and-budget.json"), {
            "case": name,
            "cycle_id": cycle_id,
            "coverage_attempts": [
                {key: call.get(key) for key in (
                    "cycle_id", "attempt", "answer_origin", "raw_output",
                    "raw_answer_coverage_audit", "effective_answer_coverage",
                    "answer_coverage_normalizations", "answer_coverage_audit",
                    "validation_issues",
                )}
                for call in capture["model_calls"] if call.get("schema") == "KeeperNarration"
            ],
            "memory_selections": [
                {"cycle_id": run["cycle_id"], "phase": run["graph_node"],
                 "selection": run["context"].get("memory_selection_audit"),
                 "prompt_budget": run["context"].get("prompt_budget_audit"),
                 "memory_evidence": run["context"].get("memory_evidence")}
                for run in capture["runs"] if run["context"].get("memory_selection_audit")
            ],
            "request_budgets": [
                {"cycle_id": call.get("cycle_id"), "schema": call.get("schema"),
                 "attempt": call.get("attempt"), "budget": call.get("request_budget")}
                for call in capture["model_calls"]
            ],
            "metrics": capture["metrics"],
        })
        return capture

    def replay_nonzero(self):
        return {
            "status": "not_repeated",
            "reason": "Batch 46 settled-operation replay evidence retained; outside Batch 47 scope",
        }

    def task_case(self):
        # This inherited check was only a presentation heuristic, not coverage.
        self.result.get("checks", {}).pop("multiple_body_sentences", None)
        previous.write(self.directory / "main-captured-result.json", self.result)
        return super().task_case()

    def close(self):
        super().close()
        after = protected_files(self.source)
        retained_after = retained_documents(self.directory)
        previous.write(self.directory / "source-protection-audit.json", {
            "files": {
                path: {"before": value, "after": after[path], "unchanged": value == after[path]}
                for path, value in self.protected_before.items()
            },
            "retained_documents": {
                key: {"source": value, "run": retained_after[key],
                      "equal": value == retained_after[key]}
                for key, value in self.retained_before.items()
            },
            "effective_config_equal": (
                previous.read(self.source / "effective-config.json")
                == previous.read(self.directory / "effective-config.json")
            ) if (self.directory / "effective-config.json").exists() else None,
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--source", default="real-05")
    parser.add_argument("--backend-port", type=int, default=8147)
    parser.add_argument("--frontend-port", type=int, default=5247)
    args = parser.parse_args()
    previous.streaming.OUTPUT = previous.fixed.ROOT / "data/prepared/batch-47"
    previous.fixed.BACKEND_PORT = previous.streaming.BACKEND_PORT = args.backend_port
    previous.fixed.FRONTEND_PORT = previous.streaming.FRONTEND_PORT = args.frontend_port
    check = Batch47Check(args.name, previous.SOURCE_ROOT / args.source)
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
