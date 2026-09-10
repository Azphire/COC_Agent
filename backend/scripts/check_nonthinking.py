"""Six sequential local-only comparisons; no game mutation or model installation."""

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.action_runtime import NARRATION_INSTRUCTION, PLAN_INSTRUCTION  # noqa: E402
from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan  # noqa: E402
from app.agents.schemas import SummaryOutput  # noqa: E402
from app.memory.recovery import SUMMARY_INSTRUCTION  # noqa: E402
from app.models.ollama import generation_schema  # noqa: E402


async def main(directory, only_summary=False):
    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / ".cache").resolve()):
        raise ValueError("isolated acceptance directory required")
    connection = sqlite3.connect((directory / "game.db").as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    room_id = json.loads((directory / "report.json").read_text(encoding="utf-8"))["room_id"]
    rows = list(
        connection.execute(
            "SELECT * FROM agent_runs WHERE room_id=? ORDER BY created_at",
            (room_id,),
        )
    )
    connection.close()
    plan = next(
        r
        for r in rows
        if r["graph_node"] == "plan_keeper_action"
        and json.loads(r["context"]).get("check_requirements")
        and "模糊细节"
        in json.loads(r["context"]).get("triggering_action", {}).get("payload", {}).get("text", "")
    )
    narrator = next(r for r in rows if r["graph_node"] == "generate_keeper_narration")
    summary = next(r for r in rows if "summary" in r["graph_node"])
    cases = [
        ("plan", plan, KeeperPlan, PLAN_INSTRUCTION),
        ("narration", narrator, KeeperNarration, NARRATION_INSTRUCTION),
        (
            "summary",
            summary,
            SummaryOutput,
            SUMMARY_INSTRUCTION,
        ),
    ]
    report = {"model": "qwen3:8b", "context_limit": 8192, "output_limit": 1100, "cases": []}
    if only_summary:
        report = json.loads((directory / "nonthinking-comparison.json").read_text(encoding="utf-8"))
        report["cases"] = [c for c in report["cases"] if c["stage"] != "summary"]
        cases = [c for c in cases if c[0] == "summary"]
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        timeout=240,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        report["ollama_version"] = (await client.get("/api/version")).json()
        for stage, row, schema, instruction in cases:
            context = json.loads(row["context"])
            for think in (False, True):
                started = time.monotonic()
                response = await client.post(
                    "/api/chat",
                    json={
                        "model": "qwen3:8b",
                        "stream": False,
                        "think": think,
                        "keep_alive": "5m",
                        "messages": [
                            {"role": "system", "content": instruction},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                        ],
                        "format": generation_schema(schema.model_json_schema()),
                        "options": {"temperature": 0.3, "num_ctx": 8192, "num_predict": 1100},
                    },
                )
                response.raise_for_status()
                data = response.json()
                item = {
                    "stage": stage,
                    "think": think,
                    "source_run_id": row["id"],
                    "seconds": round(time.monotonic() - started, 2),
                    "done_reason": data.get("done_reason"),
                    "output_tokens": data.get("eval_count"),
                    "thinking_chars": len(data.get("message", {}).get("thinking", "")),
                    "schema_valid": False,
                    "quality_valid": False,
                }
                content = data.get("message", {}).get("content", "")
                try:
                    output = schema.model_validate_json(content)
                    item["schema_valid"] = data.get("done_reason") != "length"
                    if stage == "plan":
                        expected = context["check_requirements"][0]
                        check = output.proposed_check
                        item["quality_valid"] = bool(
                            output.parsed_intent.type == "investigate"
                            and check
                            and check.target_entity_id == expected["entity_id"]
                            and check.name == expected["successful_check"]["name"]
                            and check.uncertainty
                            and check.success_effect
                            and check.failure_consequence
                            and not output.proposed_transition_id
                        )
                    elif stage == "narration":
                        options = context.get("PUBLIC_CLAIM_OPTIONS", [])
                        item["quality_valid"] = (
                            bool(output.grounded_claims)
                            and all(
                                any(
                                    c.statement == o["statement"]
                                    and c.entity_ids == o.get("entity_ids", [])
                                    for o in options
                                )
                                for c in output.grounded_claims
                            )
                            and not re.search(r"[a-z]+_[a-z_]+", output.public_narration)
                        )
                    else:
                        ids = re.findall(r"(?:event|事件|seq)[ #:=：]*(\d+)", output.content)
                        item["quality_valid"] = bool(output.content.strip()) and set(
                            map(int, ids)
                        ) <= {e["seq"] for e in context["events"]}
                    item["output"] = output.model_dump(mode="json")
                except ValueError:
                    item["error"] = "invalid_or_truncated_structured_output"
                report["cases"].append(item)
                (directory / "nonthinking-comparison.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(json.dumps({k: v for k, v in item.items() if k != "output"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--only-summary", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.directory, args.only_summary))
