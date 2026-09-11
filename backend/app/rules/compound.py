"""Noncombat opposed and shared-roll checks, local 1907 PDF 78–80, 85."""

from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from app.domain.character import DomainModel
from app.rules.checks import judge

Value = Annotated[StrictInt, Field(ge=0, le=999)]
Name = Annotated[str, Field(min_length=1, max_length=80)]
RANK = {"fumble": -1, "failure": 0, "regular": 1, "hard": 2, "extreme": 3, "critical": 4}


class NPCCheckStats(DomainModel):
    attributes: dict[Name, Value] = Field(default_factory=dict, max_length=20)
    skills: dict[Name, Value] = Field(default_factory=dict, max_length=80)
    source: str = Field(min_length=1, max_length=300)
    page: Annotated[StrictInt, Field(ge=1)] | None = None


class OpposedCheck(DomainModel):
    opponent_member_id: str | None = Field(default=None, max_length=80)
    opponent_npc_id: str | None = Field(default=None, max_length=80)
    kind: Literal["attribute", "skill"] = "skill"
    name: Name
    bonus_dice: Annotated[StrictInt, Field(ge=0, le=2)] = 0
    penalty_dice: Annotated[StrictInt, Field(ge=0, le=2)] = 0

    @model_validator(mode="after")
    def one_opponent(self):
        if bool(self.opponent_member_id) == bool(self.opponent_npc_id):
            raise ValueError("对抗必须且只能指定一个成员或 NPC 对手")
        return self


class CombinedCheck(DomainModel):
    name: Name
    difficulty: Literal["regular", "hard", "extreme"] = "regular"
    # Explicit before the roll; there is no implicit any/all conversion.
    requirement: Literal["any", "all"]


def noncombat_name(name):
    return name.lower() not in {
        "dodge",
        "brawl",
        "fighting_brawl",
        "handgun",
    } and not name.lower().startswith(("fighting", "firearms"))


def preserves_ordinary_condition(check):
    """An ordinary skill gate cannot silently become relative victory or any-success."""
    return not check.opposed and (not check.combined or check.combined.requirement == "all")


def check_result(check, total):
    """Judge every component against one selected (possibly luck-adjusted) total."""
    combined = check.get("combined")
    if not isinstance(combined, dict):
        return judge(check["value"], check["difficulty"], total)
    components = [
        {**c, **judge(c["value"], c["difficulty"], total)} for c in check["compound"]["components"]
    ]
    requirement = combined["requirement"]
    passed = (any if requirement == "any" else all)(c["passed"] for c in components)
    # Summary only; component levels and fumbles are always retained.
    representative = (max if requirement == "any" else min)(
        components, key=lambda c: (c["passed"], RANK[c["level"]])
    )
    return {
        **{k: representative[k] for k in ("total", "threshold", "level", "outcome")},
        "passed": passed,
        "components": components,
        "requirement": requirement,
        "partial_success": any(c["passed"] for c in components)
        and not all(c["passed"] for c in components),
    }


def result_signature(result):
    return tuple((r["level"], r["passed"]) for r in result.get("components", [result]))


def opposed_result(sides):
    """Six levels, then full values; exact ties end in stalemate (no extra dice).

    Noncombat failure ties use the same value comparison. Combat's both-fail
    exception is deliberately not applied here (PDF 107 belongs to combat).
    """
    left, right = sides
    keys = [(RANK[s["result"]["level"]], s["value"]) for s in sides]
    winner = None if keys[0] == keys[1] else 0 if keys[0] > keys[1] else 1
    return {
        **left["result"],
        "passed": winner == 0,
        "outcome": "stalemate" if winner is None else "success" if winner == 0 else "failure",
        "winner": winner,
        "winner_id": sides[winner]["participant_id"] if winner is not None else None,
        "both_failed": not left["result"]["passed"] and not right["result"]["passed"],
        "comparison": "stalemate"
        if winner is None
        else "level"
        if keys[0][0] != keys[1][0]
        else "value",
        "side_results": [s["result"] for s in sides],
    }


def compound_context(check):
    """A bounded model receipt; fixed dice/decision ledgers stay on the check card."""
    if not check.get("compound"):
        return check
    return {
        **{
            k: check[k]
            for k in (
                "id",
                "target_member_id",
                "name",
                "kind",
                "value",
                "difficulty",
                "visibility",
                "status",
                "result",
                "display_text",
            )
            if k in check
        },
        "check_type": "opposed" if check.get("opposed") else "combined",
        **(
            {
                "participants": [
                    {k: side[k] for k in ("label", "display_name", "value", "result")}
                    for side in check["compound"]["participants"]
                ]
            }
            if check.get("opposed")
            else {"requirement": check["combined"]["requirement"]}
        ),
    }
