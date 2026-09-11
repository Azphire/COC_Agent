from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_action_adjudication import modern_response
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_module_navigation import navigation_game, structure_data  # noqa: F401
from test_module_navigation_runtime import act, running_navigation  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.action_policy import ActionFacts
from app.agents.adjudication import ActionAdjudicationService
from app.agents.adjudication_schemas import KeeperNarration, PlayerIntent
from app.agents.check_policy import CheckPolicyEvaluator, CheckProposal, entity_access
from app.agents.model import AgentModelClient, FakeModelAdapter
from app.agents.narration import NarrationValidator, fallback_narration
from app.agents.teammate_eligibility import TeammateEligibilityPolicy
from app.config import Settings
from app.models.ollama import ModelFormatError
from app.rooms.service import RoomError
from app.rules.checks import judge
from app.rules.display import check_display, resolve_check_name
from app.rules.topics import RuleTopicRegistry, relevant_evidence


def policy_case():
    actor = str(uuid4())
    text = "我冒着失去平衡的风险检查门上的痕迹"
    facts = ActionFacts(
        room_id="room",
        cycle_id="cycle",
        raw_text=text,
        actor_member_id=actor,
        actor_slot_id="slot",
        actor_authorized=True,
        scene_id="scene",
        visible_entity_ids={"door"},
        local_entity_ids={"door"},
        approved_entities={
            "door": {
                "id": "door",
                "type": "clue",
                "title": "门上的痕迹",
                "reveal_conditions": {
                    "access_policy": "requires_check",
                    "successful_check": {
                        "kind": "skill",
                        "name": "spot_hidden",
                        "difficulty": "regular",
                    },
                },
            }
        },
        characters={
            actor: {
                "ruleset_id": "coc7-character-creation",
                "skill_values": {"spot_hidden": 60},
                "effective_attributes": {"dex": 60},
            }
        },
    )
    intent = PlayerIntent(
        type="investigate",
        actor_member_id=actor,
        actor_character_slot_id="slot",
        evidence_quote=text,
        confidence=1,
        target_id="door",
    )
    proposal = CheckProposal(
        target_member_id=actor,
        name="spot_hidden",
        reason="调查目标",
        clue_id="door",
        target_entity_id="door",
        basis_entity_id="door",
        necessity="required",
        uncertainty="痕迹很隐蔽",
        success_effect="辨识痕迹",
        failure_consequence="无法辨识痕迹",
    )
    return facts, intent, proposal


@pytest.mark.parametrize(
    "kind,text",
    [
        ("observe", "环顾明显环境"),
        ("converse", "与NPC普通交谈"),
        ("move", "走到无障碍邻接场景"),
        ("interact", "拿起桌上的杯子"),
    ],
)
def test_routine_actions_do_not_roll(kind, text):
    facts, intent, proposal = policy_case()
    facts.raw_text, intent.type = text, kind
    facts.approved_entities["door"]["reveal_conditions"] = {}
    proposal.necessity = "unnecessary"
    assert CheckPolicyEvaluator().evaluate(proposal, intent, facts).code == "unnecessary"


def test_published_information_does_not_roll():
    facts, intent, p = policy_case()
    facts.revealed_entity_ids.add("door")
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).code == "already_public"


def test_kp_can_resolve_pronouns_without_exact_entity_title():
    facts, intent, p = policy_case()
    intent.type = "observe"
    facts.raw_text = "我凑近看看上面的痕迹"
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed
    intent.type = "investigate"
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed


def test_hidden_configured_clue_requires_check():
    facts, intent, p = policy_case()
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed


def test_configured_entity_alias_is_completed_without_inventing_requirement():
    facts, intent, p = policy_case()
    p.clue_id = None
    from app.agents.adjudication_schemas import KeeperPlan

    plan = KeeperPlan(
        plan_id="plan",
        cycle_id="cycle",
        current_scene_id="scene",
        parsed_intent=intent,
        proposed_check=p,
    )
    actions = ActionAdjudicationService.actions(plan, facts)
    assert p.clue_id == "door" and actions[0].arguments["clue_id"] == "door"
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed
    p.clue_id = None
    facts.approved_entities["door"]["reveal_conditions"] = {}
    ActionAdjudicationService.actions(plan, facts)
    assert p.clue_id is None


