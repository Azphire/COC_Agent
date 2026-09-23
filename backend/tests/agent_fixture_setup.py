"""Prerequisites for fixtures that exercise enabled Agent rooms."""

from test_rooms import ok


def bind_fixture_rules(client, prefix):
    knowledge = client.app.state.agent_service.knowledge
    path = client.app.state.settings.data_dir / "rules" / "CoC7-fixture.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text("第七版技能检定使用百分骰，结果不高于技能值时成功。", encoding="utf-8")
    knowledge.indexer.index("rules")
    source = next(s for s in knowledge.repository.sources() if s.title == path.name)
    binding = ok(client.get(prefix + "/knowledge"))
    ok(client.patch(prefix + "/knowledge", json={
        "enabled": True, "module": binding["module"],
        "rules": [{"source_id": source.source_id, "source_hash": source.source_hash}],
    }))
