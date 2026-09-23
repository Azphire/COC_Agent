"""Independent boundary probes for persisted launch/party orchestration."""

import asyncio
import json
from uuid import uuid4

from sqlalchemy import select
from test_batch48_launch import launch  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import headers, join, ok

from app.agents.model import FakeModelAdapter
from app.domain.handouts import PreparedHandout
from app.launch.schemas import LaunchPatch
from app.launch.service import LaunchService
from app.party.schemas import BatchInput
from app.party.service import PartyService
from app.persistence.preparation_models import ModulePreparation
from app.persistence.room_models import GameRoom
from app.preparation.packages import content_digest
from app.rooms.service import RoomError


def test_two_service_instances_share_durable_assemble_idempotency(client, launch):  # noqa: F811
    first = client.app.state.launch_service
    second = LaunchService(first.agents, first.characters, first.party, first.models)

    async def concurrent():
        gate = asyncio.Event()
        reached = 0

        async def adopt_without_party(draft_id):
            nonlocal reached
            reached += 1
            if reached == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), 5)
            return {"character_ids": [], "profile_ids": []}

        first._adopt = second._adopt = adopt_without_party
        results = await asyncio.gather(
            first.assemble(launch["draft"]["id"]), second.assemble(launch["draft"]["id"]),
            return_exceptions=True,
        )
        async with first.rooms.database.sessions() as session:
            rooms = list(await session.scalars(select(GameRoom)))
        assert len(rooms) == 1
        assert not any(isinstance(r, BaseException) for r in results), [
            type(r).__name__ if isinstance(r, BaseException) else r["room_id"] for r in results
        ]
        assert results[0]["room_id"] == results[1]["room_id"]

    client.portal.call(concurrent)


def test_two_party_services_do_not_repeat_one_persisted_generation_request(client):
    first = client.app.state.party_service
    second = PartyService(first.agents, first.characters)
    profile = {"name": "随机人物", "background": "从事本职工作并协助调查。",
               "personality": "谨慎，但有好奇心。", "goals": "查清眼前事件。",
               "speaking_style": "简短直接。", "action_tendency": "按实际技能尝试。"}
    adapter = FakeModelAdapter(responder=lambda messages, kwargs: profile)
    first.agents.model.adapter = adapter

    async def concurrent():
        batch = await first.create(BatchInput(request_id=uuid4(), count=1, seed="review"))
        original = first.get
        gate = asyncio.Event()
        reached = 0

        async def get_before_either_claims(batch_id):
            nonlocal reached
            value = await original(batch_id)
            reached += 1
            if reached == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), 5)
            return value

        first.get = second.get = get_before_either_claims
        request_id = uuid4()
        results = await asyncio.gather(
            first.next(batch["id"], request_id), second.next(batch["id"], request_id),
            return_exceptions=True,
        )
        assert not any(isinstance(r, BaseException)
                       and not (isinstance(r, RoomError) and r.status == 409)
                       for r in results), results
        stored = await original(batch["id"])
        assert len(adapter.prompts) == 1, {
            "actual_model_calls": len(adapter.prompts),
            "persisted_model_calls": stored["members"][0]["model_calls"],
        }

    client.portal.call(concurrent)


def test_draft_patch_between_adoption_and_assembly_requires_new_confirmation(client, launch):  # noqa: F811
    first = client.app.state.launch_service
    second = LaunchService(first.agents, first.characters, first.party, first.models)

    async def interleave():
        original_version = launch["draft"]["version"]

        async def adopt_then_external_patch(draft_id):
            await second.patch(draft_id, LaunchPatch(version=original_version, name="另一份选择"))
            return {"character_ids": [], "profile_ids": []}

        first._adopt = adopt_then_external_patch
        try:
            await first.assemble(launch["draft"]["id"], original_version)
        except RoomError as error:
            assert error.status == 409
        else:
            raise AssertionError("A changed draft must be reconfirmed before creating its room")
        async with first.rooms.database.sessions() as session:
            assert not list(await session.scalars(select(GameRoom)))

    client.portal.call(interleave)


def test_assembled_missing_local_card_returns_fixable_preflight_issue(client, launch):  # noqa: F811
    assembled = ok(client.post(launch["path"] + "/assemble", json={}))
    prefix = "/api/rooms/" + assembled["room_id"]
    room = ok(client.get(prefix))
    slot_id = room["character_slots"][0]["id"]
    ok(client.delete(prefix + "/character-assignments/" + slot_id))
    preflight = ok(client.post(launch["path"] + "/preflight"))
    assert any(i["code"] == "character" for i in preflight["issues"])
    assert not any(i["code"] == "ready" for i in preflight["issues"])
    blocked = ok(client.post(launch["path"] + "/start", json={}))
    assert blocked["status"] == "assembled"
    assert any(i["code"] == "character" for i in blocked["issues"])
    repaired = ok(client.post(launch["path"] + "/repair"))
    assert repaired["room_id"] == assembled["room_id"]
    ok(client.post(launch["path"] + "/repair"))
    repaired_room = ok(client.get(prefix))
    assert len(repaired_room["character_slots"]) == 1
    assert repaired_room["character_slots"][0]["id"] == slot_id
    assert repaired_room["character_slots"][0]["member_id"] == assembled["local_member_id"]
    assert ok(client.post(launch["path"] + "/start", json={}))["status"] == "started"


