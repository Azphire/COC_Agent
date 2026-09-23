"""No browser/model startup: task-only driver scope, source backup and DOM timing."""

import importlib
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("recheck_batch47_task")


def test_readonly_backup_includes_live_wal_and_preserves_original(driver, tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "copied.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE evidence (value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('real observation after prior task')")
        connection.commit()
        audit = driver.backup_sqlite(source, target)
        assert audit["source_unchanged"]
        assert audit["source_sha256"][str(source) + "-wal"] is not None
        with sqlite3.connect(target) as copied:
            assert copied.execute("SELECT value FROM evidence").fetchone() == (
                "real observation after prior task",)
        with pytest.raises(AssertionError, match="new file"):
            driver.backup_sqlite(source, target)


@pytest.mark.parametrize("status,cancel", [(None, False), ("completed", False),
                                         ("cancelled", False), ("failed", True)])
def test_cancel_is_only_submitted_for_a_cancellable_cycle(driver, monkeypatch, status, cancel):
    requests = []

    def request(self, method, path, body=None, actor="host"):
        requests.append((method, path))
        return {"game": {"cycle": {"id": "old", "status": status} if status else None}}

    monkeypatch.setattr(driver.streaming.StreamingCheck, "request", request)
    check = driver.TaskRecheck.__new__(driver.TaskRecheck)
    check.prefix, check.result = "/rooms/retained", {}
    check.request("POST", check.prefix + "/agent-cycle/cancel")
    if cancel:
        assert ("POST", check.prefix + "/agent-cycle/cancel") in requests
    else:
        assert len(requests) == 1
    assert check.result["setup_cancel_check"]["cancel_submitted"] is cancel


def test_run_submits_fixed_task_once_and_never_runs_main_or_replay(driver, monkeypatch, tmp_path):
    check = driver.TaskRecheck.__new__(driver.TaskRecheck)
    check.directory, check.prefix, check.result, check.pages = tmp_path, "/rooms/r", {}, []
    check.history_difference = {"retained_history": True}
    actions = []
    capture = {"case_cycle_ids": ["task", "child"], "events": [], "final": None,
               "descendant_narrations": [], "model_calls": [], "status": "captured",
               "metrics": {"actual_call_count": 0}, "captured_at": 110}
    check.launch = lambda: None
    check.setup = lambda: None

    def act(text, *, cycle_field):
        actions.append(text)
        check.result[cycle_field] = "task"

    def capture_case(name, cycle_id, start, error):
        assert (name, cycle_id, start, error) == ("task", "task", 100, None)
        return capture

    check.act, check.capture_case = act, capture_case
    check.capture_windows = lambda *_: None
    check.model_calls = lambda: []
    check.request = lambda *_args, **_kwargs: []
    monkeypatch.setattr(driver.time, "time", lambda: 100)
    check.run()
    assert actions == [driver.fixed.TASK_ACTION]
    assert not (tmp_path / "main-case.json").exists()
    assert (tmp_path / "task-case.json").exists()
    assert check.result["main_result"].startswith("not rerun")
    assert driver.previous.read(tmp_path / "task-timing.json")["task_wall_seconds"] == 10


def test_first_dom_uses_this_task_and_child_excludes_old_source_history(driver):
    capture = {"case_cycle_ids": ["task", "child"],
               "events": [{"seq": 20, "type": "keeper.narration"}]}
    audit = {"observations": [{"at": 99, "cycle_id": "old", "text": "old result"},
                              {"at": 104, "cycle_id": "child", "text": "new prefix"}],
             "formal_observations": [{"at": 100, "seq": 10}, {"at": 107, "seq": 20}]}
    assert driver.first_dom(audit, capture, 100) == {
        "first_dom_seconds": 4, "first_public_dom_seconds": 4}


def test_setup_reuses_normal_restore_flow_without_preparation_actions(
    driver, monkeypatch, tmp_path,
):
    called = []

    def setup(self):
        called.append("normal member replacement and card assignment")
        driver.previous.write(self.directory / "preflight.json", {"status": "passed"})

    monkeypatch.setattr(driver.previous.ResumeCheck, "setup", setup)
    check = driver.TaskRecheck.__new__(driver.TaskRecheck)
    check.directory, check.history_difference = tmp_path, {"retained_history": True}
    check.setup()
    assert called == ["normal member replacement and card assignment"]
    assert driver.previous.read(tmp_path / "preflight.json")["task_only_recheck"] is True
