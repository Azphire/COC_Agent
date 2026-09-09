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
