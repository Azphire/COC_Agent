"""Three real Chrome identities + Windows launcher/HTTP/WS/Ctrl+C, isolated, no inference.

Run from the repository root: backend/.venv/Scripts/python.exe -X utf8
backend/scripts/check_batch27_ui.py <new-run-name>. Uses ports 8027/5187.
"""

import base64
import ctypes
import json
import os
import signal
import sqlite3
import subprocess
import sys
from uuid import uuid4

import httpx
from check_character_creation import ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage


class SubmissionUI(SmokeCheck):
    def __init__(self, run_name):
        root = (ROOT / "data/prepared/changan/batch-27").resolve()
        self.directory = (root / run_name).resolve()
        assert self.directory.is_relative_to(root) and self.directory != root
        self.directory.mkdir(parents=True, exist_ok=False)
        self.processes, self.logs, self.pages = [], [], []
        self.http = httpx.Client(
            trust_env=False, timeout=8, headers={"Authorization": f"Bearer {TEST_HOST}"}
        )
        self.cdp, self.launcher, self.request_id = None, None, 0
        self.requests, self.exceptions = [], []
        self.frontend_url = "http://127.0.0.1:5187"
        self.report = {
            "platform": sys.platform,
            "network": "same Windows host, loopback",
            "provider": "openai (unconfigured, no key, example.test)",
            "model": "unconfigured-api",
            "inference_calls": 0,
            "remote_device_acceptance": "not_run",
        }

    def request(self, method, path, body=None):
        response = self.http.request(method, self.frontend_url + "/api" + path, json=body)
        assert response.is_success, f"HTTP {response.status_code}: {response.text}"
        return response.json()

    def startup(self):
        for port in (8027, 5187):
            port_free(port)
        # Detach this test process, then create its own hidden console. Ctrl+C cannot
        # reach the user's existing terminal, Ollama or Tailscale processes.
        kernel = ctypes.windll.kernel32
        kernel.FreeConsole()
        assert kernel.AllocConsole()
        ctypes.windll.user32.ShowWindow(kernel.GetConsoleWindow(), 0)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        assert kernel.SetConsoleCtrlHandler(None, True)
        env = {
            **os.environ,
            "APP_PORT": "8027",
            "APP_HOST": "127.0.0.1",
            "DATA_DIR": str(self.directory),
            "HOST_ADMIN_TOKEN": TEST_HOST,
            "DATABASE_URL": "sqlite+aiosqlite:///" + (self.directory / "game.db").as_posix(),
            "MODEL_SETTINGS_PATH": str(self.directory / "host-model-settings.json"),
            "CHECKPOINT_DB_PATH": str(self.directory / "checkpoint.db"),
            "KNOWLEDGE_DB_PATH": str(self.directory / "knowledge.db"),
            "MODEL_PROVIDER": "openai",
            "MODEL_BASE_URL": "https://example.test/v1/",
            "MODEL_NAME": "unconfigured-api",
            "MODEL_API_KEY": "",
            "OPENAI_API_KEY": "",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
        }
        log = (self.directory / "launcher.log").open("w", encoding="utf-8")
        self.logs.append(log)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        self.launcher = subprocess.Popen(
            ["cmd.exe", "/c", str(ROOT / "start.cmd"), "--tailscale", "--frontend-port", "5187"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            startupinfo=startup,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        wait_for(lambda: self.http.get(self.frontend_url + "/api/health").status_code == 200, 55)
        wait_for(
            lambda: "Ctrl+C stops" in (self.directory / "launcher.log").read_text(encoding="utf-8"),
            15,
        )
        status = self.request("GET", "/model/status")
        assert status["provider"] == "openai" and status["state"] == "unconfigured"
        self.report["http_proxy_ready"] = True

    def card_file(self):
        values = dict(str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60)
        card = self.request(
            "POST",
            "/characters/point-buy",
            {
                "ruleset_id": "coc7-character-creation",
                "name": "异地文件调查员",
                "age": 25,
                "attributes": {k: {"value": v} for k, v in values.items()},
            },
        )
        specs = [
            {"id": f"custom_{group}_{uuid4().hex}", "group": group, "name": name}
            for group, name in [
                ("language", "葡萄牙语"),
                ("art_craft", "陶艺"),
                ("science", "地球物理学"),
            ]
        ]
        language, art, science = [s["id"] for s in specs]
        path = "/characters/" + card["id"]
        card = self.request(
            "PATCH",
            path,
            {
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
                "background": {"beliefs": "PRIVATE-BATCH27-CHARACTER"},
            },
        )
        assert card["validation"]["valid"]
        document = self.request("GET", path + "/export")
        document["character"]["status"] = "finalized"
        document["character"]["skill_values"][science] = 999
        document["character"]["derived_values"]["hp"] = 999
        file = self.directory / "remote-character.json"
        file.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return file, card

    @staticmethod
    def select_file(page, file):
        root = page.command("DOM.getDocument")["root"]["nodeId"]
        node = page.command(
            "DOM.querySelector",
            {"nodeId": root, "selector": '[data-testid="character-submissions"] input[type=file]'},
        )["nodeId"]
        page.command("DOM.setFileInputFiles", {"nodeId": node, "files": [str(file)]})

    @staticmethod
    def capture_panel(page, name):
        page.evaluate(
            "document.querySelector('[data-testid=character-submissions]').scrollIntoView()"
        )
        result = page.command("Page.captureScreenshot", {"format": "png"})
        (page.directory / name).write_bytes(base64.b64decode(result["data"]))

    def run(self):
        self.startup()
        file, card = self.card_file()
        host = BrowserPage(self, "host")
        self.pages.append(host)
        host.fill("#host-key", TEST_HOST)
        host.click("解锁主机")
        wait_for(lambda: host.contains("创建房间"))
        host.fill("#room-name", "第27批隔离角色提交")
        host.click("创建房间")
        wait_for(host.connected)
        invite = host.text_at('[data-testid="invite-code"]')
        rid = host.evaluate("location.hash.split('/').at(-1)")
        prefix = f"/rooms/{rid}"

        def current():
            return self.request("GET", prefix)

        player = BrowserPage(self, "submitter")
        self.pages.append(player)
        other = BrowserPage(self, "other-member")
        self.pages.append(other)
        for page, name in ((player, "提交者"), (other, "另一成员")):
            page.fill("#join-invite", invite)
            page.fill("#join-name", name)
            page.click("加入房间")
            wait_for(page.connected)
            assert page.evaluate("sessionStorage.getItem('coc.host')") is None
        self.report["three_browser_identities_joined"] = True
        invalid = self.directory / "invalid.json"
        invalid.write_text("not-json", encoding="utf-8")
        self.select_file(player, invalid)
        wait_for(lambda: player.contains("文件不是有效 JSON"))
        self.select_file(player, file)
        wait_for(lambda: player.contains("服务端已重算"))
        for value in ("外语（葡萄牙语）：31", "艺术／手艺（陶艺）：25", "科学（地球物理学）：11"):
            assert player.contains(value)
        self.capture_panel(player, "preview.png")
        player.click("提交角色")
        wait_for(lambda: len(current()["character_submissions"]) == 1)
        wait_for(lambda: host.contains("待处理") and other.contains("待处理"))
        host.click("预览 · 提交者")
        wait_for(lambda: host.contains("科学（地球物理学）：11"))
        self.capture_panel(host, "pending.png")
        first = current()["character_submissions"][0]
        # Replacement makes the open host preview stale; old acceptance cannot act.
        self.select_file(player, file)
        wait_for(lambda: player.contains("文件：remote-character.json"))
        player.click("提交新版本")
        wait_for(lambda: host.contains("已有新版本"))
        assert host.evaluate(
            "[...document.querySelectorAll('button')].find(b=>b.textContent==='接受并分配').disabled"
        )
        response = self.http.post(
            self.frontend_url
            + "/api"
            + prefix
            + "/character-submissions/"
            + first["id"]
            + "/review",
            json={"expected_version": 1, "decision": "accept", "client_request_id": str(uuid4())},
        )
        assert response.status_code == 409
        host.click("预览 · 提交者")
        wait_for(lambda: host.contains("提交版本 2"))
        host.click("拒绝提交")
        wait_for(lambda: player.contains("已拒绝"))
        self.select_file(player, file)
        wait_for(lambda: player.contains("文件：remote-character.json"))
        player.click("提交新版本")
        wait_for(lambda: current()["character_submissions"][0]["version"] == 3)
        host.click("预览 · 提交者")
        wait_for(lambda: host.contains("提交版本 3"))
        host.click("接受并分配")
        wait_for(lambda: player.contains("已接受") and len(current()["character_slots"]) == 1)
        accepted = current()
        slot = accepted["character_slots"][0]
        assert slot["character_snapshot"]["derived_values"]["hp"] == 12
        assert slot["character_snapshot"]["status"] == "finalized"
        assert [r["dice"] for r in slot["character_snapshot"]["roll_records"]] == [
            r["dice"] for r in card["roll_records"]
        ]
        assert len(self.request("GET", "/characters")) == 2
        self.capture_panel(player, "accepted.png")
        frames = other.evaluate("window.roomFrames")
        assert "PRIVATE-BATCH27-CHARACTER" not in json.dumps(frames)
        assert "地球物理学" not in json.dumps(frames, ensure_ascii=False)
        result = other.evaluate(
            "fetch('/api"
            + prefix
            + "/character-submissions/"
            + first["id"]
            + "', {headers:{Authorization:'Bearer '+localStorage.getItem('coc.room."
            + rid
            + "')}}).then(r=>r.status)"
        )
        assert result == 403
        self.report["submission_flow"] = {
            "format_error": True,
            "recalculated_preview": True,
            "custom_specializations": 3,
            "replacement_and_stale_approval": True,
            "reject_and_resubmit": True,
            "accepted_assigned": True,
            "other_member_details_and_ws_filtered": True,
        }
        # Keep the original host publication / empty-slot self-selection path covered.
        local = self.request(
            "POST", "/characters/" + card["id"] + "/finalize", {"version": card["version"]}
        )
        self.request("POST", prefix + "/character-slots", {"character_id": local["id"]})
        wait_for(lambda: other.contains("选择 · 异地文件调查员"))
        other.click("选择 · 异地文件调查员")
        for page, name in ((player, "提交者"), (other, "另一成员")):
            page.click("准备 · " + name)
            wait_for(lambda: page.contains("取消准备 · " + name))
        host.click("开始游戏")
        wait_for(
            lambda: all(p.text_at('[data-testid="room-status"]') == "运行中" for p in self.pages)
        )
        assert current()["session_state"]["characters"][slot["id"]]["hp"] == 12
        # Transport-only public message, explicitly not natural model adjudication.
        player.fill("#chat-message", "第27批本机事件同步验证")
        player.click("发送消息")
        wait_for(
            lambda: all(
                "第27批本机事件同步验证" in (p.text_at('[data-testid="timeline"]') or "")
                for p in self.pages
            )
        )
        player.command(
            "Network.emulateNetworkConditions",
            {"offline": True, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
        )
        player.evaluate("window.roomSockets.forEach(s=>s.close())")
        wait_for(lambda: player.contains("重连"))
        player.command(
            "Network.emulateNetworkConditions",
            {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
        )
        wait_for(player.connected, 25)
        wait_for(lambda: player.contains("第27批本机事件同步验证"))
        player.screenshot("running-reconnected.png")
        self.report.update(
            room_id=rid,
            local_ready_and_start=True,
            websocket_proxy=True,
            local_offline_reconnect=True,
            natural_model_action="not_run",
        )
        with sqlite3.connect(self.directory / "game.db") as db:
            calls = db.execute("SELECT count(*) FROM agent_runs").fetchone()[0]
            assert calls == 0
            self.report["inference_calls"] = calls
        for page in self.pages:
            assert not page.exceptions, page.exceptions
        self.report["ui_status"] = "passed"
        self.save_report()

    def save_report(self):
        (self.directory / "report.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def close(self):
        try:
            for page in self.pages:
                if page.cdp:
                    page.cdp.close()
            for _, process in reversed(self.processes):
                self.stop(process)
            if self.launcher and self.launcher.poll() is None:
                assert ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0)
                self.launcher.wait(timeout=25)
                self.report["launcher_exit_code"] = self.launcher.returncode
                assert self.launcher.returncode == 0
                for port in (8027, 5187):
                    port_free(port)
                self.report["ctrl_c_and_ports_released"] = True
        finally:
            for log in self.logs:
                log.close()
            self.http.close()
            self.save_report()
            print(f"ARTIFACTS={self.directory}", flush=True)


if __name__ == "__main__":
    check = SubmissionUI(sys.argv[1])
    try:
        check.run()
    except Exception:
        for page in check.pages:
            try:
                page.screenshot("failed.png")
                (page.directory / "failed-page.txt").write_text(
                    page.evaluate("document.body.innerText"), encoding="utf-8"
                )
            except Exception:
                pass
        raise
    finally:
        check.close()
