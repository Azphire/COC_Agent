"""Two bounded fixes from the saved natural play failures; no model calls."""

import hashlib
import json
from pathlib import Path

import pytest

from app.agents.adjudication_schemas import TeammateDecision
from app.agents.generation_contracts import restore_output
from app.memory.facts import render_facts, select_facts
from app.preparation.action_authority import action_kinds, operative_fragments

EVIDENCE = Path(__file__).resolve().parents[2] / "data/prepared/batch-48/real-01"


def document(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.fixture(scope="module")
def saved_turns():
    paths = [EVIDENCE / name for name in (
        "investigation-state-01.json", "second-turn-review.json",
    )]
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    yield [json.loads(path.read_text(encoding="utf-8-sig")) for path in paths]
    assert original == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_saved_reading_refusal_never_grants_opening_authority(saved_turns):
    state = saved_turns[1]
    room_id = state["launch_drafts"][0]["room_id"]
    run = next(r for r in state["agent_runs"] if r["room_id"] == room_id
               and r["graph_node"] == "plan_keeper_action"
               and document(r["context"]).get("triggering_action", {}).get("seq") == 55)
    raw = document(run["context"])["triggering_action"]["payload"]["text"]
    # Retain the original wrong frozen result as evidence of the real failure.
    assert document(run["structured_output"])["action_authority"]["kinds"] == ["open"]
    assert "open" not in action_kinds(raw)
    assert all("不尝试开门" not in f["text"] for f in operative_fragments(raw))


@pytest.mark.parametrize("text", [
    "不尝试开门。", "我不试着打开面板。", "先不尝试开锁。", "我不试着拉动车门。",
])
def test_explicit_refusal_has_no_operation(text):
    assert not action_kinds(text)


@pytest.mark.parametrize("text,expected", [
    ("我尝试开门。", {"open"}),
    ("我试着拉动车门。", {"control"}),
    ("我先不尝试开门，再检查便签边缘。", {"search"}),
    ("我不试着拉门，然后观察门锁。", {"observe"}),
])
def test_real_attempt_and_later_independent_action_keep_authority(text, expected):
    assert set(action_kinds(text)) == expected


def test_saved_deduplicated_recall_retains_original_public_source(saved_turns):
    state = saved_turns[0]
    room_id = state["launch_drafts"][0]["room_id"]
    run = next(r for r in state["agent_runs"] if r["room_id"] == room_id
               and r["graph_node"] == "decide_teammates")
    context = document(run["context"])
    call = next(document(c["document"]) for c in state["agent_model_calls"]
                if c["run_id"] == run["id"])
    assert context["readonly_recall"] and not context["fact_evidence"]
    assert any(row.get("source", {}).get("seq") == 6 for row in context["memory_evidence"])
    assert "只管前进" in call["generated_output"]["speech_text"]
    result = restore_output(TeammateDecision.model_validate(call["generated_output"]),
                            TeammateDecision, context)
    assert "只管前进吧，已经没有退路了。" in result.speech_text
    assert result.mode == "speak" and result.action_text is None
    assert result.fact_ids == ["event:6"]
    # Batch 49 reads the same selected original from its deduplicated projection.
    assert not context["fact_evidence"]


def test_nonempty_recall_retains_the_saved_public_source(saved_turns):
    state = saved_turns[0]
    room_id = state["launch_drafts"][0]["room_id"]
    events = [{**e, "payload": document(e["payload"])} for e in state["room_events"]
              if e["room_id"] == room_id]
    entities = [e["payload"] for e in events if e["type"] == "entity.revealed"]
    records = select_facts(events, entities, "便签具体写着什么？")
    original = next(e["payload"]["public_summary"] for e in events if e["seq"] == 6)
    rendered = render_facts(records)
    assert original in rendered and "门上便签正面" in rendered
    assert "暂时不能确认" not in rendered
