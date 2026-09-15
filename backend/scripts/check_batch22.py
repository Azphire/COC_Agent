"""Isolated real Windows launcher/browser/Ollama short verification, no historical writes."""

import argparse
import ctypes
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
from uuid import uuid4

import httpx
from check_character_creation import ROOT, SmokeCheck, wait_for
from check_multiplayer import BrowserPage
from websockets.sync.client import connect

HOST = "batch22-isolated-host"
PORT = 8024
FRONT = 5173


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class Check:
    def __init__(self, name, resume=False):
        root = (ROOT / "data/prepared/changan/batch-22").resolve()
        self.directory = (root / name).resolve()
        assert self.directory.is_relative_to(root) and self.directory != root
        self.directory.mkdir(parents=True, exist_ok=resume)
        self.processes, self.logs = [], []
        self.http = httpx.Client(
            trust_env=False, timeout=600, headers={"Authorization": "Bearer " + HOST}
        )
        self.launcher = None
        self.page = None
        self.run = 0
        self.prefix = None
        self.player = None
        self.result = {"real_api": "not configured; not tested", "turns": [], "launcher": []}
        if resume:
            self.result = json.loads((self.directory / "result.json").read_text(encoding="utf-8"))
            session = json.loads((self.directory / "session.json").read_text(encoding="utf-8"))
            self.prefix, self.player = session["prefix"], session["token"]
            self.run = len(self.result["launcher"])
        self.env = {
            **os.environ,
            "DATA_DIR": str(self.directory),
            "DATABASE_URL": "sqlite+aiosqlite:///" + (self.directory / "game.db").as_posix(),
            "CHECKPOINT_DB_PATH": str(self.directory / "checkpoint.db"),
            "KNOWLEDGE_DB_PATH": str(self.directory / "knowledge.db"),
            "MODEL_SETTINGS_PATH": str(self.directory / "host-model-settings.json"),
            "APP_PORT": str(PORT),
            "APP_HOST": "127.0.0.1",
            "HOST_ADMIN_TOKEN": HOST,
            "MODEL_PROVIDER": "ollama",
            "MODEL_NAME": "qwen3:8b",
            "MODEL_BASE_URL": "http://127.0.0.1:11434/v1/",
            "MODEL_API_KEY": "ollama",
            "MODEL_TIMEOUT_SECONDS": "240",
            "PYTHONUTF8": "1",
        }

    start = SmokeCheck.start

    def request(self, method, path, body=None, player=False):
        response = self.http.request(
            method,
            f"http://127.0.0.1:{FRONT}/api" + path,
            json=body,
            headers={"Authorization": "Bearer " + self.player} if player else None,
        )
        assert response.is_success, f"{method} {path}: HTTP {response.status_code} {response.text}"
        return response.json()

    def launch(self):
        from launch import available_port

        for port in (PORT, FRONT):
            available_port("127.0.0.1", port)
        self.run += 1
        log = (self.directory / f"launcher-{self.run}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        # Called from a different cwd and through the delivered start.cmd.
        self.launcher = subprocess.Popen(
            ["cmd.exe", "/c", str(ROOT / "start.cmd"), "--lan"],
            cwd=self.directory,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            startupinfo=startup,
        )
        wait_for(
            lambda: self.http.get(f"http://127.0.0.1:{FRONT}/api/health").status_code == 200, 50
        )
        self.result["launcher"].append(
            {"start": self.run, "backend_port": PORT, "frontend_port": FRONT, "http": True}
        )
        with connect(f"ws://127.0.0.1:{FRONT}/ws", proxy=None) as ws:
            ws.send(json.dumps({"type": "auth", "credential_type": "host", "token": HOST}))
            assert json.loads(ws.recv())["type"] == "connected"
            ws.send(json.dumps({"type": "test", "text": "batch22-proxy"}))
            assert json.loads(ws.recv())["type"] == "echo"
        self.result["launcher"][-1]["websocket"] = True
        print(f"launcher {self.run}: HTTP and WS ready", flush=True)

    def stop(self):
        if not self.launcher:
            return
        self.launcher.send_signal(signal.CTRL_BREAK_EVENT)
        self.launcher.wait(timeout=25)
        self.launcher = None

        def released():
            for port in (PORT, FRONT):
                with socket.socket() as sock:
                    if sock.connect_ex(("127.0.0.1", port)) == 0:
                        return False
            return True

        wait_for(released, 15)
        self.result["launcher"][-1]["ports_released"] = True
        assert httpx.get("http://127.0.0.1:11434/api/tags", trust_env=False).is_success
        self.result["launcher"][-1]["existing_ollama_preserved"] = True

    def browser(self):
        page = self.page = BrowserPage(self, "browser")
        page.evaluate(
            f"sessionStorage.setItem('coc.host',{json.dumps(HOST)});location.hash='#/status';location.reload()"
        )
        wait_for(lambda: page.contains("当前模型设置"))
        wait_for(
            lambda: page.evaluate("document.querySelector('#model-provider')?.matches(':enabled')")
        )
        page.fill("#model-provider", "openai")
        page.fill("#model-url", "https://api.example.test/v1/")
        page.fill("#model-name", "not-a-real-api-test")
        page.click("保存并切换")
        wait_for(lambda: page.contains("已保存并生效"))
        assert self.request("GET", "/model/status")["state"] == "unconfigured"
        page.command("Page.reload")
        wait_for(lambda: page.contains("当前模型设置"))
        wait_for(
            lambda: page.evaluate("document.querySelector('#model-provider')?.value==='openai'")
        )
        assert page.evaluate("document.querySelector('#model-key').value") == ""
        page.fill("#model-provider", "ollama")
        page.fill("#model-name", "qwen3:8b")
        page.click("保存并切换")
        wait_for(lambda: page.contains("已保存并生效"))
        page.fill("#test-message", "batch22-browser-ws")
        wait_for(lambda: page.contains("WebSocket：已连接"))
        page.click("发送测试消息")
        wait_for(lambda: page.contains('"type": "echo"'))
        page.command("Page.captureScreenshot", {"format": "png"})
        import base64

        (self.directory / "settings-desktop.png").write_bytes(
            base64.b64decode(page.command("Page.captureScreenshot", {"format": "png"})["data"])
        )
        page.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        (self.directory / "settings-mobile.png").write_bytes(
            base64.b64decode(page.command("Page.captureScreenshot", {"format": "png"})["data"])
        )
        self.result["browser"] = {
            "save_switch_reload": True,
            "websocket": True,
            "exceptions": page.exceptions,
            "api_generation": False,
        }
        assert not page.exceptions
        print("browser settings/save/switch/reload passed", flush=True)

    def setup(self):
        source = ROOT / "data/prepared/changan/batch-21/browser-c2/musician-export.json"
        raw = source.read_bytes()
        write(
            self.directory / "scenario.json",
            {
                "module": "仓库原创停摆的钟楼",
                "card_source": str(source.relative_to(ROOT)),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "dice": "actual random; no resets",
                "formal_package_used": False,
            },
        )
        card = self.request("POST", "/characters/import", json.loads(raw))
        card = self.request(
            "POST", f"/characters/{card['id']}/finalize", {"version": card["version"]}
        )
        created = self.request("POST", "/rooms", {"name": "第二十二批原创隔离短测"})
        self.prefix = "/rooms/" + created["room"]["id"]
        joined = self.request(
            "POST",
            "/rooms/join",
            {"invite_code": created["invite_code"], "display_name": "测试调查员"},
        )
        self.player = joined["member_token"]
        member = joined["room"]["self_member_id"]
        room = self.request("POST", self.prefix + "/character-slots", {"character_id": card["id"]})[
            "room"
        ]
        slot = room["character_slots"][0]["id"]
        self.request(
            "POST", self.prefix + "/character-assignments", {"slot_id": slot, "member_id": member}
        )
        self.request("POST", self.prefix + "/module", {"module_id": "stopped-clock"})
        profile = self.request("POST", "/agent-profiles", {"role": "keeper", "name": "测试KP"})
        self.request(
            "POST",
            self.prefix + "/agent-bindings",
            {"member_id": room["host_member_id"], "profile_id": profile["id"]},
        )
        self.request("POST", self.prefix + "/ready", {"ready": True}, player=True)
        self.request("POST", self.prefix + "/start")
        write(
            self.directory / "session.json",
            {"prefix": self.prefix, "token": self.player, "player_id": member},
        )

    def turn(self, text):
        before = self.request("GET", self.prefix)
        start = time.monotonic()
        self.request(
            "POST",
            self.prefix + "/actions",
            {"text": text, "client_request_id": str(uuid4())},
            player=True,
        )
        tested_busy = False
        handled = set()
        while time.monotonic() - start < 600:
            room = self.request("GET", self.prefix)
            cycle = (room.get("game") or {}).get("cycle") or {}
            checks = self.request("GET", self.prefix + "/checks")
            for check in checks:
                if check["status"] != "pending":
                    continue
                stage = (check.get("settlement") or {}).get("stage", "initial")
                key = (check["id"], stage)
                if key in handled:
                    continue
                if not tested_busy:
                    response = self.http.post(
                        f"http://127.0.0.1:{FRONT}/api/model/config",
                        json={
                            "provider": "openai",
                            "model": "must-not-apply",
                            "base_url": "https://api.example.test/v1/",
                        },
                    )
                    assert response.status_code == 409
                    tested_busy = True
                path, body = (
                    (f"/checks/{check['id']}/choice", {"operation": "accept"})
                    if stage == "choice"
                    else (f"/checks/{check['id']}/roll", {})
                )
                self.request("POST", self.prefix + path, body, player=True)
                handled.add(key)
            if cycle.get("status") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        events = self.request(
            "GET", self.prefix + f"/events?after_seq={before['revision']}&limit=200"
        )
        record = {
            "text": text,
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "cycle": cycle,
            "checks": checks,
            "events": events,
            "busy_switch_rejected": tested_busy,
        }
        self.result["turns"].append(record)
        write(self.directory / f"turn-{len(self.result['turns'])}.json", record)
        print(
            f"turn {len(self.result['turns'])}: {cycle.get('status')} "
            f"({record['elapsed_seconds']} s)",
            flush=True,
        )
        if cycle.get("status") != "completed":
            raise AssertionError("Live turn did not complete; preserve failure evidence")

    def restore(self):
        before = self.request("GET", self.prefix)
        checks = self.request("GET", self.prefix + "/checks")
        saved = self.request("POST", self.prefix + "/snapshots", {"name": "batch22正常恢复"})[
            "snapshot"
        ]
        self.request("POST", self.prefix + "/pause")
        self.stop()
        self.launch()
        self.request("POST", self.prefix + f"/snapshots/{saved['id']}/load")
        after = self.request("GET", self.prefix)
        comparison = {
            k: before["session_state"][k] == after["session_state"][k]
            for k in (
                "characters",
                "module_runtime",
                "combat",
                "time_receipts",
                "game_minute",
                "game_round",
                "sanity_day",
            )
        }
        comparison["checks"] = checks == self.request("GET", self.prefix + "/checks")
        comparison["inventory"] = before["inventory"] == after["inventory"]
        self.result["restore"] = comparison
        self.result.setdefault("restores", []).append(comparison)
        write(self.directory / f"restore-comparison-{self.run}.json", comparison)
        write(self.directory / "restore-comparison.json", comparison)
        assert all(comparison.values()), comparison
        self.request("POST", self.prefix + "/resume")

    def run_check(self, live, complete_check=False):
        # Allocate an invisible console so Windows can deliver genuine console signals.
        kernel = ctypes.windll.kernel32
        if not kernel.GetConsoleWindow():
            kernel.AllocConsole()
            ctypes.windll.user32.ShowWindow(kernel.GetConsoleWindow(), 0)
        try:
            self.launch()
            if complete_check:
                self.turn("我仔细搜索维修间工作台的背面和抽屉，寻找被灰尘遮住的细节。")
                self.turn("请只回顾刚才实际完成的检定结果，以及我持有的小刀，不执行新行动。")
                self.restore()
                assert self.request("GET", self.prefix + "/checks"), "No real check was created"
                runs = self.request("GET", self.prefix + "/agent-runs")
                write(self.directory / "agent-debug.json", [
                    self.request("GET", self.prefix + "/agent-runs/" + run["id"]) for run in runs])
                write(self.directory / "final-room.json", self.request("GET", self.prefix))
            elif live:
                self.browser()
                trial = self.request("POST", "/model/test", {})
                write(self.directory / "minimal-structured.json", trial)
                assert trial["state"] == "available", trial
                self.setup()
                self.turn("我观察钟楼广场的钟面和公告。")
                self.turn("我对管理员林守时说：请问钟楼是什么时候停摆的？")
                self.turn("我进入维修间，仔细检查工作台后有没有隐藏的物品。")
                self.turn("我把自己的小刀放在脚边地上。")
                self.restore()
                self.turn("我拾回刚才放在脚边的小刀。")
                # Reapply same installed Ollama model: real adapter closes/recreates.
                self.request(
                    "POST",
                    "/model/config",
                    {
                        "provider": "ollama",
                        "model": "qwen3:8b",
                        "base_url": "http://127.0.0.1:11434/v1/",
                    },
                )
                self.turn("请回顾刚才工作台的检定结果，以及我现在实际持有的物品。不要进行新行动。")
                self.turn("我查看墙边已经看见的交接簿。")
                self.restore()
                runs = self.request("GET", self.prefix + "/agent-runs")
                debug = [
                    self.request("GET", self.prefix + "/agent-runs/" + run["id"]) for run in runs
                ]
                write(self.directory / "agent-debug.json", debug)
                write(self.directory / "final-room.json", self.request("GET", self.prefix))
            else:
                self.browser()
                self.stop()
                self.launch()
                assert self.request("GET", "/model/config")["model"] == "qwen3:8b"
            self.result["status"] = "passed"
        except Exception as error:
            self.result.update(status="failed", failure=str(error))
            raise
        finally:
            if self.page and self.page.cdp:
                self.page.cdp.close()
            self.stop()
            for _, process in reversed(self.processes):
                SmokeCheck.stop(process)
            for log in self.logs:
                log.close()
            write(self.directory / "result.json", self.result)
            self.http.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--complete-check", action="store_true")
    arguments = parser.parse_args()
    Check(arguments.run, resume=arguments.complete_check).run_check(
        arguments.live, arguments.complete_check
    )
