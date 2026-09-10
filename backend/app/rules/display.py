"""Public check labels are resolved from the character's ruleset, never the model."""

import re
from functools import lru_cache

from app.rules.loader import load_rulesets

DIFFICULTIES = {"regular": "普通", "hard": "困难", "extreme": "极难"}
LEVELS = {
    "critical": "大成功",
    "fumble": "大失败",
    "extreme": "极难成功",
    "hard": "困难成功",
    "regular": "普通成功",
    "failure": "失败",
}


@lru_cache
def display_names():
    names = {}
    for ruleset in load_rulesets().values():
        for kind, items in (("skill", ruleset.skills), ("attribute", ruleset.attributes)):
            for item in items:
                label = item.display_name
                names[(ruleset.id, kind, item.key)] = {
                    "skill_id": item.key,
                    "display_name": re.sub(r"\s+[A-Z]{3}$", "", label),
                    "ruleset_id": ruleset.id,
                    "english_name": item.key.replace("_", " ").title(),
                }
    return names


def resolve_check_name(name, kind="skill", ruleset_id="coc7-character-creation"):
    return display_names().get(
        (ruleset_id, kind, name),
        {
            "skill_id": name,
            "display_name": "技能" if kind == "skill" else "属性",
            "ruleset_id": ruleset_id,
            "english_name": None,
        },
    )


def check_display(document):
    label = (
        document.get("display_name")
        or resolve_check_name(
            document["name"],
            document["kind"],
            document.get("ruleset_id", "coc7-character-creation"),
        )["display_name"]
    )
    difficulty = DIFFICULTIES[document["difficulty"]]
    result = document.get("result")
    text = f"{label}检定（{difficulty}）"
    if result:
        text += (
            f"：骰点 {result['total']}，目标 {result['threshold']}，"
            f"{LEVELS[result['level']]}，{'通过' if result['passed'] else '未通过'}。"
        )
    elif document.get("settlement"):
        progress = document["settlement"]
        raw = progress["original_result"]
        stage = {
            "choice": "等待玩家选择",
            "push_review": "等待主机核准孤注",
            "push_roll": "等待确认孤注掷骰",
            "consequence": "等待主机处理后果",
        }
        text += f"：原骰点 {raw['total']}，{LEVELS[raw['level']]}；"
        text += stage.get(progress["stage"], "待结算") + "。"
    else:
        text += f"，技能值 {document['value']}，等待掷骰。"
    return {
        "display_name": label,
        "difficulty_display": difficulty,
        "display_text": text,
        "result": {
            **result,
            "difficulty": document["difficulty"],
            "bonus_dice": document["bonus_dice"],
            "penalty_dice": document["penalty_dice"],
            "display_text": text,
        }
        if result
        else None,
    }
