"""One request envelope for selection, transport auditing and token admission.

No tokenizer is downloaded. Unknown model vocabularies use a deliberately
conservative estimate, labelled as such, with an upward-only usage calibration.
Character limits are a separate application constraint, never token units.
"""

import json
import math
import re
from collections import Counter
from functools import lru_cache

from app.models.base import ModelError

_calibration = {}


def _model_key(settings):
    return (settings.model_provider, settings.model_base_url, settings.model_name,
            settings.model_output_mode)


def context_character_budget(settings):
    return settings.agent_context_chars


def compact_schema(schema):
    """Remove unreachable definitions and display-only titles, preserving constraints."""
    def clean(value, names=False):
        if isinstance(value, dict):
            if names:
                return {k: clean(v) for k, v in value.items()}
            return {k: (v if k in {"const", "enum", "examples"} else clean(
                v, names=k in {"properties", "$defs", "patternProperties", "dependentSchemas"},
            )) for k, v in value.items() if k not in {"title", "default"}}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    schema = clean(schema)
    definitions = schema.pop("$defs", {})
    needed = set()

    def collect(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                if name not in needed and name in definitions:
                    needed.add(name)
                    collect(definitions[name])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(schema)
    if needed:
        schema["$defs"] = {k: v for k, v in definitions.items() if k in needed}
    # Compound checks repeat the same complete property grammars for each
    # alternative. JSON Schema references preserve their exact constraints.
    counts, values = Counter(), {}

    def count(value):
        if isinstance(value, dict):
            kind = value.get("type")
            is_schema = isinstance(kind, str) or (
                isinstance(kind, list) and all(isinstance(item, str) for item in kind)
            ) or any(isinstance(value.get(key), list) for key in ("anyOf", "oneOf", "enum"))
            if is_schema:
                encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"))
                if len(encoded) > 80:
                    counts[encoded] += 1
                    values[encoded] = value
            for key, child in value.items():
                if key not in {"const", "enum", "examples"}:
                    count(child)
        elif isinstance(value, list):
            for child in value:
                count(child)

    count(schema)
    shared = {}
    for encoded, n in counts.items():
        if n > 1 and n * (len(encoded) - 35) > len(encoded) + 35:
            name = "BudgetShared" + str(len(shared))
            while name in definitions:
                name += "_"
            shared[encoded] = name

    def replace(value, own=None):
        if isinstance(value, dict):
            encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if encoded in shared and encoded != own:
                return {"$ref": "#/$defs/" + shared[encoded]}
            return {k: v if k in {"const", "enum", "examples"} else replace(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return value

    schema = replace(schema)
    if shared:
        schema.setdefault("$defs", {}).update({name: replace(values[encoded], own=encoded)
                                              for encoded, name in shared.items()})
    return schema


def schema_envelope(messages, response_schema, tools, *, provider, output_mode):
    """Match the provider's actual schema/tools placement, including JSON mode."""
    from app.models.ollama import generation_schema

    result = {"messages": [dict(m) for m in messages]}
    if tools:
        result["tools"] = [dict(t) for t in tools]
    if response_schema is not None:
        schema = compact_schema(generation_schema(
            response_schema.model_json_schema()
            if isinstance(response_schema, type) else dict(response_schema)
        ))
        if provider == "ollama":
            result["format"] = schema
        elif output_mode == "json_object":
            result["response_format"] = {"type": "json_object"}
            result["messages"].insert(0, {
                "role": "system",
                "content": "Return only a JSON object matching this schema. "
                "Do not output reasoning. JSON schema: "
                + json.dumps(schema, ensure_ascii=False),
            })
        else:
            from app.models.openai_compatible import strict_schema

            result["response_format"] = {
                "type": "json_schema", "json_schema": {
                    "name": "model_response", "strict": True, "schema": strict_schema(schema),
                },
            }
    return result


@lru_cache(maxsize=16)
def _tokenizer(model):
    # Use only an already installed and cached tokenizer for a recognized model.
    # encoding_for_model can fetch data; inspecting installed encoding instances
    # avoids introducing network/download work in a game turn.
    try:
        import tiktoken
        from tiktoken.registry import ENCODINGS

        name = tiktoken.model.encoding_name_for_model(model)
        return ENCODINGS.get(name)
    except (ImportError, KeyError):
        return None


def _estimate(text):
    ascii_chars = sum(len(s) for s in re.findall(r"[\x00-\x7f]+", text))
    cjk = sum("\u2e80" <= c <= "\u9fff" for c in text)
    other = sum(len(c.encode("utf-8")) for c in text if ord(c) > 127
                and not "\u2e80" <= c <= "\u9fff")
    return math.ceil(ascii_chars / 3 + cjk * 1.25 + other)


def measure_request(settings, messages, response_schema=None, tools=None, output_limit=None):
    envelope = schema_envelope(
        messages, response_schema, tools, provider=settings.model_provider,
        output_mode=settings.model_output_mode,
    )
    text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    tokenizer = _tokenizer(settings.model_name)
    method = "cached_model_tokenizer" if tokenizer else "conservative_estimate"
    raw = len(tokenizer.encode(text)) if tokenizer else _estimate(text)
    key = _model_key(settings)
    calibration = _calibration.get(key, {"factor": 1.0, "samples": 0})
    # Chat framing / provider wrappers are not described by a vocabulary alone.
    input_tokens = math.ceil(raw * calibration["factor"]) + 64 + 8 * len(envelope["messages"])
    reserve = output_limit or settings.model_output_limit
    return {
        "method": method, "raw_estimate": raw, "input_tokens": input_tokens,
        "output_reserve": reserve, "total_tokens": input_tokens + reserve,
        "token_limit": settings.model_context_limit,
        "within_token_limit": input_tokens + reserve <= settings.model_context_limit,
        "request_chars": len(text),
        "messages_chars": len(json.dumps(envelope["messages"], ensure_ascii=False)),
        "system_chars": sum(len(m.get("content", "")) for m in envelope["messages"]
                            if m.get("role") == "system" and isinstance(m.get("content"), str)),
        "schema_tools_chars": len(json.dumps({k: v for k, v in envelope.items()
                                               if k != "messages"}, ensure_ascii=False)),
        "calibration_factor": calibration["factor"], "calibration_samples": calibration["samples"],
    }


def calibrate_usage(settings, measured, usage):
    actual = (usage or {}).get("input") or (usage or {}).get("prompt_tokens")
    if not isinstance(actual, int) or actual <= 0:
        return
    key = _model_key(settings)
    old = _calibration.get(key, {"factor": 1.0, "samples": 0})
    _calibration[key] = {
        "factor": max(old["factor"], actual / max(1, measured["raw_estimate"]) * 1.05),
        "samples": old["samples"] + 1,
    }
    measured["actual_input_tokens"] = actual
    measured["estimate_error_tokens"] = measured["input_tokens"] - actual


def require_request_fit(measured):
    if not measured["within_token_limit"]:
        raise ModelError(
            "当前请求（含系统指令、结构及输出预留）超过模型上下文预算；"
            "请增大模型上下文或缩小当前必要资料"
        )
