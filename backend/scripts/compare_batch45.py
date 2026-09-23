"""Two isolated planning measurements from the same frozen, valid real turn.

Expand only selected historical references to their original public events, then
compare with the actual selected messages. Never executes either generated plan.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")


def source_context(messages, events, segments=()):
    by_seq = {e["seq"]: e for e in events}
    changed, selected_sources = [], set()

    def expand(value):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if key not in {"memory_evidence", "historical_memory"}:
                    result[key] = expand(child)
                    continue
                original, seen = [], set()
                for row in child:
                    source = row.get("source", {})
                    if row.get("kind") in {"pending_task", "short_term_goal"}:
                        original.append(row)
                        continue
                    seqs = [source["seq"]] if source.get("seq") is not None else []
                    if source.get("range"):
                        matching = []
                        for segment in segments:
                            chunks = segment.get("source_chunks", [])
                            signature = (
                                source.get("visibility"),
                                tuple(
                                    sorted(
                                        (c["seq"], c["start"], c["end"], c["digest"])
                                        for c in chunks
                                    )
                                ),
                            )
                            digest = hashlib.sha256(repr(signature).encode()).hexdigest()[:12]
                            if digest == source.get("hash"):
                                matching.append(chunks)
                        if not matching or any(chunks != matching[0] for chunks in matching):
                            raise ValueError("Cannot prove the selected segment's exact sources")
                        for chunk in matching[0]:
                            if chunk["start"] or chunk["end"] != chunk["total"]:
                                raise ValueError("Partial source chunks need a separate comparison")
                            event = by_seq[chunk["seq"]]
                            canonical = json.dumps(
                                event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                            )
                            if hashlib.sha256(canonical.encode()).hexdigest() != chunk["digest"]:
                                raise ValueError("A selected source changed since compression")
                            seqs.append(chunk["seq"])
                    if not seqs:
                        original.append(row)
                    for seq in seqs:
                        if seq in seen:
                            continue
                        event = by_seq[seq]
                        if event.get("visibility") != "public":
                            raise ValueError("Comparison cannot expand a private source")
                        original.append(
                            {
                                "historical_only": True,
                                "epistemic": row.get("epistemic"),
                                "source": {"ref": f"e{seq}", "seq": seq},
                                "original_event": event,
                            }
                        )
                        seen.add(seq)
                        selected_sources.add(seq)
                result[key] = original
                changed.append(key)
            return result
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    expanded = copy.deepcopy(messages)
    for message in expanded:
        if message["role"] != "user":
            continue
        try:
            context = json.loads(message["content"])
        except ValueError:
            continue
        if message["content"] == json.dumps(context, ensure_ascii=False, separators=(",", ":")):
            separators = (",", ":")
        elif message["content"] == json.dumps(context, ensure_ascii=False):
            separators = (", ", ": ")
        else:
            raise ValueError("Cannot preserve the actual message's JSON serialization")
        message["content"] = json.dumps(expand(context), ensure_ascii=False, separators=separators)
    if not changed or not selected_sources:
        raise ValueError("No actual selected historical sources to compare")
    return expanded, sorted(selected_sources)


async def compare(directory, output_name="input-comparison.json"):
    from app.config import Settings
    from app.models.budget import measure_request, require_request_fit, schema_envelope

    if Path(output_name).name != output_name or not output_name.endswith(".json"):
        raise ValueError("Measurement output must be a JSON filename in the source directory")
    result_path = directory / output_name
    if result_path.exists():
        raise ValueError("An existing measurement cannot be overwritten")
    preflight = json.loads((directory / "preflight.json").read_text("utf-8"))
    if preflight["status"] != "passed":
        raise ValueError("Preparation failure is not a valid comparison scene")
    config = json.loads((directory / "effective-config.json").read_text("utf-8"))
    endpoint = urlsplit(config["model_base_url"])
    if config["model_provider"] != "ollama" or endpoint.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("Only the existing loopback model can be measured")
    calls = json.loads((directory / "fixed-turn-calls.json").read_text("utf-8"))
    plan = next(c for c in calls if c["schema"] == "KeeperPlan" and c["attempt"] == 1)
    selected = plan["transmitted_messages"]
    events = json.loads((directory / "public-events.json").read_text("utf-8"))
    original, seqs = source_context(selected, events, preflight["segments"])
    settings = Settings(**config)
    # Freeze the same output grammar, reserve and options as the observed call.
    schema = plan["output_contract"]
    if (
        schema_envelope(
            selected, schema, None, provider="ollama", output_mode=config["model_output_mode"]
        )["format"]
        != schema
    ):
        raise ValueError("The measured schema differs from the actual request")
    reserve = plan["request_budget"]["output_reserve"]
    measurements = []
    result = {
        "mode": "same frozen turn: selected source originals versus actual selection",
        "required_facts": ["托马斯估计少了六本", "托马斯不知道具体书名"],
        "source_seqs": seqs,
        "measurement_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "same_config": config,
        "observed_live_budget": plan["request_budget"],
        "measurement_calibration": (
            "fresh estimator shared by both arms; live calibration recorded separately"
        ),
        "executed_plans": 0,
        "extra_acceptance_calls": 0,
        "measurements": measurements,
        "limitations": (
            "One ordered pair; no causal latency/window-size conclusion. "
            "Whole-turn DOM latency is measured separately."
        ),
    }
    for label, messages in (("selected_source_originals", original), ("selected_memory", selected)):
        text = json.dumps(messages, ensure_ascii=False)
        if not all(word in text for word in ("估计少了六本", "不知道具体书名")):
            raise ValueError("Required evidence absent; do not compare an unfair shorter prompt")
        measure = measure_request(settings, messages, schema, output_limit=reserve)
        require_request_fit(measure)
        entry = {"label": label, "messages": messages, "request_budget": measure}
        measurements.append(entry)
        write(result_path, result)
        request = {
            "model": settings.model_name,
            "messages": messages,
            "stream": False,
            "think": settings.model_think,
            "keep_alive": settings.model_keep_alive,
            "format": schema,
            "options": {
                "num_ctx": settings.model_context_limit,
                "num_predict": reserve,
                "temperature": settings.model_temperature,
            },
        }
        started = time.monotonic()
        async with httpx.AsyncClient(
            trust_env=False, timeout=settings.model_timeout_seconds
        ) as client:
            response = await client.post(
                f"{endpoint.scheme}://{endpoint.netloc}/api/chat", json=request
            )
            response.raise_for_status()
            raw = response.json()
        result["extra_acceptance_calls"] += 1
        actual = raw.get("prompt_eval_count")
        entry.update(
            seconds=time.monotonic() - started,
            actual_input_tokens=actual,
            actual_output_tokens=raw.get("eval_count"),
            raw_response=raw,
            estimate_error_tokens=measure["input_tokens"] - actual if actual else None,
        )
        write(result_path, result)
    result["input_reduction_tokens"] = (
        measurements[0]["actual_input_tokens"] - measurements[1]["actual_input_tokens"]
    )
    result["messages_reduction_chars"] = (
        measurements[0]["request_budget"]["messages_chars"]
        - measurements[1]["request_budget"]["messages_chars"]
    )
    write(result_path, result)
    print(json.dumps({k: v for k, v in result.items() if k != "measurements"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output-name", default="input-comparison.json")
    args = parser.parse_args()
    asyncio.run(compare(args.directory.resolve(), args.output_name))
