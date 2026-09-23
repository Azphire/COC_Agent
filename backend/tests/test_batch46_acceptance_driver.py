"""Acceptance evidence must not turn no-ops, old clues or task failures into a pass."""

import copy
import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("validate_batch46")


def snapshot(effect_seq=11):
    return {
        "submission_events": [{"seq": 10, "payload": {"cycle_id": "original"}}],
        "effect_events": [{"seq": effect_seq, "type": "entity.revealed", "payload": {}}],
        "tool_receipts": [
            {
                "cycle_id": "original",
                "result": {"ok": True, "tool": "reveal_entity", "data": {"event_seq": effect_seq}},
            }
        ],
        "session": {"time": 4, "resources": {"hp": 12}},
        "checks": [{"id": "check-original", "dice": 56}],
        "behavior": {"pending_requests": ["request-original"]},
    }


def test_old_reveal_receipt_is_not_nonzero_current_operation(driver):
    assert driver.nonzero_evidence(snapshot(effect_seq=8), "original") == []
    assert len(driver.nonzero_evidence(snapshot(), "original")) == 1
    assert driver.nonzero_evidence(snapshot(), "different-cycle") == []


def test_descendant_operation_belongs_to_original_request_but_other_case_does_not(driver):
    state = snapshot()
    state["cycles"] = [
        {"id": "original", "state": {}},
        {"id": "child", "state": {"related_player_cycle_id": "original"}},
        {"id": "grandchild", "state": {"parent_cycle_id": "child"}},
        {"id": "other-question", "state": {"parent_cycle_id": "original"}},
        {"id": "other-child", "state": {"related_player_cycle_id": "other-question"}},
    ]
    state["submission_events"].append({"seq": 20, "payload": {"cycle_id": "other-question"}})
    state["tool_receipts"][0]["cycle_id"] = "grandchild"
    assert driver.case_cycle_ids(state, "original") == {"original", "child", "grandchild"}
    assert len(driver.nonzero_evidence(state, "original")) == 1
    state["tool_receipts"][0]["cycle_id"] = "other-child"
    state["effect_events"][0]["seq"] = 21
    state["tool_receipts"][0]["result"]["data"]["event_seq"] = 21
    assert driver.nonzero_evidence(state, "original") == []
    assert len(driver.nonzero_evidence(state, "other-question")) == 1


def replay_check(driver, tmp_path, state):
    check = driver.Batch46Check.__new__(driver.Batch46Check)
    check.directory, check.prefix = tmp_path, "/rooms/room"
    body = {"text": "original action", "client_request_id": "same-id", "target_entity_id": "target"}
    original = {
        "method": "POST",
        "path": "/rooms/room/actions",
        "actor": "player",
        "body": body,
        "response_event": {"seq": 10, "payload": {"cycle_id": "original"}},
    }
    check.submissions = {"original": original}
    check.settlement_snapshot = lambda: copy.deepcopy(state)
    check.settle = lambda: None
    requests = []

    def request(method, path, submitted=None, actor="host"):
        requests.append((method, path, copy.deepcopy(submitted), actor))
        return {"event": copy.deepcopy(original["response_event"])}

    check.request = request
    return check, requests, body


def test_replay_uses_same_body_and_cycle_before_and_after_pause(driver, tmp_path):
    check, requests, body = replay_check(driver, tmp_path, snapshot())
    result = check.replay_nonzero()
    assert result["status"] == "passed"
    actions = [request for request in requests if request[1].endswith("/actions")]
    assert len(actions) == 2 and all(request[2] == body for request in actions)
    assert [request[1].split("/")[-1] for request in requests] == [
        "actions",
        "pause",
        "resume",
        "actions",
    ]


def test_zero_operations_do_not_send_a_new_action_to_obtain_pass(driver, tmp_path):
    check, requests, _ = replay_check(driver, tmp_path, snapshot(effect_seq=8))
    assert check.replay_nonzero()["status"] == "not_proven"
    assert not requests


def test_changed_resource_dice_or_pending_task_fails_replay(driver, tmp_path):
    check, requests, _ = replay_check(driver, tmp_path, snapshot())
    states = iter([snapshot(), {**snapshot(), "checks": [{"id": "new-roll", "dice": 9}]}])
    check.settlement_snapshot = lambda: next(states)
    result = check.replay_nonzero()
    assert result["status"] == "failed"
    assert result["replays"][0]["checks"]["checks"] is False
    assert len(requests) == 1


def test_task_failure_keeps_frozen_main_result_and_latency(driver, tmp_path):
    check = driver.Batch46Check.__new__(driver.Batch46Check)
    check.directory, check.prefix = tmp_path, "/rooms/room"
    check.result = {"cycle_id": "main", "action_requested_at": 1.0, "first_dom_seconds": 8.5}
    check.submissions, check.case_results, check.frozen_main = {}, {}, {}
    driver.write(tmp_path / "main-captured-result.json", check.result)
    original = (tmp_path / "main-captured-result.json").read_bytes()

    def capture(name, cycle_id, start, error=None):
        result = {"status": "failed" if error else "captured", "cycle_id": cycle_id}
        driver.write(tmp_path / (name + "-case.json"), result)
        driver.write(tmp_path / (name + "-settlement.json"), {"cycle": cycle_id})
        return result

    def failed_task(text, cycle_field=None):
        check.result[cycle_field] = "failed-task"
        raise RuntimeError("original task generation failure")

    check.capture_case, check.act, check.model_calls = capture, failed_task, lambda: []
    check.task_case()
    assert (tmp_path / "main-captured-result.json").read_bytes() == original
    assert check.result["first_dom_seconds"] == 8.5
    assert check.result["task_status"] == "failed"
    assert check.result["main_preserved_after_task"]
    assert driver.read(tmp_path / "idempotency.json")["status"] == "not_run"


