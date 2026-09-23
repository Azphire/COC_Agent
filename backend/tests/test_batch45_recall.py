"""Bounded recall regressions; synthetic evidence is never real-session acceptance."""

import json
from types import SimpleNamespace

from app.memory.recall import select_memory
from app.memory.segments import make_segment, select_chunks


def event(seq, kind, payload):
    return {"seq": seq, "type": kind, "payload": payload, "visibility": "public",
            "actor_member_id": "alice"}


def segment(events, content, identifier):
    _, chunks = select_chunks(events, {}, lambda _: True)
    doc = make_segment(content, events, chunks)
    return SimpleNamespace(id=identifier, kind="summary_segment", active=True,
                           content=json.dumps(doc), coverage_start=events[0]["seq"],
                           coverage_end=events[-1]["seq"], scope="public")


def test_current_scene_ranks_same_name_without_discarding_cross_scene_history():
    events = [event(1, "clue.revealed", {"title": "铁门", "content": "铁门密码：3149。",
                                        "scene_id": "library"}),
              event(2, "clue.revealed", {"title": "铁门", "content": "铁门密码：8765。",
                                        "scene_id": "cellar"})]
    rows, _ = select_memory(events, "铁门密码", scene_id="library", budget=600)
    assert rows[0]["source"]["scene"] == "library"
    assert any(r["source"]["scene"] == "cellar" and r["historical_only"] for r in rows)


def test_same_public_source_segment_is_selected_only_once():
    events = [event(1, "action.submitted", {"text": "研究铁门", "scene_id": "library"})]
    memories = [segment(events, "研究铁门，尚未开启。", name) for name in ("alice", "bob")]
    rows, audit = select_memory(events, "铁门", memories=memories, budget=4000)
    assert len([r for r in rows if r["kind"] == "segment"]) == 1
    assert audit["deduplicated"]


def test_source_quote_is_not_repeated_inside_selected_segment():
    text = "铁门铭文是赤鸟归巢，仍需研究其含义。"
    events = [event(1, "clue.revealed", {"title": "铁门", "content": text})]
    rows, _ = select_memory(events, "铁门铭文", memories=[segment(events, text, "s1")])
    assert json.dumps(rows, ensure_ascii=False).count(text) == 1


def test_resolved_target_alias_recalls_cross_scene_exact_evidence():
    events = [event(1, "clue.revealed", {"id": "archive-code", "target_id": "north-door",
                "title": "旧档案", "content": "口令：3149。", "scene_id": "archive"})]
    rows, audit = select_memory(events, "打开北边入口", scene_id="hall",
        targets=[{"id": "north-door", "title": "北门", "aliases": ["北边入口"]}])
    assert rows[0]["text"] == "口令：3149。"
    assert rows[0]["historical_only"] and rows[0]["source"]["scene"] == "archive"
    assert audit["reference_resolution"]["target_ids"] == ["north-door"]


def test_deictic_target_requires_visible_recent_unique_reference():
    targets = [{"id": "north-door", "title": "北门"}, {"id": "south-door", "title": "南门"}]
    events = [event(1, "clue.revealed", {"target_id": "north-door", "content": "口令：3149。"}),
              event(2, "module.interaction", {"target_id": "north-door", "scene_id": "hall",
                                              "text": "北门有锁。"})]
    rows, audit = select_memory(events, "我打开这扇门", targets=targets, scene_id="hall")
    assert "3149" in json.dumps(rows, ensure_ascii=False)
    assert audit["reference_resolution"]["target_ids"] == ["north-door"]
    ambiguous = [*events, event(3, "action.submitted", {"text": "比较北门和南门",
                                                        "scene_id": "hall"})]
    _, audit = select_memory(ambiguous, "我打开这扇门", targets=targets, scene_id="hall")
    assert not audit["reference_resolution"]["target_ids"]
    assert audit["reference_resolution"]["status"] == "ambiguous"


def test_old_scene_reference_cannot_resolve_current_door():
    events = [event(1, "module.interaction", {"target_id": "old-door", "scene_id": "old",
                                              "text": "铁门口令：3149。"})]
    _, audit = select_memory(events, "打开这扇门", scene_id="new",
                            targets=[{"id": "new-door", "title": "铁门"}])
    assert not audit["reference_resolution"]["target_ids"]
    assert audit["reference_resolution"]["status"] == "unresolved"


