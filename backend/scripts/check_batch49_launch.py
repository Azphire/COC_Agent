"""Real Chrome recovery checks on a read-only backup of the prior fake UI run."""

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import httpx
from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage

SOURCE = ROOT / "data/prepared/batch-48/ui-fake-03"


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def serve(directory):
    import uvicorn

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app
    from app.party.schemas import Persona

    settings = Settings(
        _env_file=None, host_admin_token=TEST_HOST, data_dir=directory,
        database_url=f"sqlite+aiosqlite:///{(directory / 'ui.db').as_posix()}",
        model_provider="ollama", model_name="qwen3:8b",
        cors_origins="http://127.0.0.1:5259",
    )
    calls = []

    async def responder(messages, kwargs):
        assert kwargs.get("response_schema") is Persona, "No natural turn is part of this UI check"
        context = json.loads(messages[-1]["content"])
        calls.append({"index": len(calls) + 1, "kind": "persona", "external": False})
        dump(directory / "fake-calls.json", calls)
        await asyncio.sleep(1)
        return dict(name=context.get("public_name_hint") or f"新调查员{len(calls)}",
                    background=f"从事{context['occupation']}，平时习惯记录生活中的疑问。",
                    personality="对安排改变缺乏耐心，愿意听取同伴解释。",
                    goals="了解眼前发生的事情。", speaking_style="先说明观察再提出疑问。",
                    action_tendency="依照实际技能逐项查证。")

    app = create_app(settings)
    app.state.agent_model_adapter = FakeModelAdapter(responder=responder)
    original = httpx.AsyncClient

    def catalogue(request):
        assert str(request.url).endswith("/api/tags"), "External generation is forbidden"
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    httpx.AsyncClient = lambda **kwargs: original(
        transport=httpx.MockTransport(catalogue), **kwargs,
    )
    uvicorn.run(app, host="127.0.0.1", port=8159, log_level="warning")


