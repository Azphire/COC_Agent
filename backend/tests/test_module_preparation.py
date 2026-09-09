import json
import time
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_rooms import headers, lobby, ok, prepare  # noqa: F401

from app.agents.model import FakeModelAdapter
from app.persistence.preparation_models import GenerationRun, ModuleEntity
from app.preparation.schemas import GenerationOutput, Scope


def wait_preparation(client, prep_id):
    for _ in range(300):
        prep = ok(client.get(f"/api/module-preparations/{prep_id}"))
        if prep["status"] != "extracting":
            return prep
        time.sleep(0.01)
    raise AssertionError("generation did not finish")


def generated(messages, kwargs):
    context = json.loads(messages[-1]["content"])
    evidence = context["evidence"]
    ref = evidence[0]["evidence_id"]
    return {
        "entities": [
            {
                "local_id": "hall",
                "type": "scene",
                "title": "候车厅",
                "keeper_summary": "私密标记紫月机关",
                "public_summary": "你们站在安静的候车厅。",
                "evidence_ids": [ref],
            },
            {
                "local_id": "platform",
                "type": "location",
                "title": "站台",
                "keeper_summary": "私密标记紫月机关",
                "public_summary": "外面是一座旧站台。",
                "evidence_ids": [ref],
            },
            {
                "local_id": "notice",
                "type": "clue",
                "title": "公告",
                "keeper_summary": "私密标记紫月机关",
                "public_summary": "公告提醒旅客保留车票。",
                "evidence_ids": [ref],
            },
            {
                "local_id": "ticket",
                "type": "item",
                "title": "车票",
                "public_summary": "一张纸质车票。",
                "evidence_ids": [ref],
            },
            {
                "local_id": "bad",
                "type": "clue",
                "title": "无依据草稿",
                "public_summary": "错误信息。",
                "evidence_ids": ["ev_forged"],
            },
            {
                "local_id": "hall_duplicate",
                "type": "scene",
                "title": "候车厅",
                "public_summary": "同一地点。",
                "evidence_ids": [ref],
            },
        ],
        "relations": [
            {
                "source_entity_id": "notice",
                "target_entity_id": "hall",
                "relation_type": "appears_in",
                "keeper_note": "隐藏关系测试",
                "evidence_ids": [ref],
            }
        ],
    }


@pytest.fixture
def preparation(client):
    svc = client.app.state.agent_service
    data = svc.settings.data_dir
    folder = data / "modules/原创候车厅"
    folder.mkdir(parents=True)
    source_file = folder / "开场.md"
    source_file.write_text(
        "# 开场\n候车厅里有站台、公告和车票。私密标记紫月机关。\n"
        "公告提醒旅客保留车票。站台边有一本尚未批准的时刻表。",
        encoding="utf-8",
    )
    other = data / "modules/另一模组"
    other.mkdir()
    (other / "原文.txt").write_text("不能混入的赤红宝石。候车厅。", encoding="utf-8")
    svc.knowledge.indexer.index("modules")
    source = next(s for s in svc.knowledge.repository.sources() if s.title == "原创候车厅")
    body = {
        "source_id": source.source_id,
        "source_hash": source.source_hash,
        "display_title": "原创开场",
        "scope": {"section": "开场"},
    }
    prep = ok(client.post("/api/module-preparations", json=body))
    svc.model.adapter = FakeModelAdapter(responder=generated)
    ok(client.post(f"/api/module-preparations/{prep['id']}/generate"))
    finished = wait_preparation(client, prep["id"])
    assert finished["status"] == "review_ready", finished
    entities = ok(client.get(f"/api/module-preparations/{prep['id']}/entities"))
    return {"prep": finished, "entities": entities, "body": body, "source_file": source_file}


def approve_opening(client, data):
    prep_id = data["prep"]["id"]
    entities = data["entities"]
    for entity in entities:
        action = "reject" if entity["validation_errors"] else "approve"
        ok(client.post(f"/api/module-entities/{entity['id']}/{action}"))
    scene = next(e for e in entities if e["type"] == "scene")
    required = [e["id"] for e in entities if e["type"] != "scene" and not e["validation_errors"]]
    ok(
        client.patch(
            f"/api/module-preparations/{prep_id}",
            json={"initial_scene_entity_id": scene["id"], "required_entity_ids": required},
        )
    )
    return ok(client.post(f"/api/module-preparations/{prep_id}/approve"))