def test_recent_code_reference_and_ambiguous_codes_keep_their_source_boundary():
    events = [event(1, "clue.revealed", {"content": "暗号：3149。", "scene_id": "hall"})]
    rows, audit = select_memory(events, "刚才那个暗号是什么", scene_id="hall")
    assert "3149" in json.dumps(rows, ensure_ascii=False)
    assert audit["reference_resolution"]["source_seqs"] == [1]
    events.append(event(2, "clue.revealed", {"content": "暗号：7654。密码：5522。",
                                            "scene_id": "hall"}))
    _, audit = select_memory(events, "刚才那个暗号是什么", scene_id="hall")
    assert audit["reference_resolution"]["status"] == "ambiguous"
    assert not audit["reference_resolution"]["source_seqs"]


def test_recent_revealed_entity_code_uses_public_summary_not_keeper_prose():
    events = [event(1, "entity.revealed", {"id": "note", "type": "clue",
        "public_summary": "暗号：3149。", "keeper_summary": "秘密暗号：5522。",
        "scene_id": "hall"})]
    rows, audit = select_memory(events, "刚才那个暗号是什么", scene_id="hall")
    assert audit["reference_resolution"]["status"] == "recent_quote"
    assert audit["reference_resolution"]["source_seqs"] == [1]
    text = json.dumps(rows, ensure_ascii=False)
    assert "3149" in text and "5522" not in text


def test_task_item_identity_recalls_receipt_when_display_words_differ():
    events = [event(1, "module.interaction", {"operation": "take", "result": "success",
                "entity_id": "archive-key", "text": "已领取。", "scene_id": "archive"})]
    task = {"kind": "pending_task", "text": "把它交给队员乙", "item_ids": ["archive-key"],
            "remaining_quantity": 1, "source": {"ref": "e2"}}
    rows, _ = select_memory(events, "继续刚才的交接", tasks=[task])
    assert task in rows
    assert any(r.get("source", {}).get("seq") == 1 for r in rows)


def test_segment_with_same_event_does_not_hide_unrepresented_tail_quote():
    text = "墙面的旧痕迹并无关联。" * 180 + "尾部证词：黑鸟在清晨离开。"
    events = [event(1, "npc.spoke", {"text": text}),
              event(2, "action.submitted", {"text": "我想核对尾部证词。"})]
    memory = segment(events, "核对尾部证词", "s1")
    rows, _ = select_memory(events, "核对尾部证词", memories=[memory], budget=5000)
    assert any(row["kind"] == "npc_statement" and "黑鸟在清晨离开" in row["text"]
               for row in rows)


def test_required_task_and_exact_fact_precede_long_supplemental_prose():
    events = [event(1, "clue.revealed", {"content": "铁门密码：3149。"}),
              event(2, "npc.spoke", {"text": "铁门一直在这里，" * 80})]
    task = {"kind": "pending_task", "text": "输入铁门密码", "source": {"ref": "e3"}}
    rows, audit = select_memory(events, "铁门", tasks=[task], budget=550)
    assert task in rows
    assert any("3149" in r.get("text", "") for r in rows)
    assert audit["required_omitted"] == []


def test_unfit_required_record_is_reported_without_claiming_coverage():
    events = [event(1, "clue.revealed", {"content": "铁门密码：3149。"})]
    rows, audit = select_memory(events, "铁门密码", budget=20)
    assert not rows
    assert audit["required_omitted"]
    assert not audit["required_complete"]


def test_inherited_scene_title_does_not_make_unrelated_long_numbered_archive_required():
    events = [event(1, "scene.updated", {"scene_id": "gate", "scene_title": "青铜门"}),
              event(2, "clue.revealed", {"content": "青铜门口令：003149。"}),
              event(3, "clue.revealed", {"title": "超长档案",
                "content": "无关档案印章。" * 300 + "档案编号8152"})]
    rows, audit = select_memory(events, "核对青铜门口令", scene_id="gate", budget=600)
    assert any("003149" in row.get("text", "") for row in rows)
    assert not any(row["source"]["seq"] == 3 for row in rows)
    assert audit["required_complete"]


