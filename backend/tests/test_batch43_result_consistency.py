"""A generation failure cannot rewrite an already settled check or operation."""

from copy import deepcopy

import pytest

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.narration import NarrationValidator, fallback_narration
from app.agents.results import check_result_error, ordinary_check_receipts, result_facts
from app.rooms.service import RoomError


def check_event(passed=True, **changes):
    payload = {
        "id": "check", "cycle_id": "cycle", "target_member_id": "player",
        "kind": "skill", "name": "stealth", "display_name": "潜行",
        "difficulty": "regular", "value": 50, "bonus_dice": 0, "penalty_dice": 0,
        "result": {"passed": passed, "total": 25 if passed else 90,
                   "threshold": 50, "level": "hard" if passed else "failure"},
        **changes,
    }
    return {"seq": 85, "type": "check.resolved", "payload": payload}


def stealth_authority():
    return {"kinds": ["pass", "take"], "actual_fragments": [
        {"operations": ["pass"], "text": "不被她发现地潜行过去。"},
        {"operations": ["take"], "text": "拿走门边的钥匙。"},
    ], "operation_item_ids": {"take": ["key"]}}


def validator(results, text, *, prefix=False, brief=None):
    method = NarrationValidator().validate_prefix if prefix else NarrationValidator().validate
    return method(
        KeeperNarration(public_narration=text), documents=[], public_ids={"scene"},
        scene_id="scene", results=results, brief=brief,
    )


def test_check_projection_keeps_success_separate_from_operations():
    facts = result_facts([check_event()])
    assert [(f["operation"], f["status"]) for f in facts] == [("check", "success")]
    assert facts[0]["check_id"] == "check"
    missing = result_facts([check_event(result={})])
    assert missing[0]["status"] == "attempted"


@pytest.mark.parametrize("passed", [True, False])
def test_local_stealth_check_settles_only_its_own_attempt(passed):
    receipt = ordinary_check_receipts(
        [check_event(passed)], cycle_id="cycle", actor_id="player", check_id="check",
        authority=stealth_authority(),
    )
    assert len(receipt) == 1
    assert receipt[0]["operation"] == "pass"
    assert receipt[0]["status"] == ("success" if passed else "failure")
    assert receipt[0]["source_event_seq"] == 85
    assert "钥匙" not in receipt[0]["action_text"]


@pytest.mark.parametrize("change", [
    {"cycle_id": "old"}, {"target_member_id": "other"}, {"id": "other"},
    {"name": "spot_hidden"}, {"combined": {"name": "listen"}},
    {"result": {}},
])
def test_no_borrowing_unrelated_or_unsettled_check(change):
    assert not ordinary_check_receipts(
        [check_event(**change)], cycle_id="cycle", actor_id="player", check_id="check",
        authority=stealth_authority(),
    )


def test_successful_check_does_not_complete_route_or_item_operation():
    for authority in [
        {**stealth_authority(), "route": {"target_scene_node_id": "future"}},
        {**stealth_authority(), "operation_item_ids": {"pass": ["door"]}},
    ]:
        assert not ordinary_check_receipts(
            [check_event()], cycle_id="cycle", actor_id="player", check_id="check",
            authority=authority,
        )


def test_fallback_reports_successful_check_and_failed_item_independently():
    facts = result_facts([check_event()]) + [{
        "operation": "take", "status": "not_executed", "target_name": "钥匙",
        "source_event_seq": 66,
    }]
    results = {"events": [check_event()], "current_result_facts": facts, "result_facts": facts}
    before = deepcopy(results)
    text = fallback_narration("interact", results, "前院", rejected=True)
    assert "困难成功，通过" in text
    assert "钥匙的取物没有执行成功" in text
    assert "行动达到了" not in text
    assert validator(results, text)["valid"]
    assert results == before  # Formatting fallback has no settlement side effects.


def test_run08_success_remains_visible_when_generation_fails():
    event = check_event()
    receipts = ordinary_check_receipts(
        [event], cycle_id="cycle", actor_id="player", check_id="check",
        authority=stealth_authority(),
    )
    facts = result_facts([event]) + receipts
    results = {"events": [event], "current_result_facts": facts, "result_facts": facts}
    text = fallback_narration("interact", results, "前院", rejected=True)
    assert "困难成功，通过" in text
    assert "没有执行成功" not in text
    assert validator(results, text)["valid"]
    with pytest.raises(RoomError, match="实际结算"):
        validator(results, "这次潜行没有执行成功。", prefix=True)


def test_missing_result_is_not_a_failed_check():
    text = fallback_narration("interact", {"events": [check_event(result={})]}, "前院")
    assert "尚未确认" in text
    assert "失败" not in text and "未通过" not in text


def test_legacy_receipt_without_summary_still_has_fallback_feedback():
    event = {"seq": 2, "type": "combat.resolved", "payload": {
        "operation": "first_aid", "rolls": {"treatment": {"result": {"passed": False}}},
    }}
    text = fallback_narration("assist", {
        "events": [event], "current_result_facts": result_facts([event]),
    }, "")
    assert text == "这次急救没有成功。"


@pytest.mark.parametrize("passed,text", [
    (True, "潜行检定失败。"), (True, "潜行检定没有成功。"),
    (False, "潜行检定已经通过。"),
])
def test_explicit_check_contradictions_are_rejected_even_in_prefix(passed, text):
    results = {"events": [check_event(passed)]}
    with pytest.raises(RoomError, match="真实检定结果"):
        validator(results, text, prefix=True)


def test_check_success_does_not_deny_other_operation_failure():
    assert not check_result_error("潜行检定通过。打开门没有成功。", [check_event()["payload"]])
    assert not check_result_error(
        "潜行检定通过，开锁检定失败。",
        [check_event()["payload"], check_event(False, id="lock", name="locksmith",
                                              display_name="开锁")["payload"]],
    )


def test_prefix_defers_coverage_but_keeps_effect_restrictions():
    facts = [{"operation": "take", "status": "not_executed", "source_event_seq": 1}]
    results = {"events": [], "current_result_facts": facts, "result_facts": facts}
    assert validator(results, "屋里很安静。", prefix=True)["valid"]
    with pytest.raises(RoomError):
        validator(results, "屋里很安静。")
    with pytest.raises(RoomError):
        validator(results, "你拿到了钥匙。", prefix=True)


def test_prefix_defers_success_feedback_but_not_private_references():
    results = {"events": [check_event(), {"seq": 86, "type": "clue.revealed",
                                         "payload": {"content": "墙上写着蓝色的数字七。"}}]}
    assert validator(results, "你停下脚步。", prefix=True)["valid"]
    with pytest.raises(RoomError, match="具体反馈"):
        validator(results, "你停下脚步。")
    with pytest.raises(RoomError, match="私密"):
        NarrationValidator().validate_prefix(
            KeeperNarration(public_narration="墙上有字。"),
            documents=[{"visibility": "keeper_only", "entity_ids": ["scene"]}],
            public_ids={"scene"}, scene_id="scene", results=results,
        )
