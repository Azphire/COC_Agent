"""Read-only review probes for fact binding within a shared selected source."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_batch46_coverage_normalization import mixed_answer

from app.agents.narration_coverage import (
    coverage_audit,
    freeze_verified_fact,
    normalize_coverage_spans,
    retained_fact_errors,
)

context, raw = mixed_answer()
brief = context["response_brief"]
original, _ = normalize_coverage_spans(raw, brief)
frozen = [freeze_verified_fact(r, brief) for r in coverage_audit(original, brief)["verified"]]
probes = []
for text in [
    "木箱表面留着划痕，铁门上有灰尘。",
    "建议检查已被擦干净的木箱。",
    "下一步可以核对铁门，铁门上不存在划痕。",
    "这些已经是准确事实。",
    "周女士早先估计展品少了九件，林先生当时在场。",
]:
    candidate = dict(original)
    if text.startswith("木箱"):
        candidate["public_narration"] = text + raw["public_narration"].split("。", 1)[1]
    else:
        candidate["public_narration"] += text
    effective, changes = normalize_coverage_spans(candidate, brief)
    probes.append({"extra": text, "effective": effective, "audit": coverage_audit(effective, brief),
                   "retention_errors": retained_fact_errors(effective, brief, frozen)})
conditional_brief = {
    "answer_requirements": [{"id": "door", "kind": "observation", "text": "铁门",
                             "source_ids": ["condition"]}],
    "answer_sources": [{"id": "condition", "kind": "observation",
                        "text": "只有电源接通，铁门才可以打开。"}],
}
conditional = {"public_narration": "电源接通，铁门可以打开。", "answer_coverage": [{
    "requirement_id": "door", "source_id": "condition", "body_quote": "铁门",
    "source_quote": "wrong", "status": "answered",
}]}
effective, _ = normalize_coverage_spans(conditional, conditional_brief)
probes.append({"extra": "conditional premise becomes unconditional assertion",
               "effective": effective, "audit": coverage_audit(effective, conditional_brief),
               "retention_errors": []})
print(json.dumps(probes, ensure_ascii=False, indent=2))
