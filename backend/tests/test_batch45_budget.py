"""Stable-schema caching must preserve complete request counts and constraints."""

import json

import pytest
from pydantic import BaseModel

from app.agents.adjudication_schemas import KeeperPlan
from app.config import Settings
from app.models import budget


class FixedAnswer(BaseModel):
    answer: str


@pytest.mark.parametrize("provider,mode", [
    ("ollama", "json_schema"), ("openai_compatible", "json_object"),
    ("openai_compatible", "json_schema"),
])
def test_cached_schema_measurement_equals_whole_envelope(provider, mode, monkeypatch):
    monkeypatch.setattr(budget, "_tokenizer", lambda _: None)
    budget._stable_estimate_units.cache_clear()
    settings = Settings(_env_file=None, model_provider=provider, model_output_mode=mode,
                        model_name="batch45-cache")
    tools = [{"type": "function", "function": {"name": "inspect", "description": "检查🗝️"}}]
    schema = (FixedAnswer if provider == "openai_compatible" and mode == "json_schema"
              else KeeperPlan)
    for text in ["口令：07-3149。", "再询问钥匙归属，甲→乙；🎲"]:
        messages = [{"role": "user", "content": text}]
        result = budget.measure_request(settings, messages, schema, tools)
        envelope = budget.schema_envelope(messages, schema, tools,
                                          provider=provider, output_mode=mode)
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        assert result["raw_estimate"] == budget._estimate(encoded)
        assert result["method"] == "conservative_estimate"
    assert result["schema_measurement_cache_hits"] >= 1


def test_cached_transport_schemas_cannot_be_mutated_by_a_previous_request():
    messages = [{"role": "user", "content": "检查"}]
    schema = {"type": "object", "properties": {"code": {"type": "string", "const": "003149"}},
              "required": ["code"]}
    first = budget.schema_envelope(messages, schema, None, provider="ollama",
                                   output_mode="json_schema")
    first["format"]["properties"]["code"]["const"] = "999999"
    second = budget.schema_envelope(messages, schema, None, provider="ollama",
                                    output_mode="json_schema")
    assert second["format"]["properties"]["code"]["const"] == "003149"
    schema["properties"]["code"]["const"] = "new-source-code"
    changed = budget.schema_envelope(messages, schema, None, provider="ollama",
                                     output_mode="json_schema")
    assert changed["format"]["properties"]["code"]["const"] == "new-source-code"
