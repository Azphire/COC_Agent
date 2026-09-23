"""Boundaries of complete random cards, recovery, adoption and private HO context."""

import asyncio
import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.character.repository import CharacterRepository
from app.character.service import CharacterService
from app.domain.handouts import CharacterHandout, HandoutAdjustments, PreparedHandout
from app.models.base import ModelError
from app.party.generator import generate_card, member_seed
from app.party.requirements import preparation_requirements
from app.party.schemas import BatchInput, Persona
from app.party.service import MAX_PERSONA_CALLS, PartyService
from app.persistence.preparation_models import ModuleEntity, ModulePreparation
from app.preparation.packages import content_digest
from app.rules.engine import recalculate
from app.rules.handouts import approve_module_handout

RULESET = "coc7-character-creation"


def test_all_occupation_groups_pools_credit_equipment_finalize(rulesets):
    ruleset = rulesets[RULESET]
    service = CharacterService(None, rulesets)
    seen_choices, seen_formulas = set(), set()
    for era in ("1920s", "modern"):
        for occupation in ruleset.occupations:
            if era not in occupation.eras:
                continue
            for seed in range(3):
                card, _ = generate_card(
                    ruleset, str(seed), uuid4(), era=era, occupation_key=occupation.key,
                )
                assert card.validation.valid, (occupation.key, card.validation)
                assert card.remaining_points.occupation == card.remaining_points.interest == 0
                assert occupation.credit_rating_minimum <= card.skill_values["credit_rating"]
                assert card.skill_values["credit_rating"] <= occupation.credit_rating_maximum
                assert 3 <= len(card.equipment) <= 6
                assert not card.occupation_exception_approvals
                assert not card.initial_mythos_proposal
                assert not card.specialization_approvals
                frozen = service.finalize_candidate(card, card.version)
                assert frozen.status == "finalized" and frozen.validation.valid
                seen_choices.add(json.dumps(card.occupation_group_choices, sort_keys=True))
                seen_formulas.add(occupation.point_formula.display)
    assert len(seen_choices) > 200
    assert len(seen_formulas) >= 5


@pytest.mark.parametrize("age", [15, 19, 20, 39, 40, 49, 50, 59, 60, 69, 70, 79, 80, 89])
def test_age_band_boundaries(rulesets, age):
    card, _ = generate_card(rulesets[RULESET], "age-boundary", uuid4(), age=age)
    assert card.validation.valid
    assert all(value > 0 for value in card.effective_attributes.values())
    assert card.remaining_points.occupation == card.remaining_points.interest == 0
    original = copy.deepcopy(card.model_dump())
    recalculate(card, rulesets[RULESET])
    assert card.model_dump() == original


def test_seed_reproducible_varied_no_high_attribute_filter(rulesets):
    cards = [generate_card(rulesets[RULESET], str(seed), uuid4())[0] for seed in range(50)]
    assert len({c.occupation for c in cards}) > 20
    assert len({c.age for c in cards}) > 20
    assert min(v.value for c in cards for v in c.attributes.values()) <= 20
    a, _ = generate_card(rulesets[RULESET], member_seed("seed", 0, 0), uuid4())
    b, _ = generate_card(rulesets[RULESET], member_seed("seed", 0, 0), uuid4())
    assert a.attributes == b.attributes
    assert a.occupation_group_choices == b.occupation_group_choices
    assert a.occupation_skills == b.occupation_skills
    assert [r.dice for r in a.roll_records] == [r.dice for r in b.roll_records]
    assert member_seed("seed", 0, 0) != member_seed("seed", 1, 0)


def ho(number):
    return PreparedHandout(
        id=f"HO{number}", title=f"角色方案 {number}", text=f"private-only-{number}",
        source_hash="a" * 64, source_pages=[number], source_block_ids=[f"block-{number}"],
        adjustments=HandoutAdjustments(
            required_age=19, required_occupation="student", required_era="modern",
            attribute_points=30 if number == 2 else 0,
            attribute_choices=["str", "pow", "dex"] if number == 2 else [],
            attribute_maxima={"str": 99, "dex": 99} if number == 2 else {},
            credit_maximum=5 if number == 2 else 35,
            skill_bonuses={} if number == 2 else {"psychology": 30, "credit_rating": 30},
        ),
    )


