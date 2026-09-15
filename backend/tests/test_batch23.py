from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from openai import APIConnectionError
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_model_switching import completion
from test_module_preparation import generated, preparation, wait_preparation  # noqa: F401
from test_rooms import lobby, ok, prepare  # noqa: F401

from app.agents.action_runtime import compact_planning_prose, generation_prompt
from app.agents.adjudication_schemas import KeeperPlan
from app.agents.model import AgentModelClient, FakeModelAdapter
from app.agents.schemas import SummaryOutput
from app.config import Settings
from app.models.base import ModelError
from app.models.openai_compatible import OpenAICompatibleClient


@pytest.mark.parametrize("kind,expected_calls", [("refusal", 1), ("length", 2), ("network", 1)])
async def test_api_exception_paths_account_usage_without_executing_actions(kind, expected_calls):
    settings = Settings(_env_file=None, model_provider="openai", model_api_key="synthetic-key")
    adapter = OpenAICompatibleClient(settings)
    response = completion('{"content":"ok"}')
    response._request_id = "req_test"
    if kind == "refusal":
        response.choices[0].message.refusal = "private refusal body"
    elif kind == "length":
        response.choices[0].finish_reason = "length"
    create = AsyncMock(return_value=response)
    if kind == "network":
        create.side_effect = APIConnectionError(
            request=httpx.Request("POST", "https://example.test")
        )
    adapter.client.chat.completions.create = create
    gateway = AgentModelClient(settings, adapter)
    budget = AsyncMock()
    committed = AsyncMock()
    try:
        with pytest.raises(ModelError) as raised:
            await gateway.generate([], response_schema=SummaryOutput, on_call=budget)
            await committed()
        assert "private" not in str(raised.value) and "synthetic-key" not in str(raised.value)
        assert create.await_count == budget.await_count == expected_calls
        assert len(gateway.calls) == expected_calls
        committed.assert_not_called()
        for call in gateway.calls:
            assert call["error_category"]
            assert call["token_usage"] == (
                None if kind == "network" else {"input": 11, "output": 7, "total": 18}
            )
            if kind != "network":
                assert call["request_id"] == "req_test"
                assert call["response_model"] == "test-api-model"
    finally:
        await gateway.close()


def test_compaction_preserves_original_action_and_all_game_references():
    context = {
        "triggering_action": {
            "seq": 93,
            "type": "agent.action_proposed",
            "actor_member_id": "ally",
            "payload": {
                "text": "检查修表台",
                "actor_name": "同伴",
                "mode": "act",
                "controller_type": "agent",
                "target_id": "desk",
                "item_instance_ids": ["knife-1"],
                "fact_ids": ["event:86"],
            },
        },
        "current_participants": {"members": [{"id": "ally", "name": "同伴"}]},
        "module": {
            "id": "clock",
            "version": "1",
            "initial_scene": "square",
            "clues": [{"id": "pin", "prerequisites": {"successful_check": "spot_hidden"}}],
        },
        "check_requirements": [{"entity_id": "pin", "conditions": {"skill": "spot_hidden"}}],
        "module_interactions": [{"entity_id": "desk", "interactions": [{"id": "inspect"}]}],
        "inventory_state": {"instances": [{"instance_id": "knife-1", "holder_id": "ally"}]},
    }
    original = deepcopy(context)
    prompt = generation_prompt(compact_planning_prose(context, 1), KeeperPlan)
    assert context == original
    assert prompt["triggering_action"]["actor_member_id"] == "ally"
    assert prompt["triggering_action"]["payload"] == {
        "text": "检查修表台",
        "target_id": "desk",
        "item_instance_ids": ["knife-1"],
        "fact_ids": ["event:86"],
    }
    for key in ("check_requirements", "module_interactions", "inventory_state"):
        assert prompt[key] == context[key]
    assert prompt["module"]["clues"] == context["module"]["clues"]


def test_api_gateway_reused_by_profile_and_preparation_after_switch(
    client,
    preparation,  # noqa: F811
    monkeypatch,
):
    svc = client.app.state.agent_service
    made = []

    def respond(messages, kwargs):
        if kwargs["response_schema"].__name__ == "ProfileInput":
            return {"role": "keeper", "name": "环境 API 档案"}
        return generated(messages, kwargs)

    def factory(settings):
        from app.models.credentials import resolve_credential

        assert resolve_credential(settings).source == "OPENAI_API_KEY"
        made.append(settings.model_name)
        return FakeModelAdapter(responder=respond)

    svc.settings.openai_api_key = svc.settings.openai_api_key.__class__("synthetic-env")
    monkeypatch.setattr("app.agents.model.create_model", factory)
    ok(
        client.post(
            "/api/model/config",
            json={
                "provider": "openai",
                "model": "test-api",
                "base_url": "https://api.openai.com/v1/",
            },
        )
    )
    result = ok(
        client.post(
            "/api/agent-profiles/generate-draft", json={"role": "keeper", "concept": "谨慎"}
        )
    )
    assert result["draft"]["name"] == "环境 API 档案"
    created = ok(client.post("/api/module-preparations", json=preparation["body"]))
    ok(client.post(f"/api/module-preparations/{created['id']}/generate"))
    assert wait_preparation(client, created["id"])["status"] == "review_ready"
    assert (
        svc.preparation.agents.model
        is svc.summary_recovery.agents.model
        is svc.runtime.service.model
    )
    assert made == ["test-api"]
    assert {"ProfileInput", "GenerationOutput"} <= {
        c["schema"] for c in svc.model.calls if c["provider"] == "openai"
    }