def test_generation_bounded_provenance_dedup_and_host_review(client, preparation):
    data = preparation
    assert len(data["entities"]) == 5
    assert data["prep"]["generated_entity_count"] == 5
    assert data["prep"]["approved_entity_count"] == 0
    assert data["prep"]["model_call_count"] == 1
    assert client.app.state.agent_service.model.adapter.max_active == 1
    bad = next(e for e in data["entities"] if e["title"] == "无依据草稿")
    assert bad["validation_errors"] == ["evidence_not_in_generation_run"]
    assert client.post(f"/api/module-entities/{bad['id']}/approve").status_code == 422
    valid = next(e for e in data["entities"] if e["type"] == "scene")
    evidence = ok(
        client.get(
            f"/api/module-preparations/{data['prep']['id']}/evidence/{valid['evidence_ids'][0]}"
        )
    )
    assert (
        evidence["source_hash"] == data["body"]["source_hash"] and len(evidence["excerpt"]) <= 420
    )
    edited = ok(
        client.patch(
            f"/api/module-entities/{valid['id']}", json={"public_summary": "你们来到候车厅。"}
        )
    )
    assert edited["host_edited"] and edited["public_summary"] != edited["keeper_summary"]
    approved = approve_opening(client, data)
    assert approved["status"] == "approved" and approved["approved_entity_count"] == 4


def test_source_hash_scope_and_stale(client, preparation):
    data = preparation
    assert (
        client.post(
            "/api/module-preparations", json={**data["body"], "source_hash": "0" * 64}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/module-preparations", json={**data["body"], "scope": {"page_start": 999}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/module-preparations", json={**data["body"], "scope": {"section": "不存在"}}
        ).status_code
        == 422
    )
    data["source_file"].write_text("# 开场\n新的来源内容。", encoding="utf-8")
    client.app.state.agent_service.knowledge.indexer.index("modules")
    stale = ok(client.post(f"/api/module-preparations/{data['prep']['id']}/refresh-status"))
    assert stale["status"] == "stale"
    assert client.post(f"/api/module-preparations/{stale['id']}/approve").status_code == 409


def test_initial_scene_required_entities_and_host_authored(client, preparation):
    prep_id = preparation["prep"]["id"]
    assert client.post(f"/api/module-preparations/{prep_id}/approve").status_code == 422
    host_entity = ok(
        client.post(
            "/api/module-entities",
            json={
                "preparation_id": prep_id,
                "type": "npc",
                "title": "主机创作人物",
                "public_summary": "一位旅客。",
            },
        )
    )
    assert host_entity["generated_by"] == "host" and not host_entity["evidence_ids"]
    assert (
        ok(client.post(f"/api/module-entities/{host_entity['id']}/approve"))["status"] == "approved"
    )
    approve_opening(client, preparation)
    scene = next(e for e in preparation["entities"] if e["type"] == "scene")
    ok(client.post(f"/api/module-entities/{scene['id']}/draft"))
    assert client.post(f"/api/module-preparations/{prep_id}/approve").status_code == 422


@pytest.mark.parametrize("kind", ["foreign_run", "wrong_hash", "wrong_page"])
def test_evidence_validation_rejects_foreign_generation(client, preparation, kind):
    svc = client.app.state.agent_service
    good = next(e for e in preparation["entities"] if e["type"] == "scene")

    async def corrupt():
        async with svc.rooms.transaction() as session:
            entity = await session.get(ModuleEntity, good["id"])
            run = await session.get(GenerationRun, entity.generation_run_id)
            if kind == "foreign_run":
                other = GenerationRun(
                    id=str(uuid4()),
                    preparation_id=entity.preparation_id,
                    status="completed",
                    evidence=[],
                    calls=[],
                )
                session.add(other)
                entity.generation_run_id = other.id
            elif kind == "wrong_hash":
                run.evidence = [{**e, "source_hash": "f" * 64} for e in run.evidence]
            else:
                entity.document = {**entity.document, "source_pages": [999]}

    client.portal.call(corrupt)
    assert client.post(f"/api/module-entities/{good['id']}/approve").status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/module-preparations",
        "/module-preparations/{id}",
        "/module-preparations/{id}/entities",
        "/module-preparations/{id}/relations",
    ],
)
def test_preparation_host_boundary(client, preparation, lobby, path):  # noqa: F811
    response = client.get(
        "/api" + path.format(id=preparation["prep"]["id"]),
        headers=headers(lobby["remote"]["member_token"]),
    )
    assert response.status_code == 401
    assert "私密标记" not in response.text


