"""Batch 5 isolated browser acceptance. Real drafts are reviewed before --play.

All outputs stay in .cache; only an existing local qwen3:8b is used with --real.
No raw module text, credentials, prompts or model reasoning are written to reports.
"""

import argparse
import base64
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage, MultiplayerCheck


def fake_response(messages, kwargs):
    context = json.loads(messages[-1]["content"])
    if "evidence" in context:
        evidence_id = context["evidence"][0]["evidence_id"]
        specs = [
            ("scene", "候车厅", "你们来到安静的候车厅。"),
            ("location", "站台", "窗外是一座旧站台。"),
            ("item", "车票", "一张普通纸质车票。"),
            ("clue", "公告", "公告提醒旅客保留车票。"),
            ("clue", "钟面", "墙上时钟的指针停在十二点。"),
            ("clue", "重复测试草稿", "用于测试拒绝的草稿。"),
        ]
        return {
            "entities": [
                {
                    "local_id": str(i),
                    "type": kind,
                    "title": title,
                    "keeper_summary": "仅主机可见的紫月标记。",
                    "public_summary": public,
                    "evidence_ids": [evidence_id],
                }
                for i, (kind, title, public) in enumerate(specs)
            ],
            "relations": [],
        }
    if context.get("phase") == "summary":
        return {"content": "调查仍在继续，以公开实体记录为准。"}
    if context.get("phase") == "narrate_publicly":
        entity = max(context["public_entities"], key=lambda e: e["revealed_event_seq"] or 0)
        return {
            "claims": [
                {
                    "claim_id": "public",
                    "category": "module_fact",
                    "statement": entity["public_summary"],
                    "entity_ids": [entity["id"]],
                }
            ]
        }
    if context["role"] == "investigator":
        return {
            "tools": [
                {"name": "inspect_public_entities", "arguments": {}},
                {"name": "propose_action", "arguments": {"text": "我检查公告附近，留意其他旅客。"}},
            ]
        }
    if context["phase"] == "resolve_keeper_response":
        return {"tools": []}
    action = context["triggering_action"]
    if "审阅" in action["payload"]["text"]:
        return {
            "tools": [
                {
                    "name": "propose_module_fact",
                    "arguments": {
                        "entity_type": "item",
                        "proposed_title": "时刻表",
                        "proposed_public_summary": "站台旁有一本时刻表。",
                        "keeper_reason": "只供主机查看的证据核对",
                        "evidence_ids": [e["evidence_id"] for e in context["MODULE_EVIDENCE"]],
                    },
                }
            ]
        }
    if "检定" in action["payload"]["text"]:
        return {
            "tools": [
                {
                    "name": "request_skill_check",
                    "arguments": {
                        "target_member_id": action["actor_member_id"],
                        "name": "spot_hidden",
                        "reason": "调查现场",
                    },
                }
            ]
        }
    entity = next(e for e in context["module"]["approved_entities"] if e["type"] == "clue")
    return {"tools": [{"name": "reveal_entity", "arguments": {"entity_id": entity["id"]}}]}


def serve(directory, real):
    import httpx
    import uvicorn

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app

    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / ".cache").resolve()):
        raise ValueError("isolated_directory_required")
    settings = Settings(
        _env_file=None,
        host_admin_token=TEST_HOST,
        data_dir=directory / "data",
        database_url=f"sqlite+aiosqlite:///{(directory / 'game.db').as_posix()}",
        knowledge_db_path=(ROOT / ".cache/batch-4/knowledge-v2.db")
        if real
        else directory / "knowledge.db",
        model_provider="ollama",
        model_name="qwen3:8b",
        model_base_url="http://127.0.0.1:11434/v1/",
        model_context_limit=8192,
        model_output_limit=1100,
        model_timeout_seconds=240,
    )
    app = create_app(settings)
    if not real:
        app.state.agent_model_adapter = FakeModelAdapter(responder=fake_response)

        def reject(*args, **kwargs):
            raise AssertionError("Fake acceptance must not call a model API")

        httpx.AsyncHTTPTransport.handle_async_request = reject
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