def test_explicit_risk_with_rule_basis_can_roll():
    facts, intent, p = policy_case()
    facts.approved_entities["door"]["reveal_conditions"] = {}
    p.clue_id, p.rule_topic_id, p.risk_quote = None, "coc7.skill_check", facts.raw_text
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).code == "keeper_judgement"
    p.risk_quote = "模型虚构的危险"
    # Risk quote is no longer an authorization source; KP purpose/consequences
    # and the implemented rule matter. State tools remain separately guarded.
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed


@pytest.mark.parametrize("field", ["uncertainty", "success_effect", "failure_consequence"])
def test_empty_uncertainty_or_effect_rejected(field):
    from app.models.ollama import generation_schema

    assert (
        generation_schema(CheckProposal.model_json_schema())["properties"][field]["minLength"] == 1
    )
    facts, intent, p = policy_case()
    setattr(p, field, "")
    assert not CheckPolicyEvaluator().evaluate(p, intent, facts).allowed


def test_repeat_ignores_model_repeat_flag_but_allows_world_change():
    facts, intent, p = policy_case()
    d = CheckPolicyEvaluator().evaluate(p, intent, facts)
    facts.completed_checks = [
        {
            **p.model_dump(mode="json"),
            "cycle_id": "previous",
            "policy_target_id": "door",
            "policy_fingerprint": d.state_fingerprint,
        }
    ]
    p.is_repeat_attempt = False
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).code == "repeat_unchanged"
    facts.check_state = {"door": "open"}
    assert CheckPolicyEvaluator().evaluate(p, intent, facts).allowed


@pytest.mark.parametrize("case", ["unknown_skill", "not_visible", "host_review", "check_mismatch"])
def test_invalid_policy_cases(case):
    facts, intent, p = policy_case()
    if case == "unknown_skill":
        p.name = "invented"
    if case == "not_visible":
        facts.visible_entity_ids.clear()
    if case == "host_review":
        facts.approved_entities["door"]["reveal_conditions"]["access_policy"] = "host_review"
    if case == "check_mismatch":
        p.difficulty = "hard"
    d = CheckPolicyEvaluator().evaluate(p, intent, facts)
    assert d.code == case and not d.allowed
    assert d.requires_host_review == (case == "host_review")


def test_access_migration():
    assert entity_access({}) == "automatic"
    assert (
        entity_access({"reveal_conditions": {"successful_check": {"name": "x"}}})
        == "requires_check"
    )
    assert (
        entity_access({"reveal_conditions": {"required_entity_ids": ["a"]}}) == "requires_condition"
    )


def check_result(passed=True):
    result = judge(60, "regular", 10 if passed else 90)
    doc = dict(
        id="check",
        name="spot_hidden",
        kind="skill",
        difficulty="regular",
        bonus_dice=1,
        penalty_dice=0,
        value=60,
        result=result,
    )
    return {
        "events": [{"seq": 1, "type": "check.resolved", "payload": {**doc, **check_display(doc)}}]
    }


@pytest.mark.parametrize("passed", [True, False])
def test_real_result_display_and_fallback(passed):
    results = check_result(passed)
    text = fallback_narration("investigate", results, "公开场景")
    assert "侦查检定" in text and "spot_hidden" not in text
    assert ("未通过" in text) == (not passed)
    assert "bonus_dice" in results["events"][0]["payload"]["result"]
    assert resolve_check_name("dex", "attribute")["display_name"] == "敏捷"


@pytest.mark.parametrize(
    "change", ["success", "failure", "entity", "scene", "transition", "snake", "secret", "tool"]
)
def test_narration_rejects_contradictions_and_unexecuted_effects(change):
    result = check_result(change != "success")
    output = KeeperNarration(public_narration="公开场景")
    docs = [{"statement": "公开场景", "visibility": "public", "entity_ids": ["scene"]}]
    if change == "success":
        output.public_narration = "检定成功"
    if change == "failure":
        output.public_narration = "检定失败"
    if change == "entity":
        output.public_entity_references = ["hidden"]
    if change == "scene":
        output.current_scene_reference = "future"
    if change == "transition":
        output.transition_result_reference = "99"
    if change == "snake":
        output.public_narration = "侦查spot_hidden检定"
    if change == "secret":
        docs[0]["visibility"] = "keeper_only"
    if change == "tool":
        output.public_narration = "你已经受到了十点伤害"
    with pytest.raises(RoomError):
        NarrationValidator().validate(
            output, documents=docs, public_ids={"scene"}, scene_id="scene", results=result
        )