def test_binding_freeze_public_permissions_correction_and_exports(client, preparation, lobby):  # noqa: F811
    approved = approve_opening(client, preparation)
    prefix = lobby["prefix"]
    ok(client.patch(prefix + "/module-preparation", json={"preparation_id": approved["id"]}))
    clue = next(
        e for e in preparation["entities"] if e["type"] == "clue" and not e["validation_errors"]
    )
    player_headers = headers(lobby["remote"]["member_token"])
    public = ok(client.get(prefix + "/public-entities", headers=player_headers))
    assert len(public) == 1 and public[0]["type"] == "scene"
    assert "keeper_summary" not in json.dumps(public) and "私密标记" not in json.dumps(
        public, ensure_ascii=False
    )
    for suffix in ("host-entities", "review-requests"):
        assert client.get(prefix + "/" + suffix, headers=player_headers).status_code == 403
    assert (
        client.post(prefix + f"/entities/{clue['id']}/reveal", headers=player_headers).status_code
        == 403
    )
    first = ok(client.post(prefix + f"/entities/{clue['id']}/reveal"))
    repeat = ok(client.post(prefix + f"/entities/{clue['id']}/reveal"))
    assert first["result"]["event_seq"] == repeat["result"]["event_seq"]
    ok(client.post(f"/api/module-entities/{clue['id']}/draft"))
    ok(
        client.patch(
            f"/api/module-entities/{clue['id']}", json={"public_summary": "后来改动的草稿。"}
        )
    )
    public = ok(client.get(prefix + "/public-entities", headers=player_headers))
    assert (
        next(e for e in public if e["id"] == clue["id"])["public_summary"] == clue["public_summary"]
    )
    ok(
        client.post(
            prefix + f"/entities/{clue['id']}/correct",
            json={"public_summary": "公告提示妥善保管车票。", "reason": "主机修正"},
        )
    )
    public = ok(client.get(prefix + "/public-entities", headers=player_headers))
    corrected = next(e for e in public if e["id"] == clue["id"])
    assert corrected["state"] == "corrected" and corrected["correction_reference"]
    events = ok(client.get(prefix + "/events", headers=player_headers))["events"]
    assert (
        len(
            [
                e
                for e in events
                if e["type"] == "entity.revealed" and e["payload"]["id"] == clue["id"]
            ]
        )
        == 1
    )
    assert any(e["type"] == "entity.corrected" for e in events)
    for format in ("jsonl", "markdown"):
        response = client.get(prefix + "/logs", params={"format": format}, headers=player_headers)
        assert response.status_code == 200 and "私密标记" not in response.text
        assert "隐藏关系测试" not in response.text


def test_strict_generation_schema():
    with pytest.raises(ValidationError):
        Scope(page_start=4, page_end=2)
    with pytest.raises(ValidationError):
        GenerationOutput(entities=[{"local_id": "a", "type": "monster", "title": "越界类型"}])
    with pytest.raises(ValidationError):
        GenerationOutput(
            entities=[{"local_id": str(i), "type": "clue", "title": "线索"} for i in range(7)]
        )


def test_generation_repair_limit_preserves_existing_drafts(client, preparation):
    svc = client.app.state.agent_service
    before = preparation["prep"]["entity_count"]
    svc.model.adapter = FakeModelAdapter(responses=[{"bad": True}, {"bad": True}])
    prep_id = preparation["prep"]["id"]
    ok(client.post(f"/api/module-preparations/{prep_id}/generate"))
    failed = wait_preparation(client, prep_id)
    assert failed["status"] == "failed" and failed["entity_count"] == before
    assert len(svc.model.adapter.prompts) == 2
    assert len(failed["runs"][-1]["calls"]) == 2
    assert "bad" not in failed["safe_error"]


