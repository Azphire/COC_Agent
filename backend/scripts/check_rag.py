"""Batch 4: three real isolated Chrome profiles, Fake or local Ollama RAG.

Only synthetic fixtures live in this script. Real opening text is supplied by the host
in an ignored JSON config. No raw module text or game credentials enter the report.
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from check_agent_cycle import AgentCheck, fake_scenario
from check_character_creation import BACKEND, ROOT, TEST_HOST, port_free, wait_for
from check_multiplayer import BrowserPage


def serve(database, config_path, real):
    import httpx
    import uvicorn

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app

    config = json.loads(config_path.read_text(encoding="utf-8"))
    database = database.resolve()
    if not database.is_relative_to((ROOT / ".cache").resolve()):
        raise ValueError("isolated_database_required")
    settings = Settings(
        _env_file=None,
        host_admin_token=TEST_HOST,
        data_dir=Path(config["data_dir"]),
        knowledge_db_path=Path(config["knowledge_database"]),
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        model_provider="ollama",
        model_name="qwen3:8b",
        model_base_url="http://127.0.0.1:11434/v1/",
        model_timeout_seconds=240,
        model_context_limit=8192,
        model_output_limit=1100,
    )
    app = create_app(settings)
    if not real:

        def response(messages, kwargs):
            context = json.loads(messages[-1]["content"])
            if context.get("phase") == "narrate_publicly":
                evidence = context.get("RULE_EVIDENCE", [])
                if evidence:
                    return {
                        "claims": [
                            {
                                "claim_id": "rule",
                                "category": "rule",
                                "statement": "奖励骰增加一个候选结果。",
                                "evidence_ids": [evidence[0]["evidence_id"]],
                            }
                        ]
                    }
                scene = context["module"]["scene"]
                return {
                    "claims": [
                        {
                            "claim_id": "scene",
                            "category": "module_fact",
                            "statement": scene["public_description"],
                            "entity_ids": [scene["id"]],
                        }
                    ]
                }
            result = fake_scenario(messages, kwargs)
            if context.get("phase") == "keeper_decide":
                result["tools"].insert(
                    0, {"name": "search_module", "arguments": {"query": "开场 工作台"}}
                )
                if len(result["tools"]) < 4:
                    result["tools"].insert(
                        0, {"name": "search_rules", "arguments": {"query": "奖励骰"}}
                    )
            return result

        app.state.agent_model_adapter = FakeModelAdapter(responder=response)

        def reject(*args, **kwargs):
            raise AssertionError("Fake smoke cannot call any model API")

        httpx.AsyncHTTPTransport.handle_async_request = reject
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


class RagCheck(AgentCheck):
    def __init__(self, config_path, real=False):
        super().__init__(real)
        self.config_path = config_path.resolve()
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.report.update(batch=4, started_at=datetime.now(timezone.utc).isoformat())
        self.prefix = None
        self.players = []
        self.backend = None

    def checkpoint_report(self):
        if not self.prefix:
            return
        try:
            room = self.request("GET", self.prefix)
            runs = self.request("GET", self.prefix + "/agent-runs")
            events = self.request("GET", self.prefix + "/events")["events"]
            audits = {
                run["id"]: self.request("GET", f"{self.prefix}/agent-runs/{run['id']}/retrievals")
                for run in runs
            }
            self.report.update(
                room_id=room["id"],
                binding=room["game"]["knowledge"],
                kp_actions=sum(e["type"] == "keeper.narration" for e in events),
                teammate_actions=sum(
                    e["type"] in {"agent.spoke", "agent.action_proposed"} for e in events
                ),
                checks=sum(e["type"] == "check.resolved" for e in events),
                grounded_narrations=sum(
                    e["type"] == "keeper.narration" and bool(e["payload"].get("claims"))
                    for e in events
                ),
                rule_citations=sum(
                    len(e["payload"].get("citations", []))
                    for e in events
                    if e["type"] == "keeper.narration"
                ),
                model_calls=sum(len(run["model_calls"]) for run in runs),
                model_latencies=[c["latency_ms"] for run in runs for c in run["model_calls"]],
                retrievals=sum(len(a) for a in audits.values()),
                module_retrievals=sum(
                    a["source_filters"]["kind"] == "module"
                    for rows in audits.values()
                    for a in rows
                ),
            )
            public = [e for e in events if e["visibility"] == "public"]
            names = {b["member_id"]: b["name"] for b in room["game"]["bindings"]}
            names.update(
                {m["id"]: m["display_name"] for m in room["members"] if m["id"] not in names}
            )
            lines = [
                "# Batch 4 development session",
                "",
                f"Time: {self.report['started_at']}",
                f"Room: {room['id'][:8]}",
                f"Module: {self.config['module_title']}",
                f"Source hash: {self.config['binding']['module']['source_hash'][:12]}",
                "Model: " + ("qwen3:8b" if self.real else "FakeModelAdapter"),
                "",
                "## PUBLIC",
                "",
            ]
            examples = []
            for event in public:
                payload = event["payload"]
                kind = event["type"]
                text = None
                if kind in {
                    "action.submitted",
                    "keeper.narration",
                    "agent.spoke",
                    "agent.action_proposed",
                    "agent.needs_host_ruling",
                }:
                    text = payload["text"]
                elif kind == "check.requested":
                    text = f"检定请求 {payload['name']} / {payload['difficulty']}"
                elif kind == "check.resolved":
                    result = payload["result"]
                    text = f"1D100={result['total']}, {result['level']}, passed={result['passed']}"
                elif kind == "clue.revealed":
                    text = "公开线索：" + payload["title"] + " / " + payload["content"]
                elif kind == "agent.cycle_changed":
                    text = (
                        f"cycle {payload['status']} / {payload['current_node']} / "
                        f"calls={payload.get('call_count', 0)}"
                    )
                if text is not None:
                    cycle = payload.get("cycle_id", "")[:8]
                    actor = payload.get("actor_name", names.get(event["actor_member_id"], "系统"))
                    line = (
                        f"- #{event['seq']} [{event['occurred_at']}] [{cycle}] "
                        f"[{kind}:{actor}] {text.replace(chr(10), ' ')}"
                    )
                    lines.append(line)
                    if kind in {"keeper.narration", "agent.spoke", "agent.action_proposed"}:
                        examples.append(line)
                    for citation in payload.get("citations", []):
                        lines.append(
                            f"  引用 {citation['evidence_id']} / {citation['source_title']} / "
                            f"{citation['page_kind']} p.{citation['physical_page']}"
                        )
            lines += [
                "",
                "## HOST_DEBUG",
                "",
                "Only query summaries, IDs, versions, timings and safe errors. "
                "No prompts or private excerpts.",
                "",
            ]
            for run in runs:
                lines.append(
                    f"- cycle={run['cycle_id'][:8]} run={run['id'][:8]} "
                    f"phase={run['graph_node']} status={run['status']}"
                )
                for call in run["model_calls"]:
                    lines.append(
                        f"  model={call['model']} attempt={call['attempt']} "
                        f"latency_ms={call['latency_ms']}"
                    )
                if run["safe_error"]:
                    lines.append(f"  error={run['safe_error']}")
                for tool in run["tool_results"]:
                    if not tool.get("ok"):
                        lines.append(f"  rejected tool={tool.get('tool')} code={tool.get('code')}")
                for audit in audits[run["id"]]:
                    lines.append(
                        f"  query={audit['query'][:80].replace(chr(10), ' ')} "
                        f"kind={audit['source_filters']['kind']} latency_ms={audit['latency_ms']}"
                    )
                    lines.append(
                        "  returned=" + ", ".join(e["evidence_id"] for e in audit["evidence"])
                    )
                    lines.append("  injected=" + ", ".join(audit["injected_ids"]))
                    lines.append(
                        "  sources="
                        + ", ".join(
                            sorted(
                                {
                                    e["source_title"] + "@" + e["source_hash"][:12]
                                    for e in audit["evidence"]
                                }
                            )
                        )
                    )
            lines.append("\nFinal cycle: " + json.dumps(room["game"]["cycle"], ensure_ascii=False))
            # Only selected control/status fields in the final cycle; never credentials.
            output = (
                ROOT
                / ".cache/batch-4"
                / ("常暗之厢-session.md" if self.real else "fake-session.md")
            )
            output.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.report["session_record"] = str(output.relative_to(ROOT))
            self.report["public_examples"] = examples[:6]
            self.report["cycles"] = list(dict.fromkeys(run["cycle_id"] for run in runs))
        except Exception:
            self.report["record_error"] = "could_not_finish_session_record"
            raise

    def run(self):
        import hashlib

        import httpx

        self.report["user_hashes_before"] = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (".env", "data/game.db")
        }
        for port in (8000, 5173):
            port_free(port)
        if self.real:
            with httpx.Client(trust_env=False, timeout=5) as ollama:
                tags = ollama.get("http://127.0.0.1:11434/api/tags").raise_for_status().json()
                assert any(m["name"] == "qwen3:8b" for m in tags["models"])
                self.report["ollama_before"] = ollama.get("http://127.0.0.1:11434/api/ps").json()
        database = self.directory / "rag-game.db"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            str(database),
            "--config",
            str(self.config_path),
        ] + (["--ollama"] if self.real else [])
        self.backend = self.start(command, BACKEND, "backend")
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
        self.start(
            [
                shutil.which("node"),
                str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
            ],
            ROOT / "frontend",
            "frontend",
        )
        wait_for(lambda: self.http.get("http://127.0.0.1:5173").status_code == 200)
        created = self.request("POST", "/rooms", {"name": "第四批 RAG 开场验收"})
        room = created["room"]
        self.prefix = prefix = f"/rooms/{room['id']}"
        host, player, outsider = [
            BrowserPage(self, name) for name in ("host", "player-a", "player-b")
        ]
        self.pages.extend([host, player, outsider])
        self.players = [player, outsider]
        host.fill("#host-key", TEST_HOST)
        host.click("解锁主机")
        host.navigate(f"#/rooms/{room['id']}")
        wait_for(host.connected)
        for page, name in [(player, "真人A"), (outsider, "真人B")]:
            page.fill("#join-invite", created["invite_code"])
            page.fill("#join-name", name)
            page.click("加入房间")
            wait_for(page.connected)
        self.request(
            "POST", prefix + "/members", {"display_name": "AI队友", "controller_type": "agent"}
        )
        room = self.request("GET", prefix)
        players = [m for m in room["members"] if m["role"] == "player"]
        for member in players:
            sheet = self.make_character(member["display_name"])
            result = self.request(
                "POST", prefix + "/character-slots", {"character_id": sheet["id"]}
            )
            slot = next(
                s
                for s in result["room"]["character_slots"]
                if s["public_summary"]["name"] == member["display_name"]
            )
            self.request(
                "POST",
                prefix + "/character-assignments",
                {"slot_id": slot["id"], "member_id": member["id"]},
            )
        for page, name in [(player, "真人A"), (outsider, "真人B")]:
            page.click("准备 · " + name)
        host.click("准备 · AI队友")
        if not self.real:
            self.request("POST", prefix + "/module", {"module_id": "stopped-clock"})
        self.request(
            "PATCH",
            prefix + "/knowledge",
            {**self.config["binding"], "opening_scene": self.config.get("opening_scene")},
        )
        for role, name, member in [
            ("keeper", "本地KP", room["host_member_id"]),
            (
                "investigator",
                "谨慎的同伴",
                next(m["id"] for m in players if m["controller_type"] == "agent"),
            ),
        ]:
            profile = self.request(
                "POST",
                "/agent-profiles",
                {"role": role, "name": name, "personality": "简短、谨慎，只依赖可见资料"},
            )
            self.request(
                "POST",
                prefix + "/agent-bindings",
                {"member_id": member, "profile_id": profile["id"]},
            )
        self.request("POST", prefix + "/start")
        host.evaluate(
            "document.querySelector('[data-testid=host-agent-debug] details').open = true"
        )
        timeout = 600 if self.real else 45
        for index, action in enumerate(self.config["actions"]):
            previous = self.request("GET", prefix + "/agent-cycle")
            player.fill("#agent-action", action)
            player.click("提交行动")
            wait_for(
                lambda: (
                    (self.request("GET", prefix + "/agent-cycle") or {}).get("id")
                    != (previous or {}).get("id")
                )
            )
            cycle = self.settled(prefix, ("completed", "waiting_for_roll"), timeout)
            if cycle["status"] == "waiting_for_roll":
                wait_for(lambda: player.contains("掷骰完成检定"))
                player.click("掷骰完成检定")
                final = self.settled(prefix, "completed", timeout)
                assert final["id"] == cycle["id"]
                self.report["interrupt_resume_same_cycle"] = True
            wait_for(
                lambda: (
                    len(
                        player.evaluate(
                            "[...document.querySelectorAll('[data-testid=timeline] "
                            "li[data-actor-type=KP]')].map(e=>e.dataset.eventSeq)"
                        )
                    )
                    >= index + 1
                )
            )
            self.checkpoint_report()
            print(f"RAG cycle {index + 1} completed", flush=True)
        # Public timelines have identical seq ordering and one item per event.
        snapshots = []
        for page in (player, outsider):
            wait_for(lambda: page.contains("谨慎的同伴"))
            assert not page.evaluate("!!document.querySelector('[data-testid=host-agent-debug]')")
            assert not page.contains("HOST_DEBUG")
            assert not page.evaluate("document.body.innerText.includes('keeper_brief')")
            seqs = page.evaluate(
                "[...document.querySelectorAll('[data-testid=timeline] > li')]"
                ".map(e=>Number(e.dataset.eventSeq))"
            )
            assert seqs == sorted(set(seqs))
            snapshots.append(seqs)
        assert snapshots[0] == snapshots[1]
        outsider.command("Page.reload")
        wait_for(outsider.connected)
        wait_for(
            lambda: (
                outsider.evaluate("document.querySelectorAll('[data-testid=timeline] > li').length")
                == len(snapshots[1])
            )
        )
        self.report["refresh_restored_public_log"] = True
        save = self.request("POST", prefix + "/snapshots", {"name": "知识版本存档"})["snapshot"]
        self.stop(self.backend)
        self.backend = self.start(command, BACKEND, "backend-restarted")
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
        wait_for(player.connected)
        self.request("POST", prefix + "/pause")
        self.request("POST", prefix + f"/snapshots/{save['id']}/load")
        after = self.request("GET", prefix + "/knowledge")
        assert (
            after["module"] == self.config["binding"]["module"] and not after["knowledge_missing"]
        )
        outsider.command("Page.reload")
        wait_for(outsider.connected)
        wait_for(lambda: outsider.contains("谨慎的同伴"))
        runs = self.request("GET", prefix + "/agent-runs")
        all_audits = [
            audit
            for run in runs
            for audit in self.request("GET", f"{prefix}/agent-runs/{run['id']}/retrievals")
        ]
        hits = [e for a in all_audits for e in a["evidence"] if e["source_kind"] == "module"]
        assert hits and all(
            {"source_id": e["source_id"], "source_hash": e["source_hash"]} == after["module"]
            for e in hits
        )
        assert any(
            t["tool"] == "search_module" and t.get("ok") for r in runs for t in r["tool_results"]
        )
        assert any(
            r["context"].get("MODULE_EVIDENCE") for r in runs if r["graph_node"] == "keeper_decide"
        ), "module_evidence_not_injected_before_decision"
        events = self.request("GET", prefix + "/events")["events"]
        narrations = [e for e in events if e["type"] == "keeper.narration"]
        citations = [
            c["evidence_id"] for e in narrations for c in e["payload"].get("citations", [])
        ]
        for page in (player, outsider):
            credential = page.evaluate(f"localStorage.getItem('coc.room.{room['id']}')")
            headers = {"Authorization": f"Bearer {credential}"}
            response = self.http.get(
                f"http://127.0.0.1:8000/api{prefix}/evidence/{hits[0]['evidence_id']}",
                headers=headers,
            )
            assert response.status_code in (403, 404)
            response = self.http.get(
                f"http://127.0.0.1:8000/api{prefix}/agent-runs/{runs[0]['id']}/retrievals",
                headers=headers,
            )
            assert response.status_code == 403
            for evidence_id in citations:
                response = self.http.get(
                    f"http://127.0.0.1:8000/api{prefix}/evidence/{evidence_id}", headers=headers
                )
                assert response.status_code == 200
        citation_views = []
        for page in (host, player, outsider):
            citation_views.append(
                page.evaluate(
                    "[...document.querySelectorAll('.rule-citation small')]"
                    ".map(e=>e.textContent).sort()"
                )
            )
        assert citation_views[0] == citation_views[1] == citation_views[2] == sorted(citations)
        host.evaluate(
            "document.querySelectorAll('[data-testid=host-agent-debug] details')"
            ".forEach(e=>e.open=true)"
        )
        wait_for(lambda: host.contains("injected_ids") and host.contains("source_filters"))
        self.report["host_audit_visible"] = True
        self.report["evidence_permissions"] = True
        self.report["public_citations_identical"] = True
        assert any(e["payload"].get("citations") for e in narrations), (
            "missing_public_rule_citation"
        )
        assert any(e["type"] == "check.resolved" for e in events), "missing_real_dice_check"
        assert self.report.get("interrupt_resume_same_cycle"), "missing_graph_resume"
        if self.real:
            assert any(
                c["category"] == "module_fact"
                for e in narrations
                for c in e["payload"].get("claims", [])
            ), "missing_grounded_scene_narration"
            assert narrations[-1]["payload"]["needs_host_ruling"], "unknown_rule_must_abstain"
        for e in events:
            if e["type"] in {"keeper.narration", "agent.spoke", "agent.action_proposed"}:
                assert e["payload"]["controller_type"] == "agent"
                assert e["payload"]["actor_name"] in {"本地KP", "谨慎的同伴"}
        restored = outsider.evaluate(
            "[...document.querySelectorAll('[data-testid=timeline] > li')]"
            ".map(e=>Number(e.dataset.eventSeq))"
        )
        assert set(snapshots[1]) <= set(restored)
        for run in runs:
            serialized = json.dumps(run["context"], ensure_ascii=False)
            assert all(
                title not in serialized for title in self.config.get("other_module_titles", [])
            )
            if run["graph_node"] in {"run_teammates", "narrate_publicly"}:
                assert all(
                    e["visibility"] == "public_rules"
                    for e in run["context"].get("RULE_EVIDENCE", [])
                )
                assert not run["context"].get("MODULE_EVIDENCE")
        secrets = [TEST_HOST, created["invite_code"]] + [
            page.evaluate(f"localStorage.getItem('coc.room.{room['id']}')") for page in self.players
        ]
        for page in self.pages:
            page.click("导出 JSONL")
            page.click("导出 Markdown")
            wait_for(
                lambda: (
                    len(list(page.directory.glob("room-*.jsonl"))) >= 1
                    and len(list(page.directory.glob("room-*.md"))) >= 1
                )
            )
            for path in [*page.directory.glob("room-*.jsonl"), *page.directory.glob("room-*.md")]:
                text = path.read_text(encoding="utf-8")
                assert all(secret not in text for secret in secrets)
                assert (
                    "chain_of_thought" not in text
                    and "<think>" not in text
                    and '"context"' not in text
                )
                assert all(
                    title not in text for title in self.config.get("other_module_titles", [])
                )
                if page is not host:
                    assert "keeper_brief" not in text and "HOST_DEBUG" not in text
            assert not page.exceptions
            page.screenshot("final.png")
            page.evaluate(
                "(()=>{const timeline=document.querySelector('[data-testid=timeline]');"
                "timeline.scrollTop=timeline.scrollHeight;timeline.scrollIntoView({block:'end'});})()"
            )
            import base64

            capture = page.command(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (page.directory / "timeline.png").write_bytes(base64.b64decode(capture["data"]))
        if self.real:
            with httpx.Client(trust_env=False, timeout=5) as ollama:
                self.report["ollama_after"] = ollama.get("http://127.0.0.1:11434/api/ps").json()
        self.checkpoint_report()
        self.report.update(
            passed=True,
            browser_contexts=3,
            ordered_deduplicated_logs=True,
            log_export_regression=True,
            hash_after_restart=True,
            user_hashes_after={
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in (".env", "data/game.db")
            },
        )
        assert self.report["user_hashes_after"] == self.report["user_hashes_before"]
        print("RAG_BROWSER_RESTART_EXPORT=passed", flush=True)


def synthetic_config():
    from uuid import uuid4

    from app.agents.modules import load_modules
    from app.knowledge.indexer import KnowledgeIndexer
    from app.knowledge.repository import KnowledgeRepository

    directory = ROOT / ".cache" / "batch-4" / ("synthetic-" + uuid4().hex)
    data = directory / "data"
    (data / "rules").mkdir(parents=True)
    (data / "rules/原创规则.txt").write_text(
        "奖励骰增加一个候选结果。技能检定使用百分骰。", encoding="utf-8"
    )
    scene_text = "\n".join(s.public_description for s in load_modules()["stopped-clock"].scenes)
    for title in ("停摆的钟楼", "其他合成模组"):
        folder = data / "modules" / title
        folder.mkdir(parents=True)
        (folder / "开场.txt").write_text(
            "开场 钟楼广场 维修间 工作台 检定调查。原创私密标记紫色月轮。\n" + scene_text,
            encoding="utf-8",
        )
    (data / "modules/停摆的钟楼/manifest.json").write_text(
        json.dumps({"module_id": "stopped-clock"}), encoding="utf-8"
    )
    repo = KnowledgeRepository(directory / "knowledge.db")
    KnowledgeIndexer(data, repo).index()
    sources = repo.sources()

    def ref(source):
        return {"source_id": source.source_id, "source_hash": source.source_hash}

    document = {
        "data_dir": str(data),
        "knowledge_database": str(repo.path),
        "module_title": "停摆的钟楼",
        "binding": {
            "rules": [ref(s) for s in sources if s.kind != "module"],
            "module": ref(next(s for s in sources if s.title == "停摆的钟楼")),
            "enabled": True,
        },
        "actions": [
            "奖励骰如何判定？请给出简短规则引用。",
            "我检查维修间工作台，请做一次侦查检定。检定结束后阅读交接簿。",
        ],
        "other_module_titles": ["其他合成模组"],
    }
    path = directory / "config.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    real = "--ollama" in sys.argv
    if real and "--config" not in sys.argv:
        raise SystemExit("Real acceptance requires --config with host-approved public opening")
    config = (
        Path(sys.argv[sys.argv.index("--config") + 1])
        if "--config" in sys.argv
        else synthetic_config()
    )
    if "--serve" in sys.argv:
        serve(Path(sys.argv[sys.argv.index("--serve") + 1]), config, real)
    else:
        check = RagCheck(config, real)
        try:
            check.run()
        finally:
            try:
                check.checkpoint_report()
            finally:
                check.close()
        for port in (8000, 5173):
            port_free(port)
        print("TEMPORARY_PORTS_RELEASED=8000,5173", flush=True)
