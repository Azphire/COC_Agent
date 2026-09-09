"""Three isolated real Chrome profiles, real HTTP/WS/SQLite, offline model transport.

Run from backend: uv run python scripts/check_multiplayer.py
Uses ports 8000/5173 only after proving they are free. Retains ignored artifacts.
"""

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from websockets.sync.client import connect


class BrowserPage(SmokeCheck):
    def __init__(self, owner, name):
        self.directory = owner.directory / name
        self.directory.mkdir(exist_ok=True)
        self.http = owner.http
        self.cdp = None
        self.request_id = 0
        self.requests = []
        self.exceptions = []
        chrome = next(
            path
            for path in [
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            ]
            if path.is_file()
        )
        profile = self.directory / "profile"
        devtools = profile / "DevToolsActivePort"
        if devtools.exists():
            devtools.unlink()
        owner.start(
            [
                str(chrome),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--window-size=1280,960",
                "about:blank",
            ],
            ROOT,
            f"chrome-{name}",
        )
        wait_for(devtools.exists)
        port = devtools.read_text().splitlines()[0]
        target = next(
            t for t in self.http.get(f"http://127.0.0.1:{port}/json").json() if t["type"] == "page"
        )
        # CDP events may remain unread while a local model runs. Disable transport
        # pings so a full debug-event queue cannot cause a spurious ping timeout.
        self.cdp = connect(
            target["webSocketDebuggerUrl"], proxy=None, open_timeout=5, ping_interval=None
        )
        for method in ("Page.enable", "Network.enable", "Runtime.enable"):
            self.command(method)
        self.command(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(self.directory)},
        )
        self.command(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
            window.roomSockets = []; window.roomFrames = [];
            const OriginalSocket = window.WebSocket;
            window.WebSocket = class extends OriginalSocket {
              constructor(...args) {
                super(...args);
                if (String(args[0]).includes('/ws/rooms/')) {
                  window.roomSockets.push(this);
                  this.addEventListener('message', event => {
                    window.roomFrames.push(JSON.parse(event.data));
                  });
                }
              }
            };
        """
            },
        )
        self.command("Page.navigate", {"url": "http://127.0.0.1:5173/#/rooms"})
        wait_for(lambda: self.contains("加入房间"))

    def connected(self):
        return self.evaluate(
            "document.querySelector('[data-testid=connection]')?.textContent === '已连接'"
        )

    def text_at(self, selector):
        return self.evaluate(f"document.querySelector({json.dumps(selector)})?.textContent")


class MultiplayerCheck(SmokeCheck):
    def __init__(self):
        super().__init__(artifact_prefix="rooms")
        self.pages = []

    def request(self, method, path, body=None):
        response = self.http.request(method, "http://127.0.0.1:8000/api" + path, json=body)
        assert response.is_success, f"HTTP {response.status_code}: {response.text}"
        return response.json()

    def make_character(self, name):
        values = dict(str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60)
        character = self.request(
            "POST",
            "/characters/point-buy",
            {
                "ruleset_id": "coc7-character-creation",
                "name": name,
                "age": 25,
                "attributes": {key: {"value": value} for key, value in values.items()},
            },
        )
        path = f"/characters/{character['id']}"
        character = self.request(
            "PATCH",
            path,
            {
                "version": character["version"],
                "occupation": "professor",
                "selected_occupation_skills": ["history", "biology", "chemistry", "occult"],
                "occupation_skills": {"credit_rating": {"points": 20}},
            },
        )
        return self.request("POST", path + "/finalize", {"version": character["version"]})

    def run(self):
        for port in (8000, 5173):
            port_free(port)
        database_path = self.directory / "multiplayer.db"
        backend_command = [
            sys.executable,
            str(BACKEND / "scripts/check_character_creation.py"),
            "--serve",
            str(database_path),
        ]
        backend = self.start(backend_command, BACKEND, "backend")
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
        sheets = [
            self.make_character(name) for name in ("图书馆调查员", "Agent 调查员", "第三位调查员")
        ]
        host = BrowserPage(self, "host")
        self.pages.append(host)
        host.fill("#host-key", TEST_HOST)
        host.click("解锁主机")
        wait_for(lambda: host.contains("创建房间"))
        host.fill("#room-name", "夜访旧图书馆")
        host.click("创建房间")
        wait_for(host.connected)
        wait_for(lambda: host.text_at('[data-testid="invite-code"]'))
        room_id = host.evaluate("location.hash.split('/').at(-1)")
        prefix = f"/rooms/{room_id}"

        def current():
            return self.request("GET", prefix)

        invite = host.text_at('[data-testid="invite-code"]')
        assert len(invite) == 32
        for index, sheet in enumerate(sheets):
            wait_for(
                lambda: host.evaluate(
                    "!!document.querySelector('#publish-character "
                    f"option[value={json.dumps(sheet['id'])}]')"
                )
            )
            host.fill("#publish-character", sheet["id"])
            host.click("发布角色")
            wait_for(lambda: len(current()["character_slots"]) == index + 1)

        player = BrowserPage(self, "player")
        self.pages.append(player)
        outsider = BrowserPage(self, "outsider")
        self.pages.append(outsider)
        for page, name, sheet in (
            (player, "远程小林", sheets[0]),
            (outsider, "远程小周", sheets[2]),
        ):
            assert page.evaluate("sessionStorage.getItem('coc.host')") is None
            page.fill("#join-invite", invite)
            page.fill("#join-name", name)
            page.click("加入房间")
            wait_for(page.connected)
            wait_for(lambda: page.contains(sheet["name"]))
            assert page.evaluate("document.querySelectorAll('.room-character details').length") == 0
            page.click("选择 · " + sheet["name"])
            wait_for(
                lambda: page.evaluate(
                    "document.querySelectorAll('.room-character details').length === 1"
                )
            )
            page.click("准备 · " + name)
            wait_for(lambda: page.contains("取消准备 · " + name))
        host.fill("#member-name", "占位队友")
        host.fill("#member-controller", "agent")
        host.click("添加席位")
        wait_for(lambda: host.contains("占位队友"))
        agent = next(m for m in current()["members"] if m["controller_type"] == "agent")
        host.fill('[aria-label="分配 Agent 调查员"]', agent["id"])
        host.click("分配 · Agent 调查员")
        wait_for(lambda: any(s["member_id"] == agent["id"] for s in current()["character_slots"]))
        host.click("准备 · 占位队友")
        wait_for(lambda: host.contains("取消准备 · 占位队友"))
        host.click("开始游戏")
        wait_for(
            lambda: all(
                page.text_at('[data-testid="room-status"]') == "运行中" for page in self.pages
            )
        )
        assert host.contains("尚未接入自动行动")
        self.report["host_remote_agent_lobby_and_start"] = "passed"
        print("CHROME_ROOM_HOST_REMOTE_AGENT_START=passed", flush=True)

        for page, message in ((host, "主机公开消息"), (player, "玩家公开消息")):
            page.fill("#chat-message", message)
            page.click("发送消息")
            wait_for(
                lambda: all(
                    message in (p.text_at('[data-testid="timeline"]') or "") for p in self.pages
                )
            )
        player.fill("#dice-expression", "3d6+2")
        player.fill("#dice-reason", "共同检定")
        player.click("服务端掷骰")
        wait_for(
            lambda: all(
                "共同检定" in (p.text_at('[data-testid="timeline"]') or "") for p in self.pages
            )
        )
        events = self.request("GET", prefix + "/events")["events"]
        roll = next(e for e in events if e["type"] == "dice.rolled")
        assert roll["payload"]["total"] == sum(roll["payload"]["dice"]) + 2
        selector = f'[data-event-seq="{roll["seq"]}"] strong'
        assert {p.text_at(selector) for p in self.pages} == {str(roll["payload"]["total"])}
        player.fill("#event-visibility", "actor_and_host")
        player.fill("#chat-message", "仅小林与主机可见的线索")
        player.click("发送消息")
        wait_for(
            lambda: "仅小林与主机可见的线索" in (host.text_at('[data-testid="timeline"]') or "")
        )
        self.request(
            "POST",
            prefix + "/messages",
            {"text": "隐私验证屏障", "client_request_id": str(uuid4())},
        )
        wait_for(lambda: outsider.contains("隐私验证屏障"))
        assert "仅小林与主机可见的线索" not in outsider.text_at('[data-testid="timeline"]')
        assert "仅小林与主机可见的线索" not in outsider.evaluate(
            "JSON.stringify(window.roomFrames)"
        )
        self.report["public_chat_authoritative_dice_private_filter"] = "passed"
        print("CHROME_CHAT_DICE_PRIVATE_VISIBILITY=passed", flush=True)

        host.click("暂停游戏")
        wait_for(lambda: host.text_at('[data-testid="room-status"]') == "已暂停")
        host.click("编辑场景与资源")
        host.fill("#scene-title", "旧图书馆大厅")
        host.fill("#scene-summary", "雨水敲打窗户，调查员在大厅集合。")
        host.click("保存场景与资源")
        wait_for(lambda: host.text_at('[data-testid="scene-title"]') == "旧图书馆大厅")
        original = current()["session_state"]
        host.fill("#save-name", "大厅检查点")
        host.click("创建存档")
        wait_for(lambda: host.contains("载入 · 大厅检查点"))
        snapshot = self.request("GET", prefix + "/snapshots")[0]
        before_change_seq = current()["revision"]
        host.click("编辑场景与资源")
        host.fill("#scene-title", "尚未探索的地下室")
        slot_id = current()["character_slots"][0]["id"]
        host.fill(f'[aria-label="{slot_id}-hp"]', 1)
        host.click("保存场景与资源")
        wait_for(lambda: current()["session_state"]["characters"][slot_id]["hp"] == 1)
        # Wait for the completed HTTP response to re-enable the button. The DB
        # commit above can be observed before React clears its busy state.
        wait_for(
            lambda: host.evaluate(
                "[...document.querySelectorAll('button')]"
                ".some(b => b.textContent === '载入 · 大厅检查点' && !b.disabled)"
            )
        )
        point = host.evaluate("""(() => {
            const button = [...document.querySelectorAll('button')]
              .find(b => b.textContent === '载入 · 大厅检查点');
            button.scrollIntoView({block: 'center'});
            const rect = button.getBoundingClientRect();
            return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        })()""")
        host.command(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "button": "left",
                "clickCount": 1,
                **point,
            },
        )
        # Do not await mouseReleased: the real native dialog can hold its reply.
        host.request_id += 1
        host.cdp.send(
            json.dumps(
                {
                    "id": host.request_id,
                    "method": "Input.dispatchMouseEvent",
                    "params": {"type": "mouseReleased", "button": "left", "clickCount": 1, **point},
                }
            )
        )

        def accept_dialog():
            try:
                host.command("Page.handleJavaScriptDialog", {"accept": True})
                return True
            except RuntimeError as error:
                if "No dialog is showing" not in str(error):
                    raise
                return False

        wait_for(accept_dialog)
        wait_for(lambda: current()["session_state"] == original)
        wait_for(lambda: player.text_at('[data-testid="scene-title"]') == "旧图书馆大厅")
        assert current()["status"] == "paused"
        assert current()["revision"] > before_change_seq
        assert any(
            e["type"] == "session.updated"
            and e["payload"]["state"]["scene_title"] == "尚未探索的地下室"
            for e in self.request("GET", prefix + "/events")["events"]
        )
        assert player.connected() and outsider.connected()
        self.report["save_load_confirmation_state_history_presence"] = "passed"
        print("CHROME_SAVE_LOAD_STATE_HISTORY=passed", flush=True)

        previous_sockets = player.evaluate("window.roomSockets.length")
        player.command(
            "Network.emulateNetworkConditions",
            {"offline": True, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
        )
        player.evaluate("window.roomSockets.at(-1).close()")
        gap = self.request(
            "POST",
            prefix + "/messages",
            {"text": "断线期间的补发消息", "client_request_id": str(uuid4())},
        )["event"]
        player.command(
            "Network.emulateNetworkConditions",
            {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
        )
        wait_for(
            lambda: (
                player.connected()
                and player.evaluate("window.roomSockets.length") > previous_sockets
            )
        )
        wait_for(lambda: "断线期间的补发消息" in player.text_at('[data-testid="timeline"]'))
        assert (
            player.evaluate(
                f"document.querySelectorAll('[data-event-seq=\"{gap['seq']}\"]').length"
            )
            == 1
        )
        self.report["websocket_disconnect_replay_deduplication"] = "passed"

        before_restart = current()
        self.stop(backend)
        wait_for(lambda: not player.connected())
        self.start(backend_command, BACKEND, "backend-restarted")
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
        wait_for(lambda: all(page.connected() for page in self.pages))
        after_restart = current()
        for key in ("id", "status", "revision", "session_state", "character_slots"):
            assert after_restart[key] == before_restart[key]
        assert self.request("GET", prefix + "/snapshots") == [snapshot]
        self.report["backend_restart_database_recovery"] = "passed"
        print("CHROME_RECONNECT_AND_BACKEND_RESTART=passed", flush=True)

        credentials = [TEST_HOST, invite] + [
            page.evaluate(f"localStorage.getItem('coc.room.{room_id}')")
            for page in (player, outsider)
        ]
        for page in self.pages:
            for label, extension in (("导出 JSONL", "jsonl"), ("导出 Markdown", "md")):
                page.click(label)
                filename = page.directory / f"room-{room_id}.{extension}"
                wait_for(filename.exists)
                content = filename.read_text(encoding="utf-8")
                assert all(secret not in content for secret in credentials)
                assert ("仅小林与主机可见的线索" in content) == (page is not outsider)
                if extension == "jsonl":
                    assert all("seq" in json.loads(line) for line in content.splitlines())
                else:
                    assert content.startswith("# 房间")
            assert all("localhost" not in url and ":11434" not in url for url in page.requests)
            socket_urls = page.evaluate("window.roomSockets.map(s => s.url)")
            assert all(
                url.startswith("ws://127.0.0.1:5173/ws/rooms/") and "?" not in url
                for url in socket_urls
            )
            assert all(secret not in url for secret in credentials for url in socket_urls)
            assert not page.exceptions, page.exceptions
            page.screenshot("room-session.png")
        assert host.evaluate("localStorage.getItem('coc.host')") is None
        with sqlite3.connect(database_path) as database:
            dump = "\n".join(database.iterdump())
            assert all(secret not in dump for secret in credentials)
        for logfile in self.directory.glob("*.log"):
            content = logfile.read_text(encoding="utf-8", errors="replace")
            assert all(secret not in content for secret in credentials)
        player.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False},
        )
        assert player.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        player.screenshot("room-mobile.png")
        player.command("Emulation.clearDeviceMetricsOverride")
        outsider.click("离开房间并清除凭据")
        wait_for(lambda: outsider.contains("加入房间"))
        assert outsider.evaluate(f"localStorage.getItem('coc.room.{room_id}')") is None
        self.report["exports_secret_scan_relative_urls_mobile_leave"] = "passed"
        self.report["browser_contexts"] = 3
        self.report["database"] = str(database_path)
        self.report["passed"] = True
        print("CHROME_EXPORTS_CREDENTIALS_MOBILE_LEAVE=passed", flush=True)

    def close(self):
        for page in self.pages:
            if page.cdp:
                page.cdp.close()
        super().close()


if __name__ == "__main__":
    check = MultiplayerCheck()
    try:
        check.run()
    finally:
        check.close()
    for port in (8000, 5173):
        port_free(port)
    print("TEMPORARY_PORTS_RELEASED=8000,5173", flush=True)
