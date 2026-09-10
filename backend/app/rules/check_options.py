"""Local 1907 CoC7: PDF 74–78, 85 (printed 73–77, 84)."""

from app.rules.checks import judge

EXCLUDED = {"san", "sanity", "luck", "理智", "幸运", "damage", "san_loss"}
COMBAT = {"dodge", "fighting_brawl", "brawl", "firearms_handgun", "firearms_rifle_shotgun"}


def ordinary(check):
    name = check.get("name", "").lower()
    return (
        not check.get("sanity")
        and check.get("kind") in {"skill", "attribute"}
        and name not in EXCLUDED
        and not check.get("combat")
        and not check.get("opposed")
        and not check.get("combined")
        and not check.get("malfunction")
    )


def luck_options(check, luck, enabled):
    settlement = check.get("settlement") or {}
    raw = settlement.get("original_result") or check.get("result")
    if (
        not enabled
        or not ordinary(check)
        or not raw
        or luck is None
        or luck <= 0
        or settlement.get("push_requested")
        or settlement.get("luck_spent", 0)
        or raw["level"] in {"critical", "fumble"}
    ):
        return []
    # Every legal amount is server derived. 01 cannot be purchased; no downgrade.
    return [
        {"spend": spend, "result": judge(check["value"], check["difficulty"], raw["total"] - spend)}
        for spend in range(1, min(luck, raw["total"] - 2) + 1)
    ]


def can_push(check):
    settlement = check.get("settlement") or {}
    raw = settlement.get("original_result") or check.get("result")
    name = check.get("name", "").lower()
    return bool(
        ordinary(check)
        and raw
        and not raw["passed"]
        and raw["level"] != "fumble"
        and not settlement.get("push_requested")
        and not settlement.get("luck_spent", 0)
        and name not in COMBAT
        and not name.startswith(("fighting", "firearms"))
    )