def test_empty_public_summary_cannot_be_revealed(client, preparation, lobby):  # noqa: F811
    entity = ok(
        client.post(
            "/api/module-entities",
            json={
                "preparation_id": preparation["prep"]["id"],
                "type": "item",
                "title": "尚未编写公开说明",
            },
        )
    )
    ok(client.post(f"/api/module-entities/{entity['id']}/approve"))
    approve_opening(client, preparation)
    ok(
        client.patch(
            lobby["prefix"] + "/module-preparation",
            json={"preparation_id": preparation["prep"]["id"]},
        )
    )
    assert client.post(lobby["prefix"] + f"/entities/{entity['id']}/reveal").status_code == 422


def test_preparation_relation_validation_and_frozen_binding(client, preparation, lobby):  # noqa: F811
    prep_id = preparation["prep"]["id"]
    relation = ok(client.get(f"/api/module-preparations/{prep_id}/relations"))[0]
    assert client.post(f"/api/module-relations/{relation['id']}/approve").status_code == 422
    approve_opening(client, preparation)
    ok(client.post(f"/api/module-relations/{relation['id']}/approve"))
    ok(client.post(f"/api/module-preparations/{prep_id}/approve"))
    ok(client.patch(lobby["prefix"] + "/module-preparation", json={"preparation_id": prep_id}))
    ok(client.post(f"/api/module-relations/{relation['id']}/reject"))
    assert ok(client.get(f"/api/module-preparations/{prep_id}"))["status"] == "review_ready"

    async def frozen():
        svc = client.app.state.agent_service
        async with svc.rooms.database.sessions() as session:
            return (await svc.entities.binding(session, lobby["room"]["id"])).relations

    assert client.portal.call(frozen)[0]["status"] == "approved"


def test_source_changes_preserve_old_room_and_cannot_bind_new_room(client, preparation, lobby):  # noqa: F811
    from test_rooms import create_room

    approve_opening(client, preparation)
    prep_id = preparation["prep"]["id"]
    ok(client.patch(lobby["prefix"] + "/module-preparation", json={"preparation_id": prep_id}))
    source_hash = preparation["body"]["source_hash"]
    preparation["source_file"].write_text("# 开场\n新版本开场内容。", encoding="utf-8")
    client.app.state.agent_service.knowledge.indexer.index("modules")
    newer = create_room(client)["room"]
    assert (
        client.patch(
            f"/api/rooms/{newer['id']}/module-preparation", json={"preparation_id": prep_id}
        ).status_code
        == 422
    )
    game = ok(client.get(lobby["prefix"]))["game"]
    assert game["preparation"]["source_hash"] == source_hash
    assert not game["knowledge"]["knowledge_missing"]


def test_host_input_path_redaction(client, preparation):
    entity = ok(
        client.post(
            "/api/module-entities",
            json={
                "preparation_id": preparation["prep"]["id"],
                "type": "item",
                "title": "主机笔记",
                "public_summary": r"来源 C:\Users\Private\secret.txt",
            },
        )
    )
    assert "C:" not in json.dumps(entity)


def test_scope_limits_physical_pages(client, tmp_path):
    import pymupdf

    svc = client.app.state.agent_service
    folder = svc.settings.data_dir / "modules/pages"
    folder.mkdir(parents=True)
    pdf = pymupdf.open()
    for text in ("Opening scene first page", "Second page clue", "Third page secret"):
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    pdf.save(folder / "pages.pdf")
    pdf.close()
    svc.knowledge.indexer.index("modules")
    source = svc.knowledge.repository.sources()[0]
    body = {
        "source_id": source.source_id,
        "source_hash": source.source_hash,
        "display_title": "指定页码",
        "scope": {"page_start": 2, "page_end": 2},
    }
    prep = ok(client.post("/api/module-preparations", json=body))
    seen = []

    def output(messages, kwargs):
        evidence = json.loads(messages[-1]["content"])["evidence"]
        seen.extend(evidence)
        return {
            "entities": [
                {
                    "local_id": "clue",
                    "type": "clue",
                    "title": "Clue",
                    "public_summary": "Second page clue",
                    "evidence_ids": [evidence[0]["evidence_id"]],
                    "source_pages": [2],
                }
            ]
        }

    svc.model.adapter = FakeModelAdapter(responder=output)
    ok(client.post(f"/api/module-preparations/{prep['id']}/generate"))
    assert wait_preparation(client, prep["id"])["status"] == "review_ready"
    assert seen and {e["physical_page"] for e in seen} == {2}