def test_actor_identifier_digits_do_not_make_ordinary_prose_a_required_exact_value():
    evidence = event(1, "clue.revealed", {"content": "铁门表面有灰尘。"})
    evidence["actor_member_id"] = "7343dfcc-b237-4253-8619-bb71e77599e2"
    rows, audit = select_memory([evidence], "观察铁门", budget=800)
    assert rows and not audit["required_refs"]


def test_unrequested_old_rolls_do_not_displace_exact_testimony_for_current_observation():
    events = [event(1, "clue.revealed", {"title": "托马斯的委托",
        "content": "托马斯估计少了六本；托马斯不知道具体书名。"}),
        event(2, "npc.spoke", {"actor_name": "托马斯", "text": "藏书估计少了六本，书名不清楚。"})]
    events.extend(event(seq, "check.resolved", {
        "reason": "调查托马斯的藏书。", "display_text": "侦查失败。",
        "result": {"total": 100, "threshold": 25, "passed": False},
        "target_member_id": "alice",
    }) for seq in range(3, 7))
    query = "我查看书架空档。托马斯早先估计少了多少本书，他知道具体书名吗？"
    rows, audit = select_memory(events, query, budget=750)
    assert audit["required_complete"]
    assert set(audit["required_refs"]) == {"e1", "e2"}
    assert all(word in json.dumps(rows, ensure_ascii=False)
               for word in ("估计少了六本", "不知道具体书名", "attributed_testimony"))


def test_direct_result_question_and_task_object_receipts_remain_required():
    events = [event(1, "module.interaction", {"operation": "give", "result": "success",
        "item_instance_id": "key-instance", "text": "钥匙已交出。"})]
    _, direct = select_memory(events, "钥匙交接的实际结果是什么？", budget=900)
    assert direct["required_refs"] == ["e1"]
    task = {"kind": "pending_task", "text": "继续交接", "item_instance_ids": ["key-instance"],
            "executor_member_id": "alice", "source": {"ref": "e2"}}
    _, linked = select_memory(events, "继续", tasks=[task], budget=900)
    assert linked["required_refs"] == ["e2", "e1"]
    other = event(3, "module.interaction", {"operation": "give", "result": "success",
        "item_instance_id": "other-key", "text": "另一把钥匙已交出。"})
    _, unrelated = select_memory([other], "继续钥匙交接", tasks=[task], budget=900)
    assert unrelated["required_refs"] == ["e2"]


def test_legacy_check_complete_object_phrase_preserves_results_for_current_attempt():
    events = [event(1, "check.resolved", {"reason": "确认密码盘", "text": "此前侦查成功。",
        "result": {"total": 17, "threshold": 55, "passed": True}}),
        event(2, "check.resolved", {"reason": "本次确认密码盘", "text": "本次侦查失败。",
        "result": {"total": 83, "threshold": 55, "passed": False}})]
    _, audit = select_memory(events, "我查看青铜门边的密码盘。", budget=1500)
    assert set(audit["required_refs"]) == {"e1", "e2"}


def test_unrelated_opening_year_and_price_do_not_displace_pending_work_and_window():
    events = [event(1, "clue.revealed", {"title": "托马斯家",
        "content": "1922年。托马斯提供10美元酬金。"}),
        event(2, "clue.revealed", {"id": "window", "title": "窗锁",
        "content": "书房窗锁松动。"})]
    task = {"kind": "pending_task", "text": "托马斯早先的证词还有什么需要核对？",
            "source": {"ref": "e3"}}
    rows, audit = select_memory(events, "根据托马斯早先说法查看窗锁。", tasks=[task],
                               targets=[{"id": "window", "title": "窗锁"}], budget=700)
    assert audit["required_complete"] and set(audit["required_refs"]) == {"e3", "e2"}
    assert task in rows
    _, exact = select_memory(events, "托马斯家现在是哪一年，酬金多少美元？", budget=700)
    assert "e1" in exact["required_refs"]
    _, pending = select_memory(events, "查看窗锁", tasks=[{
        "kind": "pending_task", "text": "确认托马斯的10美元酬金。", "source": {"ref": "e4"},
    }], budget=700)
    assert "e1" in pending["required_refs"]
