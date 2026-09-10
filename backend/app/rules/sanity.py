"""CoC7 1907, PDF 77, 130-135, 139-140. No model-selected amounts."""

import re

from app.dice.service import parse_dice

RULE_SOURCE = "coc7-1907:bc8455d443ec4c64ae86ec2d6e6637889eff2cd59a4cc4686076e95b130d08dc"
REALTIME_SYMPTOMS = (
    "失忆",
    "假性残疾",
    "暴力倾向",
    "偏执",
    "人际依赖",
    "昏厥",
    "逃避行为",
    "歇斯底里",
    "恐惧",
    "躁狂",
)
SUMMARY_SYMPTOMS = (
    "失忆",
    "被窃",
    "遍体鳞伤",
    "暴力倾向",
    "思想与信念改变",
    "重要之人",
    "被收容",
    "逃避行为",
    "恐惧",
    "躁狂",
)


def loss_bounds(formula: str) -> tuple[int, int]:
    if re.fullmatch(r"0|[1-9][0-9]{0,2}", formula):
        value = int(formula)
        if value <= 100:
            return value, value
    expr = parse_dice(formula)
    low, high = expr.count + expr.modifier, expr.count * expr.sides + expr.modifier
    if expr.count > 10 or expr.sides > 100 or low < 0 or high > 100:
        raise ValueError("SAN 损失骰式限 1–10 颗、2–100 面，所有结果须在 0–100")
    return low, high


def judge_sanity(value: int, total: int) -> dict:
    if type(value) is not int or not 0 <= value <= 99 or not 1 <= total <= 100:
        raise ValueError("SAN 检定值或骰点不合法")
    # §8.1 defines a binary comparison; §5.5 supplies fumble boundaries.
    passed = total <= value
    fumble = total == 100 or value < 50 and total >= 96
    return dict(
        total=total,
        threshold=value,
        passed=passed,
        level="fumble" if fumble else "regular" if passed else "failure",
        outcome="fumble" if fumble else "success" if passed else "failure",
    )


def insanity_trigger(san: int, loss: int, day_start: int, day_loss: int, kind: str) -> str:
    if san == 0:
        return "permanent"
    if loss and day_start and day_loss * 5 >= day_start:
        return "indefinite"
    if loss and kind in {"temporary", "indefinite"}:
        return kind
    return "int" if loss >= 5 else "none"