@pytest.mark.parametrize("number", [1, 2])
def test_ho_adjustments_and_consent(rulesets, number):
    for seed in range(12):
        handout = CharacterHandout(preparation_id="prep", handout_id=f"HO{number}",
                                   definition=ho(number))
        card, _ = generate_card(rulesets[RULESET], str(seed), uuid4(), handout=handout)
        assert (card.age, card.occupation, card.era) == (19, "student", "modern")
        assert {issue.code for issue in card.validation.issues} == {"keeper_approval"}
        assert card.skill_values["credit_rating"] <= (5 if number == 2 else 35)
        approve_module_handout(card, rulesets[RULESET])
        recalculate(card, rulesets[RULESET])
        assert card.validation.valid, card.validation.issues
        if number == 2:
            assert sum(card.module_handout.attribute_allocations.values()) == 30
        else:
            assert card.skill_values["psychology"] >= 40
            assert card.skill_values["psychology"] <= 99


class FakeModel:
    def __init__(self):
        self.messages = []
        self.fail = False
        self.gate = None
        self.started = None
        self.background_override = None
        self.forced_name = None

    async def generate(self, messages, response_schema, on_call=None, **kwargs):
        assert response_schema is Persona
        assert kwargs["max_attempts"] == 1
        self.messages.append(messages)
        if on_call:
            await on_call()
        if self.started:
            self.started.set()
        if self.gate:
            await self.gate.wait()
        if self.fail:
            raise ModelError("当前模型暂不可用")
        context = json.loads(messages[-1]["content"])
        persona = Persona(
            name=self.forced_name or f"调查者 {len(self.messages)}",
            background=self.background_override or f"从事{context['occupation']}",
            personality="谨慎但缺乏耐心", goals="偿还朋友的人情", speaking_style="先问再说",
            action_tendency="先做自己熟悉的工作，弱项会请队友帮忙",
        )
        if kwargs.get("on_result"):
            await kwargs["on_result"]({
                "provider": "fake", "model": "selected-test-model", "config_revision": 7,
                "input_messages": messages, "transmitted_messages": messages,
                "raw_output": persona.model_dump_json(), "token_usage": {"total_tokens": 55},
            })
        return SimpleNamespace(structured=persona), 123


@pytest.fixture
def party(client):
    characters = CharacterService(
        CharacterRepository(client.app.state.database), client.app.state.character_rulesets,
    )
    model = FakeModel()
    agents = SimpleNamespace(
        rooms=client.app.state.room_service, settings=client.app.state.settings, model=model,
    )
    svc = PartyService(agents, characters)
    client.app.state.party_service = svc
    return svc, model


def create(client, **extra):
    response = client.post("/api/party-batches", json={
        "request_id": str(uuid4()), "count": 2, "seed": "fixed-batch-seed", **extra,
    })
    assert response.status_code == 200, response.text
    return response.json()