class Check(SmokeCheck):
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.processes, self.logs, self.pages = [], [], []
        self.http = httpx.Client(trust_env=False, timeout=20,
                                 headers={"Authorization": f"Bearer {TEST_HOST}"})
        self.frontend_url = "http://127.0.0.1:5259"
        self.report = {"mode": "real Chrome + HTTP/WebSocket; fake persona; zero external calls"}

    def request(self, method, path, body=None):
        response = self.http.request(method, "http://127.0.0.1:8159/api" + path, json=body)
        assert response.is_success, (response.status_code, response.text)
        return response.json()

    def calls(self):
        path = self.directory / "fake-calls.json"
        return len(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else 0

    def unlock(self, page):
        wait_for(lambda: page.evaluate('!!document.querySelector("#host-key")'))
        page.fill("#host-key", TEST_HOST)
        page.click("解锁主机")

    def run(self):
        for port in (8159, 5259):
            port_free(port)
        protected = [SOURCE / "ui.db", SOURCE / "knowledge/knowledge.db"]
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
        for original in protected:
            target = self.directory / original.relative_to(SOURCE)
            target.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(f"file:{original.as_posix()}?mode=ro", uri=True) as source:
                with sqlite3.connect(target) as destination:
                    source.backup(destination)
        for kind in ("rules", "modules"):
            shutil.copytree(SOURCE / kind, self.directory / kind)
        self.start([sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)],
                   BACKEND, "backend")
        wait_for(lambda: self.http.get("http://127.0.0.1:8159/api/health").status_code == 200, 60)
        os.environ["COC_BACKEND_PORT"], os.environ["COC_FRONTEND_PORT"] = "8159", "5259"
        self.start([shutil.which("node"), str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                    "--host", "127.0.0.1"], ROOT / "frontend", "frontend")
        wait_for(lambda: self.http.get(self.frontend_url).status_code == 200)
        if "--recover-only" in sys.argv:
            self.recover_only()
            after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
            assert before == after
            self.report.update(source_protection=after, model_calls=0)
            dump(self.directory / "report.json", self.report)
            print(json.dumps(self.report, ensure_ascii=False), flush=True)
            return
        if "--cold-only" in sys.argv:
            second = BrowserPage(self, "cold-browser")
            self.pages.append(second)
            self.cold_room(second, 0)
            after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
            assert before == after
            self.report.update(source_protection=after, model_calls=0)
            dump(self.directory / "report.json", self.report)
            print(json.dumps(self.report, ensure_ascii=False), flush=True)
            return
        options = self.request("GET", "/launch/options")
        prep_id = options["preparations"][0]["id"]
        requirement_path = f"/launch/preparations/{prep_id}/requirements"
        self.request("POST", requirement_path,
                     {"minimum_players": 1, "maximum_players": 2,
                      "source": "测试夹具：与追书人相同的已核准1至2人边界"})
        host = BrowserPage(self, "host")
        self.pages.append(host)
        host.navigate("#/new")
        self.unlock(host)
        wait_for(lambda: host.evaluate('!!document.querySelector("#launch-module")'))
        host.fill("#launch-module", prep_id)
        host.click("下一步：配队伍")
        wait_for(lambda: host.evaluate('!!document.querySelector("#launch-party-count")'))
        assert host.evaluate('document.querySelector("#launch-party-count").value') == "1"
        host.fill("#launch-party-count", "2")
        wait_for(lambda: host.contains("超过模组已核准人数"))
        assert host.evaluate('document.querySelector("[data-testid=random-party] button").disabled')
        assert self.calls() == 0
        host.screenshot("01-count-blocked-before-model.png")
        self.report["count_limit"] = {"default_ai": 1, "attempted_ai": 2, "model_calls": 0}
        self.request("POST", requirement_path,
                     {"minimum_players": 1, "maximum_players": 3, "source": "测试夹具三人边界"})
        host.command("Page.reload")
        wait_for(lambda: host.contains("快速生成完整角色"))
        host.fill("#launch-era", "modern")
        host.click("快速生成完整角色")
        wait_for(lambda: self.calls() == 1)
        host.command("Page.reload")
        wait_for(lambda: host.contains("采用为我的角色"), 40)
        assert self.calls() == 1
        self.report["refresh_during_generation"] = {"persona_calls": 1, "duplicated": False}
        saved = host.evaluate('JSON.parse(localStorage.getItem("coc.launch.selection"))')
        draft_id = saved["draftId"]
        own_batch = self.request("GET", f"/launch-drafts/{draft_id}")["document"]["own_batch_id"]
        calls = self.calls()
        host.evaluate("localStorage.clear(); location.reload()")
        wait_for(lambda: host.contains("恢复未完成的开团"))
        host.evaluate('[...document.querySelectorAll("summary")].find('
                      's => s.textContent === "恢复未完成的开团").click()')
        host.click("继续 · 原创开场")
        wait_for(lambda: host.contains("采用为我的角色"))
        assert self.calls() == calls
        assert host.evaluate('document.querySelector("#launch-era").value') == "modern"
        host.click("采用为我的角色")
        wait_for(lambda: host.evaluate('!!document.querySelector("#launch-character")?.value'))
        host.fill("#launch-party-count", "2")
        host.click("随机生成 2 名完整队友")
        wait_for(lambda: host.contains("已完成 2 / 2 人"), 40)
        draft = self.request("GET", f"/launch-drafts/{draft_id}")
        batch_id = draft["document"]["party_batch_id"]
        batch = self.request("GET", f"/party-batches/{batch_id}")
        assert batch["count"] == 2 and not draft["room_id"]
        host.fill("#launch-party-count", "1")
        host.click("应用为 1 名队友（保留前 1 人）")
        wait_for(lambda: self.request("GET", f"/party-batches/{batch_id}")["count"] == 1)
        resized = self.request("GET", f"/party-batches/{batch_id}")
        assert resized["members"][0] == batch["members"][0]
        assert resized["metrics"]["model_calls"] == batch["metrics"]["model_calls"]
        host.screenshot("02-resized-retained-person.png")
        second = BrowserPage(self, "second-browser")
        self.pages.append(second)
        second.navigate("#/new")
        self.unlock(second)
        wait_for(lambda: second.contains("恢复未完成的开团"))
        second.evaluate('[...document.querySelectorAll("summary")].find('
                        's => s.textContent === "恢复未完成的开团").click()')
        second.click("继续 · 原创开场")
        wait_for(lambda: second.contains("已完成 1 / 1 人"))
        recovered = second.evaluate('JSON.parse(localStorage.getItem("coc.launch.selection"))')
        assert recovered["partyId"] == batch_id and recovered["ownBatchId"] == own_batch
        assert recovered["era"] == "modern" and recovered["count"] == 1
        assert self.calls() == 3
        second.screenshot("03-second-browser-server-resume.png")
        self.report["recovery"] = {"draft_id": draft_id, "batch_id": batch_id,
                                   "own_batch_id": own_batch, "before_team_check": True,
                                   "count": 1, "era": "modern", "additional_model_calls": 0,
                                   "retained_member_identical": True}
        dump(self.directory / "recovery-report.json", self.report)
        self.cold_room(second, 3)
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
        assert before == after
        self.report.update(source_protection=after, model_calls=self.calls(),
                           errors=[error for page in self.pages for error in page.exceptions])
        dump(self.directory / "report.json", self.report)
        print(json.dumps(self.report, ensure_ascii=False), flush=True)

    def cold_room(self, second, expected_calls):
        room_id = self.request("GET", "/rooms")[0]["id"]
        before_room = self.request("GET", f"/rooms/{room_id}")
        second.evaluate("localStorage.clear(); sessionStorage.clear()")
        second.command("Page.navigate", {"url": self.frontend_url + f"/#/rooms/{room_id}"})
        self.unlock(second)
        wait_for(second.connected, 30)
        frames = second.evaluate("window.roomFrames")
        snapshots = [frame["data"] for frame in frames if frame["type"] == "room.snapshot"]
        assert snapshots and all(not frame["is_host"] for frame in snapshots)
        assert second.evaluate("location.hash") == f"#/rooms/{room_id}"
        after_room = self.request("GET", f"/rooms/{room_id}")
        dump(self.directory / "cold-room-before.json", before_room)
        dump(self.directory / "cold-room-after.json", after_room)
        for snapshot in (before_room, after_room):
            for member in snapshot["members"]:
                member.pop("last_seen_at", None)  # WebSocket authentication records presence.
        assert after_room == before_room
        assert self.calls() == expected_calls
        second.screenshot("04-cold-room-inplace-unlock.png")
        self.report["cold_room"] = {"room_id": room_id, "player_projection": True,
                                    "room_unchanged_except_presence": True,
                                    "additional_model_calls": 0}

    def recover_only(self):
        draft = self.request("GET", "/launch/options")["drafts"][0]
        doc = draft["document"]
        key = "party_batch_id" if doc.get("party_batch_id") else "own_batch_id"
        batch = self.request("GET", f"/party-batches/{doc[key]}")
        for name in ("first-browser", "second-browser"):
            page = BrowserPage(self, name)
            self.pages.append(page)
            page.navigate("#/new")
            self.unlock(page)
            wait_for(lambda: page.contains("恢复未完成的开团"))
            page.evaluate('[...document.querySelectorAll("summary")].find('
                          's => s.textContent === "恢复未完成的开团").click()')
            page.click("继续 · 原创开场")
            marker = "已完成 1 / 1 人" if key == "party_batch_id" else "采用为我的角色"
            wait_for(lambda: page.contains(marker))
            if name == "first-browser":
                page.evaluate("localStorage.clear(); location.reload()")
                wait_for(lambda: page.contains("恢复未完成的开团"))
                page.click("继续 · 原创开场")
                wait_for(lambda: page.contains(marker))
            restored = page.evaluate('JSON.parse(localStorage.getItem("coc.launch.selection"))')
            assert restored["draftId"] == draft["id"]
            assert restored["era"] == batch["era"] == "modern"
            assert restored["partyId" if key == "party_batch_id" else "ownBatchId"] == batch["id"]
            if name == "first-browser" and key == "party_batch_id":
                retained = batch["members"][0]
                previous_calls = batch["metrics"]["model_calls"]
                page.fill("#launch-party-count", "2")
                page.click("应用为 2 名队友（保留前 1 人）")
                wait_for(lambda: page.contains("已完成 2 / 2 人"))
                page.evaluate('[...document.querySelectorAll("label")].find('
                              'l => l.textContent.includes("邀请朋友加入"))'
                              '.querySelector("input").click()')
                page.fill("#launch-party-count", "1")
                page.click("应用为 1 名队友（保留前 1 人）")
                wait_for(lambda: page.contains("已完成 1 / 1 人"))
                batch = self.request("GET", f"/party-batches/{batch['id']}")
                draft = self.request("GET", f"/launch-drafts/{draft['id']}")
                assert batch["members"][0] == retained
                assert batch["metrics"]["model_calls"] == previous_calls
                assert draft["document"]["reserved_humans"] == 1
                self.report["resize"] = {"from": 2, "to": 1, "retained_identical": True,
                                         "reserved_humans_persisted": 1, "model_calls": 0}
            page.screenshot("server-restored.png")
        assert self.request("GET", f"/launch-drafts/{draft['id']}") == draft
        assert self.request("GET", f"/party-batches/{batch['id']}") == batch
        assert self.calls() == 0
        self.report["recovery"] = {"draft_id": draft["id"], "batch_id": batch["id"],
                                   "role": key, "era": "modern", "count": batch["count"],
                                   "status": batch["status"],
                                   "before_team_check": not draft["room_id"],
                                   "batch_unchanged": True, "additional_model_calls": 0,
                                   "browsers": 2, "cleared_cache": True}

    def close_all(self):
        for page in self.pages:
            if page.cdp:
                page.cdp.close()
        for _, process in reversed(self.processes):
            self.stop(process)
        self.http.close()
        for log in self.logs:
            log.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        if "--source" in sys.argv:
            SOURCE = ROOT / "data/prepared/batch-49" / sys.argv[sys.argv.index("--source") + 1]
        check = Check(ROOT / "data/prepared/batch-49" / sys.argv[1])
        try:
            check.run()
        except Exception:
            for page in check.pages:
                try:
                    page.screenshot("failure.png")
                    (page.directory / "failure.txt").write_text(
                        page.evaluate("document.body.innerText"), encoding="utf-8",
                    )
                except Exception:
                    pass
            raise
        finally:
            check.close_all()