def test_summary_uses_switched_api_gateway_and_preserves_room(client, game, monkeypatch):  # noqa: F811
    ok(submit(client, game))
    assert wait_cycle(client, game)["status"] == "completed"
    for i in range(35):
        ok(
            client.post(
                game["prefix"] + "/messages",
                json={"text": f"现场讨论{i}", "client_request_id": str(uuid4())},
            )
        )
    svc = client.app.state.agent_service
    monkeypatch.setattr(
        "app.agents.model.create_model",
        lambda _: FakeModelAdapter([{"content": "调查员在原地讨论。"}] * 3),
    )
    ok(
        client.post(
            "/api/model/config",
            json={
                "provider": "openai",
                "model": "summary-api",
                "base_url": "https://api.example.test/v1/",
                "api_key": "synthetic",
            },
        )
    )
    before = ok(client.get(game["prefix"]))
    ok(client.post(game["prefix"] + "/summary-rebuild"))
    after = ok(client.get(game["prefix"]))
    calls = [c for c in svc.model.calls if c["provider"] == "openai"]
    assert calls and all(
        c["schema"] == "SummaryOutput" and c["model"] == "summary-api" for c in calls
    )
    assert (
        before["session_state"] == after["session_state"]
        and before["inventory"] == after["inventory"]
    )


@pytest.mark.parametrize(
    "obstacle,needs_ruling",
    [
        ("时间紧迫，冒着碰落零件和弄伤手的风险", True),
        ("昏暗下搜寻可能划伤手", True),
        ("通道被封死，无法接近，有坍塌危险", False),
    ],
)
def test_risky_search_cannot_be_accepted_as_empty_narration(obstacle, needs_ruling):
    from test_action_adjudication import plan_for

    from app.agents.adjudication_schemas import TurnFocus
    from app.preparation.search import unresolved_search_plan

    raw = "我仔细搜索工作台积灰覆盖的细节。"
    plan = plan_for(raw, "investigate", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene", obstacle=obstacle)
    context = {
        "check_requirements": [
            {"access_policy": "requires_check", "successful_check": {"name": "spot_hidden"}}
        ]
    }
    assert unresolved_search_plan(plan, context) is needs_ruling
    assert plan.proposed_check is None


@pytest.mark.parametrize("case", ["own", "explicit", "ambiguous"])
def test_same_name_items_resolve_only_from_actual_ownership_or_instance(case):
    from test_action_adjudication import facts_for, plan_for

    from app.agents.action_policy import ActionPolicyValidator

    raw = "我把自己持有的小刀放在地上。" if case == "own" else "我拾起小刀。"
    plan = plan_for(raw, "interact", "mine")
    facts = facts_for(raw)
    facts.approved_entities = {
        eid: {"id": eid, "title": "小刀", "type": "item"} for eid in ("mine", "theirs")
    }
    facts.local_entity_ids = facts.visible_entity_ids = {"mine", "theirs"}
    plan.action_authority = {
        "actor_member_id": "actor",
        "kinds": ["place"],
        "held_instances": {"mine": "instance1"},
    }
    if case == "explicit":
        plan.action_authority.update(
            explicit_instance_ids=["instance1"], item_instances={"mine": "instance1"}
        )
    result = ActionPolicyValidator().validate_intent(plan.parsed_intent, plan, facts)
    assert (result is None) == (case != "ambiguous")


@pytest.mark.parametrize("case", ["own_drop", "explicit_pickup", "ambiguous"])
def test_physical_target_repair_uses_runtime_instances_before_ruling(case):
    from test_action_adjudication import facts_for, plan_for

    from app.agents.adjudication_schemas import TurnFocus
    from app.preparation.runtime_schemas import ModuleRuntimeState
    from app.preparation.search import repair_local_interaction_target

    raw = {
        "own_drop": "我把自己持有的小刀放在地上。",
        "explicit_pickup": "我拾回编号instance1的小刀。",
        "ambiguous": "我拿起小刀。",
    }[case]
    plan = plan_for(raw, "interact", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene")
    facts = facts_for(raw)
    facts.approved_entities = {
        eid: {"id": eid, "title": "小刀", "type": "item", "interactions": []}
        for eid in ("mine", "theirs")
    }
    facts.local_entity_ids = facts.revealed_entity_ids = {"mine", "theirs"}
    runtime = ModuleRuntimeState(
        item_instances={"instance1": "mine", "instance2": "theirs"},
        inventory={"instance1": "actor", "instance2": "peer"},
    )
    if case == "explicit_pickup":
        runtime.inventory.pop("instance1")
        runtime.dropped_items["instance1"] = "scene"
    repair_local_interaction_target(plan, facts, {"actor": "甲", "peer": "乙"}, runtime)
    assert plan.focus.action_target_id == ("scene" if case == "ambiguous" else "mine")
