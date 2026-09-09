import random
import re
from dataclasses import dataclass

from app.domain.character import RollRecord


@dataclass(frozen=True)
class DiceExpression:
    count: int
    sides: int
    modifier: int


def parse_dice(formula: str) -> DiceExpression:
    match = re.fullmatch(r"([1-9][0-9]{0,2})d([1-9][0-9]{0,3})([+-][0-9]{1,5})?", formula)
    if match is None:
        raise ValueError("骰式只支持 NdM、NdM+K 或 NdM-K")
    count, sides = int(match[1]), int(match[2])
    modifier = int(match[3] or 0)
    if not (1 <= count <= 100 and 2 <= sides <= 1000 and abs(modifier) <= 10_000):
        raise ValueError("骰式超出范围：数量 1–100，面数 2–1000，修正值 ±10000")
    return DiceExpression(count, sides, modifier)


class DiceService:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng if rng is not None else random.SystemRandom()

    def roll(self, formula: str, attribute: str) -> RollRecord:
        expression = parse_dice(formula)
        dice = [self.rng.randint(1, expression.sides) for _ in range(expression.count)]
        return RollRecord(
            attribute=attribute,
            formula=formula,
            dice=dice,
            modifier=expression.modifier,
            total=sum(dice) + expression.modifier,
        )
