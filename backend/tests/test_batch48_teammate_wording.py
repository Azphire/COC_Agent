"""Replay retained e306/e307 and opposite cases without rewriting old evidence."""

import hashlib
import json
from pathlib import Path

import pytest
from test_agent_runtime import submit, wait_cycle
from test_batch36_collaboration import responder, team_game  # noqa: F401
from test_rooms import character, lobby, ok, prepare  # noqa: F401

from app.agents.adjudication_schemas import BehaviorState, TeammateDecision
from app.agents.behavior import TeammateBehaviorPolicy
from app.agents.model import FakeModelAdapter
from app.agents.teammate_wording import (
    proposal_completion_error,
    public_wording_sources,
    source_claim_error,
)

WINDOW = "书房窗锁因年久而松动，从外面足够用力就能打开。"
SOURCES = [{"kind": "source_text", "text": WINDOW}]


def validate(value, **kwargs):
    return TeammateBehaviorPolicy().validate(
        value, state=BehaviorState(), recent_outputs=[], other_outputs=[],
        player_text="请查看窗户，核对窗锁是否松动。", player_intent="observe",
        public_ids={"window"}, action_seq=284, fingerprint="same", actor_id="teammate",
        wording_sources=SOURCES, explicit_action_request=value.mode in {"act", "assist"},
        **kwargs,
    )


def decision(**changes):
    return TeammateDecision(
        mode="assist", action_text="我去查看书房窗户，核对窗锁是否松动。",
        target_id="window", confidence=0.9, related_player_action_seq=284,
    ).model_copy(update=changes)


@pytest.mark.parametrize("run", ["task-recheck-01", "task-recheck-02"])
def test_original_saved_failure_is_rejected_without_changing_evidence(run):
    path = Path(__file__).resolve().parents[2] / "data/prepared/batch-47" / run
    path /= "batch-model-calls.json"
    if not path.exists():
        pytest.skip("Retained local model evidence is not distributed with this checkout")
    original = path.read_bytes()
    rows = json.loads(original)
    call = next(r for r in rows if isinstance(r.get("generated_output"), dict)
                and "轻松打开" in r["generated_output"].get("speech_text", ""))
    context = json.loads(call["input_messages"][-1]["content"])
    value = TeammateDecision.model_validate(call["generated_output"])
    sources = public_wording_sources(context, context["public_accounts"])
    assert source_claim_error(value.speech_text, sources) == "source_condition_changed"
    assert proposal_completion_error(value.action_text, "") == "premature_action_completion"
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(original).digest()


@pytest.mark.parametrize("text", [
    "窗锁已经确认松动，从外面可以轻松打开。",
    "先前得知窗锁松动，从外面就能打开。",
    "窗锁松动，从外面足够用力便能轻松打开。",
    "窗锁从外面可以轻松打开，我去查看一下。",
])
def test_prior_fact_does_not_license_weakened_opening_condition(text):
    assert source_claim_error(text, SOURCES) == "source_condition_changed"


@pytest.mark.parametrize("text", [
    "之前已经得知窗锁松动，从外面足够用力就能打开。",
    "先前得知窗锁松动，从外面使劲就能打开。",
    "先前已经知道窗锁松动。",
    "窗锁能轻松打开吗？",
    "我去看看窗锁是否松动。",
    "我试着打开窗户。",
    "我已经准备好了。",
    "足够用力才能打开窗锁；这不等于已经打开。",
    "窗锁松动不代表从外面能轻松打开。",
    "从外面未必能轻松打开窗锁。",
])
def test_knowledge_questions_intent_and_unrelated_already_are_allowed(text):
    assert source_claim_error(text, SOURCES) is None


def test_conditions_on_different_objects_do_not_leak_and_only_repeated_effect_needs_premise():
    sources = [{"kind": "source_text", "text": "只有找到钥匙，铁门才能打开。"}]
    assert source_claim_error("铁门可以打开。", sources) == "source_condition_changed"
    assert source_claim_error("只有找到钥匙，铁门才能打开。", sources) is None
    assert source_claim_error("窗户可以打开。", sources) is None
    assert source_claim_error("我建议先找铁门的钥匙。", sources) is None


