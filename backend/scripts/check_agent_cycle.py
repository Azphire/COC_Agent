"""Real HTTP/SQLite/WebSocket + three isolated Chrome contexts.

Default uses FakeModelAdapter and blocks network model transport. --ollama uses
only an already installed local qwen3:8b. All databases live under .cache/.
"""

import base64
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import httpx
from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage, MultiplayerCheck


def fake_scenario(messages, kwargs):
    from app.agents.schemas import ProfileInput

    if kwargs.get("response_schema") is ProfileInput:
        return {"role": "keeper", "name": "谨慎的守秘人", "personality": "严谨、公平"}
    context = json.loads(messages[-1]["content"])
    if context.get("phase") == "narrate_publicly":
        check = context["checks"][-1] if context["checks"] else None
        return {
            "content": f"检定出目 {check['result']['total']}，"
            f"目标 {check['result']['threshold']}。调查继续。"
            if check
            else "你们继续检查现场，入口外传来风声。"
        }
    if context.get("phase") == "summary":
        return {"content": "调查员检查现场，线索以已公开记录为准。"}
    if context["role"] == "investigator":
        return {
            "tools": [
                {
                    "name": "propose_action",
                    "arguments": {"text": "我守住维修间入口，留意周围的动静。"},
                }
            ]
        }
    if context["phase"] == "resolve_keeper_response":
        check = context["checks"][-1]
        tools = [{"name": "reveal_clue", "arguments": {"clue_id": "logbook"}}]
        if check["result"]["passed"]:
            tools.append({"name": "reveal_clue", "arguments": {"clue_id": "pin"}})
        tools.append(
            {
                "name": "send_narration",
                "arguments": {
                    "text": f"检定出目 {check['result']['total']}，"
                    f"本次目标 {check['result']['threshold']}。"
                    + ("检查取得了进展。" if check["result"]["passed"] else "你未能找到更多细节。")
                },
            }
        )
        return {"tools": tools}
    trigger = context["triggering_action"]
    if "检定" in trigger["payload"]["text"]:
        return {
            "tools": [
                {"name": "update_scene", "arguments": {"scene_id": "workshop"}},
                {
                    "name": "request_skill_check",
                    "arguments": {
                        "target_member_id": trigger["actor_member_id"],
                        "kind": "skill",
                        "name": "spot_hidden",
                        "reason": "仔细检查维修间的工作台",
                        "clue_id": "pin",
                    },
                },
            ]
        }
    return {
        "tools": [
            {
                "name": "send_narration",
                "arguments": {"text": "交接记录仍摆在桌上，楼上传来风声。你们可以继续调查。"},
            }
        ]
    }


def serve(database_path, real):
    import uvicorn

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app

    database_path = database_path.resolve()
    require_root = (ROOT / ".cache").resolve()
    if not database_path.is_relative_to(require_root):
        raise RuntimeError("Smoke database must stay inside this repository's .cache")
    settings = Settings(
        _env_file=None,
        host_admin_token=TEST_HOST,
        data_dir=database_path.parent,
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        checkpoint_db_path=database_path.with_suffix(".checkpoints.db"),
        model_provider="ollama",
        model_name="qwen3:8b",
        model_base_url="http://127.0.0.1:11434/v1/",
        model_timeout_seconds=240,
        model_context_limit=8192,
        model_output_limit=900,
    )
    app = create_app(settings)
    if not real:
        app.state.agent_model_adapter = FakeModelAdapter(responder=fake_scenario)

        def reject(*args, **kwargs):
            raise AssertionError("Fake smoke must never use model/network transport")

        httpx.AsyncHTTPTransport.handle_async_request = reject
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


