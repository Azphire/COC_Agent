"""Batch 47 inherits frozen independent cases without replaying passed history."""

import importlib
from pathlib import Path


def test_skipping_previous_idempotency_never_submits_an_action(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    driver = importlib.import_module("validate_batch47")
    check = driver.Batch47Check.__new__(driver.Batch47Check)

    def unexpected(*args, **kwargs):
        raise AssertionError("Batch 47 must not repeat settled-operation HTTP submissions")

    check.request = unexpected
    assert check.replay_nonzero()["status"] == "not_repeated"


def test_presentation_heuristic_is_removed_before_main_freeze(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    driver = importlib.import_module("validate_batch47")
    check = driver.Batch47Check.__new__(driver.Batch47Check)
    check.directory = tmp_path
    check.result = {"checks": {"multiple_body_sentences": False, "no_fallback": False}}
    captured = []

    def freeze_main(self):
        captured.append(driver.previous.read(self.directory / "main-captured-result.json"))

    monkeypatch.setattr(driver.previous.Batch46Check, "task_case", freeze_main)
    check.task_case()
    assert captured == [{"checks": {"no_fallback": False}}]
