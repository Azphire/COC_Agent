"""Current-task dependencies keep exact evidence without importing the whole ledger."""

import json
from copy import deepcopy
from types import SimpleNamespace

from app.agents.action_runtime import action_messages, generation_prompt
from app.agents.adjudication_schemas import TeammateDecision
from app.memory.recall import current_tasks, select_memory, visible_tasks
from app.memory.service import prompt_context_size


def event(seq, kind, payload):
    return {"seq": seq, "type": kind, "payload": payload, "visibility": "public",
            "actor_member_id": "alice"}


def task(seq, **extra):
    return {"kind": "pending_task", "key": f"{seq}:0", "source_event_seq": seq,
            "text": "核对窗锁", "target_id": "window", "operations": ["observe"],
            "status": "pending", "source": {"ref": f"e{seq}", "seq": seq}, **extra}


def test_many_unrelated_tasks_do_not_import_their_objects_or_numbers():
    current = task(1000)
    old = [task(i, text=f"核对旧档案{i}号密码，领取10本书", target_id=f"old-{i}")
           for i in range(1, 101)]
    events = [event(201, "clue.revealed", {"id": "window", "content": "窗锁松动。"}),
              event(202, "clue.revealed", {"id": "old-1", "content": "领取10本书。"})]
    before = deepcopy(old)
    rows, audit = select_memory(events, "核对窗锁", tasks=[*old, current], budget=900,
                               current_request_keys=["1000:0"])
    assert audit["required_complete"]
    assert set(audit["required_refs"]) == {"e1000", "e201"}
    assert current in rows and not any(row in old for row in rows)
    assert len(audit["task_index"]) == 101
    assert sum(entry["required"] for entry in audit["task_index"]) == 1
    assert old == before


def test_current_source_and_child_request_keys_resolve_without_guessing_old_goal():
    active, index, _ = current_tasks([
        task(3), task(4), {"kind": "short_term_goal", "text": "领取10本书",
                          "source": {"ref": "behavior:alice", "turn": "old-cycle"}},
    ], request_keys=["3:0"], source_seqs=[4], cycle_id="child")
    assert [r["key"] for r in active] == ["3:0", "4:0"]
    assert not index[-1]["required"]


def test_required_evidence_uses_total_budget_before_optional_prose():
    current = task(10, item_instance_ids=[f"item-{i:04}" for i in range(300)],
                   remaining_quantity=300)
    rows, audit = select_memory([], "继续", tasks=[current], current_request_keys=["10:0"],
                               budget=6000, optional_budget=3000)
    assert audit["required_complete"] and audit["required_chars"] > 3000
    assert rows[0]["item_instance_ids"] == current["item_instance_ids"]
    assert rows[0]["remaining_quantity"] == 300


def test_actual_overflow_names_exact_required_record_and_budget():
    current = task(10, item_instance_ids=[f"item-{i:04}" for i in range(300)])
    rows, audit = select_memory([], "继续", tasks=[current], current_request_keys=["10:0"],
                               budget=3000)
    assert not rows and not audit["required_complete"]
    missed = audit["required_omitted"][0]
    assert missed["ref"] == "e10" and missed["chars"] > missed["budget"] == 3000
    assert missed["excess_chars"] == missed["used_chars"] + missed["chars"] - 3000


def test_task_completion_and_target_source_require_actual_receipts_without_word_overlap():
    current = task(10, completion_event_seqs=[2], target_source={"source_event_seq": 1},
                   remaining_quantity=1, remaining_item_instance_ids=["key-2"])
    events = [event(1, "clue.revealed", {"content": "接收位置在二层。"}),
              event(2, "module.interaction", {"operation": "give", "result": "success",
                    "item_instance_id": "key-1", "text": "已交出。", "quantity": 1})]
    rows, audit = select_memory(events, "继续", tasks=[current],
                               current_request_keys=["10:0"], budget=2000)
    assert set(audit["required_refs"]) == {"e10", "e1", "e2"}
    receipt = next(r for r in rows if r["source"].get("seq") == 2)
    assert receipt["fields"]["item_instance_id"] == "key-1"
    assert current in rows


def test_duplicate_task_projection_is_counted_once_before_budget():
    current = task(10)
    rows, audit = select_memory([], "继续", tasks=[current, deepcopy(current)], budget=450,
                               current_request_keys=["10:0"])
    assert rows == [current] and audit["required_complete"]
    assert audit["deduplicated"] == [{"ref": "e10", "reason": "same_task_projection"}]