class AgentCheck(MultiplayerCheck):
    def __init__(self, real=False):
        SmokeCheck.__init__(self, artifact_prefix="agent-ollama" if real else "agent-fake")
        self.pages = []
        self.real = real
        self.report = {"mode": "ollama" if real else "fake", "external_model_api_calls": 0}

    def settled(self, prefix, status, timeout=30):
        statuses = {status} if isinstance(status, str) else set(status)

        def check():
            cycle = self.request("GET", prefix + "/agent-cycle")
            if cycle and cycle["status"] == "failed":
                raise AssertionError(
                    f"Agent failed at {cycle['current_node']}: {cycle['safe_error']}"
                )
            if cycle and status == "waiting_for_roll" and cycle["status"] == "completed":
                raise AssertionError("Model finished without requesting a valid check")
            return cycle if cycle and cycle["status"] in statuses else None

        return wait_for(check, timeout=timeout)

    def run(self):
        import hashlib

        user_db = ROOT / "data/game.db"
        initial_hash = hashlib.sha256(user_db.read_bytes()).hexdigest()
        self.report["user_database_sha256_before"] = initial_hash
        for port in (8000, 5173):
            port_free(port)
        if self.real:
            with httpx.Client(trust_env=False, timeout=5) as ollama:
                tags = ollama.get("http://127.0.0.1:11434/api/tags").raise_for_status().json()
                assert any(m["name"] == "qwen3:8b" for m in tags["models"]), (
                    "Local qwen3:8b is not installed"
                )
                self.report["ollama_before"] = ollama.get("http://127.0.0.1:11434/api/ps").json()
        database = self.directory / "agent-game.db"
        command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(database)]
        if self.real:
            command.append("--ollama")
        backend = self.start(command, BACKEND, "backend")
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
        sheets = [self.make_character(name) for name in ("真人调查员", "AI 搭档", "旁观调查员")]
        host, player, outsider = [
            BrowserPage(self, name) for name in ("host", "player", "outsider")
        ]
        self.pages.extend([host, player, outsider])
        host.fill("#host-key", TEST_HOST)
        host.click("解锁主机")
        host.navigate("#/agents")
        wait_for(lambda: host.contains("确认保存档案"))
        if not self.real:
            host.fill("#profile-concept", "谨慎、公平的守秘人")
            host.click("生成结构化草稿")
            wait_for(lambda: host.contains("草稿预览与修改"))
            assert self.request("GET", "/agent-profiles") == [], (
                "Draft must not persist before confirmation"
            )
        host.fill("#profile-name", "钟楼 KP")
        host.click("确认保存档案")
        wait_for(lambda: host.contains("编辑 · 钟楼 KP"))
        host.fill("#profile-role", "investigator")
        host.fill("#profile-name", "谨慎的队友")
        host.click("确认保存档案")
        wait_for(lambda: host.contains("编辑 · 谨慎的队友"))
        profiles = self.request("GET", "/agent-profiles")
        host.navigate("#/rooms")
        wait_for(lambda: host.contains("创建房间"))
        host.fill("#room-name", "第三批钟楼验收")
        host.click("创建房间")
        wait_for(host.connected)
        room_id = host.evaluate("location.hash.split('/').at(-1)")
        prefix = f"/rooms/{room_id}"
        invite = host.text_at('[data-testid="invite-code"]')
        for page, name in [(player, "真人"), (outsider, "另一位玩家")]:
            page.fill("#join-invite", invite)
            page.fill("#join-name", name)
            page.click("加入房间")
            wait_for(page.connected)
        host.fill("#member-name", "AI 队友")
        host.fill("#member-controller", "agent")
        host.click("添加席位")
        wait_for(lambda: host.contains("AI 队友"))
        room = self.request("GET", prefix)
        members = [
            next(m for m in room["members"] if m["display_name"] == name)
            for name in ("真人", "AI 队友", "另一位玩家")
        ]
        for sheet, member in zip(sheets, members, strict=True):
            host.fill("#publish-character", sheet["id"])
            host.click("发布角色")
            selector = f'select[aria-label="分配 {sheet["name"]}"]'
            wait_for(lambda: host.evaluate(f"!!document.querySelector({json.dumps(selector)})"))
            host.fill(f'select[aria-label="分配 {sheet["name"]}"]', member["id"])
            host.click(f"分配 · {sheet['name']}")
        player.click("准备 · 真人")
        outsider.click("准备 · 另一位玩家")
        host.click("准备 · AI 队友")
        host.click("绑定测试模组")
        for label, role in [("AI KP", "keeper"), ("AI 队友", "investigator")]:
            profile = next(p for p in profiles if p["role"] == role)
            host.fill(f'select[aria-label="绑定 {label}"]', profile["id"])
            host.click(f"绑定 · {label}")
        host.click("开始游戏")
        wait_for(lambda: player.contains("等待真人行动"))
        player.fill(
            "#agent-action",
            "我走进维修间，仔细检查工作台，请先做一次侦查检定。检定结束后再阅读交接簿。",
        )
        player.click("提交行动")
        timeout = 600 if self.real else 30
        cycle = self.settled(prefix, "waiting_for_roll", timeout)
        wait_for(lambda: player.contains("掷骰完成检定"))
        checks = self.request("GET", prefix + "/checks")
        pending = next(c for c in checks if c["status"] == "pending")
        assert pending["name"] == "spot_hidden" and pending["value"] == 25
        wait_for(lambda: outsider.contains("掷骰完成检定"))
        assert outsider.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === '掷骰完成检定').disabled"
        )
        player.click("掷骰完成检定")
        final = self.settled(prefix, "completed", timeout)
        assert final["id"] == cycle["id"]
        checks = self.request("GET", prefix + "/checks")
        result = next(c for c in checks if c["id"] == pending["id"])
        assert result["status"] == "resolved"
        events = self.request("GET", prefix + "/events")["events"]
        assert sum(e["type"] in {"agent.spoke", "agent.action_proposed"} for e in events) == 1
        assert any(e["type"] == "keeper.narration" for e in events)
        self.report["first_cycle"] = {
            "id": cycle["id"],
            "call_count": final["state"]["call_count"],
            "check": result["result"],
        }
        public_clues = self.request("GET", prefix)["game"]["module"]["clues"]
        if self.real and not public_clues:
            previous_id = self.request("GET", prefix + "/agent-cycle")["id"]
            player.fill(
                "#agent-action", "我直接打开桌上的交接簿阅读，请告诉大家上面的内容。这不需要检定。"
            )
            player.click("提交行动")
            wait_for(lambda: self.request("GET", prefix + "/agent-cycle")["id"] != previous_id)
            extra = self.settled(prefix, ("completed", "waiting_for_roll"), timeout)
            if extra["status"] == "waiting_for_roll":
                wait_for(lambda: player.contains("掷骰完成检定"))
                player.click("掷骰完成检定")
                self.settled(prefix, "completed", timeout)
            public_clues = self.request("GET", prefix)["game"]["module"]["clues"]
        assert public_clues, "No clue was revealed"
        assert any(m["kind"] == "observation" for m in self.request("GET", prefix + "/memories"))
        for page in (player, outsider):
            assert not page.contains("蓝斑卵") and not page.contains("守秘真相")
            frames = json.dumps(page.evaluate("window.roomFrames"), ensure_ascii=False)
            assert "蓝斑卵" not in frames and "keeper_brief" not in frames
        runs = self.request("GET", prefix + "/agent-runs")
        for run in runs:
            if run["graph_node"] == "run_teammates":
                assert "keeper_brief" not in json.dumps(run["context"])
                assert "蓝斑卵" not in json.dumps(run["context"], ensure_ascii=False)
        save = self.request("POST", prefix + "/snapshots", {"name": "钟楼存档"})["snapshot"]
        self.stop(backend)
        backend = self.start(command, BACKEND, "backend-restarted")
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
        wait_for(player.connected)
        self.request("POST", prefix + "/pause")
        self.request("POST", prefix + f"/snapshots/{save['id']}/load")
        self.request("POST", prefix + "/resume")
        wait_for(lambda: player.contains("等待真人行动"))
        previous_id = self.request("GET", prefix + "/agent-cycle")["id"]
        player.fill("#agent-action", "我把刚才的发现告诉同伴，请他守住入口，等待下一步安排。")
        player.click("提交行动")
        wait_for(lambda: self.request("GET", prefix + "/agent-cycle")["id"] != previous_id)
        final = self.settled(prefix, ("completed", "waiting_for_roll"), timeout)
        if final["status"] == "waiting_for_roll":
            wait_for(lambda: player.contains("掷骰完成检定"))
            player.click("掷骰完成检定")
            final = self.settled(prefix, "completed", timeout)
        self.report["continued_cycle"] = {
            "id": final["id"],
            "call_count": final["state"]["call_count"],
        }
        all_runs = self.request("GET", prefix + "/agent-runs")
        tokens = [TEST_HOST, invite] + [
            p.evaluate(f"localStorage.getItem('coc.room.{room_id}')") for p in (player, outsider)
        ]
        for page in (host, player, outsider):
            page.click("导出 JSONL")
            page.click("导出 Markdown")
            wait_for(
                lambda: (
                    len([p for p in page.directory.glob("room-*") if p.suffix in {".md", ".jsonl"}])
                    >= 2
                )
            )
            for path in [p for p in page.directory.glob("room-*") if p.suffix in {".md", ".jsonl"}]:
                text = path.read_text(encoding="utf-8")
                assert all(token not in text for token in tokens)
                assert (
                    "chain_of_thought" not in text
                    and '"context"' not in text
                    and "<think>" not in text
                )
                if page is not host:
                    assert "蓝斑卵" not in text and "keeper_brief" not in text
            assert not page.exceptions
            page.screenshot("final.png")
            page.evaluate(
                "document.querySelector('[data-testid=agent-cycle-status]')"
                "?.closest('section')?.scrollIntoView({block: 'start'})"
            )
            capture = page.command(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (page.directory / "agent-panel.png").write_bytes(base64.b64decode(capture["data"]))
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            self.report["database_tables"] = [
                r[0]
                for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ]
        if self.real:
            with httpx.Client(trust_env=False, timeout=5) as ollama:
                self.report["ollama_after"] = ollama.get("http://127.0.0.1:11434/api/ps").json()
        self.report.update(
            {
                "passed": True,
                "browser_contexts": 3,
                "room_id": room_id,
                "model_runs": [
                    {
                        "node": r["graph_node"],
                        "status": r["status"],
                        "latency_ms": r["latency_ms"],
                        "token_usage": r["token_usage"],
                    }
                    for r in all_runs
                ],
                "user_database_sha256_after": hashlib.sha256(user_db.read_bytes()).hexdigest(),
            }
        )
        assert self.report["user_database_sha256_after"] == initial_hash
        print("AGENT_CYCLE_BROWSER_RESTART_EXPORT=passed", flush=True)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve(Path(sys.argv[sys.argv.index("--serve") + 1]), "--ollama" in sys.argv)
    else:
        check = AgentCheck(real="--ollama" in sys.argv)
        try:
            check.run()
        finally:
            check.close()
        for port in (8000, 5173):
            port_free(port)
        print("TEMPORARY_PORTS_RELEASED=8000,5173", flush=True)