def next_member(client, batch_id, request_id=None):
    response = client.post(f"/api/party-batches/{batch_id}/next", json={
        "request_id": request_id or str(uuid4()),
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_serial_generation_failure_retry_independent_reroll_and_adoption(client, party):
    svc, model = party
    initial = create(client)
    batch_id = initial["id"]
    assert initial["status"] == "preview" and model.messages == []
    request_id = str(uuid4())
    first = next_member(client, batch_id, request_id)
    assert first["completed"] == 1
    assert next_member(client, batch_id, request_id) == first
    assert len(model.messages) == 1
    model.fail = True
    failed = next_member(client, batch_id)
    assert failed["status"] == "failed" and failed["completed"] == 1
    assert failed["members"][0] == first["members"][0]
    assert failed["members"][1]["character"] == initial["members"][1]["character"]
    # Reconstruct the service to simulate process interruption/restart.
    client.app.state.party_service = PartyService(svc.agents, svc.characters)
    model.fail = False
    ready = next_member(client, batch_id)
    assert ready["status"] == "ready" and ready["completed"] == 2
    reroll_request = {"request_id": str(uuid4()), "member_index": 1}
    response = client.post(f"/api/party-batches/{batch_id}/reroll", json=reroll_request)
    assert response.status_code == 200, response.text
    rerolled = response.json()
    assert rerolled["members"][0] == ready["members"][0]
    assert rerolled["members"][1]["reroll_count"] == 1
    assert rerolled["members"][1]["stream_seed"] != ready["members"][1]["stream_seed"]
    duplicate = client.post(f"/api/party-batches/{batch_id}/reroll", json=reroll_request)
    assert duplicate.json() == rerolled
    ready = next_member(client, batch_id)
    adoption = {"request_id": str(uuid4())}
    response = client.post(f"/api/party-batches/{batch_id}/adopt", json=adoption)
    assert response.status_code == 200, response.text
    adopted = response.json()
    assert adopted["status"] == "adopted"
    assert client.post(f"/api/party-batches/{batch_id}/adopt", json=adoption).json() == adopted
    assert client.post(f"/api/party-batches/{batch_id}/adopt", json={
        "request_id": request_id,
    }).status_code == 409
    for character_id in adopted["character_ids"]:
        frozen = client.get(f"/api/characters/{character_id}").json()
        assert frozen["status"] == "finalized" and frozen["validation"]["valid"]
    profiles = client.get("/api/agent-profiles").json()
    assert len(profiles) == 2
    assert all(p["personality"] and p["goals"] and p["action_tendency"] for p in profiles)
    assert client.post(f"/api/party-batches/{batch_id}/reroll", json={
        "request_id": str(uuid4()), "member_index": 0,
    }).status_code == 409
    assert len(model.messages) == 4
    internal = client.portal.call(svc.get, batch_id)
    assert internal["members"][0]["_model_calls"][0]["config_revision"] == 7
    assert "_model_calls" not in json.dumps(adopted)
    for messages in model.messages:
        context = json.loads(messages[-1]["content"])
        assert context["skills"] and context["attributes"] and context["personality_options"]
        assert context["own_handout"] is None


def test_creation_idempotency_call_cap_and_edit_fallback(client, party):
    svc, model = party
    request_id = str(uuid4())
    initial = create(client, count=1, request_id=request_id)
    assert create(client, count=1, request_id=request_id) == initial
    conflict = client.post("/api/party-batches", json={"count": 2, "request_id": request_id})
    assert conflict.status_code == 409
    model.fail = True
    for _ in range(MAX_PERSONA_CALLS):
        next_member(client, initial["id"])
    assert client.post(f"/api/party-batches/{initial['id']}/next", json={
        "request_id": str(uuid4()),
    }).status_code == 422
    assert len(model.messages) == MAX_PERSONA_CALLS
    response = client.patch(f"/api/party-batches/{initial['id']}/members/0", json={
        "request_id": str(uuid4()), "profile": {
            "role": "investigator", "name": "手工补全文字", "background": "有稳定工作",
            "personality": "不善表达", "goals": "保护朋友", "speaking_style": "简短",
            "action_tendency": "先查证再行动",
        },
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    assert (
        response.json()["members"][0]["character"]["attributes"]
        == initial["members"][0]["character"]["attributes"]
    )


def test_trusted_ho_minimum_model_context_and_projection(client, party, monkeypatch):
    svc, model = party
    definitions = [ho(1), ho(2)]
    registry = {"source_handouts": {"a" * 64: {
        d.id: content_digest(d.model_dump(mode="json")) for d in definitions
    }}}
    monkeypatch.setattr("app.preparation.packages.evidence_registry", lambda: registry)

    async def setup():
        async with svc.database.sessions.begin() as session:
            session.add(ModulePreparation(
                id="private-prep", source_id="source", source_hash="a" * 64, status="approved",
                version=3, document={
                    "handouts": [d.model_dump(mode="json") for d in definitions],
                    "initial_scene_entity_id": "public-scene", "keeper_summary": "SECRET ENDING",
                    "launch_requirements": {
                        "minimum_players": 2, "maximum_players": 3, "source": "模组第 1 页",
                    },
                },
            ))
            session.add(ModuleEntity(
                id="public-scene", preparation_id="private-prep", type="scene", status="approved",
                document={"public_summary": "公开导入", "keeper_summary": "SECRET CLUE"},
            ))

    client.portal.call(setup)
    initial = create(
        client, preparation_id="private-prep", handout_ids=["HO1", "HO2"], era="modern",
    )
    assert initial["requirements"]["players_verified"]
    assert not initial["requirements"]["era_verified"]
    for hidden in ("private-only-1", "private-only-2", "SECRET ENDING", "SECRET CLUE"):
        assert hidden not in json.dumps(initial)
    first = next_member(client, initial["id"])
    second = next_member(client, initial["id"])
    assert first["members"][0]["profile"] is None
    assert second["completed"] == 2
    for index, messages in enumerate(model.messages, start=1):
        content = json.loads(messages[-1]["content"])
        assert content["own_handout"] == f"private-only-{index}"
        assert f"private-only-{3-index}" not in str(messages)
        assert "SECRET" not in str(messages)
        assert content["public_introduction"] == "公开导入"
    response = client.post(f"/api/party-batches/{initial['id']}/adopt", json={
        "request_id": str(uuid4()),
    })
    assert response.status_code == 422
    response = client.post(f"/api/party-batches/{initial['id']}/adopt", json={
        "request_id": str(uuid4()), "approve_handouts": True,
    })
    assert response.status_code == 200, response.text


def test_defaults_are_explicit_not_hard_inferences():
    defaults = preparation_requirements({"display_title": "modern title mentions 4 players"})
    assert defaults["defaults_adjustable"] and not defaults["verified"]
    configured = preparation_requirements({"launch_requirements": {
        "minimum_players": 3, "maximum_players": 5, "era": "modern", "source": "模组第 2 页",
    }})
    assert configured["verified"] and configured["minimum_players"] == 3
    only_players = preparation_requirements({"launch_requirements": {
        "minimum_players": 2, "maximum_players": 3, "source": "模组第 1 页",
    }})
    assert only_players["players_verified"] and not only_players["era_verified"]
    assert only_players["defaults_adjustable"]
    adjusted_default = preparation_requirements({"launch_requirements": {"era": "modern"}})
    assert adjusted_default["era"] == "modern" and not adjusted_default["era_verified"]


def test_concurrent_duplicate_next_and_persisted_midflight_recovery(client, party):
    svc, model = party

    async def run():
        doc = await svc.create(BatchInput(request_id=uuid4(), count=2, seed="concurrent"))
        model.gate, model.started = asyncio.Event(), asyncio.Event()
        request_id = uuid4()
        first = asyncio.create_task(svc.next(doc["id"], request_id))
        await model.started.wait()
        second = asyncio.create_task(svc.next(doc["id"], request_id))
        midflight = await svc.get(doc["id"])
        assert midflight["status"] == "generating"
        assert midflight["members"][0]["model_calls"] == 1
        model.gate.set()
        a, b = await asyncio.gather(first, second)
        assert a == b and a["completed"] == 1 and len(model.messages) == 1
        first_member = copy.deepcopy(a["members"][0])
        # This is the durable state left by a terminated process, after the next
        # model call began but before a response could be persisted.
        lost_request = uuid4()
        operation, _ = svc._operation(a, lost_request, "next")
        operation["member_index"] = 1
        a["members"][1]["status"] = "generating"
        a["members"][1]["model_calls"] = 1
        svc._status(a)
        await svc._save(a)
        recovered = PartyService(svc.agents, svc.characters)
        model.gate = None
        result = await recovered.next(a["id"], lost_request)
        assert result["completed"] == 2
        assert result["members"][0] == first_member
        assert result["members"][1]["model_calls"] == 2

    client.portal.call(run)


def test_cancelled_generation_preserves_card_and_retries_only_text(client, party):
    svc, model = party

    async def run():
        doc = await svc.create(BatchInput(request_id=uuid4(), count=1, seed="cancelled"))
        original_card = copy.deepcopy(doc["members"][0]["character"])
        model.gate, model.started = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(svc.next(doc["id"], uuid4()))
        await model.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        failed = await svc.get(doc["id"])
        assert failed["status"] == "failed"
        assert failed["members"][0]["character"] == original_card
        model.gate = None
        completed = await svc.next(doc["id"], uuid4())
        assert completed["status"] == "ready"
        assert completed["members"][0]["character"]["roll_records"] == original_card["roll_records"]
        assert completed["members"][0]["model_calls"] == 2

    client.portal.call(run)


def test_impossible_ho_preserves_rolled_card_and_other_members(client, party, monkeypatch):
    svc, model = party
    definition = PreparedHandout(
        id="impossible", title="需要修正的来源约束", text="本人秘密",
        source_hash="b" * 64, source_pages=[1], source_block_ids=["impossible-block"],
        adjustments=HandoutAdjustments(
            attribute_points=999, attribute_choices=["str"], attribute_maximum=99,
        ),
    )
    registry = {"source_handouts": {"b" * 64: {
        definition.id: content_digest(definition.model_dump(mode="json")),
    }}}
    monkeypatch.setattr("app.preparation.packages.evidence_registry", lambda: registry)

    async def setup():
        async with svc.database.sessions.begin() as session:
            session.add(ModulePreparation(
                id="conflict-prep", source_id="source", source_hash="b" * 64, status="approved",
                document={"handouts": [definition.model_dump(mode="json")],
                          "initial_scene_entity_id": "conflict-scene"},
            ))
            session.add(ModuleEntity(
                id="conflict-scene", preparation_id="conflict-prep", type="scene",
                status="approved", document={"public_summary": "公开开场"},
            ))

    client.portal.call(setup)
    initial = create(client, preparation_id="conflict-prep", handout_ids=[None, "impossible"])
    assert initial["status"] == "failed"
    failed = initial["members"][1]
    assert failed["generation_stage"] == "numeric"
    assert failed["character"]["attributes"] and failed["character"]["roll_records"]
    assert len(model.messages) == 0
    result = next_member(client, initial["id"])
    assert result["members"][0]["status"] == "ready"
    assert result["members"][1]["character"] == failed["character"]
    assert client.post(f"/api/party-batches/{initial['id']}/next", json={
        "request_id": str(uuid4()),
    }).status_code == 422
    assert len(model.messages) == 1