def test_explicit_old_task_dependency_requires_unique_object_association():
    referenced = task(10, text="抵达门边后核对青铜门密码，尚未核对完成", target_id=None)
    unrelated = [task(i, text=f"核对旧档案{i}数量", target_id=f"archive-{i}")
                 for i in range(20, 120)]
    query = "我查看青铜门边的密码盘；请根据早先的青铜门口令和未完成的核对任务提出下一步。"
    rows, audit = select_memory([], query, tasks=[*unrelated, referenced],
                               current_request_keys=[], budget=600)
    assert rows == [referenced] and audit["required_complete"]
    assert audit["task_index"][-1]["selection_reason"] == "explicit_unique_task_reference"
    fresh = task(200, text=query)
    rows, audit = select_memory([], query, tasks=[fresh, referenced], current_request_keys=[],
                               task_source_seqs=[200], budget=1200)
    assert rows == [fresh, referenced] and audit["required_complete"]
    ambiguous = task(11, text="我会调查青铜门的别处，任务未完成", target_id=None)
    rows, audit = select_memory([], query, tasks=[referenced, ambiguous],
                               current_request_keys=[], budget=600)
    assert not rows and not audit["required_refs"]
    assert len(audit["task_index"]) == 2 and not any(t["required"] for t in audit["task_index"])


def test_duplicate_old_task_projection_does_not_create_reference_ambiguity():
    old = task(10, text="核对青铜门密码", target_id=None)
    query = "根据青铜门未完成的核对任务提出下一步。"
    rows, audit = select_memory([], query, tasks=[old, deepcopy(old)],
                               current_request_keys=[], budget=600)
    assert rows == [old] and audit["required_complete"]
    assert audit["deduplicated"] == [{"ref": "e10", "reason": "same_task_projection"}]
    changed = {**old, "remaining_quantity": 3}
    rows, audit = select_memory([], query, tasks=[old, changed],
                               current_request_keys=[], budget=600)
    assert not rows and not audit["required_refs"]


async def test_visible_ledger_preserves_progress_target_origin_and_private_permissions():
    request = {**task(1), "operation_operands": {"give": {"quantity": 3}},
               "operation_progress": {"give": {"remaining_quantity": 1}},
               "target_source": {"source_event_seq": 2}, "required_items": {"key": 3},
               "remaining_items": {"key": 1}, "completion_event_seqs": [3],
               "technical_failure": {"request_key": "1:0", "executed": False}}

    class Session:
        async def scalars(self, query):
            return [SimpleNamespace(member_id="alice", document={"pending_requests": [request]}),
                    SimpleNamespace(member_id="bob", document={"current_short_term_goal": "秘密"})]

    rows = await visible_tasks(Session(), "room", [event(1, "action.submitted", {"text": "继续"})],
                               member_id="alice")
    assert len(rows) == 1
    for field in ("key", "operation_operands", "operation_progress", "target_source",
                  "required_items", "remaining_items", "completion_event_seqs",
                  "technical_failure"):
        assert rows[0][field] == request[field]


def test_actual_teammate_messages_keep_one_complete_current_task_and_defer_old_ledger():
    current = task(10, executor_member_id="alice", remaining_quantity=2,
                   item_instance_ids=["key-1", "key-2", "key-3"],
                   remaining_item_instance_ids=["key-2", "key-3"], completion_event_seqs=[8],
                   target_source={"source_event_seq": 7})
    context = {"phase": "decide_teammates", "triggering_action": {"seq": 10},
               "addressed_requests": [{**current, "kind": "delegate"}],
               "continued_requests": [task(i, text="旧任务" * 100) for i in range(100)],
               "memory_evidence": [current], "memory_selection_audit": {"task_index": ["all"]},
               "behavior_state": {"current_short_term_goal": "无关旧目标" * 1000}}
    before = deepcopy(context)
    sent = json.loads(action_messages(context, TeammateDecision, "处理本轮")[1]["content"])
    assert not sent.get("memory_evidence") and not sent.get("continued_requests")
    assert sent["deferred_task_count"] == 100
    assert "无关旧目标" not in json.dumps(sent, ensure_ascii=False)
    selected = sent["addressed_requests"][0]
    for key in ("source", "status", "key", "executor_member_id", "item_instance_ids",
                "remaining_item_instance_ids", "remaining_quantity", "completion_event_seqs",
                "target_source"):
        assert selected[key] == current[key]
    assert prompt_context_size(context) == len(json.dumps(
        generation_prompt(context, TeammateDecision), ensure_ascii=False))
    assert prompt_context_size(context) < 2500
    assert context == before
