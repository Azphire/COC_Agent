"""Real local model probes using recorded contexts; no game state or dice changes."""

import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

from app.agents.action_runtime import (
    NARRATION_INSTRUCTION,
    PLAN_INSTRUCTION,
    TEAMMATE_INSTRUCTION,
    compact_planning_prose,
    generation_prompt,
)
from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan, TeammateDecision
from app.agents.generation_contracts import generation_contract, restore_output, utterance_clauses
from app.agents.model import AgentModelClient
from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data/prepared/changan/batch-34/run-20260916T211255Z"


async def main():
    directory = (ROOT / "data/prepared/changan/batch-35" / sys.argv[1]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    runs = json.loads((BASE / "agent-runs.json").read_text(encoding="utf-8"))
    settings = Settings(
        _env_file=None,
        data_dir=directory,
        model_context_limit=8192,
        model_timeout_seconds=240,
        model_name="qwen3:8b",
        model_settings_path=directory / "model-settings.json",
    )
    client = AgentModelClient(settings)
    cases = [(seq, None) for seq in (26, 55, 549, 704, 1013, 1142, 1236, 1270)]
    cases += [
        (1142, "我停在入口旁，扶着门框探头看里面，脚还留在这边。"),
        (549, "我背对刚才出来的6号车厢，沿另一头的门继续往前走。"),
    ]
    for seq, variant in cases:
        source = next(
            r
            for r in runs
            if r["graph_node"] == "plan_keeper_action"
            and r["context"].get("triggering_action", {}).get("seq") == seq
        )
        context = deepcopy(source["context"])
        raw = variant or context["triggering_action"]["payload"]["text"]
        context["triggering_action"]["payload"]["text"] = raw
        context["current_clauses"] = utterance_clauses(raw)
        context = compact_planning_prose(context, 6092)
        await probe(
            client,
            directory,
            str(seq) + ("-variant" if variant else ""),
            KeeperPlan,
            PLAN_INSTRUCTION,
            context,
        )
    for seq in (674, 761, 484):
        source = next(
            r
            for r in runs
            if r["graph_node"] == "generate_keeper_narration"
            and r["context"].get("triggering_action", {}).get("seq") == seq
        )
        context = deepcopy(source["context"])
        context["response_brief"]["recent_dialogue"] = [
            {"speaker": "npc", "text": "我摔伤了腿，没法站起来。"}
        ]
        await probe(
            client,
            directory,
            str(seq) + "-narration",
            KeeperNarration,
            NARRATION_INSTRUCTION,
            context,
        )
    source = next(
        r
        for r in runs
        if r["graph_node"] == "decide_teammates"
        and r["context"].get("triggering_action", {}).get("seq") == 704
    )
    context = deepcopy(source["context"])
    context["self_identity"] = {"name": "周岚", "occupation": "护士", "personality": "稳重直接"}
    await probe(client, directory, "704-teammate", TeammateDecision, TEAMMATE_INSTRUCTION, context)
    await client.close()


async def probe(client, directory, name, schema, instruction, context):
    record = {
        "name": name,
        "schema": schema.__name__,
        "context_limit": 8192,
        "mode": "real Ollama context replay; no tools executed",
        "context": context,
    }
    prompt = generation_prompt(context, schema)
    record["context_chars"] = len(json.dumps(prompt, ensure_ascii=False, separators=(",", ":")))
    record["application_budget"] = 6092
    record["budget_accepted"] = record["context_chars"] <= 6092
    try:
        if not record["budget_accepted"]:
            raise ValueError(
                "Context replay exceeds the application's 6092-character envelope; "
                "no model call made"
            )
        response, elapsed = await client.generate(
            [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            response_schema=generation_contract(schema, context),
            output_limit=1600 if schema is KeeperPlan else 900,
        )
        record.update(
            generated=response.structured.model_dump(mode="json"),
            elapsed_ms=elapsed,
            restored=restore_output(response.structured, schema, context).model_dump(mode="json"),
        )
    except Exception as error:
        record["error"] = str(error)
    record["calls"] = client.calls[:]
    client.calls.clear()
    (directory / (name + ".json")).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    value = record.get("restored", {})
    print(
        json.dumps(
            {
                "name": name,
                "chars": record["context_chars"],
                "error": record.get("error"),
                "focus": value.get("focus"),
                "intent": value.get("parsed_intent", {}).get("type"),
                "narration": value.get("public_narration"),
                "npc": value.get("npc_speech"),
                "decision": value if schema is TeammateDecision else None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