def test_public_party_preview_does_not_return_freeform_private_ho_rule_notes():
    document = {"members": [{
        "index": 0, "model_calls": 1, "latency_ms": 1, "reroll_count": 0,
        "error": None, "profile": {"name": "PRIVATE-PERSONA"},
        "character": {"name": "PRIVATE-PERSONA", "module_handout": {
            "preparation_id": "prep", "handout_id": "HO1", "attribute_allocations": {},
            "definition": {"title": "HO1", "text": "PRIVATE-HO-TEXT", "adjustments": {
                "required_age": 19, "requirements_note": "PRIVATE-REQUIREMENT-EXPLANATION",
                "order_note": "PRIVATE-STORY-REASON-FOR-BONUS",
            }},
        }},
    }]}
    projected = PartyService.view(document)
    assert "PRIVATE-" not in json.dumps(projected)
    handout = projected["members"][0]["character"]["module_handout"]
    assert handout["adjustments"]["required_age"] == 19


def test_private_ho_generated_name_never_becomes_a_public_room_identity(
    client, launch, monkeypatch,  # noqa: F811
):
    svc = client.app.state.launch_service
    prep_id = launch["body"]["preparation_id"]
    private = "PRIVATE-HO-X"

    async def register_fixture_handout():
        async with svc.rooms.transaction() as session:
            prep = await session.get(ModulePreparation, prep_id)
            handout = PreparedHandout(
                id="HO1", title="可选 HO 1", text=private, source_hash=prep.source_hash,
                source_pages=[1], source_block_ids=["synthetic-reviewed-handout-block"],
            )
            prep.document = {**prep.document, "handouts": [handout.model_dump(mode="json")]}
            return handout

    handout = client.portal.call(register_fixture_handout)
    # Use the ordinary exact source/digest registry contract for this isolated
    # reviewed HO fixture, rather than bypassing HO validation or permissions.
    registry = {"source_handouts": {handout.source_hash: {
        handout.id: content_digest(handout.model_dump(mode="json")),
    }}}
    monkeypatch.setattr("app.preparation.packages.evidence_registry", lambda: registry)
    seen = []

    def persona(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        seen.append(context)
        assert context["own_handout"] == private
        return {"name": private, "background": "协助完成眼前的调查。", "personality": "谨慎好奇。",
                "goals": "保护同行者。", "speaking_style": "简短直接。",
                "action_tendency": "依据实际能力尝试，不擅长时寻求帮助。"}

    svc.agents.model.adapter = FakeModelAdapter(responder=persona)
    batch = ok(client.post("/api/party-batches", json={
        "request_id": str(uuid4()), "count": 1, "preparation_id": prep_id,
        "seed": "private-name-review", "handout_ids": [handout.id],
    }))
    preview = ok(client.post(f"/api/party-batches/{batch['id']}/next", json={
        "request_id": str(uuid4()),
    }))
    assert preview["status"] == "ready" and len(seen) == 1
    assert private not in json.dumps(preview)
    stored = client.portal.call(svc.party.get, batch["id"])
    member = stored["members"][0]
    assert member["original_name"] == private
    assert member["character"]["name"] == member["profile"]["name"] == seen[0]["public_name_hint"]
    assert member["character"]["name"] != private
    updated = ok(client.patch(launch["path"], json={
        "version": launch["draft"]["version"], "party_batch_id": batch["id"],
        "handout_acknowledged": True,
    }))
    assembled = ok(client.post(launch["path"] + "/assemble", json={
        "expected_version": updated["version"],
    }))
    assert assembled["status"] == "assembled", assembled["issues"]
    prefix = "/api/rooms/" + assembled["room_id"]
    guest = join(client, assembled["invite_code"], "旁观朋友")
    auth = headers(guest["member_token"])
    projections = {
        "host_play": client.post(prefix + "/play-session", json={}).text,
        "remote_room": client.get(prefix, headers=auth).text,
        "remote_events": client.get(prefix + "/events", headers=auth).text,
        "remote_logs": client.get(prefix + "/logs", headers=auth).text,
    }
    assert not [label for label, text in projections.items() if private in text]
    # Optional HO labels remain selectable; the body remains private.
    options = ok(client.get("/api/launch/options"))
    assert "可选 HO 1" in json.dumps(options, ensure_ascii=False)
    assert private not in json.dumps(options)
