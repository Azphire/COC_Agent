"""Exact active-branch evidence reaches ordinary action memory with attribution."""

import json
from types import SimpleNamespace

import pytest

from app.agents.adjudication_schemas import BehaviorState, TeammateDecision
from app.agents.behavior import TeammateBehaviorPolicy
from app.memory.recall import quote_chunks, select_memory, visible_tasks
from app.memory.segments import canonical, make_segment, source_digest


def event(seq, kind, payload, visibility="public"):
    return {"seq": seq, "type": kind, "payload": payload, "visibility": visibility,
            "actor_member_id": "alice", "occurred_at": "2026-09-23T10:00:00Z"}


def test_ordinary_action_recalls_early_password_with_exact_origin_and_testimony():
    events = [event(1, "clue.revealed", {"title": "保险柜密码", "content": "密码：零七-3149。",
                                       "scene_id": "library", "cycle_id": "early"}),
              event(2, "npc.spoke", {"text": "我猜保险柜密码已经作废。", "actor_name": "管家",
                                     "scene_id": "library", "cycle_id": "early"})]
    events += [event(i, "keeper.narration", {"text": "走廊没有其他动静。"}) for i in range(3, 80)]
    rows, audit = select_memory(events, "我使用保险柜密码开锁。", budget=3000)
    password = next(r for r in rows if r["kind"] == "source_text")
    assert password["text"] == "密码：零七-3149。"
    assert password["source"] == {"ref": "e1", "seq": 1, "scene": "library", "turn": "early",
                                  "actor": "alice", "visibility": "public",
                                  "time": "2026-09-23T10:00:00Z"}
    testimony = next(r for r in rows if r["kind"] == "npc_statement")
    assert testimony["epistemic"] == "attributed_testimony"
    assert all(r["historical_only"] for r in rows)
    assert not audit["omitted"]


def test_branch_filter_removes_future_password_and_stream_draft():
    events = [event(1, "action.submitted", {"text": "查看便签"}),
              event(2, "clue.revealed", {"content": "保险柜密码：旧321。"}),
              event(3, "clue.revealed", {"content": "保险柜密码：废弃987。"}),
              event(4, "snapshot.loaded", {"source_seq": 2}),
              event(5, "keeper.stream.delta", {"text": "保险柜密码：草稿111。"})]
    rows, _ = select_memory(events, "我使用保险柜密码。")
    text = json.dumps(rows, ensure_ascii=False)
    assert "旧321" in text and "废弃987" not in text and "草稿111" not in text


def test_long_quote_is_rehydrated_by_source_offsets_and_not_silently_cut():
    text = "这是一段无关的墙面描写。" * 160 + "保险柜密码：09-零八-1172。" + "墙面完整。" * 80
    records = quote_chunks({"text": text}, "我使用保险柜密码。")
    selected = next(r for r in records if "09-零八-1172" in r["text"])
    span = selected["excerpt"]
    assert selected["text"] == text[span["start"]:span["end"]]
    assert span["partial"] and span["total"] == len(text)


def test_full_source_ids_stay_server_side_but_real_segment_prose_is_selected():
    e = event(4, "action.submitted", {"text": "我调查保险柜。", "scene_id": "library"})
    raw = canonical(e)
    chunk = {"seq": 4, "start": 0, "end": len(raw), "total": len(raw), "digest": source_digest(e)}
    from app.memory.segments import source_metadata

    chunk.update(source_metadata(e))
    doc = make_segment("调查员开始研究保险柜，暂未打开。", [e], [chunk])
    memory = SimpleNamespace(id="segment-id", kind="summary_segment", active=True,
                             content=json.dumps(doc), coverage_start=4, coverage_end=4,
                             scope="public", source_event_ids=list(range(10000)))
    rows, _ = select_memory([e], "我继续研究保险柜。", memories=[memory])
    segment = next(r for r in rows if r["kind"] == "segment")
    assert '[e4 action.submitted] text="我调查保险柜。"' == segment["summary"]["content"]
    assert "暂未打开" not in segment["summary"]["content"]
    assert "source_event_ids" not in json.dumps(rows)
    assert segment["source"]["range"] == [4, 4]


@pytest.mark.asyncio
async def test_task_visibility_and_source_filter_never_turn_promise_into_execution():
    rows = [SimpleNamespace(member_id="alice", document={
        "pending_requests": [{"text": "开保险柜", "source_event_seq": 1, "operations": ["open"]},
                             {"text": "废弃分支任务", "source_event_seq": 99}],
        "current_short_term_goal": "我的秘密目标", "task_status": "pending",
    }), SimpleNamespace(member_id="bob", document={"current_short_term_goal": "别人的秘密目标"})]

    class Session:
        async def scalars(self, query):
            return rows

    events = [event(0, "scene.updated", {"scene_id": "library"}),
              event(1, "action.submitted", {"text": "开保险柜"}),
              event(2, "agent.spoke", {"text": "我会打开保险柜。"})]
    own = await visible_tasks(Session(), "room", events, member_id="alice")
    public = await visible_tasks(Session(), "room", events, narrator=True)
    assert len(own) == 2 and len(public) == 1
    assert public[0]["epistemic"] == "requested_not_executed"
    assert public[0]["source"]["scene"] == "library"
    assert "秘密" not in json.dumps(public, ensure_ascii=False)
    selected, _ = select_memory(events, "保险柜", tasks=public)
    promise = next(r for r in selected if r["kind"] == "commitment_candidate")
    assert promise["epistemic"] == "attributed_intent_not_execution"


def test_model_completion_claim_does_not_clear_unsettled_goal():
    state = BehaviorState(current_short_term_goal="用钥匙打开保险柜", task_status="pending")
    decision = TeammateDecision(mode="speak", speech_text="我来试试。", goal_status="complete",
                                confidence=1, related_player_action_seq=1)
    updated = TeammateBehaviorPolicy().advance(state, decision, cycle_id="cycle",
                                              fingerprint="f", safe_goal=None)
    assert updated.current_short_term_goal == state.current_short_term_goal
    assert updated.task_status == "pending"


def test_historical_public_quote_exemption_does_not_authorize_private_or_extra_text():
    from app.agents.narration_stream import restored_recall_private_text

    public = {"source": {"visibility": "public"}, "kind": "source_text",
              "text": "保险柜密码：3149。", "historical_only": True}
    private = {**public, "text": "密令：绝密。", "source": {"visibility": "recipient_and_host"}}
    context = {"response_brief": {"historical_memory": [public, private]}}
    residual = restored_recall_private_text(context, "之前的记录是保险柜密码：3149。密令：绝密。")
    assert "3149" not in residual and "密令：绝密。" in residual
    assert restored_recall_private_text(context, "保险柜密码：3149。") == "保险柜密码：3149。"
