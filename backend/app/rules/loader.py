from functools import lru_cache
from pathlib import Path

import yaml

from app.rules.schemas import RuleSet

DEFINITIONS_DIR = Path(__file__).parent / "definitions"


def load_rulesets(directory: Path = DEFINITIONS_DIR) -> dict[str, RuleSet]:
    rulesets = {}
    for path in sorted(directory.glob("*.yaml")):
        ruleset = RuleSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if ruleset.id in rulesets:
            raise ValueError(f"重复的规则集 ID：{ruleset.id}")
        rulesets[ruleset.id] = ruleset
    if not rulesets:
        raise ValueError("未找到角色创建规则配置")
    return rulesets


def archived_ruleset(ruleset_id: str, version: str) -> RuleSet | None:
    # Match metadata, never interpolate user-controlled IDs into a file path.
    for path in (DEFINITIONS_DIR / "legacy").glob("*.yaml"):
        rule = RuleSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if (rule.id, rule.version) == (ruleset_id, version):
            return rule
    return None


@lru_cache(maxsize=32)
def runtime_ruleset(ruleset_id: str, version: str) -> RuleSet | None:
    """Immutable published versions for read-only check/context lookups."""
    current = load_rulesets().get(ruleset_id)
    if current and current.version == version:
        return current
    return archived_ruleset(ruleset_id, version)