def test_answer_provenance_separates_native_repair_and_fallback(driver):
    call = {"schema": "KeeperNarration", "raw_output": {"public_narration": "first"}}
    final = {"payload": {"text": "formal"}}
    assert driver.generation_assessment([call], final)["outcome"] == "native_model_answer"
    repaired = driver.generation_assessment([call, {**call, "attempt": 2}], final)
    assert repaired["outcome"] == "repaired_model_answer"
    assert repaired["first_output"]["raw_output"] == call["raw_output"]
    fallback = {"payload": {"text": "safe text", "safe_fallback": True}}
    assert driver.generation_assessment([call, call], fallback)["outcome"] == "server_fallback"


def test_body_coverage_comes_from_recorded_validation_not_answer_vocabulary(driver):
    call = {
        "schema": "KeeperNarration",
        "answer_origin": "repaired",
        "answer_coverage_audit": {"complete": True, "requirements": [{"id": "historical"}]},
    }
    validation = {
        "answer_origin": "repaired",
        "answer_complete": False,
        "answer_coverage": {"complete": False, "missing": ["historical"]},
    }
    assessment = driver.generation_assessment(
        [call],
        {"payload": {"text": "A fluent observation"}},
        validation,
    )
    assert assessment["outcome"] == "repaired_model_answer"
    assert assessment["answer_complete"] is False
    assert assessment["answer_coverage"] == validation["answer_coverage"]
    assert assessment["first_output"]["answer_coverage_audit"] == call["answer_coverage_audit"]


def test_unordered_database_rows_preserve_true_native_output_and_stage_order(driver):
    native = {
        "schema": "KeeperNarration",
        "request_started_at": 10,
        "attempt": 1,
        "raw_output": {"public_narration": "actual first output"},
    }
    repair = {
        "schema": "KeeperNarration",
        "request_started_at": 20,
        "attempt": 2,
        "raw_output": {"public_narration": "repair output"},
    }
    calls = [repair, native]
    result = driver.generation_assessment(calls, {"payload": {"text": "formal"}})
    assert result["first_output"]["raw_output"] == native["raw_output"]
    assert [call["attempt"] for call in result["attempts"]] == [1, 2]
    assert [call["attempt"] for call in driver.call_metrics(calls)["stages"]] == [1, 2]
    assert calls == [repair, native]


def test_browser_observer_reconnects_once_per_visible_attempt_even_with_identical_prefix(driver):
    node = shutil.which("node")
    if not node:
        pytest.skip("Installed Node is required to execute the browser observer JavaScript")
    harness = (
        r"""
const callbacks = [], sockets = [];
let closes = 0;
class Socket {
  constructor() { this.readyState = 1; sockets.push(this); }
  addEventListener() {}
  close() { closes++; this.readyState = 3; }
}
global.window = {roomSockets:sockets, WebSocket:Socket};
new Socket();
const node = {
  dataset:{cycleId:'root', streamId:'stream', streamAttempt:'1',
    streamIndex:'1', streamStatus:'responding'},
  querySelector:() => ({textContent:'same visible prefix'})
};
global.document = {body:{}, querySelectorAll:() => [node]};
global.MutationObserver = class {
  constructor(callback) { callbacks.push(callback); }
  observe() {}
};
"""
        + driver.OBSERVER
        + r"""
window.streamAudit.armed = true;
window.streamAudit.reconnect = true;
const flush = () => callbacks.forEach(callback => callback());
flush();
new window.WebSocket('/ws/rooms/root');
flush();
node.dataset.streamAttempt = '2';
flush();
new window.WebSocket('/ws/rooms/root');
flush();
process.stdout.write(JSON.stringify({closes, audit:window.streamAudit}));
"""
    )
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    assert value["closes"] == 2
    assert [row["attempt"] for row in value["audit"]["disconnections"]] == [1, 2]
    assert [row["attempt"] for row in value["audit"]["observations"]] == [1, 2]
    assert all(
        row["visible_text"] == "same visible prefix" for row in value["audit"]["disconnections"]
    )


def test_failed_native_reconnect_cannot_satisfy_final_repair_reconnect(driver):
    final = {"payload": {"text": "AB", "stream_id": "stream", "stream_attempt": 2}}
    observations = [
        {"cycle_id": "root", "stream_id": "stream", "attempt": 2, "at": 21, "text": "A"},
        {"cycle_id": "root", "stream_id": "stream", "attempt": 2, "at": 22, "text": "AB"},
    ]
    audits = [{"observations": observations, "frames": []} for _ in range(2)]
    audits[1]["disconnections"] = [
        {"cycle_id": "root", "stream_id": "stream", "attempt": 1, "at": 11},
    ]
    snapshot = {
        "type": "keeper.stream.snapshot",
        "at": 22,
        "data": {"cycle_id": "root", "stream_id": "stream", "attempt": 2, "status": "responding"},
    }
    audits[1]["frames"] = [snapshot]
    calls = [{"attempt": 2, "cycle_id": "root", "request_started_at": 20, "model_finished_at": 30}]
    assert not driver.streaming_checks(audits, calls, final, "root")["reconnect_during_generation"]
    audits[1]["disconnections"].append(
        {"cycle_id": "root", "stream_id": "stream", "attempt": 2, "at": 21.5}
    )
    result = driver.streaming_checks(audits, calls, final, "root")
    assert result["reconnect_during_generation"]
    assert result["matched_disconnect"]["attempt"] == 2
    assert len(result["all_disconnections"]) == 2
