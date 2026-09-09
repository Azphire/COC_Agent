from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.domain.character import CharacterDraft
from app.rules.engine import calculate, recalculate
from app.rules.loader import load_rulesets
from app.rules.schemas import CalculationRule, RuleSet


@pytest.mark.parametrize(
    "spec,expected",
    [
        ({"operation": "copy", "source": "a"}, 5),
        ({"operation": "multiply", "source": "a", "factor": 2}, 10),
        ({"operation": "sum", "sources": ["a", "b"]}, 12),
        ({"operation": "average", "sources": ["a", "b"]}, 6),
        (
            {
                "operation": "table_lookup",
                "source": "a",
                "lookup_table": [
                    {"upper_bound": 5, "value": -1},
                    {"upper_bound": None, "value": 2},
                ],
            },
            -1,
        ),
        (
            {
                "operation": "table_lookup",
                "source": "b",
                "lookup_table": [
                    {"upper_bound": 5, "value": "0"},
                    {"upper_bound": None, "value": "+1d4"},
                ],
            },
            "+1d4",
        ),
    ],
)
def test_calculation_operations(spec, expected):
    assert calculate(CalculationRule.model_validate(spec), {"a": 5, "b": 7}) == expected


@pytest.mark.parametrize(
    "value,rounding,expected",
    [
        (2.5, "none", 2.5),
        (2.5, "floor", 2),
        (2.5, "ceil", 3),
        (2.5, "nearest", 3),
        (-2.5, "floor", -3),
        (-2.5, "ceil", -2),
        (-2.5, "nearest", -3),
    ],
)
def test_rounding(value, rounding, expected):
    rule = CalculationRule(operation="copy", source="a", rounding=rounding)
    assert calculate(rule, {"a": value}) == expected


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["attributes"][0].update(random_formula="3d6*5"),
        lambda data: data["attributes"][0].update(minimum=20),
        lambda data: data["attributes"][0].update(point_buy_cost=0),
        lambda data: data["attributes"].append(deepcopy(data["attributes"][0])),
        lambda data: data["derived_values"][0].update(sources=["unknown"]),
        lambda data: data["derived_values"][0].update(operation="eval"),
        lambda data: data["derived_values"][0].update(sources=["endurance"]),
        lambda data: data["derived_values"][1].update(factor=float("inf")),
        lambda data: data["derived_values"][2]["lookup_table"].reverse(),
        lambda data: data["occupations"][0].update(fixed_skills=["missing"]),
        lambda data: data["occupations"][0].update(required_selection_count=20),
        lambda data: data.update(verification_status="verified"),
        lambda data: data.update(edition="coc7"),
        lambda data: data.update(unexpected="value"),
    ],
)
def test_invalid_rule_configuration(development, mutation):
    data = development.model_dump()
    mutation(data)
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


@pytest.mark.parametrize(
    "spec",
    [
        {"operation": "copy"},
        {"operation": "sum", "sources": []},
        {"operation": "multiply", "source": "a"},
        {"operation": "copy", "source": "a", "factor": 2},
        {"operation": "movement", "sources": ["a"]},
        {
            "operation": "table_lookup",
            "source": "a",
            "lookup_table": [
                {"upper_bound": 5, "value": 1},
                {"upper_bound": 5, "value": 2},
                {"upper_bound": None, "value": 3},
            ],
        },
    ],
)
def test_invalid_operation_shape(spec):
    with pytest.raises(ValidationError):
        CalculationRule.model_validate(spec)


def test_yaml_loader_is_safe_and_rejects_duplicates(tmp_path, development):
    (tmp_path / "unsafe.yaml").write_text("!!python/object/apply:os.system ['echo BAD']")
    import yaml

    with pytest.raises(yaml.YAMLError):
        load_rulesets(tmp_path)
    (tmp_path / "unsafe.yaml").write_text(development.model_dump_json(), encoding="utf-8")
    (tmp_path / "duplicate.yaml").write_text(development.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_rulesets(tmp_path)


def test_unspent_and_multiple_errors_are_reported(development):
    data = development.model_dump()
    data["points"]["allow_unspent_points"] = False
    rules = RuleSet.model_validate(data)
    draft = CharacterDraft(
        ruleset_id=rules.id,
        ruleset_version=rules.version,
        creation_mode="point_buy",
        attributes={"vigor": {"value": 1}, "insight": {"value": 13}},
        occupation="researcher",
        selected_occupation_skills=["craft", "craft"],
        interest_skills={"observe": {"points": -2}},
    )
    recalculate(draft, rules)
    codes = {issue.code for issue in draft.validation.issues}
    assert {"required", "range", "unspent", "negative", "selection", "duplicate"} <= codes
    assert not draft.validation.valid
