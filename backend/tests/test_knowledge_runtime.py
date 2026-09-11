import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_agent_runtime import accept_original, game, scenario, submit, wait_cycle  # noqa: F401
from test_rooms import headers, lobby, ok  # noqa: F401

from app.knowledge.schemas import GroundedClaim
from app.persistence.agent_models import AgentRun, ProfileRecord, RoomAgentBinding
from app.persistence.knowledge_models import RetrievalRecord
from app.rooms.service import RoomError


def test_run_evidence_excludes_uninjected_candidates_and_uncropped_text(
    client, rag_game, monkeypatch
):
    from app.knowledge.service import KnowledgeContextBuilder

    ok(submit(client, rag_game, "奖励骰规则"))
    assert wait_cycle(client, rag_game)["status"] == "completed"
    retriever = client.app.state.agent_service.knowledge.retriever
    search = retriever.search

    def extra_candidate(*args, **kwargs):
        found = search(*args, **kwargs)
        return found + [{**found[0], "evidence_id": "ev_not_injected"}] if found else []

    monkeypatch.setattr(retriever, "search", extra_candidate)
    monkeypatch.setattr(
        KnowledgeContextBuilder,
        "select",
        staticmethod(
            lambda evidence, budget, public: [{**evidence[0], "excerpt": "奖励骰增加一个候选"}]
        ),
    )

    async def verify():
        svc = client.app.state.agent_service
        async with svc.rooms.transaction() as session:
            room = await svc.rooms.room(session, rag_game["room"]["id"])
            original = await session.scalar(
                select(AgentRun).where(
                    AgentRun.room_id == room.id, AgentRun.graph_node == "plan_keeper_action"
                )
            )
            run = AgentRun(
                **{
                    column.name: getattr(original, column.name)
                    for column in AgentRun.__table__.columns
                    if column.name != "id"
                },
                id=str(uuid4()),
            )
            session.add(run)
            await session.flush()
            profile = await session.get(ProfileRecord, run.profile_id)
            binding = await session.scalar(
                select(RoomAgentBinding).where(
                    RoomAgentBinding.room_id == room.id,
                    RoomAgentBinding.member_id == run.actor_member_id,
                )
            )
            selected, records = await svc.knowledge.pre_context(
                session, room, binding, profile, run.id, run.context, 1000
            )
            actual = await svc.knowledge.evidence_for_run(session, run.id, room.id, run.profile_id)
            assert actual == {e["evidence_id"]: e for e in selected}
            assert next(iter(actual.values()))["excerpt"] == "奖励骰增加一个候选"
            assert any(r.source_filters["candidate_count"] > len(r.evidence) for r in records)

    client.portal.call(verify)


