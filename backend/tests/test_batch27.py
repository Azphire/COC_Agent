"""Remote character submission, transaction and privacy checks; zero model calls."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_batch26 import custom
from test_coc7_creation import create_seventh
from test_rooms import create_room, headers, join, ok

from app.dice.service import DiceService
from app.main import create_app


@pytest.fixture
def submission_lobby(client, monkeypatch):
    card = create_seventh(client)
    specs = [custom(), custom("art_craft", "陶艺"), custom("science", "地球物理学")]
    language, art, science = [s["id"] for s in specs]
    card = ok(
        client.patch(
            f"/api/characters/{card['id']}",
            json={
                "version": card["version"],
                "occupation": "professor",
                "custom_specializations": specs,
                "occupation_group_choices": {
                    "language": [language],
                    "academic": ["history", "biology", "chemistry", "occult"],
                },
                "selected_specializations": [art, science],
                "occupation_skills": {"credit_rating": {"points": 20}, language: {"points": 30}},
                "interest_skills": {art: {"points": 20}, science: {"points": 10}},
                "background": {"beliefs": "PRIVATE-SUBMISSION-BACKGROUND"},
            },
        )
    )
    assert card["validation"]["valid"]
    document = ok(client.get(f"/api/characters/{card['id']}/export"))
    # Even a claimed finalized status and fake final values must be recalculated.
    document["character"]["status"] = "finalized"
    document["character"]["skill_values"][science] = 999
    document["character"]["derived_values"]["hp"] = 999
    created = create_room(client)
    owner = join(client, created["invite_code"], "提交者")
    other = join(client, created["invite_code"], "另一成员")
    prefix = f"/api/rooms/{created['room']['id']}"

    def no_dice(*args, **kwargs):
        raise AssertionError("Import, approval and startup must not generate dice")

    monkeypatch.setattr(DiceService, "roll", no_dice)
    return dict(
        document=document,
        specs=specs,
        card=card,
        owner=owner,
        other=other,
        prefix=prefix,
        endpoint=prefix + "/character-submissions",
        created=created,
    )


def submit(client, lobby, version=0, document=None, request_id=None):
    body = dict(
        document=document or lobby["document"],
        expected_version=version,
        client_request_id=request_id or str(uuid4()),
    )
    result = client.post(
        lobby["endpoint"], headers=headers(lobby["owner"]["member_token"]), json=body
    )
    return result, body


def review(client, lobby, row, decision="accept", **extra):
    return client.post(
        lobby["endpoint"] + f"/{row['id']}/review",
        json={
            "expected_version": row["version"],
            "decision": decision,
            "client_request_id": str(uuid4()),
            **extra,
        },
    )


def rows(client, lobby, token=None):
    return ok(client.get(lobby["endpoint"], **({"headers": headers(token)} if token else {})))


def test_recalculation_privacy_versions_accept_and_start(client, submission_lobby):
    b = submission_lobby
    owner, other = b["owner"]["member_token"], b["other"]["member_token"]
    count = len(ok(client.get("/api/characters")))
    preview = ok(
        client.post(
            b["endpoint"] + "/preview", headers=headers(owner), json={"document": b["document"]}
        )
    )
    assert preview["status"] == "draft" and preview["derived_values"]["hp"] == 12
    assert preview["skill_values"][b["specs"][2]["id"]] == 11
    result, body = submit(client, b)
    ok(result)
    first = rows(client, b)[0]
    assert len(ok(client.get("/api/characters"))) == count
    assert rows(client, b, owner)[0] == first
    assert "character" not in rows(client, b, other)[0]
    assert client.get(b["endpoint"] + "/" + first["id"], headers=headers(other)).status_code == 403
    ok(client.post(b["endpoint"], headers=headers(owner), json=body))
    assert len(rows(client, b)) == 1 and rows(client, b)[0]["version"] == 1
    ok(submit(client, b, 1)[0])
    second = rows(client, b)[0]
    assert second["version"] == 2
    assert review(client, b, first).status_code == 409
    assert client.get(b["endpoint"] + "/" + first["id"]).json()["status"] == "rejected"
    assert submit(client, b, 1)[0].status_code == 409
    request_id = str(uuid4())
    accepted = ok(review(client, b, second, client_request_id=request_id))["room"]
    slot = accepted["character_slots"][0]
    assert slot["member_id"] == b["owner"]["room"]["self_member_id"]
    final = slot["character_snapshot"]
    assert final["status"] == "finalized"
    assert final["custom_specializations"] == b["specs"]
    assert final["skill_values"][b["specs"][2]["id"]] == 11
    assert all(r["source"] == "imported" for r in final["roll_records"])
    for actual, original in zip(final["roll_records"], b["card"]["roll_records"], strict=True):
        assert {k: v for k, v in actual.items() if k not in {"source", "id"}} == {
            k: v for k, v in original.items() if k not in {"source", "id"}
        }
    assert len(ok(client.get("/api/characters"))) == count + 1
    revision = accepted["revision"]
    assert (
        ok(review(client, b, second, client_request_id=request_id))["room"]["revision"] == revision
    )
    assert review(client, b, second).status_code == 409
    for suffix in ("", "/events", "/logs"):
        text = client.get(b["prefix"] + suffix, headers=headers(other)).text
        assert "PRIVATE-SUBMISSION-BACKGROUND" not in text
        assert b["specs"][0]["name"] not in text
    # Existing local publish and remote self-selection still work for the other player.
    local = ok(
        client.post(
            f"/api/characters/{b['card']['id']}/finalize", json={"version": b["card"]["version"]}
        )
    )
    published = ok(
        client.post(b["prefix"] + "/character-slots", json={"character_id": local["id"]})
    )["room"]
    empty = next(s for s in published["character_slots"] if not s["member_id"])
    ok(
        client.post(
            b["prefix"] + "/character-assignments",
            headers=headers(other),
            json={"slot_id": empty["id"]},
        )
    )
    for token in (owner, other):
        ok(client.post(b["prefix"] + "/ready", headers=headers(token), json={"ready": True}))
    running = ok(client.post(b["prefix"] + "/start"))["room"]
    assert running["status"] == "running"
    assert running["session_state"]["characters"][slot["id"]]["hp"] == 12
    assert submit(client, b, 2)[0].status_code == 409
    ok(client.post(b["prefix"] + "/pause"))
    assert submit(client, b, 2)[0].status_code == 409
    assert review(client, b, second).status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        "age",
        "occupation",
        "points",
        "equipment",
        "specialization",
        "ruleset",
        "metadata",
        "verification",
        "roll_missing",
        "roll_total",
        "attribute_pool",
    ],
)
def test_invalid_submission_never_creates_drafts(client, submission_lobby, change):
    b = submission_lobby
    document = deepcopy(b["document"])
    c = document["character"]
    if change == "age":
        c["age"] = 90
    elif change == "occupation":
        c["occupation"] = "unknown"
    elif change == "points":
        c["interest_skills"]["spot_hidden"] = {"points": 1000}
    elif change == "equipment":
        c["equipment"] = [{"id": "bad", "catalog_id": "unknown", "quantity": 1}]
    elif change == "specialization":
        c["custom_specializations"][0]["name"] = "拉丁语"
    elif change == "ruleset":
        c["ruleset_version"] = document["ruleset"]["version"] = "99.0"
    elif change == "metadata":
        document["ruleset"]["id"] = "wrong"
    elif change == "verification":
        document["ruleset"]["verification_status"] = "unverified"
    elif change == "roll_missing":
        c["roll_records"] = []
    elif change == "roll_total":
        c["roll_records"][0]["total"] += 1
    elif change == "attribute_pool":
        c["attributes"]["str"]["value"] = 61
    result, _ = submit(client, b, document=document)
    assert result.status_code == 422, result.text
    assert rows(client, b) == []
    assert len(ok(client.get("/api/characters"))) == 1


def test_boundaries_chunked_size_and_impersonation(client, submission_lobby):
    b = submission_lobby
    other_room = create_room(client)
    foreign = f"/api/rooms/{other_room['room']['id']}/character-submissions"
    owner_headers = headers(b["owner"]["member_token"])
    _, body = submit(client, b)
    row = rows(client, b)[0]
    for path in (foreign, foreign + "/" + row["id"]):
        assert client.get(path, headers=owner_headers).status_code == 401
    assert client.get(foreign + "/" + row["id"]).status_code == 404
    assert client.post(foreign, headers=owner_headers, json=body).status_code == 401
    assert client.post(b["endpoint"], json=body).status_code == 403
    assert (
        client.post(
            b["endpoint"],
            headers=owner_headers,
            json={**body, "member_id": b["other"]["room"]["self_member_id"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            b["endpoint"] + "/" + row["id"] + "/review",
            headers=owner_headers,
            json={"decision": "accept", "expected_version": 1, "client_request_id": str(uuid4())},
        ).status_code
        == 403
    )
    assert client.get("/api/characters", headers=owner_headers).status_code == 401
    for content in (b"x" * (256 * 1024 + 1), iter([b"x" * 200000, b"x" * 100000])):
        assert client.post(b["endpoint"], headers=owner_headers, content=content).status_code == 413
    assert client.post(b["endpoint"], headers=owner_headers, content=b"not json").status_code == 422
    changed = deepcopy(body)
    changed["document"]["character"]["name"] = "changed"
    assert client.post(b["endpoint"], headers=owner_headers, json=changed).status_code == 409


def test_rollback_then_retry_reject_and_occupied_member(
    client, character_app, submission_lobby, monkeypatch
):
    b = submission_lobby
    ok(submit(client, b)[0])
    row = rows(client, b)[0]
    service = character_app.state.room_service
    original = service.apply

    async def fail_after_assign(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[3] == "assign":
            raise RuntimeError("injected failure after slot assignment")
        return result

    request_id = str(uuid4())
    with monkeypatch.context() as patch:
        patch.setattr(service, "apply", fail_after_assign)
        with pytest.raises(RuntimeError, match="injected failure"):
            review(client, b, row, client_request_id=request_id)
    assert rows(client, b)[0]["status"] == "pending"
    assert len(ok(client.get("/api/characters"))) == 1
    assert ok(client.get(b["prefix"]))["character_slots"] == []
    accepted = ok(review(client, b, row, client_request_id=request_id))["room"]
    assert len(accepted["character_slots"]) == 1
    ok(submit(client, b, 1)[0])
    new = rows(client, b)[0]
    assert review(client, b, new).status_code == 409
    ok(review(client, b, new, "reject", reason="PRIVATE-REJECTION"))
    assert rows(client, b)[0]["reason"] == "PRIVATE-REJECTION"
    assert "PRIVATE-REJECTION" not in json.dumps(rows(client, b, b["other"]["member_token"]))
    assert len(ok(client.get("/api/characters"))) == 2
    ok(submit(client, b, 2)[0])
    last = rows(client, b)[0]
    ok(
        client.delete(
            b["prefix"] + "/character-assignments/" + accepted["character_slots"][0]["id"]
        )
    )
    ok(review(client, b, last))
    events = ok(client.get(b["prefix"] + "/events"))["events"]
    assert sum(e["type"] == "character.published" for e in events) == 2
    assert sum(e["type"] == "character.assigned" for e in events) == 2


def test_restart_and_websocket_filters(client, character_settings, submission_lobby):
    b = submission_lobby
    ok(submit(client, b)[0])
    row = rows(client, b)[0]
    with TestClient(create_app(character_settings)) as restarted:
        assert rows(restarted, b, b["owner"]["member_token"])[0] == row
        for who in ("owner", "other"):
            with restarted.websocket_connect("/ws/rooms/" + b["created"]["room"]["id"]) as ws:
                ws.send_json(
                    {
                        "type": "auth",
                        "credential_type": "member",
                        "token": b[who]["member_token"],
                        "after_seq": 0,
                    }
                )
                messages = []
                while True:
                    message = ws.receive_json()
                    messages.append(message)
                    if message["type"] == "room.synced":
                        break
                raw = json.dumps(messages, ensure_ascii=False)
                assert ("PRIVATE-SUBMISSION-BACKGROUND" in raw) == (who == "owner")
                assert (b["specs"][2]["name"] in raw) == (who == "owner")
    with sqlite3.connect(character_settings.data_dir / "characters.db") as db:
        assert db.execute("SELECT count(*) FROM room_character_submissions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM character_drafts").fetchone()[0] == 1


def test_committed_accept_retry_after_lost_response_and_restart(
    client, character_app, character_settings, submission_lobby, monkeypatch
):
    b = submission_lobby
    ok(submit(client, b)[0])
    row = rows(client, b)[0]
    request_id = str(uuid4())

    async def lost_response(*args, **kwargs):
        raise RuntimeError("injected lost response after commit")

    with monkeypatch.context() as patch:
        patch.setattr(character_app.state.room_service, "broadcast", lost_response)
        with pytest.raises(RuntimeError, match="lost response"):
            review(client, b, row, client_request_id=request_id)
    with TestClient(create_app(character_settings), headers=client.headers) as restarted:
        retried = ok(review(restarted, b, row, client_request_id=request_id))["room"]
        assert len(retried["character_slots"]) == 1
        assert len(ok(restarted.get("/api/characters"))) == 2
        events = ok(restarted.get(b["prefix"] + "/events"))["events"]
        for kind in ("character.published", "character.assigned", "character.submission.accepted"):
            assert sum(e["type"] == kind for e in events) == 1


def test_concurrent_retries_create_one_slot(client, submission_lobby):
    b = submission_lobby
    ok(submit(client, b)[0])
    row = rows(client, b)[0]
    request_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(lambda _: review(client, b, row, client_request_id=request_id), range(2))
        )
    results = [ok(response)["room"] for response in responses]
    assert results[0]["revision"] == results[1]["revision"]
    assert all(len(room["character_slots"]) == 1 for room in results)
    assert len(ok(client.get("/api/characters"))) == 2
