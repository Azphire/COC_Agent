"""Supplemental reporting must not promote stale evidence or overwrite prior audits."""

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("audit_batch47")


def case(*, formal_valid=True, source_cycle="child", source_type="keeper.narration"):
    text = "窗锁目前松动。"
    request = {"key": "10:0", "source_event_seq": 10, "target_id": "window"}
    state = {"origin": "teammate", "related_player_cycle_id": "root",
             "triggering_member_id": "peer", "request_keys": ["10:0"],
             "request_operands": {"10:0": request},
             "teammate_attempt": {"target_id": "window", "operations": ["observe"]}}
    fact = {"operation": "observe", "status": "success", "cycle_id": "child",
            "actor_id": "peer", "target_id": "window", "source_event_seq": 12}
    document = {"plan": {"focus": {"action_target_id": "window"}},
                "narration": {"public_narration": text},
                "narration_validation": {"valid": formal_valid, "answer_complete": formal_valid}}
    task = {"cycle_id": "root", "original_submission": {"response_event": {"seq": 10}},
            "behavior": [{"member_id": "peer", "pending_requests": [request],
                          "last_result": {"cycle_id": "child", "result_facts": [fact]}}],
            "validated_plans": [{"cycle_id": "child", "document": document}],
            "events": [{"seq": 12, "type": source_type,
                        "payload": {"cycle_id": source_cycle, "text": text,
                                    "safe_fallback": not formal_valid}}]}
    snapshot = {"cycles": [{"id": "root", "state": {}},
                           {"id": "child", "status": "completed", "state": state}],
                "submission_events": [{"seq": 10, "payload": {"cycle_id": "root"}}]}
    return task, snapshot


@pytest.mark.parametrize("updates,supported", [
    ({}, True), ({"formal_valid": False}, False), ({"source_cycle": "old-main"}, False),
    ({"source_type": "agent.action_proposed"}, False),
    ({"source_type": "entity.revealed"}, False),
])
def test_only_this_child_validated_formal_result_supports_observation(audit, updates, supported):
    task, snapshot = case(**updates)
    result = audit.task_audit(task, snapshot, "window")
    assert result["entered_actual_child"]
    assert result["child_has_supported_current_success_receipt"] is supported
    assert result["children"][0]["matches_reviewed_expected_target"]


def test_false_module_event_cannot_support_success_fact(audit):
    task, snapshot = case(source_type="module.interaction")
    task["events"][0]["payload"].update(operation="observe", passed=False)
    assert not audit.task_audit(task, snapshot)["child_has_supported_current_success_receipt"]
    task["events"][0]["payload"]["passed"] = True
    assert audit.task_audit(task, snapshot)["child_has_supported_current_success_receipt"]


def test_a_queued_child_is_not_execution(audit):
    task, snapshot = case()
    snapshot["cycles"][1]["status"] = "queued"
    task["events"], task["behavior"] = [], []
    report = audit.task_audit(task, snapshot)
    assert report["child_created"] and not report["entered_actual_child"]
    assert not report["child_has_supported_current_success_receipt"]


def test_scope_excludes_an_independent_submission_and_its_child(audit):
    _, snapshot = case()
    snapshot["submission_events"].append({"seq": 20, "payload": {"cycle_id": "other"}})
    snapshot["cycles"] += [
        {"id": "other", "state": {"parent_cycle_id": "root"}},
        {"id": "other-child", "state": {"related_player_cycle_id": "other"}},
    ]
    assert audit.scope_ids(snapshot, "root") == {"root", "child"}


def test_encoded_messages_retain_exact_multiline_body_and_missing_usage_is_visible(audit):
    body = "窗锁松动。\n书名未知。"
    paths = audit.text_paths([{"content": json.dumps({"text": body}, ensure_ascii=False)}], body)
    assert paths == [{"path": "$[0].content<json>.text", "exact_value": True}]
    metrics = audit.call_metrics([{"cycle_id": "main", "schema": "KeeperNarration"}])
    assert metrics["actual_calls"] == 1 and len(metrics["calls_missing_usage"]) == 1


def test_existing_report_stops_before_reading_inputs_or_writing_other_outputs(audit, tmp_path):
    output = tmp_path / "audit"
    output.mkdir()
    previous = output / audit.OUTPUTS[1]
    previous.write_text("original evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        audit.audit(tmp_path / "missing-inputs", output)
    assert previous.read_text(encoding="utf-8") == "original evidence"
    assert list(output.iterdir()) == [previous]


@pytest.mark.parametrize("scope_key", ["mode", "scope", "task_only_recheck"])
def test_task_only_recheck_ignores_source_main_calls_and_reports_no_new_main(
    audit, tmp_path, scope_key,
):
    task, snapshot = case()
    # A delegation-only parent legitimately has no KP narration of its own.
    task["validated_plans"].append({"cycle_id": "root", "document": {"narration": None}})
    task["model_calls"] = [
        {"cycle_id": "root", "schema": "TeammateDecision",
         "token_usage": {"input": 10, "output": 2}},
        {"cycle_id": "child", "schema": "KeeperNarration",
         "token_usage": {"input": 20, "output": 3}},
        {"cycle_id": "old-main", "token_usage": {"input": 9999, "output": 9999}},
    ]
    for name, data in (("task-case.json", task), ("task-settlement.json", snapshot),
                       ("acceptance.json", {scope_key: True if scope_key == "task_only_recheck"
                                            else "task_only_recheck"}),
                       ("task-timing.json", {"first_dom_seconds": 1.2}),
                       ("task-member-1-stream.json", {"final_records": [
                           {"seq": 12, "text": "窗锁目前松动。", "bubbles": 1},
                       ]})):
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    source = tmp_path / "source-main"
    source.mkdir()
    # Copied evidence may be unreadable or belong to earlier calls; it is not this run.
    (source / "main-case.json").write_text("untouched frozen main evidence", encoding="utf-8")
    output = tmp_path / "audit"
    audit.audit(tmp_path, output)
    formal, metrics, execution = [json.loads((output / name).read_text(encoding="utf-8"))
                                   for name in audit.OUTPUTS]
    assert formal["main_not_rerun"] and not formal["new_main_evidence_collected"]
    assert not formal["source_main_included"]
    child = next(c for c in formal["cycles"] if c["cycle_id"] == "child")
    assert child["adjudication_matches_event"]
    assert child["members"][0]["matches_official_once"]
    assert not child["members"][1]["captured"]
    assert metrics["independent_task"]["actual_calls"] == 2
    assert metrics["independent_task"]["known_usage"] == {"input": 30, "output": 5}
    assert "main" not in metrics and "first_dom_seconds" not in metrics
    assert metrics["task_timing"]["first_dom_seconds"] == 1.2
    assert execution["child_has_supported_current_success_receipt"]
    assert (source / "main-case.json").read_text(encoding="utf-8") == (
        "untouched frozen main evidence")


def test_normal_audit_still_requires_main_case_and_does_not_write_partial_reports(audit, tmp_path):
    task, _ = case()
    (tmp_path / "task-case.json").write_text(json.dumps(task), encoding="utf-8")
    (tmp_path / "acceptance.json").write_text(
        json.dumps({"mode": "real_local_ollama"}), encoding="utf-8",
    )
    output = tmp_path / "audit"
    with pytest.raises(ValueError, match="Both main-case.json and task-case.json"):
        audit.audit(tmp_path, output)
    assert not output.exists()
