import random

import pytest

from app.dice.service import DiceService, parse_dice


@pytest.mark.parametrize(
    "formula,count,sides,modifier",
    [
        ("1d100", 1, 100, 0),
        ("3d6", 3, 6, 0),
        ("2d6+6", 2, 6, 6),
        ("2d8-3", 2, 8, -3),
        ("100d1000+10000", 100, 1000, 10000),
    ],
)
def test_legal_dice(formula, count, sides, modifier):
    parsed = parse_dice(formula)
    assert (parsed.count, parsed.sides, parsed.modifier) == (count, sides, modifier)


@pytest.mark.parametrize(
    "formula",
    [
        "",
        "d6",
        "0d6",
        "101d6",
        "1d1",
        "1d0",
        "1d1001",
        "1d6+10001",
        "1d6-10001",
        "1d6*5",
        "(2d6+6)*5",
        "2d6+1d4",
        "-1d6",
        "1d6\n",
        "1d6;print(1)",
        "__import__('os')",
        "1 d6",
        "1D6",
    ],
)
def test_illegal_dice(formula):
    with pytest.raises(ValueError):
        parse_dice(formula)


def test_seed_and_audit_record():
    first = DiceService(random.Random(42)).roll("3d6+2", "strength")
    second = DiceService(random.Random(42)).roll("3d6+2", "strength")
    assert first.dice == second.dice == [6, 1, 1]
    assert first.modifier == 2 and first.total == 10
    assert first.attribute == "strength" and first.formula == "3d6+2"
    assert first.rolled_at.utcoffset().total_seconds() == 0
    assert first.source == "system" and first.id != second.id
    assert isinstance(DiceService().rng, random.SystemRandom)