class PreparationCheck(MultiplayerCheck):
    def __init__(self, real, directory=None):
        SmokeCheck.__init__(self, artifact_prefix="batch5-real" if real else "batch5-fake")
        if directory:
            self.directory = directory.resolve()
            if not self.directory.is_relative_to((ROOT / ".cache").resolve()):
                raise ValueError("isolated_directory_required")
        self.real, self.pages = real, []
        self.report = {"mode": "local qwen3:8b" if real else "Fake Model", "passed": False}
        self.backend = None
        self.stage = "prepare"

    def start_services(self):
        for port in (8000, 5173):
            port_free(port)
        self.start_backend()
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

    def start_backend(self):
        command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)]
        if self.real:
            command.append("--real")
        self.backend = self.start(command, BACKEND, "backend-" + str(len(self.processes)))
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)

    def host_page(self, name):
        host = BrowserPage(self, name + "-" + uuid4().hex[:6])
        self.pages.append(host)
        host.fill("#host-key", TEST_HOST)
        host.click("解锁主机")
        return host

    def prepare(self):
        if not self.real:
            from app.knowledge.indexer import KnowledgeIndexer
            from app.knowledge.repository import KnowledgeRepository

            folder = self.directory / "data/modules/原创候车厅"
            folder.mkdir(parents=True)
            (folder / "开场.md").write_text(
                "# 开场\n候车厅、旧站台、车票和公告。公告提醒旅客保留车票。墙上的时钟停在十二点。"
                "站台旁有一本时刻表。仅主机可见的紫月标记。",
                encoding="utf-8",
            )
            KnowledgeIndexer(
                self.directory / "data", KnowledgeRepository(self.directory / "knowledge.db")
            ).index()
        self.start_services()
        sources = self.request("GET", "/knowledge/sources")
        source = next(
            s for s in sources if s["title"] == ("常暗之厢" if self.real else "原创候车厅")
        )
        host = self.host_page("host-prepare")
        host.navigate("#/preparations")
        wait_for(lambda: host.contains("创建准备任务"))
        wait_for(
            lambda: host.evaluate(
                "!!document.querySelector('#preparation-source "
                f'option[value="{source["source_id"]}"]\')'
            )
        )
        host.fill("#preparation-source", source["source_id"])
        host.fill("#preparation-title", "常暗之厢开场" if self.real else "原创候车厅开场")
        if self.real:
            host.fill("#preparation-page-start", 2)
            host.fill("#preparation-page-end", 3)
        host.click("创建准备任务")
        wait_for(lambda: len(self.request("GET", "/module-preparations")) > 0)
        prep = self.request("GET", "/module-preparations")[0]
        wait_for(lambda: host.contains("生成实体草稿"))
        host.click("生成实体草稿")
        print("PREPARATION_GENERATING=" + prep["id"], flush=True)

        def done():
            latest = self.request("GET", "/module-preparations/" + prep["id"])
            if latest["status"] in {"failed", "stale"}:
                raise AssertionError(latest["safe_error"] or latest["status"])
            return latest if latest["status"] == "review_ready" else None

        completed = wait_for(done, timeout=900 if self.real else 30)
        entities = self.request("GET", f"/module-preparations/{prep['id']}/entities")
        host.screenshot("preparation.png")
        self.report.update(
            stage="draft_generation",
            preparation_id=prep["id"],
            source_hash=source["source_hash"],
            entity_types=dict(Counter(e["type"] for e in entities)),
            calls=completed["model_call_count"],
            runs=completed["runs"],
            passed=True,
        )
        (self.directory / "preparation-result.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("PREPARATION_READY=" + json.dumps(self.report["entity_types"]), flush=True)

    def click_entity(self, host, entity_id, label):
        selector = f'[data-entity-id="{entity_id}"]'
        lookup = (
            f"[...document.querySelector({json.dumps(selector)}).querySelectorAll('button')]"
            f".find(b => b.textContent === {json.dumps(label)})"
        )
        wait_for(lambda: host.evaluate(f"!!({lookup}) && !({lookup}).disabled"))
        host.evaluate(f"({lookup}).click()")

    @staticmethod
    def capture(page, selector, filename):
        page.evaluate(f"document.querySelector({json.dumps(selector)}).scrollIntoView()")
        result = page.command(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": False,
            },
        )
        (page.directory / filename).write_bytes(base64.b64decode(result["data"]))

    def review_drafts(self, host, decisions):
        prep_id = decisions["preparation_id"]
        host.navigate("#/preparations")
        wait_for(lambda: host.contains("选择任务"))
        wait_for(
            lambda: host.evaluate(
                f"!!document.querySelector('#preparation-select option[value=\"{prep_id}\"]')"
            )
        )
        host.fill("#preparation-select", prep_id)
        wait_for(lambda: host.evaluate("document.querySelectorAll('[data-entity-id]').length > 0"))
        for decision in decisions["entities"]:
            entity_id = decision["id"]
            self.click_entity(host, entity_id, "查看有限证据摘录")
            wait_for(
                lambda: host.evaluate(
                    f'document.querySelector(\'[data-entity-id="{entity_id}"] '
                    "blockquote') !== null"
                )
            )
            if "public_summary" in decision:
                current = next(
                    e
                    for e in self.request("GET", f"/module-preparations/{prep_id}/entities")
                    if e["id"] == entity_id
                )
                if current["status"] != "draft":
                    self.click_entity(host, entity_id, "返回草稿")
                    wait_for(
                        lambda: host.evaluate(
                            f'!document.querySelector(\'[data-entity-id="{entity_id}"] '
                            "textarea[aria-label]').disabled"
                        )
                    )
                host.fill(
                    f'[data-entity-id="{entity_id}"] textarea[aria-label]',
                    decision["public_summary"],
                )
                if decision.get("title"):
                    host.fill(f'[data-entity-id="{entity_id}"] input', decision["title"])
                for field, index in (
                    ("keeper_summary", 0),
                    ("suggested_checks", 2),
                    ("reveal_conditions", 3),
                ):
                    if field in decision:
                        value = decision[field]
                        value = value if isinstance(value, str) else json.dumps(value)
                        selector = f'[data-entity-id="{entity_id}"] textarea'
                        host.evaluate(
                            f"(() => {{const el = document.querySelectorAll({json.dumps(selector)})"
                            f"[{index}]; Object.getOwnPropertyDescriptor("
                            "HTMLTextAreaElement.prototype,"
                            f" 'value').set.call(el, {json.dumps(value)});"
                            "el.dispatchEvent(new Event('input', {bubbles: true}));})()"
                        )
                before = next(
                    e
                    for e in self.request("GET", f"/module-preparations/{prep_id}/entities")
                    if e["id"] == entity_id
                )["version"]
                self.click_entity(host, entity_id, "保存编辑")
                wait_for(
                    lambda: any(
                        e["id"] == entity_id
                        and e["version"] > before
                        and e["public_summary"] == decision["public_summary"]
                        for e in self.request("GET", f"/module-preparations/{prep_id}/entities")
                    )
                )
            current = next(
                e
                for e in self.request("GET", f"/module-preparations/{prep_id}/entities")
                if e["id"] == entity_id
            )
            expected = "approved" if decision["approve"] else "rejected"
            if current["status"] != expected:
                self.click_entity(
                    host, entity_id, "批准实体" if decision["approve"] else "拒绝实体"
                )
            wait_for(
                lambda: (
                    next(
                        e
                        for e in self.request("GET", f"/module-preparations/{prep_id}/entities")
                        if e["id"] == entity_id
                    )["status"]
                    == ("approved" if decision["approve"] else "rejected")
                )
            )
        self.click_entity(host, decisions["initial_scene"], "设为初始场景")
        wait_for(
            lambda: (
                self.request("GET", f"/module-preparations/{prep_id}")["initial_scene_entity_id"]
                == decisions["initial_scene"]
            )
        )
        host.click("批准准备版本")
        wait_for(
            lambda: self.request("GET", f"/module-preparations/{prep_id}")["status"] == "approved"
        )
        self.capture(host, "[data-testid=preparation-status]", "approved-preparation.png")

    def play(self, decisions_path):
        self.stage = "play"
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        self.start_services()
        host = self.host_page("host-play")
        self.review_drafts(host, decisions)
        player, second = [
            BrowserPage(self, name + "-" + uuid4().hex[:6]) for name in ("player-a", "player-b")
        ]
        self.pages.extend([player, second])
        created = self.request("POST", "/rooms", {"name": "第五批开场验收"})
        room_id = created["room"]["id"]
        prefix = "/rooms/" + room_id
        self.report["room_id"] = room_id
        host.navigate("#/rooms/" + room_id)
        wait_for(host.connected)
        for page, name in [(player, "玩家A"), (second, "玩家B")]:
            page.fill("#join-invite", created["invite_code"])
            page.fill("#join-name", name)
            page.click("加入房间")
            wait_for(page.connected)
        self.request(
            "POST", prefix + "/members", {"display_name": "AI队友", "controller_type": "agent"}
        )
        room = self.request("GET", prefix)
        for name in ("玩家A", "玩家B", "AI队友"):
            member = next(m for m in room["members"] if m["display_name"] == name)
            character = self.make_character(name + "调查员")
            result = self.request(
                "POST", prefix + "/character-slots", {"character_id": character["id"]}
            )
            slot = next(
                s
                for s in result["room"]["character_slots"]
                if s["source_character_id"] == character["id"]
            )
            self.request(
                "POST",
                prefix + "/character-assignments",
                {"member_id": member["id"], "slot_id": slot["id"]},
            )
            if name == "AI队友":
                teammate_id = member["id"]
                self.request("POST", prefix + "/ready", {"ready": True, "member_id": teammate_id})
        player.click("准备 · 玩家A")
        second.click("准备 · 玩家B")
        wait_for(
            lambda: host.evaluate(
                "!!document.querySelector('#room-preparation "
                f'option[value="{decisions["preparation_id"]}"]\')'
            )
        )
        host.fill("#room-preparation", decisions["preparation_id"])
        host.click("绑定准备版本")
        for role, member in [("keeper", room["host_member_id"]), ("investigator", teammate_id)]:
            profile = self.request(
                "POST",
                "/agent-profiles",
                {"role": role, "name": "本地KP" if role == "keeper" else "谨慎的同伴"},
            )
            self.request(
                "POST",
                prefix + "/agent-bindings",
                {"member_id": member, "profile_id": profile["id"]},
            )
        host.click("开始游戏")
        wait_for(lambda: player.contains("调查板"))
        for action in decisions["actions"]:
            previous = self.request("GET", prefix + "/agent-cycle")
            player.fill("#agent-action", action)
            player.click("提交行动")
            cycle = self.settle(prefix, previous_id=previous["id"] if previous else None)
            cycle = self.handle_waits(prefix, cycle, host, player, decisions)
            assert cycle["status"] == "completed", cycle
            print("CYCLE_COMPLETED=" + cycle["id"], flush=True)
        self.verify(prefix, room_id)
        snapshot = self.request("POST", prefix + "/snapshots", {"name": "第五批开场存档"})[
            "snapshot"
        ]
        self.request("POST", prefix + "/pause")
        self.stop(self.backend)
        wait_for(lambda: port_free(8000) is None)
        self.start_backend()
        self.request("POST", prefix + f"/snapshots/{snapshot['id']}/load", {})
        self.request("POST", prefix + "/resume")
        for page in self.pages:
            page.command("Page.reload")
            wait_for(page.connected)
        player.fill("#agent-action", decisions["continuation"])
        previous = self.request("GET", prefix + "/agent-cycle")
        player.click("提交行动")
        cycle = self.settle(prefix, previous_id=previous["id"])
        assert self.handle_waits(prefix, cycle, host, player, decisions)["status"] == "completed"
        self.verify(prefix, room_id)
        assert self.report["reviews"] >= 1, "No host review occurred"
        assert self.report["checks"] >= 1 and self.report["kp_responses"] >= 3
        assert self.report["teammate_actions"] >= 2 and self.report["public_entity_count"] >= 2
        self.report.update(stage="play", browser_contexts=3, save_restart_load=True, passed=True)

    def handle_waits(self, prefix, cycle, host, player, decisions):
        for _ in range(3):
            if cycle["status"] == "waiting_for_review":
                self.report["review_cycles"] = [*self.report.get("review_cycles", []), cycle["id"]]
                wait_for(lambda: host.contains("拒绝并恢复"))
                if decisions.get("review_decision") == "reject":
                    host.click("拒绝并恢复")
                else:
                    host.fill('textarea[aria-label="审阅公开摘要"]', decisions["review_summary"])
                    host.click("编辑后批准并恢复")
                cycle = self.settle(prefix, skip_review=True)
            elif cycle["status"] == "waiting_for_roll":
                wait_for(lambda: player.contains("掷骰完成检定"))
                player.click("掷骰完成检定")
                cycle = self.settle(prefix, skip_roll=True)
            else:
                return cycle
        raise AssertionError("Unexpected repeated wait sequence")

    def settle(self, prefix, skip_review=False, skip_roll=False, completed=False, previous_id=None):
        def settled():
            cycle = self.request("GET", prefix + "/agent-cycle")
            if cycle and cycle["id"] == previous_id:
                return None
            if cycle and cycle["status"] == "failed":
                raise AssertionError("Agent failed: " + str(cycle["safe_error"]))
            states = (
                {"completed"}
                if completed
                else {"completed"}
                | (set() if skip_roll else {"waiting_for_roll"})
                | (set() if skip_review else {"waiting_for_review"})
            )
            return cycle if cycle and cycle["status"] in states else None

        return wait_for(settled, timeout=900 if self.real else 30)

    def verify(self, prefix, room_id):
        public = self.request("GET", prefix + "/public-entities")
        runs = self.request("GET", prefix + "/agent-runs")
        events = self.request("GET", prefix + "/events")["events"]
        for page in self.pages[1:]:
            body = page.evaluate("document.body.innerText")
            assert "HOST_DEBUG" not in body and "主机理由" not in body
            for path in ("host-entities", "review-requests"):
                status = page.evaluate(
                    f"fetch('/api{prefix}/{path}', {{headers: {{Authorization: 'Bearer ' + "
                    f"localStorage.getItem('coc.room.{room_id}')}}}}).then(r => r.status)"
                )
                assert status == 403
            view = page.evaluate(
                f"fetch('/api{prefix}/public-entities', {{headers: {{Authorization: 'Bearer ' + "
                f"localStorage.getItem('coc.room.{room_id}')}}}}).then(r => r.json())"
            )
            assert view == public
            assert page.evaluate(
                "!JSON.stringify(window.roomFrames).includes('keeper_summary') && "
                "!JSON.stringify(window.roomFrames).includes('review.requested') && "
                "!JSON.stringify(window.roomFrames).includes('review.resolved')"
            )
            seqs = page.evaluate(
                "[...document.querySelectorAll('[data-event-seq]')]"
                ".map(e => Number(e.dataset.eventSeq))"
            )
            assert seqs == sorted(set(seqs))
            self.capture(page, "[data-testid=investigation-board]", "public-board.png")
        for run in runs:
            if run["graph_node"] in {"narrate_publicly", "run_teammates"}:
                assert "keeper_summary" not in json.dumps(run["context"])
        for format in ("jsonl", "markdown"):
            response = self.http.get(f"http://127.0.0.1:8000/api{prefix}/logs?format={format}")
            assert response.status_code == 200 and len(response.content) > 100
        self.report.update(
            kp_responses=sum(e["type"] == "keeper.narration" for e in events),
            teammate_actions=sum(
                e["type"] in {"agent.spoke", "agent.action_proposed"} for e in events
            ),
            checks=sum(e["type"] == "check.resolved" for e in events),
            reviews=sum(e["type"] == "review.requested" for e in events),
            public_entity_count=len(public),
            model_calls=[c for r in runs for c in r["model_calls"]],
            log_export_regression=True,
        )
        (self.directory / "play-result.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.real)
        return
    check = PreparationCheck(args.real, args.directory)
    print("DIRECTORY=" + str(check.directory.relative_to(ROOT)), flush=True)
    try:
        if args.decisions:
            check.play(args.decisions)
        else:
            check.prepare()
    finally:
        check.close()
    for port in (8000, 5173):
        wait_for(lambda: port_free(port) is None)


if __name__ == "__main__":
    main()