@pytest.mark.parametrize(
    "concept,id,page",
    [
        ("技能检定", "coc7.skill_check", 72),
        ("困难", "coc7.difficulty", 73),
        ("大失败", "coc7.critical_fumble", 77),
        ("奖励骰", "coc7.bonus_penalty", 79),
    ],
)
def test_known_rule_topics_have_verified_locations(concept, id, page):
    topic = next(t for t in RuleTopicRegistry.select([concept]) if t["mechanic_id"] == id)
    assert (
        topic["pages"][0] == page
        and topic["source_version"] == "1907"
        and topic["edition"] == "coc7"
    )


def test_rule_concepts_are_separate_and_unrelated_excerpts_excluded():
    concepts = RuleTopicRegistry.concepts("奖励骰和教育增强如何处理", ["理智"])
    assert {"奖励骰", "教育增强", "理智"} <= set(concepts)
    assert "教育增强" in RuleTopicRegistry.unstructured(concepts)
    assert not relevant_evidence("奖励骰", "人物的心理学50%，侦查40%。")
    assert relevant_evidence("奖励骰", "奖励骰取较小结果。")


def test_mechanic_ids_normalize_and_difficulty_aliases_stay_structured():
    concepts = RuleTopicRegistry.concepts("困难成功", ["coc7.skill_check", "coc7.reveal_entity"])
    assert "技能检定" in concepts
    assert not RuleTopicRegistry.unstructured(concepts)


@pytest.mark.parametrize(
    "query,matching,expected",
    [
        ("奖励骰", True, "structured"),
        ("奖励骰", False, "rag"),
        ("教育增强", True, "rag"),
    ],
)
async def test_rule_search_routes_and_records_relevance(query, matching, expected):
    from app.knowledge.service import KnowledgeService

    source = SimpleNamespace(
        source_id="rules",
        source_hash=RuleTopicRegistry.source_hash,
        edition="coc7",
        visibility="public_rules",
    )
    service = object.__new__(KnowledgeService)
    calls, rows = [], []

    async def binding(*args):
        return {
            "enabled": True,
            "rules": [
                {
                    "source_id": "rules",
                    "source_hash": source.source_hash if matching else "other",
                }
            ],
        }

    async def sanitize(session, room, value):
        return value

    def search(*args, **kwargs):
        calls.append(args[0])
        return [{"evidence_id": "irrelevant", "excerpt": "人物正在阅读报纸。"}]

    service.binding = binding
    service.agents = SimpleNamespace(sanitize=sanitize)
    service.repository = SimpleNamespace(source=lambda *a: source)
    service.retriever = SimpleNamespace(search=search)
    evidence, record = await service.search(
        SimpleNamespace(add=rows.append),
        SimpleNamespace(id="room"),
        run_id="run",
        profile=SimpleNamespace(id="profile", role="keeper"),
        actor_id="actor",
        query=query,
        kind="rules",
    )
    assert record.source_filters["mode"] == expected
    assert bool(calls) == (expected == "rag")
    if expected == "rag":
        assert evidence == []
        assert record.source_filters["excluded"] == [
            {"evidence_id": "irrelevant", "reason": "irrelevant"}
        ]
    else:
        assert evidence[0]["physical_page"] == 79


async def test_multiconcept_context_search_is_split():
    from app.knowledge.service import KnowledgeService

    service, queries = object.__new__(KnowledgeService), []

    async def binding(*args):
        return {"enabled": True, "rules": []}

    async def available(*args):
        return None

    async def search(*args, **kwargs):
        queries.append(kwargs["query"])
        return [], SimpleNamespace(source_filters={}, evidence=[], injected_ids=[])

    service.binding, service.require_available, service.search = binding, available, search
    context = {
        "phase": "generate_keeper_narration",
        "module": {},
        "triggering_action": {"payload": {"text": "奖励骰和教育增强规则如何处理"}},
        "rule_concepts": ["奖励骰", "教育增强"],
    }
    await service.pre_context(
        None,
        SimpleNamespace(id="room"),
        SimpleNamespace(member_id="actor"),
        SimpleNamespace(role="keeper"),
        "run",
        context,
        2000,
    )
    assert queries == ["奖励骰", "教育增强"]
    queries.clear()
    context["triggering_action"]["payload"]["text"] = "我与眼前的人交谈"
    context["rule_concepts"] = ["coc7.move", "coc7.skill_check"]
    await service.pre_context(
        None,
        SimpleNamespace(id="room"),
        SimpleNamespace(member_id="actor"),
        SimpleNamespace(role="keeper"),
        "run",
        context,
        2000,
    )
    assert queries == []


