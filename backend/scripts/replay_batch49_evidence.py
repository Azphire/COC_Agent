"""Read-only replay of the frozen first failure; never rewrite batch 48."""

import hashlib
import json
from pathlib import Path

from app.agents.adjudication_schemas import KeeperNarration, TeammateDecision
from app.agents.generation_contracts import generation_contract, restore_output

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/prepared/batch-48/real-01/investigation-state-01.json"


def document(value):
    return json.loads(value) if isinstance(value, str) else value


def replay():
    state = document(SOURCE.read_text(encoding="utf-8-sig"))
    room = state["launch_drafts"][0]["room_id"]
    result = {"source": str(SOURCE), "sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "calls": [], "new_model_calls": 0}
    for run in state["agent_runs"]:
        if run["room_id"] != room or run["graph_node"] not in {
            "decide_teammates", "generate_keeper_narration",
        }:
            continue
        context = document(run["context"])
        schema = TeammateDecision if run["graph_node"] == "decide_teammates" else KeeperNarration
        for call in state["agent_model_calls"]:
            if call["run_id"] != run["id"]:
                continue
            call = document(call["document"])
            output = call.get("generated_output") or call.get("raw_output")
            row = {"run": run["id"], "schema": schema.__name__, "raw_output": output,
                   "fact_evidence": context.get("fact_evidence"),
                   "memory_evidence": context.get("memory_evidence"),
                   "questions": context.get("response_brief", {}).get("questions")}
            try:
                row["restored"] = restore_output(
                    generation_contract(schema, context).model_validate(output), schema, context,
                ).model_dump(mode="json")
            except Exception as error:
                row["error"] = str(error)
            result["calls"].append(row)
    assert result["sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    return result


if __name__ == "__main__":
    import sys

    destination = ROOT / "data/prepared/batch-49" / sys.argv[1]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(replay(), stream, ensure_ascii=False, indent=2)
    print(destination)