@pytest.fixture
def rag_game(client, game):  # noqa: F811
    service = client.app.state.agent_service
    data = service.settings.data_dir
    (data / "rules").mkdir(exist_ok=True)
    rule = data / "rules/原创规则.txt"
    rule.write_text(
        "奖励骰增加一个候选结果。技能检定使用百分骰。困难成功是一个难度标签。", encoding="utf-8"
    )
    folder = data / "modules/原创钟楼"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps({"module_id": "stopped-clock"}), encoding="utf-8"
    )
    (folder / "开场.txt").write_text("维修间工作台检定调查。私密测试标记紫月。", encoding="utf-8")
    other = data / "modules/另一个模组"
    other.mkdir()
    (other / "原文.txt").write_text("维修间工作台检定调查。不可混入的红门。", encoding="utf-8")
    service.knowledge.indexer.index()
    sources = service.knowledge.repository.sources()
    refs = {s.title: {"source_id": s.source_id, "source_hash": s.source_hash} for s in sources}
    binding = {"rules": [refs["原创规则.txt"]], "module": refs["原创钟楼"], "enabled": True}
    ok(client.post(game["prefix"] + "/pause"))
    ok(client.patch(game["prefix"] + "/knowledge", json=binding))
    ok(client.post(game["prefix"] + "/resume"))

    def responder(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if context.get("response_brief"):
            rules = context.get("RULE_EVIDENCE", [])
            if rules:
                return {
                    "public_narration": "奖励骰增加一个候选结果。",
                    "grounded_claims": [
                        {
                            "claim_id": "rule-1",
                            "category": "rule",
                            "statement": "奖励骰增加一个候选结果。",
                            "evidence_ids": [rules[0]["evidence_id"]],
                        }
                    ],
                }
            return {
                "claim_ids": [c["claim_id"] for c in context["PUBLIC_CLAIM_OPTIONS"][:1]],
            }
        response = scenario(messages, kwargs)
        if context["phase"] == "plan_keeper_action":
            response["tools"].insert(
                0, {"name": "search_module", "arguments": {"query": "维修间工作台检定调查"}}
            )
            response["tools"].insert(0, {"name": "search_rules", "arguments": {"query": "奖励骰"}})
        return response

    game["adapter"].responder = responder
    return {**game, "binding": binding, "rule_file": rule, "refs": refs}


def test_rag_fake_cycle_interrupt_tools_claims_visibility_and_exports(client, rag_game):
    game = rag_game  # noqa: F811
    ok(submit(client, game, "奖励骰规则；检查维修间工作台并请求检定；我冒着失去平衡的风险尝试。"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "waiting_for_roll", cycle
    check = ok(client.get(game["prefix"] + "/checks"))[-1]
    ok(
        client.post(
            game["prefix"] + f"/checks/{check['id']}/roll",
            json={},
            headers=headers(game["remote"]["member_token"]),
        )
    )
    accept_original(client, game, check["id"])
    completed = wait_cycle(client, game, ("completed", "failed"))
    assert completed["id"] == cycle["id"] and completed["status"] == "completed", completed
    runs = ok(client.get(game["prefix"] + "/agent-runs"))
    audits = []
    for run in runs:
        audits.extend(ok(client.get(game["prefix"] + f"/agent-runs/{run['id']}/retrievals")))
        if run["graph_node"] in {"decide_teammates", "generate_keeper_narration"}:
            assert "私密测试标记紫月" not in json.dumps(run["context"], ensure_ascii=False)
        assert run["model_calls"]
    assert audits and any(a["injected_ids"] for a in audits)
    module_hits = [e for a in audits for e in a["evidence"] if e["source_kind"] == "module"]
    assert module_hits and all(
        e["source_hash"] == game["binding"]["module"]["source_hash"] for e in module_hits
    )
    assert all(e["source_title"] == "原创钟楼" for e in module_hits)
    for format_name in ("jsonl", "markdown"):
        response = client.get(
            game["prefix"] + f"/logs?format={format_name}",
            headers=headers(game["remote"]["member_token"]),
        )
        assert response.status_code == 200
        assert "私密测试标记紫月" not in response.text and "不可混入的红门" not in response.text
        assert "ev_" in response.text and "奖励骰" in response.text
    assert (
        client.get(
            game["prefix"] + f"/evidence/{module_hits[0]['evidence_id']}",
            headers=headers(game["remote"]["member_token"]),
        ).status_code
        == 404
    )
    narrator = next(r for r in runs if r["graph_node"] == "generate_keeper_narration")
    citation = narrator["structured_output"]["grounded_claims"][0]["evidence_ids"][0]
    assert (
        client.get(
            game["prefix"] + f"/evidence/{citation}",
            headers=headers(game["remote"]["member_token"]),
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "case",
    [
        "forged",
        "wrong_run",
        "wrong_edition",
        "wrong_hash",
        "rule_without_evidence",
        "module_without_evidence",
        "private_to_public",
        "fake_entity",
        "unrelated_rule",
        "flavor_world_fact",
    ],
)
def test_grounding_rejects_invalid_claims(client, rag_game, case):
    game = rag_game  # noqa: F811
    ok(submit(client, game, "奖励骰规则"))
    assert wait_cycle(client, game)["status"] == "completed"

    async def verify():
        svc = client.app.state.agent_service
        async with svc.rooms.database.sessions() as session:
            room = await svc.rooms.room(session, game["room"]["id"])
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.room_id == room.id, AgentRun.graph_node == "plan_keeper_action"
                )
            )
            rows = list(
                await session.scalars(
                    select(RetrievalRecord).where(RetrievalRecord.run_id == run.id)
                )
            )
            rule = next(e for r in rows for e in r.evidence if e["source_kind"] == "rulebook")
            module = next(e for r in rows for e in r.evidence if e["source_kind"] == "module")
            claim = dict(
                claim_id="x",
                category="rule",
                statement="奖励骰增加一个候选结果。",
                evidence_ids=[rule["evidence_id"]],
            )
            if case == "forged":
                claim["evidence_ids"] = ["ev_forged"]
            if case == "wrong_run":
                run.id = str(uuid4())
            if case in {"wrong_edition", "wrong_hash"}:
                for row in rows:
                    row.evidence = [
                        {**e, "edition": "coc6"}
                        if case == "wrong_edition"
                        else {**e, "source_hash": "0" * 64}
                        for e in row.evidence
                    ]
            if case == "rule_without_evidence":
                claim["evidence_ids"] = []
            if case == "module_without_evidence":
                claim.update(category="module_fact", evidence_ids=[])
            if case == "private_to_public":
                claim.update(
                    category="module_fact",
                    statement="私密测试标记紫月。",
                    evidence_ids=[module["evidence_id"]],
                )
            if case == "fake_entity":
                claim.update(category="module_fact", evidence_ids=[], entity_ids=["invented"])
            if case == "unrelated_rule":
                claim["statement"] = "奖励骰固定增加 900 点。"
            if case == "flavor_world_fact":
                claim.update(
                    category="flavor", evidence_ids=[], statement="你发现了新的钥匙和角色。"
                )
            with session.no_autoflush:
                with pytest.raises(RoomError):
                    await svc.knowledge.validate_claim(session, room, run, GroundedClaim(**claim))

    client.portal.call(verify)


def test_knowledge_host_api_and_unbound_player_query(client, rag_game):
    game = rag_game  # noqa: F811
    player = headers(game["remote"]["member_token"])
    for method, path, body in [
        ("GET", "/api/knowledge/sources", None),
        ("POST", "/api/knowledge/index", {"kind": "all"}),
        ("POST", "/api/knowledge/query", {"query": "开场", "kind": "module"}),
    ]:
        assert client.request(method, path, json=body, headers=player).status_code == 401
    assert (
        client.patch(
            game["prefix"] + "/knowledge", json=game["binding"], headers=player
        ).status_code
        == 403
    )
    rules = ok(
        client.post(game["prefix"] + "/knowledge/query", json={"query": "奖励骰"}, headers=player)
    )
    assert rules and all(e["visibility"] == "public_rules" for e in rules)
    assert (
        client.post(
            game["prefix"] + "/knowledge/query",
            json={"query": "开场", "kind": "module"},
            headers=player,
        ).status_code
        == 422
    )
    assert client.get(game["prefix"] + "/evidence/ev_forged", headers=player).status_code == 404


def test_binding_save_version_retention_missing_index_and_memory(client, rag_game):
    game = rag_game  # noqa: F811
    ok(submit(client, game, "奖励骰规则"))
    assert wait_cycle(client, game)["status"] == "completed"
    original = game["binding"]
    saved = ok(client.post(game["prefix"] + "/snapshots", json={"name": "RAG save"}))["snapshot"]
    for i in range(32):
        ok(
            client.post(
                game["prefix"] + "/messages",
                json={"text": f"公开无关消息 {i}", "client_request_id": str(uuid4())},
            )
        )
    ok(submit(client, game, "继续调查"))
    assert wait_cycle(client, game)["status"] == "completed"
    memories = ok(client.get(game["prefix"] + "/memories"))
    assert any(
        original["rules"][0]["source_hash"] in m["content"]
        for m in memories
        if m["kind"] == "observation"
    )
    svc = client.app.state.agent_service
    game["rule_file"].write_text("新的规则版本。", encoding="utf-8")
    svc.knowledge.indexer.index("rules")
    ok(client.post(game["prefix"] + "/pause"))
    ok(client.post(game["prefix"] + f"/snapshots/{saved['id']}/load"))
    binding = ok(client.get(game["prefix"] + "/knowledge"))
    assert binding["rules"] == original["rules"] and binding["module"] == original["module"]
    assert not binding["knowledge_missing"]
    svc.knowledge.repository.path.rename(svc.knowledge.repository.path.with_suffix(".offline"))
    assert ok(client.get(game["prefix"] + "/knowledge"))["knowledge_missing"]
    ok(client.post(game["prefix"] + "/resume"))
    assert submit(client, game, "新的行动").status_code == 409


@pytest.mark.parametrize("scene_evasion", [False, True])
def test_unsupported_rule_request_needs_host_ruling(client, rag_game, scene_evasion):
    game = rag_game  # noqa: F811
    original = game["adapter"].responder
    attempted = []

    def responder(messages, kwargs):
        context = json.loads(messages[-1]["content"])
        if context.get("response_brief"):
            attempted.append(True)
            if scene_evasion:
                scene = next(
                    c for c in context["PUBLIC_CLAIM_OPTIONS"] if c["category"] == "module_fact"
                )
                return {
                    "public_narration": scene["statement"],
                    "grounded_claims": [scene],
                }
            return {
                "public_narration": "未验证规则为9999",
                "grounded_claims": [
                    {
                        "claim_id": "bad",
                        "category": "rule",
                        "statement": "未验证规则为9999",
                        "evidence_ids": ["ev_fake"],
                    }
                ],
            }
        return original(messages, kwargs)

    game["adapter"].responder = responder
    ok(submit(client, game, "未知量子跃迁规则"))
    assert wait_cycle(client, game)["status"] == "completed"
    assert attempted
    log = client.get(
        game["prefix"] + "/logs?format=jsonl", headers=headers(game["remote"]["member_token"])
    ).text
    published = [json.loads(line)["payload"].get("text", "") for line in log.splitlines()]
    assert any("需要主持人裁定" in text for text in published)
    assert all("未验证规则为9999" not in text for text in published)
