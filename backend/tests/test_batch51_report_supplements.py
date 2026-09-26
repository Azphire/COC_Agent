"""HTTP report recovery does not replay executed investigation or its dice."""

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_agent_runtime import game  # noqa: F401
from test_batch50_parts_runtime import BAD, RESULT_ID, UNKNOWN_ID, seed_inspection
from test_batch51_mixed_runtime import freeze_mixed_contract, saved_evidence
from test_rooms import headers, join, lobby, ok  # noqa: F401

from app.agents.model import FakeModelAdapter, model_semaphore
from app.agents.report_supplements import INTRODUCTION, NODE, ReportSupplementService
from app.models.base import ModelResponse
from app.persistence.adjudication_models import ActionPlanRecord, AgentBehaviorRecord
from app.persistence.agent_models import AgentCycle, AgentRun, ToolReceipt
from app.persistence.knowledge_models import AgentModelCall
from app.persistence.room_models import RoomEvent, RoomMember


def failed_report(client, game, monkeypatch, retained=True):  # noqa: F811
    service = client.app.state.agent_service
    seeded = client.portal.call(seed_inspection, service, game, True)
    async def parent_state(session, room):
        from app.agents.conversation import initial_state

        parent = await session.get(AgentCycle, seeded["state"]["related_player_cycle_id"])
        parent.state = {**initial_state(room.id, parent.id, game["player"],
                                       seeded["request"]["source_event_seq"], []),
                        "current_node": "finish_cycle", "status": "completed"}
    client.portal.call(service.mutate, seeded["state"]["room_id"], parent_state)
    freeze_mixed_contract(monkeypatch, seeded, with_server=False)
    raw = {"answer_parts": [
        {"requirement_id": UNKNOWN_ID, "text": BAD},
        {"requirement_id": RESULT_ID, "text": seeded["result"] if retained else "我试着检查公告。"},
    ]}
    second = {"answer_parts": raw["answer_parts"][:1] if retained else raw["answer_parts"]}
    service.model.adapter = FakeModelAdapter([
        ModelResponse(text=json.dumps(r, ensure_ascii=False)) for r in (raw, second)
    ])

    async def execute():
        state = await service.runtime.generate_keeper_narration(seeded["state"])
        return await service.runtime.finish_cycle(state)

    client.portal.call(execute)
    # The fixture adapter is reachable despite its deliberately invalid prose;
    # later pause/resume should not exercise model-configuration diagnostics.
    if service.model.configuration:
        service.model.configuration.verification = "available"
        service.model.configuration.error = None
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    report = next(e for e in reversed(events) if e["type"] == "keeper.narration"
                  and e["payload"].get("cycle_id") == seeded["state"]["cycle_id"])
    assert report["payload"]["safe_fallback"]
    _, _, calls, behavior, record = client.portal.call(
        saved_evidence, service, seeded, game["agent"],
    )
    assert len(calls) == 2 and behavior["pending_requests"]
    return seeded, deepcopy(report), deepcopy(record)


async def await_reports(service):
    await asyncio.gather(*list(service.report_supplements.tasks.values()))


async def snapshot(service, game, seeded):  # noqa: F811
    async with service.rooms.database.sessions() as session:
        room = await service.rooms.room(session, game["room"]["id"])
        runs = list(await session.scalars(select(AgentRun).where(
            AgentRun.room_id == room.id, AgentRun.graph_node == NODE,
        ).order_by(AgentRun.created_at)))
        behavior = await session.get(AgentBehaviorRecord, (room.id, game["agent"]))
        record = await session.get(ActionPlanRecord, seeded["state"]["cycle_id"])
        receipts = list(await session.scalars(select(ToolReceipt).where(
            ToolReceipt.room_id == room.id,
        )))
        calls = list(await session.scalars(select(AgentModelCall).where(
            AgentModelCall.run_id.in_([r.id for r in runs]),
        ))) if runs else []
        return {"runs": [{"id": r.id, "status": r.status, "context": deepcopy(r.context),
                          "output": deepcopy(r.structured_output), "error": r.safe_error}
                         for r in runs], "calls": [deepcopy(c.document) for c in calls],
                "behavior": deepcopy(behavior.document), "record": deepcopy(record.document),
                "world": deepcopy(room.session_state), "receipts": [r.id for r in receipts]}


