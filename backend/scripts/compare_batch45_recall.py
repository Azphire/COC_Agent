"""Offline duplicate-source A/B; synthetic cases, no inference or game operations."""

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.adjudication_schemas import KeeperPlan  # noqa: E402
from app.config import Settings  # noqa: E402
from app.memory.recall import select_memory  # noqa: E402
from app.memory.segments import make_segment, select_chunks  # noqa: E402
from app.models.budget import measure_request  # noqa: E402


def compare():
    baseline = "260415936b32898eb79a3d5630c00455b8457034"
    raw = subprocess.run(
        ["git", "show", baseline + ":backend/app/memory/recall.py"],
        cwd=Path(__file__).resolve().parents[2], check=True, capture_output=True,
    ).stdout.decode("utf-8")
    old = ModuleType("batch45_old_recall")
    exec(compile(raw, baseline + ":recall.py", "exec"), old.__dict__)
    events = [
        {"seq": 1, "type": "clue.revealed", "visibility": "public", "actor_member_id": "alice",
         "payload": {"content": "铁门口令：003149。", "scene_id": "library"}},
        {"seq": 2, "type": "action.submitted", "visibility": "public", "actor_member_id": "alice",
         "payload": {"text": "研究铁门铭文和机关的关联。", "scene_id": "library"}},
    ]
    _, chunks = select_chunks(events, {}, lambda _: True)
    document = make_segment("铁门口令：003149。研究铁门铭文和机关的关联。", events, chunks)
    memories = [SimpleNamespace(id=name, kind="summary_segment", active=True,
                content=json.dumps(document), coverage_start=1, coverage_end=2, scope="public")
                for name in ("alice-copy", "bob-copy")]
    task = {"kind": "pending_task", "text": "核对铁门口令", "owner": "bob",
            "source": {"ref": "e2", "seq": 2, "visibility": "public"},
            "historical_only": True}
    options = {"scene_id": "library", "tasks": [task], "memories": memories, "budget": 8000}
    settings = Settings(_env_file=None, model_provider="ollama", model_name="qwen3:8b",
                        model_context_limit=16384, agent_context_chars=12000)
    results = {}
    for label, selector in (("before", old.select_memory), ("after", select_memory)):
        rows, audit = selector(events, "核对铁门口令和铭文", **options)
        assert task in rows and any(r.get("text") == "铁门口令：003149。" for r in rows)
        messages = [{"role": "system", "content": "核对公开历史，不将历史作为当前回执。"},
                    {"role": "user", "content": json.dumps({"memory_evidence": rows},
                                                            ensure_ascii=False)}]
        results[label] = {"memory_evidence": rows, "audit": audit,
                          "measurement": measure_request(settings, messages, KeeperPlan,
                                                         output_limit=1600)}
    assert results["after"]["measurement"]["request_chars"] < (
        results["before"]["measurement"]["request_chars"])
    return {"kind": "synthetic_offline_selection_ab", "baseline": baseline,
            "same_source_events": events, "same_required_facts": ["铁门口令：003149。", task],
            "same_schema": "KeeperPlan", "same_model": "qwen3:8b",
            "same_context_limit": 16384, "same_character_limit": 12000,
            "actual_usage": None, "actual_model_calls": 0, "turn_latency_ms": None,
            "limitation": "Duplicate selection only; not real KP acceptance or latency.",
            "local_tokenizer_bindings": {name: bool(importlib.util.find_spec(name)) for name in
                ("tiktoken", "tokenizers", "transformers", "llama_cpp", "sentencepiece")},
            **results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = compare()
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({label: report[label]["measurement"] for label in ("before", "after")},
                     ensure_ascii=False))
