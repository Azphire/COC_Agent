"""Effort/manner words in public prose do not invent a physical tool instance."""

import pytest

from app.preparation.inventory import bind_item_prose
from app.rooms.service import RoomError


def inventory(*, held=False):
    return {"known_items": [{"id": "pry", "names": ["撬棍"]}],
            "holders": [{"item_id": "pry", "instance_id": "pry-one", "holder_id": "actor",
                         "title": "撬棍"}] if held else [], "members": []}


@pytest.mark.parametrize("text", [
    "书房窗锁因年久而松动，从外面足够用力就能打开。",
    "从外面用力便可以打开。",
    "我用手就能打开盒盖。",
    "我用这种方式来打开。",
])
def test_effort_or_manner_with_modal_is_not_a_tool(text):
    assert bind_item_prose(text, inventory(), "actor") == []


@pytest.mark.parametrize("text", [
    "我用撬棍用力打开窗户。",
    "我用工具打开窗户。",
    "我用力拿出工具打开窗户。",
    "我用这种方式拿出铁钳撬开窗户。",
    "我用手打开门再用工具撬开盒子。",
])
def test_real_or_generic_tool_claim_still_requires_a_held_instance(text):
    with pytest.raises(RoomError):
        bind_item_prose(text, inventory(), "actor")


def test_actual_named_tool_binds_only_its_held_instance_after_manner_clause():
    text = "从外面足够用力就能打开。我用撬棍用力打开窗户。"
    assert bind_item_prose(text, inventory(held=True), "actor") == ["pry-one"]
    other = inventory(held=True)
    other["holders"][0]["holder_id"] = "other"
    with pytest.raises(RoomError):
        bind_item_prose(text, other, "actor")
