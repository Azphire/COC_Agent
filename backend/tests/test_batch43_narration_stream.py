"""Release only validated sentence prefixes, with frozen state and no side effects."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_action_adjudication import modern_response
from test_agent_runtime import accept_original, game, submit, wait_cycle  # noqa: F401
from test_rooms import headers, lobby, ok  # noqa: F401

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.generation_contracts import generation_contract, narration_body_field
from app.agents.model import FakeModelAdapter
from app.agents.narration import NarrationValidator
from app.agents.narration_stream import NarrationStream, private_fragments
from app.models.base import ModelError, ModelResponse
from app.rooms.realtime import RoomHub
from app.rooms.service import RoomError


def controller(*, secret="黑色祭坛下面藏着秘密契约", context=None):
    hub = RoomHub(None)
    runtime = SimpleNamespace(rooms=SimpleNamespace(hub=hub),
                              validate_narration_output=AsyncMock())
    snapshot = {
        "run": SimpleNamespace(context=context or {}), "room": None, "cycle": None,
        "private": private_fragments([secret], ""), "sources": "雨水沿石阶流下。钟面停着。",
        "internal_ids": {"secret_entity"},
    }
    return NarrationStream(runtime, {"room_id": "r", "cycle_id": "c"}, "run",
                           KeeperNarration, snapshot)


async def feed(stream, text):
    await stream({"type": "delta", "attempt": 1, "text": text})


@pytest.mark.asyncio
async def test_cross_chunk_secret_and_later_fields_never_release():
    stream = controller()
    await stream({"type": "start", "attempt": 1})
    await feed(stream, '{"public_narration":"雨水沿石阶流下。黑色祭')
    assert stream.accepted == "雨水沿石阶流下。"
    await feed(stream, '坛下面藏着秘密契约。","thinking":"private","npc_speech":null}')
    assert stream.accepted == "雨水沿石阶流下。"
    assert "秘密" not in stream.hub.stream_snapshot("r")["data"]["text"]
    assert stream.runtime.validate_narration_output.await_count == 1


@pytest.mark.asyncio
async def test_secret_across_sentence_boundary_holds_its_first_fragment():
    stream = controller(secret="星光之门将在午夜开启")
    await stream({"type": "start", "attempt": 1})
    await feed(stream, '{"public_narration":"星光之门。')
    assert not stream.accepted
    await feed(stream, '将在午夜开启。"}')
    assert not stream.accepted


@pytest.mark.asyncio
async def test_short_ho_password_and_same_chunk_safe_sentence():
    stream = controller(secret="口令：黑莲。")
    await stream({"type": "start", "attempt": 1})
    await feed(stream, '{"public_narration":"雨水沿石阶流下。黑莲。"}')
    assert stream.accepted == "雨水沿石阶流下。"


@pytest.mark.asyncio
async def test_public_suffix_does_not_become_private_by_coincidence():
    stream = controller(secret="餐点将在午夜变成怪物")
    stream.snapshot["sources"] = "佣人正在传递餐点。"
    await stream({"type": "start", "attempt": 1})
    await feed(stream, '{"public_narration":"佣人正在传递餐点。"}')
    assert stream.accepted == "佣人正在传递餐点。"


@pytest.mark.asyncio
async def test_common_final_character_is_not_a_private_password():
    stream = controller(secret="餐厅灯。口令：黑莲。")
    await stream({"type": "start", "attempt": 1})
    await feed(stream, '{"public_narration":"佣人来回穿梭送餐。黑。')
    assert stream.accepted == "佣人来回穿梭送餐。"
    await feed(stream, '莲。"}')
    assert stream.accepted == "佣人来回穿梭送餐。"


@pytest.mark.asyncio
async def test_unknown_member_uuid_cannot_escape_in_body():
    stream = controller()
    await stream({"type": "start", "attempt": 1})
    await feed(stream, '{"public_narration":"调查员编号11111111-2222-4333-8444-555555555555。"}')
    assert not stream.accepted


@pytest.mark.asyncio
async def test_complete_sentence_and_accepted_prefix_are_validated_together():
    stream = controller()
    await stream({"type": "start", "attempt": 1})
    await feed(stream, '{"public_narration":"雨水沿石阶')
    assert not stream.accepted
    await feed(stream, '流下。钟面')
    assert stream.accepted == "雨水沿石阶流下。"
    await feed(stream, '停着。"}')
    assert stream.accepted == "雨水沿石阶流下。钟面停着。"
    outputs = [c.args[4].public_narration
               for c in stream.runtime.validate_narration_output.await_args_list]
    assert outputs == ["雨水沿石阶流下。", "雨水沿石阶流下。钟面停着。"]
    assert all("prefix_snapshot" in c.kwargs and "partial" not in c.kwargs
               for c in stream.runtime.validate_narration_output.await_args_list)


@pytest.mark.parametrize("outcomes,text,accepted", [
    ([True, False], "本次检定成功。", False),
    ([False, True], "本次检定失败。", False),
    ([True, False], "本次检定失败。", True),
    ([False, True], "本次检定成功。", True),
    ([True], "本次检定成功。", True),
    ([False], "本次检定失败。", True),
    ([True], "本次检定失败。", False),
    ([False], "本次检定成功。", False),
    ([True, False], "侦查检定成功。", True),
])
async def test_prefix_binds_same_frozen_check_reference_as_final_restore(outcomes, text, accepted):
    from app.agents.generation_contracts import restore_output

    events = [{
        "seq": index + 1, "type": "check.resolved", "payload": {
            "id": f"check-{index}", "display_name": "侦查" if index == 0 else "潜行",
            "result": {"passed": outcome},
        },
    } for index, outcome in enumerate(outcomes)]
    events.append({"seq": 9, "type": "scene.updated", "payload": {}})
    results = {"events": events, "result_facts": []}
    stream = controller()
    stream.snapshot["results"] = results

    async def validate_output(_session, _room, _cycle, _run, output, *, prefix_snapshot):
        return NarrationValidator().validate_prefix(
            output, documents=[], public_ids=set(), scene_id="scene",
            results=prefix_snapshot["results"],
        )

    stream.runtime.validate_narration_output.side_effect = validate_output
    await stream({"type": "start", "attempt": 1})
    await feed(stream, json.dumps({"public_narration": text}, ensure_ascii=False))
    assert bool(stream.accepted) is accepted
    checked = stream.runtime.validate_narration_output.await_args.args[4]
    restored = restore_output(
        KeeperNarration(public_narration=text), KeeperNarration,
        {"public_tool_results": results},
    )
    assert checked.check_result_reference == restored.check_result_reference
    assert checked.transition_result_reference == restored.transition_result_reference


@pytest.mark.asyncio
async def test_cancel_barrier_and_retry_invalidate_old_identity():
    stream = controller()
    await stream({"type": "start", "attempt": 1})
    old = stream.stream_id
    stream.hub.invalidate_stream("r")
    await feed(stream, '{"public_narration":"雨水沿石阶流下。"}')
    assert not stream.accepted
    await stream({"type": "start", "attempt": 2})
    assert old != stream.stream_id
    await feed(stream, '{"public_narration":"旧的迟到内容。"}')
    assert not stream.accepted


@pytest.mark.asyncio
async def test_recall_waits_for_server_restoration():
    stream = controller(context={"readonly_recall": True})
    await stream({"type": "start", "attempt": 1})
    await feed(stream, '{"public_narration":"模型改写的旧记录。"}')
    assert not stream.accepted and stream.runtime.validate_narration_output.await_count == 0


@pytest.mark.parametrize("change", ["exact", "append_ho", "other_scene", "omit", "not_recall"])
def test_recall_exception_requires_exact_current_server_render(change):
    from app.agents.generation_contracts import restore_output
    from app.agents.narration_stream import restored_recall_private_text

    context = {"readonly_recall": True, "fact_evidence": [
        {"id": "old-scene", "kind": "source_text", "source_type": "scene",
         "title": "石阶", "text": "雨水沿石阶流下。"},
        {"id": "old-item", "kind": "source_text", "source_type": "item",
         "title": "钟面", "text": "钟面停在九点。"},
    ]}
    restored = restore_output(
        KeeperNarration(fact_ids=["old-item"]), KeeperNarration, context,
    )
    text = restored.public_narration
    if change == "append_ho":
        text += "黑莲。"
    elif change == "other_scene":
        text = "未曾公开的暗室里响着钟声。"
    elif change == "omit":
        text = text.split("\n")[0]
    elif change == "not_recall":
        context["readonly_recall"] = False
    checked = restored_recall_private_text(context, text, restored.fact_ids)
    assert checked == ("" if change == "exact" else text)


def test_readonly_old_public_scene_passes_full_validation_and_publication(client, game):  # noqa: F811
    svc = client.app.state.agent_service

    async def seed_public_history():
        async def operation(session, room):
            module = await svc.module(session, room.id)
            old_scene, current_scene = module.document["scenes"][:2]
            unseen = "紫色帷幕后悬着一只银色怀表。"
            scenes = list(module.document["scenes"])
            if len(scenes) > 2:
                scenes[2] = {**scenes[2], "public_description": unseen}
            else:
                scenes.append({"id": "unvisited", "title": "未访暗室",
                               "public_description": unseen})
            module.document = {**module.document, "scenes": scenes}
            svc.rooms.append(session, room, "entity.revealed", room.host_member_id, {
                "id": old_scene["id"], "type": "scene", "title": old_scene["title"],
                "public_summary": old_scene["public_description"],
            })
            module.state = {**module.state, "scene_id": current_scene["id"]}
            svc.rooms.append(session, room, "scene.updated", room.host_member_id, {
                "scene_id": current_scene["id"], "scene_title": current_scene["title"],
                "scene_summary": current_scene["public_description"],
            })
            room.session_state = {**room.session_state, "handout_catalog": {
                "handouts": [{"text": "口令：黑莲。"}],
            }}
            return old_scene, unseen

        return await svc.mutate(game["room"]["id"], operation)

    old_scene, unseen = client.portal.call(seed_public_history)
    observed = {}

    def answer(messages, kwargs):
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            return {"parsed_intent": {"type": "recall"},
                    "focus": {"question_clause_ids": ["u1"]}}
        if kwargs["response_schema"].__name__ == "KeeperNarration":
            return {"public_narration": unseen}
        return modern_response(messages, kwargs)

    class RecallAdapter(FakeModelAdapter):
        async def generate(self, messages, **kwargs):
            if kwargs["response_schema"].__name__ == "KeeperNarration":
                stream = next(iter(svc.runtime.narration_streams.values()))
                observed["eligible"] = stream.eligible
                stream.check_private(old_scene["public_description"])
                observed["old_authorized"] = True
                for key, text in [("unseen", unseen), ("ho", "黑莲。")]:
                    try:
                        stream.check_private(text)
                    except RoomError:
                        observed[key + "_rejected"] = True
                await kwargs["on_delta"](json.dumps({"public_narration": unseen}))
                observed["accepted"] = stream.accepted
            return await super().generate(messages, **kwargs)

    svc.model.adapter = RecallAdapter(responder=answer)
    ok(submit(client, game, "复述刚才" + old_scene["title"] + "的原文。"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    narration = [event for event in events if event["type"] == "keeper.narration"][-1]
    assert old_scene["public_description"] in narration["payload"]["text"]
    assert unseen not in narration["payload"]["text"] and "黑莲" not in narration["payload"]["text"]
    # Both runtime full validation and the publication gate passed: the same
    # text arriving through the deterministic fallback would mark this true.
    assert not narration["payload"]["safe_fallback"]
    assert observed == {"eligible": False, "old_authorized": True,
                        "unseen_rejected": True, "ho_rejected": True, "accepted": ""}
    assert sum(call["schema"] == "KeeperNarration" for call in svc.model.calls) == 1


def test_dynamic_observed_detail_is_the_only_body():
    context = {"current_scene_reference": "scene", "response_brief": {
        "ordinary_observation": True, "responder": {"kind": "keeper"}}}
    contract = generation_contract(KeeperNarration, context)
    assert narration_body_field(contract) == "observed_detail"
    description = contract.model_json_schema()["properties"]["observed_detail"]["description"]
    assert "多个公开对象" in description and "答复格式" in description
    assert "每个问题" in description and "历史问题" in description


def test_generation_uses_only_this_turn_public_claim_ids():
    from pydantic import ValidationError

    context = {"current_scene_reference": "scene", "PUBLIC_CLAIM_OPTIONS": [{
        "claim_id": "public_scene", "statement": "雨水沿石阶流下。",
    }]}
    contract = generation_contract(KeeperNarration, context)
    assert contract(claim_ids=["public_scene"]).claim_ids == ["public_scene"]
    with pytest.raises(ValidationError):
        contract(claim_ids=["invented_reference"])
    context["PUBLIC_CLAIM_OPTIONS"] = []
    assert generation_contract(KeeperNarration, context)().claim_ids == []


class StreamingScenario(FakeModelAdapter):
    def __init__(self, *, fail=False, block=False):
        super().__init__(responder=modern_response)
        self.fail, self.block = fail, block
        self.first, self.finished, self.cancelled = None, None, False
        self.narrations = 0

    async def generate(self, messages, **kwargs):
        schema = kwargs.get("response_schema")
        if schema.__name__ != "KeeperNarration":
            return await super().generate(messages, **kwargs)
        self.narrations += 1
        field = narration_body_field(schema)
        text = "雨水沿石阶流下。钟面停着。"
        context = next(json.loads(m["content"]) for m in reversed(messages)
                       if m.get("role") == "user" and m.get("content", "").startswith("{"))
        brief = context.get("response_brief", {})
        sources = {s["id"]: s for s in brief.get("answer_sources", [])}
        coverage = []
        for requirement in brief.get("answer_requirements", []):
            source = next((sources[s] for s in requirement["source_ids"] if s in sources), None)
            if source:
                coverage.append({"requirement_id": requirement["id"], "body_quote": text,
                                 "source_id": source["id"], "source_quote": source["text"],
                                 "status": "answered"})
        body = json.dumps({field: text, "answer_coverage": coverage}, ensure_ascii=False)
        cut = body.index("。") + 1
        await kwargs["on_delta"](body[:cut])
        self.first = time.time()
        try:
            await asyncio.sleep(20 if self.block else 0.2)
            if self.fail:
                raise ModelError("定向故障注入：连接中断")
            await kwargs["on_delta"](body[cut:])
            self.finished = time.time()
            return ModelResponse(text=body)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.parametrize("fail", [False, True])
def test_runtime_real_callback_path_commits_once_and_recovers_failure(client, game, fail):  # noqa: F811
    adapter = StreamingScenario(fail=fail)
    client.app.state.agent_service.model.adapter = adapter
    hub = client.app.state.room_hub
    observed = []
    original = hub.stream_delta

    async def capture(*args, **kwargs):
        accepted = await original(*args, **kwargs)
        observed.append((time.time(), args[3], accepted))
        return accepted

    hub.stream_delta = capture
    response = ok(submit(client, game, "我看看四周"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    narrations = [e for e in events if e["type"] == "keeper.narration"]
    assert len(narrations) == 1
    assert observed and observed[0][2]
    assert "stream_id" in narrations[0]["payload"]
    assert adapter.narrations == 1
    assert not client.app.state.agent_service.runtime.narration_streams
    if not fail:
        assert observed[0][0] < adapter.finished
        assert narrations[0]["payload"]["text"] == "雨水沿石阶流下。钟面停着。"
    else:
        assert "定向故障" not in narrations[0]["payload"]["text"]
    assert response["room"]["id"] == game["room"]["id"]


def test_runtime_pause_closes_stream_and_prevents_late_write(client, game):  # noqa: F811
    adapter = StreamingScenario(block=True)
    client.app.state.agent_service.model.adapter = adapter
    ok(submit(client, game, "我看看四周"))
    deadline = time.monotonic() + 30
    while adapter.first is None and time.monotonic() < deadline:
        time.sleep(0.03)
    assert adapter.first is not None
    ok(client.post(game["prefix"] + "/pause"))
    assert adapter.cancelled
    stream = client.app.state.room_hub.stream_snapshot(game["room"]["id"])["data"]
    assert stream["status"] == "interrupted" and not stream["text"]
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert not any(e["type"] == "keeper.narration" for e in events)
    # Resuming may regenerate prose, but preserves the original action and any
    # settled tool receipts. It does not submit the user's action again.
    adapter.block = False
    ok(client.post(game["prefix"] + "/resume"))
    assert wait_cycle(client, game)["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert len([e for e in events if e["type"] == "action.submitted"]) == 1
    assert len([e for e in events if e["type"] == "keeper.narration"]) == 1


def test_loading_save_after_stream_pause_never_restarts_generation(client, game):  # noqa: F811
    saved = ok(client.post(game["prefix"] + "/snapshots", json={"name": "before-stream"}))[
        "snapshot"
    ]
    adapter = StreamingScenario(block=True)
    client.app.state.agent_service.model.adapter = adapter
    ok(submit(client, game, "我看看四周"))
    deadline = time.monotonic() + 30
    while adapter.first is None and time.monotonic() < deadline:
        time.sleep(0.03)
    assert adapter.first is not None
    ok(client.post(game["prefix"] + "/pause"))
    ok(client.post(game["prefix"] + f'/snapshots/{saved["id"]}/load'))
    assert adapter.cancelled and adapter.narrations == 1
    assert not client.app.state.agent_service.runtime.tasks
    stream = client.app.state.room_hub.stream_snapshot(game["room"]["id"])["data"]
    assert stream["status"] == "interrupted" and not stream["text"]


def test_successful_check_survives_mid_model_failure_without_reroll(client, game):  # noqa: F811
    from app.dice.service import DiceService

    class FixedDice:
        calls = 0

        def randint(self, lower, upper):
            self.calls += 1
            return lower  # Deterministic regression only; never used by live acceptance.

    dice = FixedDice()
    client.app.state.room_service.dice = DiceService(dice)
    adapter = StreamingScenario(fail=True)
    client.app.state.agent_service.model.adapter = adapter
    ok(submit(client, game, "对工作台进行侦查检定；我冒着失去平衡的风险尝试。"))
    assert wait_cycle(client, game)["status"] == "waiting_for_roll"
    check = ok(client.get(game["prefix"] + "/checks"))[0]
    path = game["prefix"] + f'/checks/{check["id"]}/roll'
    ok(client.post(path, json={}, headers=headers(game["remote"]["member_token"])))
    accept_original(client, game, check["id"])
    original_rolls = dice.calls
    assert wait_cycle(client, game)["status"] == "completed"
    final_check = ok(client.get(game["prefix"] + "/checks"))[0]
    assert final_check["result"]["passed"] is True
    assert dice.calls == original_rolls
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert len([e for e in events if e["type"] == "check.resolved"]) == 1
    narration = next(e["payload"]["text"] for e in events if e["type"] == "keeper.narration"
                     and e["payload"].get("stream_id"))
    assert "通过" in narration and "检定失败" not in narration
    assert adapter.narrations == 1


def test_ho_rejected_by_stream_cannot_return_through_partial_fallback(client, game):  # noqa: F811
    class PrivateScenario(StreamingScenario):
        async def generate(self, messages, **kwargs):
            schema = kwargs["response_schema"]
            if schema.__name__ != "KeeperNarration":
                return await super().generate(messages, **kwargs)
            value = {narration_body_field(schema): "雨水沿石阶流下。黑莲。"}
            text = json.dumps(value, ensure_ascii=False)
            await kwargs["on_delta"](text)
            return ModelResponse(text=text, structured=value)

    # Inject the frozen private-material fixture at the server snapshot boundary,
    # rather than persisting an invalid handout assignment into a real room.
    original = NarrationStream.create.__func__

    async def with_private(cls, *args):
        result = await original(cls, *args)
        result.snapshot["private"].update(private_fragments(["口令：黑莲。"], ""))
        return result

    from unittest.mock import patch

    with patch.object(NarrationStream, "create", classmethod(with_private)):
        client.app.state.agent_service.model.adapter = PrivateScenario()
        ok(submit(client, game, "我看看四周"))
        assert wait_cycle(client, game)["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    public = [e for e in events if e["visibility"] == "public"]
    assert "黑莲" not in json.dumps(public, ensure_ascii=False)
    assert len([e for e in public if e["type"] == "keeper.narration"]) == 1
