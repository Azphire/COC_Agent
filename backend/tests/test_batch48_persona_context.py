"""Full own persona reaches the existing request and stays within its normal gate."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_agent_runtime import submit, wait_cycle
from test_batch36_collaboration import responder, team_game  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.action_runtime import TEAMMATE_INSTRUCTION, action_messages, generation_prompt
from app.agents.adjudication_schemas import TeammateDecision
from app.agents.generation_contracts import generation_contract
from app.agents.model import FakeModelAdapter
from app.config import Settings
from app.models import budget
from app.models.base import ModelError
from app.persistence.agent_models import ProfileRecord

FIELDS = ("background", "speaking_style", "action_tendency")
EVIDENCE = Path(__file__).resolve().parents[2] / "data/prepared/batch-48/real-01"


def document(value):
    return json.loads(value) if isinstance(value, str) else value


def test_own_full_persona_is_present_in_actual_transmitted_messages(client, team_game):  # noqa: F811
    svc = client.app.state.agent_service
    fields = {"background": "曾替小城报馆整理地方档案，习惯保留来源与不同证人的分歧。",
              "speaking_style": "先说能确认的短结论，再用温和语气追问缺少的细节。",
              "action_tendency": "先核对公开说明；需要实际检查时，说明具体动作再尝试。"}

    async def configure_persona():
        async with svc.rooms.transaction() as session:
            binding = next(b for b in await svc.bindings(session, team_game["room"]["id"])
                           if b.member_id == team_game["chen"])
            profile = await session.get(ProfileRecord, binding.profile_id)
            profile.document = {**profile.document, **fields}

    client.portal.call(configure_persona)
    captured = []

    def responding(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if kwargs["response_schema"].__name__ == "TeammateDecision" and context.get(
            "self_identity", {}
        ).get("member_id") == team_game["chen"]:
            captured.append(context)
            return {"mode": "speak", "speech_text": "我还不清楚，先核对公开说明比较稳妥。",
                    "confidence": 1,
                    "related_player_action_seq": context["triggering_action"]["seq"]}
        return responder(messages, kwargs)

    svc.model.adapter = FakeModelAdapter(responder=responding)
    ok(submit(client, team_game, "陈拓，你知道拉杆有什么用吗？"))
    assert wait_cycle(client, team_game)["status"] == "completed"
    assert len(captured) == 1
    transmitted = []
    for call in svc.model.calls:
        context = json.loads(call["transmitted_messages"][-1]["content"])
        if context.get("self_identity", {}).get("member_id") == team_game["chen"]:
            transmitted.append(context)
    assert len(transmitted) == 1
    assert {key: transmitted[0]["self_identity"][key] for key in FIELDS} == fields
    assert transmitted[0]["self_identity"] == captured[0]["self_identity"]
    events = ok(client.get(team_game["prefix"] + "/events"))["events"]
    assert any(e["type"] == "agent.spoke" and e["actor_member_id"] == team_game["chen"]
               for e in events)


@pytest.fixture(scope="module")
def saved_contexts():
    state = json.loads((EVIDENCE / "delegation-state-01.json").read_text(encoding="utf-8-sig"))
    room_id = state["launch_drafts"][0]["room_id"]
    return [document(run["context"]) for run in state["agent_runs"]
            if run["room_id"] == room_id
            and run["cycle_id"] == "e5b296ca-f571-4949-ba65-5cc5eef98ca1"
            and run["graph_node"] in {"decide_teammates", "repair_teammate_decision"}]


def compile_saved_request(context, monkeypatch):
    context = deepcopy(context)
    audit = context["prompt_budget_audit"]
    context["self_identity"].update({key: context["profile"][key] for key in FIELDS})
    settings = Settings(_env_file=None, model_context_limit=audit["token_limit"],
                        agent_context_chars=audit["character_limit"],
                        model_output_limit=audit["output_reserve"])
    monkeypatch.setitem(budget._calibration, budget._model_key(settings), {
        "factor": audit["calibration_factor"], "samples": audit["calibration_samples"],
    })
    messages = action_messages(context, TeammateDecision, TEAMMATE_INSTRUCTION)
    contract = generation_contract(TeammateDecision, context)
    measured = budget.measure_request(settings, messages, contract)
    return context, settings, messages, contract, measured


@pytest.mark.parametrize("index", range(4))
def test_saved_real_decision_and_repair_fit_original_limits_without_clipping(
    saved_contexts, monkeypatch, index,
):
    original = saved_contexts[index]
    context, settings, messages, _, measured = compile_saved_request(original, monkeypatch)
    sent = json.loads(messages[-1]["content"])
    assert {key: sent["self_identity"][key] for key in FIELDS} == {
        key: original["profile"][key] for key in FIELDS
    }
    assert len(json.dumps(generation_prompt(context, TeammateDecision), ensure_ascii=False,
                          separators=(",", ":"))) <= settings.agent_context_chars
    budget.require_request_fit(measured)
    assert measured["token_limit"] == original["prompt_budget_audit"]["token_limit"]
    assert measured["output_reserve"] == original["prompt_budget_audit"]["output_reserve"]


def test_same_full_persona_is_rejected_by_a_too_small_request_budget(saved_contexts, monkeypatch):
    context, settings, messages, contract, _ = compile_saved_request(saved_contexts[0], monkeypatch)
    settings.model_context_limit = 2048
    measured = budget.measure_request(settings, messages, contract)
    with pytest.raises(ModelError, match="上下文预算"):
        budget.require_request_fit(measured)
    assert json.loads(messages[-1]["content"])["self_identity"] == context["self_identity"]
