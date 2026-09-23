"""A later teammate proposal must never rewrite an earlier receipt actor."""

from app.agents.results import result_facts
from app.memory.facts import fact_records
from app.memory.recall import select_memory


def event(seq, kind, actor, payload):
    return {"seq": seq, "type": kind, "actor_member_id": actor,
            "payload": {"cycle_id": "parent", **payload}, "visibility": "public"}


def test_historical_movement_keeps_committed_actor_target_after_teammate_proposal():
    committed = {"actor_id": "human", "target_id": "study-node", "target_name": "书房",
                 "operation": "move", "status": "success", "effect": "已到达书房。",
                 "operated_items": [], "source_event_seq": 74, "cycle_id": "parent"}
    events = [event(61, "action.submitted", "human", {"text": "我进入书房"}),
              event(74, "scene.updated", "keeper", {"scene_title": "书房"}),
              event(79, "action.result", "human", {"facts": [committed]}),
              event(83, "agent.action_proposed", "teammate", {"text": "我观察书房窗户"})]
    history = [r for r in fact_records(events) if r["source_event_seq"] == 79]
    assert len(history) == 1
    assert history[0]["result_fact"]["actor_id"] == "human"
    assert history[0]["result_fact"]["target_id"] == "study-node"
    selected, _ = select_memory(events, "谁进入了书房", budget=6000)
    retained = [r["result_fact"] for r in selected if r.get("source", {}).get("seq") == 79]
    assert retained and all(f["actor_id"] == "human" and f["target_id"] == "study-node"
                            for f in retained)
    movement = [f for f in result_facts(events) if f["operation"] == "move"]
    assert len(movement) == 1 and movement[0]["actor_id"] == "human"


def test_explicit_check_actor_beats_cycle_proposal_actor_in_unsorted_source_subset():
    events = [event(83, "agent.action_proposed", "teammate", {"text": "我试着开门"}),
              event(74, "check.resolved", "keeper", {"target_member_id": "human",
                    "display_text": "侦查成功", "result": {"passed": True}}),
              event(61, "action.submitted", "human", {"text": "我检查门"})]
    facts = result_facts(events)
    assert len(facts) == 1 and facts[0]["actor_id"] == "human"


def test_same_cycle_later_real_teammate_action_keeps_its_own_receipt_actor():
    events = [event(1, "action.submitted", "human", {"text": "我交出钥匙"}),
              event(2, "module.interaction", "human", {"operation": "give", "passed": True}),
              event(3, "agent.action_proposed", "teammate", {"text": "我放下书"}),
              event(4, "module.interaction", "teammate", {"operation": "place", "passed": True})]
    facts = result_facts(events)
    assert [(f["operation"], f["actor_id"]) for f in facts] == [
        ("give", "human"), ("place", "teammate"),
    ]
    # Explicit source action binding also wins over a different intervening proposal.
    events += [event(5, "agent.action_proposed", "another", {"text": "我等待"}),
               event(6, "scene.updated", "keeper", {"scene_title": "走廊", "source_event_seq": 3})]
    assert result_facts(events)[-1]["actor_id"] == "teammate"


def test_legacy_receipt_without_action_result_facts_remains_recallable():
    events = [event(1, "action.submitted", "human", {"text": "我检查门"}),
              event(2, "check.resolved", "keeper", {"target_member_id": "human",
                    "display_text": "侦查成功", "result": {"passed": True}})]
    facts = result_facts(events)
    assert facts[0]["actor_id"] == "human" and facts[0]["operation"] == "check"
    assert any(r["source_event_seq"] == 2 for r in fact_records(events))