def test_testimony_keeps_owner_but_questions_and_independent_revealed_facts_are_free():
    sources = [{"kind": "npc_statement", "speaker": "托马斯·金博尔",
                "text": "书房少了六本藏书。"}]
    assert source_claim_error("书房少了六本藏书。", sources) == "source_attribution_missing"
    assert source_claim_error("托马斯早先说书房少了六本藏书。", sources) is None
    assert source_claim_error("书房少了几本藏书？", sources) is None
    assert source_claim_error("我建议核对书房的藏书。", sources) is None
    uncertain = [{"kind": "npc_statement", "speaker": "托马斯",
                  "text": "我不清楚具体书名。"}]
    assert source_claim_error("我不清楚具体书名。", uncertain) is None
    confirmed = [*sources, {"kind": "source_text", "text": "书房少了六本藏书。"}]
    assert source_claim_error("书房少了六本藏书。", confirmed) is None


@pytest.mark.parametrize("action", [
    "我查看了书房窗户，确认窗锁确实因年久而松动，从外面足够用力就能打开。",
    "我已经检查窗锁。",
    "我去查看窗锁，确认窗锁确实松动。",
    "我查看了旧窗锁。",
])
def test_proposal_cannot_claim_execution_even_when_old_receipt_exists(action):
    result = validate(decision(action_text=action), result_facts=[{
        "actor_id": "teammate", "target_id": "window", "operation": "observe",
        "status": "success", "source_event_seq": 230, "effect": WINDOW,
    }])
    assert not result.accepted and result.reason == "premature_action_completion"


@pytest.mark.parametrize("action", [
    "我去看看窗锁是否松动。", "我试着打开窗户。", "我查看窗户，核对窗锁是否松动。",
    "我检查自己的口袋，确认实际保留下来的物品。",
])
def test_real_attempt_does_not_need_mechanical_disclaimer(action):
    assert validate(decision(action_text=action)).accepted


def test_confirmed_own_result_can_say_already_and_unconfirmed_still_cannot():
    value = decision(mode="speak", action_text=None, speech_text="我已经拿到了钥匙。")
    facts = [{"actor_id": "teammate", "target_id": "key", "operation": "take",
              "status": "success", "source_event_seq": 230, "effect": "拿到了钥匙。"}]
    assert validate(value, result_facts=facts).accepted
    assert not validate(value, result_facts=[]).accepted


def test_source_projection_excludes_private_handouts_opinions_and_summaries():
    context = {"memory_evidence": [
        {"kind": "source_text", "text": "private", "source": {"visibility": "agent_private"}},
        {"kind": "segment", "text": "summary", "source": {"visibility": "public"}},
        {"kind": "npc_statement", "text": "public", "source": {"visibility": "public"}},
    ]}
    accounts = [{"kind": "judgment", "status": "unverified_opinion", "text": "opinion"}]
    assert [r["text"] for r in public_wording_sources(context, accounts)] == ["public"]


@pytest.mark.parametrize("repair_valid", [True, False])
def test_publication_reuses_single_repair_and_never_publishes_failed_completion(
    client, team_game, repair_valid,  # noqa: F811
):
    attempts = []

    def responding(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ != "TeammateDecision" or not context.get(
            "addressed_requests"
        ):
            return responder(messages, kwargs)
        attempts.append(context)
        repaired = bool(context.get("behavior_rejection"))
        if repaired:
            assert context["behavior_rejection"]["reason"] == "premature_action_completion"
            assert "旧知识" in context["behavior_repair"]
        return {
            "mode": "assist", "action_type": "observe", "confidence": 1,
            "related_player_action_seq": context["triggering_action"]["seq"],
            "action_text": "我去查看入口，留意是否有人靠近。" if repaired and repair_valid
            else "我查看了入口，确认入口确实松动。",
        }

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=responding)
    ok(submit(client, team_game, "陈拓，帮我查看入口。"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    outputs = [e for e in events if e["type"] in {"agent.spoke", "agent.action_proposed"}]
    assert all("我查看了入口" not in e["payload"]["text"] for e in outputs)
    proposals = [e for e in outputs if e["type"] == "agent.action_proposed"]
    assert len(proposals) == int(repair_valid)
    assert len(attempts) == 2
