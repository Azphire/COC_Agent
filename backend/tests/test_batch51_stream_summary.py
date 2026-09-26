"""The report cannot count server text or slow callbacks as model streaming."""

import importlib
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def sample(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    summary = importlib.import_module("summarize_batch51_browser")
    first, body = "便签背面写着原文。", "便签背面写着原文。\n\n纸张是否有夹层或折叠，目前不能确定。"
    ids = {"cycle_id": "cycle", "stream_id": "stream", "attempt": 1}
    event = {"seq": 6, "payload": {"cycle_id": "cycle", "stream_id": "stream",
             "stream_attempt": 1, "answer_origin": "mixed", "text": body}}
    doc = {"stream_id": "stream", "attempt": 1, "terminal_received_at": 105,
           "adapter_returned_at": 110, "model_finished_at": 110,
           "provider_chunks": [{"received_at": 101, "terminal": False},
                               {"received_at": 105, "terminal": True}],
           "stream_segments": [
               {"source": "model", "stream_id": "stream", "attempt": 1,
                "published_at": 102, "received_at": 101, "chunk_index": 1,
                "body_start": 0, "body_end": len(first)},
               {"source": "server", "stream_id": "stream", "attempt": 1,
                "published_at": 104, "received_at": None, "chunk_index": None,
                "body_start": len(first) + 2, "body_end": len(body)},
           ]}
    evidence = {
        "frames": [
            {"at": 102000, "type": "keeper.stream.delta", "data": {**ids, "text": first}},
            {"at": 102200, "type": "observer.forced_reconnect", "data": ids},
            {"at": 102400, "type": "keeper.stream.snapshot",
             "data": {**ids, "status": "responding", "text": first}},
            {"at": 104000, "type": "keeper.stream.delta",
             "data": {**ids, "text": body[len(first):]}},
            {"at": 111000, "type": "keeper.stream.end", "data": {**ids, "event_seq": 6}},
        ],
        "dom": [{"at": stamp, "timeline": [], "streams": [{
            "cycle": "cycle", "stream": "stream", "attempt": "1", "status": "responding",
            "index": str(index), "text": text,
        }]} for stamp, index, text in ((102050, 1, first), (104050, 2, body))]
        + [{"at": 111050, "streams": [], "timeline": [{"seq": "6", "text": body}]}],
    }
    return summary, event, {"id": "call", "document": doc}, evidence


def assess(sample):
    module, event, call, evidence = sample
    return module.assess_answer(
        event, {"answer_complete": True, "valid": True}, call, evidence, 100,
    )


def test_one_model_part_plus_server_is_formal_and_visible_without_multi_model_claim(sample):
    row = assess(sample)
    assert row["same_attempt_formal_passed"]
    assert row["same_attempt_generation_visible"]
    assert row["part_counts_by_source"] == {"model": 1, "server": 1, "retained": 0}
    assert not row["model_incremental_append"]
    assert row["model_incrementality_evidence"] == "single_model_part_no_multi_part_claim"
    assert row["reconnect_evidence"] == "obtained"


def test_terminal_frame_before_slow_callback_does_not_pass_generation_visibility(sample):
    doc = sample[2]["document"]
    doc["terminal_received_at"] = 101
    doc["provider_chunks"] = [{"received_at": 101, "terminal": True}]
    row = assess(sample)
    assert row["same_attempt_formal_passed"]
    assert not row["same_attempt_generation_visible"]
    assert not row["model_published_before_terminal"]
    assert row["reconnect_evidence"] == "not_obtained"


def test_snapshot_must_follow_this_reconnect_and_precede_terminal_receipt(sample):
    frames = sample[3]["frames"]
    frames[1]["at"] = 104900  # Disconnect before terminal receipt at 105.
    frames[2]["at"] = 102100  # This earlier snapshot cannot prove that reconnect.
    late_snapshot = deepcopy(frames[2])
    late_snapshot["at"] = 105100
    frames.append(late_snapshot)
    row = assess(sample)
    assert row["same_attempt_generation_visible"]
    assert row["reconnect_during_generation"]
    assert not row["responding_snapshot_during_generation"]
    assert row["reconnect_evidence"] == "not_obtained"
    assert row["reconnect_snapshot_pairs"] == []


@pytest.mark.parametrize("source", ["server", "retained"])
def test_early_non_model_text_is_not_model_first_display(sample, source):
    sample[2]["document"]["stream_segments"][0]["source"] = source
    row = assess(sample)
    assert row["first_dom_at_by_source"]["model"] is None
    assert not row["same_attempt_generation_visible"]


@pytest.mark.parametrize("missing", ["terminal_received_at", "provider_chunks", "stream_segments"])
def test_absent_wire_evidence_is_not_inferred_from_adapter_return(sample, missing):
    sample[2]["document"].pop(missing)
    row = assess(sample)
    assert not row["same_attempt_generation_visible"]
    assert row["reconnect_evidence"] == "not_obtained"


def test_two_model_segments_in_one_wire_frame_are_not_incremental_generation(sample):
    parts = sample[2]["document"]["stream_segments"]
    parts[1].update(source="model", received_at=101, chunk_index=1)
    row = assess(sample)
    assert not row["model_incremental_append"]


def test_two_model_segments_with_separate_receipts_and_visible_append_are_recorded(sample):
    parts = sample[2]["document"]["stream_segments"]
    parts[1].update(source="model", received_at=103, chunk_index=2)
    assert assess(sample)["model_incremental_append"]


def test_different_attempt_or_missing_formal_body_cannot_supply_evidence(sample):
    changed = (sample[0], *deepcopy(sample[1:]))
    changed[2]["document"]["attempt"] = 2
    assert not assess(changed)["same_attempt_generation_visible"]
    sample[3]["dom"][-1]["timeline"][0]["text"] += "改写"
    assert not assess(sample)["same_attempt_formal_passed"]
