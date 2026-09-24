"""Batch creation must be recoverable before any text generation or team check."""
# ruff: noqa: F811

import copy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_batch48_launch import launch  # noqa: F401
from test_module_preparation import preparation  # noqa: F401
from test_rooms import ok

from app.persistence.party_models import PartyBatch


def body(launch, count=1, role="party"):
    return {"request_id": str(uuid4()), "count": count,
            "preparation_id": launch["body"]["preparation_id"],
            "launch_draft_id": launch["draft"]["id"],
            "launch_draft_version": launch["draft"]["version"], "launch_role": role}


def requirements(client, launch, maximum):
    ok(client.post("/api/launch/preparations/" + launch["body"]["preparation_id"]
                  + "/requirements", json={"minimum_players": 1, "maximum_players": maximum,
                                            "source": "冻结人数来源页"}))


def test_create_atomically_links_batch_before_next_and_stale_version_cannot_replace(client, launch):
    request = body(launch)
    batch = ok(client.post("/api/party-batches", json=request))
    draft = ok(client.get(launch["path"]))
    assert draft["document"]["party_batch_id"] == batch["id"]
    assert draft["version"] == launch["draft"]["version"] + 1
    assert draft["document"]["ai_count"] == 1
    assert batch["metrics"]["model_calls"] == 0
    assert ok(client.post("/api/party-batches", json=request))["id"] == batch["id"]
    assert client.post("/api/party-batches", json=body(launch)).status_code == 409
    recovered = next(d for d in ok(client.get("/api/launch/options"))["drafts"]
                     if d["id"] == draft["id"])
    assert recovered["document"] == draft["document"]


@pytest.mark.parametrize("count,reserved,role", [(2, 0, "party"), (1, 1, "party"),
                                                (1, 2, "self")])
def test_player_limit_before_generation_creates_no_batch(client, launch, count, reserved, role):
    requirements(client, launch, 2)
    launch["draft"] = ok(client.patch(launch["path"], json={
        "version": launch["draft"]["version"], "reserved_humans": reserved, "ai_count": count,
    }))
    response = client.post("/api/party-batches", json=body(launch, count, role))
    assert response.status_code == 422, response.text

    async def batches():
        async with client.app.state.party_service.database.sessions() as session:
            return list(await session.scalars(select(PartyBatch)))

    assert client.portal.call(batches) == []


def test_legal_default_accounts_for_self_and_reserved_humans(client, launch):
    requirements(client, launch, 2)
    for reserved, expected in [(0, 1), (1, 0)]:
        draft = ok(client.post("/api/launch-drafts", json={
            **launch["body"], "client_request_id": str(uuid4()), "reserved_humans": reserved,
        }))
        assert draft["document"]["ai_count"] == expected


def test_own_batch_and_era_recovered_before_adoption(client, launch):
    launch["draft"] = ok(client.patch(launch["path"], json={
        "version": launch["draft"]["version"], "character_id": None, "era": "modern",
    }))
    batch = ok(client.post("/api/party-batches", json={**body(launch, role="self"),
                                                     "era": "modern"}))
    recovered = ok(client.get(launch["path"]))
    assert recovered["document"]["own_batch_id"] == batch["id"]
    assert recovered["document"]["era"] == "modern"
    assert recovered["document"]["character_id"] is None
    assert batch["metrics"]["model_calls"] == 0


def test_resize_preserves_retained_members_and_cumulative_history(client, launch):
    batch = ok(client.post("/api/party-batches", json=body(launch, 2)))
    member = copy.deepcopy(batch["members"][0])
    path = "/api/party-batches/" + batch["id"]
    resized = ok(client.post(path + "/resize", json={"request_id": str(uuid4()), "count": 1}))
    assert resized["members"][0] == member
    assert resized["seed"] == batch["seed"] and resized["count"] == 1
    draft = ok(client.get(launch["path"]))
    assert draft["document"]["ai_count"] == 1
    assert draft["document"]["party_batch_id"] == batch["id"]
    empty = ok(client.post(path + "/resize", json={"request_id": str(uuid4()), "count": 0}))
    assert empty["count"] == 0 and empty["status"] == "ready"
    assert ok(client.get(launch["path"]))["document"]["party_batch_id"] == batch["id"]


def test_reserved_players_allow_invitation_but_must_really_join_before_start(client, launch):
    ok(client.post("/api/launch/preparations/" + launch["body"]["preparation_id"]
                   + "/requirements", json={"minimum_players": 2, "maximum_players": 2,
                                             "source": "已核准两人边界"}))
    ok(client.patch(launch["path"], json={"version": launch["draft"]["version"],
                                        "ai_count": 0, "reserved_humans": 1}))
    assembled = ok(client.post(launch["path"] + "/assemble", json={}))
    assert assembled["room_id"], assembled
    blocked = ok(client.post(launch["path"] + "/start", json={}))
    assert blocked["status"] != "started"
    assert any(issue["code"] == "count" for issue in blocked["issues"])


def test_new_reservation_rechecks_count_before_first_model_call(client, launch):
    requirements(client, launch, 2)
    batch = ok(client.post("/api/party-batches", json=body(launch)))
    draft = ok(client.get(launch["path"]))
    ok(client.patch(launch["path"], json={"version": draft["version"], "reserved_humans": 1}))
    response = client.post("/api/party-batches/" + batch["id"] + "/next",
                           json={"request_id": str(uuid4())})
    assert response.status_code == 422, response.text
    after = ok(client.get("/api/party-batches/" + batch["id"]))
    assert after["metrics"]["model_calls"] == 0