def test_teammate_has_no_default_trigger_and_clue_allows_call():
    trigger = SimpleNamespace(seq=3, payload={"text": "我查看四周"})
    kw = dict(events=[], trigger=trigger, profile={"name": "同伴"}, member_id="member")
    policy = TeammateEligibilityPolicy()
    assert policy.evaluate(**kw) is None
    kw["events"] = [SimpleNamespace(seq=4, type="clue.revealed", payload={})]
    assert policy.evaluate(**kw) == "new_public_entity"
    kw["events"] = []
    trigger.payload["text"] = "同伴，请告诉我你的看法"
    assert policy.evaluate(**kw) == "direct_conversation"


@pytest.mark.parametrize("repair_success", [True, False])
async def test_single_shared_schema_semantic_repair_budget(repair_success):
    adapter = FakeModelAdapter(
        responses=[
            {"public_narration": "spot_hidden"},
            {"public_narration": "可以继续观察" if repair_success else "spot_hidden"},
        ]
    )
    model = AgentModelClient(Settings(_env_file=None), adapter)

    async def validate(output):
        if "_" in output.public_narration:
            raise ModelFormatError("内部标识")

    if repair_success:
        await model.generate([], KeeperNarration, validate_output=validate)
    else:
        with pytest.raises(ModelFormatError):
            await model.generate([], KeeperNarration, validate_output=validate)
    assert len(adapter.prompts) == 2


def test_ordinary_cycle_is_plan_narration_only(client, game):  # noqa: F811
    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=modern_response)
    ok(submit(client, game, "我环顾明显环境"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    assert cycle["state"]["call_count"] == 2
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert not any(e["type"].startswith("check.") for e in events)
    assert any(
        e["type"] == "agent.teammate_decision" and e["payload"]["deterministically_skipped"]
        for e in events
    )


def test_narration_fallback_does_not_repeat_effects(client, game):  # noqa: F811
    calls = []

    def response(messages, kwargs):
        schema = kwargs["response_schema"].__name__
        calls.append(schema)
        if schema == "KeeperNarration":
            return {"public_narration": "spot_hidden泄漏"}
        output = modern_response(messages, kwargs)
        if schema == "KeeperPlan":
            output["proposed_reveal_entity_ids"] = ["notice"]
        return output

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "我查看公告"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "clue.revealed" for e in events) == 1
    assert calls.count("KeeperPlan") == 1 and calls.count("KeeperNarration") == 2
    narration = next(e["payload"] for e in events if e["type"] == "keeper.narration")
    assert narration["safe_fallback"] and "spot_hidden" not in narration["text"]


def test_partial_plan_keeps_legal_reveal_without_replanning(client, game):  # noqa: F811
    calls = []

    def response(messages, kwargs):
        schema = kwargs["response_schema"].__name__
        calls.append(schema)
        output = modern_response(messages, kwargs)
        if schema == "KeeperPlan":
            output["proposed_reveal_entity_ids"] = ["notice"]
            output["proposed_check"] = {
                "target_member_id": output["parsed_intent"]["actor_member_id"],
                "name": "spot_hidden",
                "reason": "多余的日常检定",
                "target_entity_id": "notice",
                "necessity": "unnecessary",
            }
        return output

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "我查看公告"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed", cycle
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert calls.count("KeeperPlan") == 1
    assert sum(e["type"] == "clue.revealed" for e in events) == 1
    assert not any(e["type"].startswith("check.") for e in events)
    validation = ok(client.get(game["prefix"] + f"/cycles/{cycle['id']}/validation"))["validation"]
    assert validation["status"] == "partially_approved"


def test_navigation_envelope_is_budgeted_after_local_selection(
    client,
    running_navigation,  # noqa: F811
    monkeypatch,
):
    import json

    service = client.app.state.agent_service
    original = service.module_context.resolve
    budgets = []

    async def resolve(*args, **kwargs):
        context = await original(*args, **kwargs)
        if args[2] == "keeper":
            budget = kwargs["budget"]
            budgets.append(budget)
            module = context["module"]
            size = len(json.dumps(module, ensure_ascii=False))
            module["bounded_test_padding"] = "x" * max(0, budget - size - 40)
            context["module_context_audit"]["test_metadata"] = "x" * 700
        return context

    monkeypatch.setattr(service.module_context, "resolve", resolve)
    service.model.adapter = FakeModelAdapter(responder=modern_response)
    cycle = act(client, running_navigation, "我环顾明显环境")
    assert cycle["status"] == "completed", cycle
    assert len(budgets) >= 2 and min(budgets) < max(budgets)
