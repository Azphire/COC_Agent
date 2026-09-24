"""The acceptance observer must not combine failures or post-completion frames."""

import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def observed(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("summarize_batch50_browser")
    cycle, stream = "current-child", "successful-stream"
    body = "纸张有无夹层或折叠还不清楚。\n\n便签背面写着原文。"
    first = body.split("\n\n")[0]
    common = {"cycle_id": cycle, "stream_id": stream, "attempt": 2}
    frames = [
        {"at": 102000, "type": "keeper.stream.delta", "data": {**common, "text": first}},
        {"at": 102200, "type": "observer.forced_reconnect", "data": common},
        {"at": 102400, "type": "keeper.stream.snapshot",
         "data": {**common, "status": "responding", "text": first}},
        {"at": 104000, "type": "keeper.stream.delta",
         "data": {**common, "text": body[len(first):]}},
        {"at": 106000, "type": "keeper.stream.end", "data": {**common, "event_seq": 76}},
    ]
    dom = [{"at": stamp, "streams": [{
        "cycle": cycle, "stream": stream, "attempt": "2", "index": str(index),
        "status": "responding", "text": text,
    }], "timeline": []} for stamp, index, text in ((102050, 1, first), (104050, 2, body))]
    dom.append({"at": 106100, "streams": [], "timeline": [{"seq": "76", "text": body}]})
    document = {"attempt": 2, "first_validated_segment_at": 102, "model_finished_at": 105}
    capture = {"request": "original request", "started_at": 100, "audit": {
        "agent_runs": [{"id": "run", "cycle_id": cycle,
                        "graph_node": "generate_keeper_narration"}],
        "agent_model_calls": [{"id": "call", "run_id": "run", "document": document}],
        "room_events": [
            {"type": "keeper.narration", "seq": 76, "payload": {
                "cycle_id": cycle, "text": body, "answer_origin": "repaired",
                "safe_fallback": False,
            }},
            {"type": "agent.narration_validated", "seq": 75, "payload": {
                "cycle_id": cycle, "answer_complete": True, "valid": True,
            }},
        ],
    }}
    browsers = {name: {"privacy": {"all_player_projection": True},
                       "evidence": {"frames": copy.deepcopy(frames), "dom": copy.deepcopy(dom)}}
                for name in ("player-live", "player-reconnect")}

    def read(path):
        return capture if path.name == "delegate-case.json" else browsers[path.parent.name]

    monkeypatch.setattr(module, "read", read)
    monkeypatch.setattr(module, "natural_case", lambda _: {
        "cycle_ids": [cycle], "model_call_count": 6, "usage": {"input": 100, "output": 20},
        "elapsed_seconds": 6,
    })
    return module, capture, browsers


def test_same_successful_attempt_requires_both_actual_browser_observations(observed):
    module, _, _ = observed
    assert module.summarize(Path("unused"))["same_successful_attempt_in_both_windows"]


@pytest.mark.parametrize("failure", [
    "post_completion", "one_piece", "different_formal_text", "late_reconnect",
    "other_attempt", "missing_finish", "fallback",
])
def test_observer_does_not_upgrade_incomplete_evidence(observed, failure):
    module, capture, browsers = observed
    browser = browsers["player-reconnect"]["evidence"]
    if failure == "post_completion":
        browser["dom"][0]["at"] = 105100
    elif failure == "one_piece":
        browser["dom"][1]["streams"][0]["text"] = browser["dom"][0]["streams"][0]["text"]
    elif failure == "different_formal_text":
        browser["dom"][-1]["timeline"][0]["text"] += " "
    elif failure == "late_reconnect":
        browser["frames"][1]["at"] = 105100
    elif failure == "other_attempt":
        browser["frames"][-1]["data"]["attempt"] = 1
    elif failure == "missing_finish":
        capture["audit"]["agent_model_calls"][0]["document"]["model_finished_at"] = None
    else:
        capture["audit"]["room_events"][0]["payload"]["safe_fallback"] = True
    assert not module.summarize(Path("unused"))["same_successful_attempt_in_both_windows"]
