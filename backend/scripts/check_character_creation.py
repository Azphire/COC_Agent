"""Real Chrome character workflow. Uses an isolated SQLite file; never calls a model.

Run from backend/: uv run python scripts/check_character_creation.py
Artifacts remain in .cache/character-smoke-<uuid>/. Requires installed Chrome and Node.
"""

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def serve(database_path: Path):
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    original_client = httpx.AsyncClient

    def catalogue(request):
        assert request.method == "GET"
        assert str(request.url) == "http://127.0.0.1:11434/api/tags"
        assert not request.content
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    # Only the existing status handler's catalogue transport is replaced. This
    # server has no route for generation, and the transport rejects other URLs.
    httpx.AsyncClient = lambda **kwargs: original_client(
        transport=httpx.MockTransport(catalogue), **kwargs
    )
    settings = Settings(
        _env_file=None,
        data_dir=database_path.parent,
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        model_provider="ollama",
        model_name="qwen3:8b",
        model_base_url="http://127.0.0.1:11434/v1/",
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8000, log_level="warning")


def wait_for(check, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for browser/service state")


def port_free(port):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


class SmokeCheck:
    def __init__(self):
        self.directory = ROOT / ".cache" / f"character-smoke-{uuid4().hex}"
        self.directory.mkdir(parents=True)
        self.processes = []
        self.logs = []
        self.http = httpx.Client(trust_env=False, timeout=5)
        self.cdp = None
        self.request_id = 0
        self.requests = []
        self.exceptions = []
        self.report = {"model_status": "mocked catalogue; zero Ollama/external API calls"}

    def start(self, command, cwd, name):
        log = (self.directory / f"{name}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        environment = {**os.environ, "PYTHONUTF8": "1"}
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.processes.append((name, process))
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
                check=False,
            )
            process.wait(timeout=10)

    def command(self, method, params=None):
        self.request_id += 1
        request_id = self.request_id
        self.cdp.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            response = json.loads(self.cdp.recv(timeout=15))
            if response.get("method") == "Network.requestWillBeSent":
                self.requests.append(response["params"]["request"]["url"])
            if response.get("method") == "Runtime.exceptionThrown":
                self.exceptions.append(response["params"])
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result", {})

    def evaluate(self, expression):
        result = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def fill(self, selector, value):
        self.evaluate(
            """(() => {
            const input = document.querySelector(%s);
            if (!input || input.matches(':disabled')) throw Error('Input unavailable');
            const proto = input.tagName === 'SELECT' ? HTMLSelectElement.prototype
              : input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
              : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, %s);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        })()"""
            % (json.dumps(selector), json.dumps(str(value)))
        )

    def click(self, label):
        expression = (
            "[...document.querySelectorAll('button')].find(b => b.textContent === %s)"
            % json.dumps(label)
        )
        wait_for(lambda: self.evaluate(f"!!({expression}) && !({expression}).matches(':disabled')"))
        self.evaluate(f"({expression}).click()")

    def contains(self, text):
        return self.evaluate(f"document.body.innerText.includes({json.dumps(text)})")

    def navigate(self, route):
        self.evaluate(f"window.location.hash = {json.dumps(route)}")

    def current(self):
        character_id = self.evaluate(
            "document.querySelector('[data-testid=character-id]')?.textContent"
        )
        return self.http.get(f"http://127.0.0.1:8000/api/characters/{character_id}").json()

    def screenshot(self, filename):
        self.evaluate("window.scrollTo(0, 0)")
        result = self.command(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
        )
        (self.directory / filename).write_bytes(base64.b64decode(result["data"]))

    def save_draft(self, previous_version):
        self.click("保存草稿")
        wait_for(
            lambda: self.contains("草稿已保存") and self.current()["version"] > previous_version
        )

    def run(self):
        for port in (8000, 5173):
            port_free(port)
        backend_command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            str(self.directory / "characters.db"),
        ]
        backend = self.start(backend_command, BACKEND, "backend")
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("Node is not installed")
        self.start(
            [node, str(ROOT / "frontend/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1"],
            ROOT / "frontend",
            "frontend",
        )
        wait_for(lambda: self.http.get("http://127.0.0.1:5173").status_code == 200)
        chrome = next(
            (
                path
                for path in [
                    Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                    / "Google/Chrome/Application/chrome.exe",
                    Path(os.environ.get("LOCALAPPDATA", ""))
                    / "Google/Chrome/Application/chrome.exe",
                ]
                if path.is_file()
            ),
            None,
        )
        if chrome is None:
            raise RuntimeError("Chrome is not installed")
        profile = self.directory / "chrome-profile"
        browser = self.start(
            [
                str(chrome),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-extensions",
                "--disable-component-update",
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--window-size=1280,960",
                "about:blank",
            ],
            ROOT,
            "chrome",
        )
        devtools = profile / "DevToolsActivePort"
        wait_for(devtools.exists)
        port = devtools.read_text().splitlines()[0]
        targets = self.http.get(f"http://127.0.0.1:{port}/json").json()
        target = next(target for target in targets if target["type"] == "page")
        self.cdp = connect(target["webSocketDebuggerUrl"], proxy=None, open_timeout=5)
        for method in ["Page.enable", "Network.enable", "Runtime.enable"]:
            self.command(method)
        self.command(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(self.directory)},
        )
        self.command("Page.navigate", {"url": "http://127.0.0.1:5173/#/characters"})
        wait_for(lambda: self.contains("还没有角色"))
        self.report["empty_list"] = "passed"

        # All creation/edit/finalize/import actions below use the rendered UI.
        self.navigate("#/create")
        wait_for(lambda: self.contains("随机生成整组属性并保存草稿"))
        self.fill("#character-name", "Chrome 第七版随机角色")
        self.click("随机生成整组属性并保存草稿")
        wait_for(lambda: self.contains("掷骰记录") and self.contains("派生值"))
        random_character = self.current()
        assert len(random_character["roll_records"]) == 11
        assert self.evaluate("document.querySelector('#attribute-str').readOnly")
        self.report["random_character"] = random_character["id"]
        self.screenshot("random-character.png")
        print("CHROME_RANDOM_AND_AUDIT=passed", flush=True)

        self.navigate("#/create")
        wait_for(lambda: self.contains("随机生成整组属性并保存草稿"))
        self.fill("#creation-mode", "point_buy")
        self.fill("#character-name", "Chrome 第七版购点角色")
        self.click("创建购点草稿")
        wait_for(lambda: self.contains("购点预估剩余"))
        point_character = self.current()
        for key in ["str", "con", "siz", "dex", "app", "int", "pow", "edu"]:
            self.fill(f"#attribute-{key}", 90)
        assert (
            self.evaluate("document.querySelector('[data-testid=estimated-points]').textContent")
            == "-260"
        )
        assert self.evaluate("document.querySelector('#finalize-character').disabled")
        self.save_draft(point_character["version"])
        assert not self.current()["validation"]["valid"]
        assert self.evaluate("document.querySelector('#finalize-character').disabled")
        self.report["overspending_blocked"] = "passed"
        self.screenshot("overspent-draft.png")

        for key, value in {
            "str": 60,
            "con": 60,
            "siz": 60,
            "dex": 60,
            "app": 50,
            "int": 60,
            "pow": 50,
            "edu": 60,
        }.items():
            self.fill(f"#attribute-{key}", value)
        self.fill("#occupation", "professor")
        for key in ["history", "biology", "chemistry", "occult"]:
            self.evaluate(f"document.querySelector('#occupation-choice-{key}').click()")
        self.fill("#occupation_skills-credit_rating", 20)
        self.fill("#occupation_skills-history", 40)
        self.fill("#interest_skills-spot_hidden", 30)
        self.save_draft(2)
        saved = self.current()
        assert saved["validation"]["valid"], saved["validation"]
        assert saved["remaining_points"]["attributes"] == 0
        assert saved["skill_values"]["history"] == 45
        assert saved["skill_values"]["spot_hidden"] == 55
        assert saved["roll_records"] == point_character["roll_records"]
        self.click("最终确认")
        wait_for(lambda: self.contains("角色卡 · 已最终确认"))
        finalized = self.current()
        self.report["point_buy_finalized"] = finalized["id"]
        print("CHROME_POINT_BUY_CORRECTION_SKILLS_FINALIZE=passed", flush=True)
        self.command("Page.reload")
        wait_for(lambda: self.contains("角色卡 · 已最终确认"))
        assert self.current() == finalized
        self.click("导出 JSON")
        wait_for(
            lambda: self.evaluate("document.querySelector('#character-json').value.length > 100")
        )
        exported = json.loads(self.evaluate("document.querySelector('#character-json').value"))
        assert exported["character"]["id"] == finalized["id"]
        download = self.directory / f"character-{finalized['id']}.json"
        wait_for(download.exists)
        assert json.loads(download.read_text(encoding="utf-8")) == exported
        self.click("导入为新草稿")
        wait_for(lambda: self.contains("导入自：") and self.contains("编辑角色草稿"))
        imported = self.current()
        assert imported["id"] != finalized["id"] and imported["original_id"] == finalized["id"]
        assert imported["derived_values"] == finalized["derived_values"]
        assert all(record["source"] == "imported" for record in imported["roll_records"])
        self.report["import_new_id"] = imported["id"]
        self.screenshot("imported-character.png")
        self.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False},
        )
        assert self.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        self.screenshot("mobile-character.png")
        self.command("Emulation.clearDeviceMetricsOverride")

        self.stop(backend)
        self.start(backend_command, BACKEND, "backend-restarted")
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
        self.command("Page.reload")
        wait_for(lambda: self.contains(imported["id"]))
        assert self.current() == imported
        recovered = self.http.get(
            f"http://127.0.0.1:8000/api/characters/{random_character['id']}"
        ).json()
        assert recovered == random_character
        self.report["restart_and_roll_stability"] = "passed"
        print("CHROME_REFRESH_EXPORT_IMPORT_RESTART=passed", flush=True)

        self.navigate("#/status")
        wait_for(
            lambda: (
                self.contains("后端健康状态：ok")
                and self.contains("WebSocket：已连接")
                and self.contains("模型状态：ollama / qwen3:8b · 可用")
            )
        )
        self.fill("#test-message", "第七版车卡验证 echo")
        self.click("发送测试消息")
        wait_for(
            lambda: self.evaluate(
                "document.querySelector('pre').textContent.includes('第七版车卡验证 echo')"
            )
        )
        echo = json.loads(self.evaluate("document.querySelector('pre').textContent"))
        assert echo["type"] == "echo" and echo["data"]["text"] == "第七版车卡验证 echo"
        assert not any(":11434" in url for url in self.requests)
        assert not self.exceptions, self.exceptions
        self.report["health_websocket_and_mock_model_status"] = "passed"
        self.screenshot("system-status.png")
        self.navigate("#/characters")
        wait_for(
            lambda: (
                self.contains("Chrome 第七版随机角色") and self.contains("Chrome 第七版购点角色")
            )
        )
        self.screenshot("character-list.png")
        self.report["browser_exceptions"] = self.exceptions
        self.report["passed"] = True
        self.command("Browser.close")
        self.cdp.close()
        self.cdp = None
        browser.wait(timeout=10)
        print("CHROME_STATUS_ECHO_AND_LIST=passed", flush=True)

    def close(self):
        if self.cdp:
            self.cdp.close()
        for name, process in reversed(self.processes):
            self.stop(process)
            print(f"STOPPED={name}:{process.pid}", flush=True)
        for log in self.logs:
            log.close()
        self.http.close()
        (self.directory / "report.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"ARTIFACTS={self.directory}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        check = SmokeCheck()
        try:
            check.run()
        finally:
            check.close()
        for port in (8000, 5173):
            port_free(port)
        print("TEMPORARY_PORTS_RELEASED=8000,5173", flush=True)