@pytest.mark.parametrize("retained", [True, False, "legacy_requester"])
def test_http_supplement_uses_zero_or_one_call_and_settles_original_request(
    client, game, monkeypatch, retained,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded, original, original_record = failed_report(client, game, monkeypatch, retained)
    if retained == "legacy_requester":
        async def legacy_request(session, room):
            cycle = await session.get(AgentCycle, seeded["state"]["cycle_id"])
            request = {k: v for k, v in seeded["request"].items() if k != "requester_member_id"}
            cycle.state = {**cycle.state, "request_operands": {request["key"]: request}}
            behavior = await session.get(AgentBehaviorRecord, (room.id, game["agent"]))
            behavior.document = {**behavior.document, "pending_requests": [request]}
        client.portal.call(service.mutate, seeded["state"]["room_id"], legacy_request)
    before = client.portal.call(snapshot, service, game, seeded)
    responses = [] if retained else [{"answer_parts": [
        {"requirement_id": RESULT_ID, "text": seeded["result"]},
    ]}]
    service.model.adapter = FakeModelAdapter(responses)
    available = ok(client.get(game["prefix"]))["game"]["report_recoveries"]
    assert next(r for r in available if r["report_seq"] == original["seq"])["available"]
    request = {"client_request_id": str(uuid4())}
    url = game["prefix"] + f"/reports/{original['seq']}/supplement"
    owner = headers(game["remote"]["member_token"])
    first = ok(client.post(url, json=request, headers=owner))
    duplicate = ok(client.post(url, json=request, headers=owner))
    assert (first["supplement"]["id"] == duplicate["supplement"]["id"]
            == request["client_request_id"])
    client.portal.call(await_reports, service)
    after = client.portal.call(snapshot, service, game, seeded)
    assert len(after["runs"]) == 1
    run = after["runs"][0]
    assert run["status"] == "completed", run["error"]
    assert len(after["calls"]) == (0 if retained else 1)
    assert run["context"]["model_call_count"] == len(after["calls"])
    assert [p["origin"] for p in run["context"]["part_origins"]] == ["model", "server"]
    assert after["record"] == original_record
    assert after["world"] == before["world"] and after["receipts"] == before["receipts"]
    assert after["behavior"]["task_status"] == "completed"
    assert not after["behavior"]["pending_requests"]
    completed = after["behavior"]["request_history"][0]
    assert completed["key"] == seeded["request"]["key"]
    assert completed["result_cycle_id"] == seeded["state"]["cycle_id"]
    assert completed["supplement_of"] == original["seq"]
    assert after["behavior"]["last_result"]["unknown_properties"][0]["status"] == "unknown"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    assert next(e for e in events if e["seq"] == original["seq"]) == original
    supplements = [e for e in events if e["payload"].get("supplement_of") == original["seq"]
                   and e["type"] == "keeper.narration"]
    assert len(supplements) == 1
    text = supplements[0]["payload"]["text"]
    assert text.startswith(INTRODUCTION + seeded["result"])
    assert "仅凭当时的检查结果" in text and "还不能确定" in text
    assert BAD not in text
    assert len([e for e in events if e["type"] == "clue.revealed"
                and e["seq"] == seeded["reveal_seq"]]) == 1
    assert len([e for e in events if e["type"] == "check.resolved"
                and e["payload"].get("cycle_id") == seeded["state"]["cycle_id"]]) == 1
    assert not next(r for r in ok(client.get(game["prefix"]))["game"]["report_recoveries"]
                    if r["report_seq"] == original["seq"])["available"]


def test_http_failed_supplement_does_not_auto_retry_and_alias_replay_is_durable(
    client, game, monkeypatch,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded, original, _ = failed_report(client, game, monkeypatch, False)
    started, release = asyncio.Event(), asyncio.Event()

    async def respond():
        started.set()
        await release.wait()
        return ModelResponse(structured={"answer_parts": [
            {"requirement_id": RESULT_ID, "text": seeded["result"] + BAD},
        ]})

    service.model.adapter = FakeModelAdapter([respond])
    url = game["prefix"] + f"/reports/{original['seq']}/supplement"
    first_id, alias_id = str(uuid4()), str(uuid4())
    ok(client.post(url, json={"client_request_id": first_id}))
    client.portal.call(started.wait)
    alias = ok(client.post(url, json={"client_request_id": alias_id}))
    assert alias["supplement"]["id"] == first_id
    client.portal.call(release.set)
    client.portal.call(await_reports, service)
    after = client.portal.call(snapshot, service, game, seeded)
    assert after["runs"][0]["status"] == "failed"
    assert len(after["calls"]) == 1 and after["behavior"]["pending_requests"]
    # Discard all process-local task bookkeeping. A repeated alias is bound by
    # the persisted request ledger, just as it is after a fresh service start.
    service.report_supplements = ReportSupplementService(service)
    replay = ok(client.post(url, json={"client_request_id": alias_id}))
    client.portal.call(await_reports, service)
    assert replay["supplement"]["id"] == first_id
    assert len(client.portal.call(snapshot, service, game, seeded)["calls"]) == 1
    service.model.adapter = FakeModelAdapter([{"answer_parts": [
        {"requirement_id": RESULT_ID, "text": seeded["result"]},
    ]}])
    ok(client.post(url, json={"client_request_id": str(uuid4())}))
    client.portal.call(await_reports, service)
    final = client.portal.call(snapshot, service, game, seeded)
    assert len(final["calls"]) == 2
    assert [r["status"] for r in final["runs"]] == ["failed", "completed"]


@pytest.mark.parametrize("invalid", ["receipt", "source", "scene"])
def test_http_supplement_rechecks_original_receipt_and_current_visible_context(
    client, game, monkeypatch, invalid,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded, original, _ = failed_report(client, game, monkeypatch)

    async def change(session, room):
        reveal = await session.get(RoomEvent, (room.id, seeded["reveal_seq"]))
        if invalid == "receipt":
            reveal.payload = {**reveal.payload, "cycle_id": "unrelated-cycle"}
        elif invalid == "source":
            reveal.visibility = "host_only"
        else:
            module = await service.module(session, room.id)
            other = next(s["id"] for s in module.document["scenes"]
                         if s["id"] != module.state["scene_id"])
            module.state = {**module.state, "scene_id": other}

    client.portal.call(service.mutate, seeded["state"]["room_id"], change)
    service.model.adapter = FakeModelAdapter()
    response = client.post(game["prefix"] + f"/reports/{original['seq']}/supplement",
                           json={"client_request_id": str(uuid4())})
    assert response.status_code == 409
    after = client.portal.call(snapshot, service, game, seeded)
    assert not after["runs"] and not after["calls"] and after["behavior"]["pending_requests"]


def test_unrelated_player_cannot_recover_another_request(client, game, monkeypatch):  # noqa: F811
    service = client.app.state.agent_service
    seeded, original, _ = failed_report(client, game, monkeypatch)
    ok(client.post(game["prefix"] + "/pause"))
    visitor = join(client, game["created"]["invite_code"], "旁观调查员")
    token = headers(visitor["member_token"])
    room = ok(client.get(game["prefix"], headers=token))
    assert not room["game"]["report_recoveries"]
    response = client.post(game["prefix"] + f"/reports/{original['seq']}/supplement",
                           json={"client_request_id": str(uuid4())}, headers=token)
    assert response.status_code == 403
    assert not client.portal.call(snapshot, service, game, seeded)["runs"]


def test_pausing_a_queued_supplement_persists_interruption_without_model_call(
    client, game, monkeypatch,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded, original, _ = failed_report(client, game, monkeypatch, False)
    service.model.adapter = FakeModelAdapter()
    semaphore = client.portal.call(model_semaphore)
    client.portal.call(semaphore.acquire)
    request_id = str(uuid4())
    url = game["prefix"] + f"/reports/{original['seq']}/supplement"
    try:
        ok(client.post(url, json={"client_request_id": request_id}))
        ok(client.post(game["prefix"] + "/pause"))
    finally:
        client.portal.call(semaphore.release)
    client.portal.call(await_reports, service)
    paused = client.portal.call(snapshot, service, game, seeded)
    assert paused["runs"][0]["status"] == "failed"
    assert paused["runs"][0]["context"]["model_call_count"] == 0
    assert not paused["calls"] and paused["behavior"]["pending_requests"]
    ok(client.post(game["prefix"] + "/resume"))
    ok(client.post(url, json={"client_request_id": request_id}))
    client.portal.call(await_reports, service)
    assert not client.portal.call(snapshot, service, game, seeded)["calls"]


def test_restore_before_completed_supplement_allows_new_explicit_recovery(
    client, game, monkeypatch,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded, original, _ = failed_report(client, game, monkeypatch)
    saved = ok(client.post(game["prefix"] + "/snapshots", json={"name": "仅缺报告"}))
    snapshot_id = saved["snapshot"]["id"]
    url = game["prefix"] + f"/reports/{original['seq']}/supplement"
    first_id = str(uuid4())
    ok(client.post(url, json={"client_request_id": first_id}))
    client.portal.call(await_reports, service)
    assert not client.portal.call(snapshot, service, game, seeded)["behavior"]["pending_requests"]
    ok(client.post(game["prefix"] + "/pause"))
    ok(client.post(game["prefix"] + f"/snapshots/{snapshot_id}/load"))
    ok(client.post(game["prefix"] + "/resume"))
    restored = client.portal.call(snapshot, service, game, seeded)
    assert restored["behavior"]["pending_requests"]
    available = ok(client.get(game["prefix"]))["game"]["report_recoveries"]
    assert next(r for r in available if r["report_seq"] == original["seq"])["available"]
    ok(client.post(url, json={"client_request_id": first_id}))
    client.portal.call(await_reports, service)
    assert client.portal.call(snapshot, service, game, seeded)["behavior"]["pending_requests"]
    ok(client.post(url, json={"client_request_id": str(uuid4())}))
    client.portal.call(await_reports, service)
    final = client.portal.call(snapshot, service, game, seeded)
    assert not final["behavior"]["pending_requests"]
    assert len(final["runs"]) == 2 and not final["calls"]


def test_requester_permission_is_rechecked_after_model_returns(
    client, game, monkeypatch,  # noqa: F811
):
    service = client.app.state.agent_service
    seeded, original, _ = failed_report(client, game, monkeypatch, False)
    started, release = asyncio.Event(), asyncio.Event()

    async def respond():
        started.set()
        await release.wait()
        return ModelResponse(structured={"answer_parts": [
            {"requirement_id": RESULT_ID, "text": seeded["result"]},
        ]})

    service.model.adapter = FakeModelAdapter([respond])
    ok(client.post(game["prefix"] + f"/reports/{original['seq']}/supplement",
                   json={"client_request_id": str(uuid4())},
                   headers=headers(game["remote"]["member_token"])))
    client.portal.call(started.wait)

    async def deactivate(session, room):
        requester = await session.get(RoomMember, game["player"])
        requester.active = False

    client.portal.call(service.mutate, seeded["state"]["room_id"], deactivate)
    client.portal.call(release.set)
    client.portal.call(await_reports, service)
    after = client.portal.call(snapshot, service, game, seeded)
    assert after["runs"][0]["status"] == "failed"
    assert "请求者当前已不在房间" in after["runs"][0]["error"]
    assert after["behavior"]["pending_requests"] and len(after["calls"]) == 1
