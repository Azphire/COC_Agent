"""Isolated real OpenAI game, UI switching and Windows startup acceptance.

Run: python backend/scripts/check_batch23_api.py live-c1 --live
A new run without --live or --switch-only checks startup/UI only (zero inference).
All artifacts and session credentials stay under ignored batch-23 directories.
"""

import argparse
import base64
import ctypes
import json
import os
import signal
import socket
import sqlite3
import subprocess
import time
from uuid import uuid4

import httpx
from check_batch22 import Check, write
from check_batch23_evidence import verify
from check_character_creation import ROOT, SmokeCheck, wait_for
from check_multiplayer import BrowserPage, MultiplayerCheck
from websockets.sync.client import connect

from app.config import Settings
from app.models.credentials import resolve_credential, service_identity
from app.models.settings import ModelConfiguration, ModelSettings

OFFICIAL = "https://api.openai.com/v1/"
HOST = "batch23-isolated-host"


class ApiCheck(Check):
    artifact_batch = "batch-23"

    def __init__(self, args):
        root = (ROOT / "data/prepared/changan" / self.artifact_batch).resolve()
        self.directory = (root / args.run).resolve()
        assert self.directory.is_relative_to(root) and self.directory != root
        self.directory.mkdir(parents=True, exist_ok=args.resume)
        self.port, self.front = args.backend_port, args.frontend_port
        self.frontend_url = f"http://127.0.0.1:{self.front}"
        self.processes, self.logs = [], []
        self.launcher = self.page = self.prefix = self.player = None
        self.run = 0
        self.http = httpx.Client(
            trust_env=False, timeout=600, headers={"Authorization": "Bearer " + HOST}
        )
        initial = Settings()
        manager = ModelSettings(initial)
        selected = manager.configurations.get("openai")
        if not selected or service_identity(selected.provider, selected.base_url) != (
            "openai",
            "https",
            "api.openai.com",
            443,
            "/v1/",
        ):
            selected = ModelConfiguration(
                provider="openai", model="gpt-4.1-mini", base_url=OFFICIAL
            )
        self.api = {
            "provider": "openai",
            "model": selected.model,
            "base_url": OFFICIAL,
            "output_mode": "json_object",
            "api_key": "",
        }
        credential = resolve_credential(initial, selected)
        self.result = {
            "real_api": {
                "status": "not_run",
                "model": selected.model,
                "output_mode": "json_object",
                **credential.public(),
            },
            "turns": [],
            "launcher": [],
            "trials": [],
        }
        self.env = {
            **os.environ,
            "DATA_DIR": str(self.directory),
            "DATABASE_URL": "sqlite+aiosqlite:///" + (self.directory / "game.db").as_posix(),
            "CHECKPOINT_DB_PATH": str(self.directory / "checkpoint.db"),
            "KNOWLEDGE_DB_PATH": str(self.directory / "knowledge.db"),
            "MODEL_SETTINGS_PATH": str(self.directory / "host-model-settings.json"),
            "APP_PORT": str(self.port),
            "APP_HOST": "127.0.0.1",
            "HOST_ADMIN_TOKEN": HOST,
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": selected.model,
            "MODEL_BASE_URL": OFFICIAL,
            "MODEL_OUTPUT_MODE": "json_object",
            "MODEL_API_KEY": credential.key.get_secret_value()
            if credential.source == "MODEL_API_KEY"
            else "",
            "MODEL_TIMEOUT_SECONDS": "240",
            "PYTHONUTF8": "1",
        }
        if credential.source == "saved" and not args.resume:
            # Copy only an already explicitly saved key, never an environment secret.
            write(
                self.directory / "host-model-settings.json",
                {
                    "provider": "openai",
                    "revision": 0,
                    "configurations": {
                        "openai": {
                            **self.api,
                            "api_key": selected.api_key.get_secret_value(),
                            "api_key_source": "saved",
                        }
                    },
                },
            )
        if args.resume:
            self.result = json.loads((self.directory / "result.json").read_text(encoding="utf-8"))
            session = json.loads((self.directory / "session.json").read_text(encoding="utf-8"))
            self.prefix, self.player = session["prefix"], session["token"]
            self.run = len(self.result["launcher"])
            write(self.directory / f"result-before-resume-{self.run + 1}.json", self.result)
            for filename in ("agent-debug.json", "model-calls.json", "final-room.json"):
                path = self.directory / filename
                if path.exists():
                    write(
                        self.directory / f"before-resume-{self.run + 1}-{filename}",
                        json.loads(path.read_text(encoding="utf-8")),
                    )
        self.secrets = [
            v
            for v in (
                initial.openai_api_key.get_secret_value(),
                initial._environment_model_api_key.get_secret_value(),
                selected.api_key.get_secret_value(),
            )
            if v and v.lower() != "ollama"
        ]

    def request(self, method, path, body=None, player=False):
        response = self.http.request(
            method,
            self.frontend_url + "/api" + path,
            json=body,
            headers={"Authorization": "Bearer " + self.player} if player else None,
        )
        assert response.is_success, f"{method} {path}: HTTP {response.status_code} {response.text}"
        return response.json()

    def launch(self):
        from launch import available_port

        for port in (self.port, self.front):
            available_port("127.0.0.1", port)
        self.run += 1
        log = (self.directory / f"launcher-{self.run}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        self.launcher = subprocess.Popen(
            ["cmd.exe", "/c", str(ROOT / "start.cmd"), "--frontend-port", str(self.front)],
            cwd=self.directory,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            startupinfo=startup,
        )
        wait_for(lambda: self.http.get(self.frontend_url + "/api/health").status_code == 200, 55)
        with connect(f"ws://127.0.0.1:{self.front}/ws", proxy=None) as ws:
            ws.send(json.dumps({"type": "auth", "credential_type": "host", "token": HOST}))
            assert json.loads(ws.recv())["type"] == "connected"
            ws.send(json.dumps({"type": "test", "text": "batch23-proxy"}))
            assert json.loads(ws.recv())["type"] == "echo"
        config = self.request("GET", "/model/config")
        status = self.request("GET", "/model/status")
        with sqlite3.connect(self.directory / "game.db") as db:
            calls = db.execute("SELECT COUNT(*) FROM agent_model_calls").fetchone()[0]
        self.result["launcher"].append(
            {
                "start": self.run,
                "backend_port": self.port,
                "frontend_port": self.front,
                "http": True,
                "websocket": True,
                "configuration": config,
                "state": status["state"],
                "persisted_calls_at_start": calls,
            }
        )
        assert config["provider"] != "openai" or config["api_key_set"]
        self.persist()
        print(
            f"launcher {self.run}: HTTP/WS ready, {status['provider']}/{status['model']}",
            flush=True,
        )

    def stop(self):
        if not self.launcher:
            return
        # This runner owns a private hidden console; genuine CTRL_C_EVENT is confined to it.
        assert ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0)
        self.launcher.wait(timeout=30)
        exit_code = self.launcher.returncode
        self.launcher = None

        def released():
            for port in (self.port, self.front):
                with socket.socket() as sock:
                    if sock.connect_ex(("127.0.0.1", port)) == 0:
                        return False
            return True

        wait_for(released, 15)
        if self.result["launcher"]:
            self.result["launcher"][-1].update(
                ctrl_c=True, ports_released=True, exit_code=exit_code
            )
        self.persist()

    def persist(self):
        write(self.directory / "result.json", self.result)

    def browser(self):
        self.page = BrowserPage(self, f"browser-{self.run}")
        self.page.evaluate(
            f"sessionStorage.setItem('coc.host',{json.dumps(HOST)});location.hash='#/status';location.reload()"
        )
        wait_for(lambda: self.page.contains("当前模型设置"))
        wait_for(
            lambda: self.page.evaluate(
                "document.querySelector('#model-provider')?.matches(':enabled')"
            )
        )
        self.ui_save(self.api)
        self.page.command("Page.reload")
        wait_for(lambda: self.page.contains("当前模型设置"))
        wait_for(
            lambda: self.page.evaluate(
                "document.querySelector('#model-provider')?.value==='openai'"
            )
        )
        assert self.page.evaluate("document.querySelector('#model-key').value") == ""
        self.page.screenshot("api-settings.png")
        self.result["browser"] = {
            "save_reload": True,
            "blank_key_input": True,
            "configuration": self.request("GET", "/model/config"),
        }
        self.persist()

    def ui_save(self, config):
        self.page.fill("#model-provider", config["provider"])
        if config["provider"] == "openai":
            self.page.fill("#model-url", config["base_url"])
        self.page.fill("#model-name", config["model"])
        old = self.request("GET", "/model/config")["revision"]
        self.page.click("保存并切换")
        wait_for(lambda: self.request("GET", "/model/config")["revision"] > old)
        wait_for(
            lambda: self.page.evaluate(
                "document.querySelector('#model-provider')?.matches(':enabled')"
            )
        )

    def trial(self, name):
        self.page.click("试运行已保存模型")
        wait_for(lambda: self.page.contains("试运行结束，结果见下方。"), 300)
        trial = json.loads(
            self.page.evaluate(
                "document.querySelector('section[aria-labelledby=model-heading] "
                "details pre').textContent"
            )
        )
        self.result["trials"].append({"name": name, "ui": True, **trial})
        write(self.directory / f"trial-{name}.json", trial)
        self.persist()
        print(f"{name}: {trial['state']}, {len(trial['calls'])} calls", flush=True)
        return trial["state"] == "available"

    make_character = MultiplayerCheck.make_character

    def setup(self):
        created = self.request("POST", "/rooms", {"name": "第二十三批 OpenAI 钟楼短测"})
        self.prefix = "/rooms/" + created["room"]["id"]
        joined = self.request(
            "POST",
            "/rooms/join",
            {
                "invite_code": created["invite_code"],
                "display_name": "周衡",
            },
        )
        self.player = joined["member_token"]
        self.request(
            "POST", self.prefix + "/members", {"display_name": "沈岚", "controller_type": "agent"}
        )
        room = self.request("GET", self.prefix)
        for name in ("周衡", "沈岚"):
            # Ordinary character creation, no historical export dependency.
            values = dict(str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60)
            card = self.request(
                "POST",
                "/characters/point-buy",
                {
                    "ruleset_id": "coc7-character-creation",
                    "name": name,
                    "age": 25,
                    "attributes": {k: {"value": v} for k, v in values.items()},
                },
            )
            card = self.request(
                "PATCH",
                "/characters/" + card["id"],
                {
                    "version": card["version"],
                    "occupation": "firefighter",
                    "occupation_attribute": "str",
                    "occupation_skills": {"credit_rating": {"points": 9}},
                    "interest_skills": {"spot_hidden": {"points": 35}},
                    "equipment": [
                        {"id": "knife", "catalog_id": "knife", "name": "小刀", "quantity": 1}
                    ],
                },
            )
            card = self.request(
                "POST", f"/characters/{card['id']}/finalize", {"version": card["version"]}
            )
            member = next(m for m in room["members"] if m["display_name"] == name)
            published = self.request(
                "POST", self.prefix + "/character-slots", {"character_id": card["id"]}
            )["room"]
            slot = next(
                s for s in published["character_slots"] if s["source_character_id"] == card["id"]
            )
            self.request(
                "POST",
                self.prefix + "/character-assignments",
                {"slot_id": slot["id"], "member_id": member["id"]},
            )
            if name == "沈岚":
                teammate = member["id"]
                self.request("POST", self.prefix + "/ready", {"ready": True, "member_id": teammate})
        self.request("POST", self.prefix + "/module", {"module_id": "stopped-clock"})
        for role, member in (("keeper", room["host_member_id"]), ("investigator", teammate)):
            profile = self.request(
                "POST",
                "/agent-profiles",
                {"role": role, "name": "KP" if role == "keeper" else "沈岚"},
            )
            self.request(
                "POST",
                self.prefix + "/agent-bindings",
                {"member_id": member, "profile_id": profile["id"]},
            )
        self.request("POST", self.prefix + "/ready", {"ready": True}, player=True)
        self.request("POST", self.prefix + "/start")
        write(self.directory / "session.json", {"prefix": self.prefix, "token": self.player})
        write(self.directory / "initial-room.json", self.request("GET", self.prefix))

    def blocked_switch(self, stage):
        before = self.request("GET", self.prefix)
        checks = self.request("GET", self.prefix + "/checks")
        old = self.request("GET", "/model/config")
        response = self.http.post(
            self.frontend_url + "/api/model/config", json={**self.api, "model": "must-not-apply"}
        )
        assert response.status_code == 409
        assert self.request("GET", "/model/config") == old
        if stage == "waiting_for_roll":
            after = self.request("GET", self.prefix)
            assert before["session_state"] == after["session_state"]
            assert before["inventory"] == after["inventory"]
            assert checks == self.request("GET", self.prefix + "/checks")
        self.result.setdefault("switch_blocks", []).append({"stage": stage, "http_status": 409})

    def turn(self, text, retry=False, continuing=False):
        before = self.request("GET", self.prefix)
        start = time.monotonic()
        request = {"text": text, "client_request_id": str(uuid4())}
        if not continuing and not retry:
            self.result["inflight"] = {"text": text, "request": request}
            self.persist()
        if retry:
            self.request("POST", self.prefix + "/agent-cycle/retry", {})
        elif not continuing:
            self.request("POST", self.prefix + "/actions", request, player=True)
        handled, tested = set(), set()
        cycle = {}
        while time.monotonic() - start < 600:
            room = self.request("GET", self.prefix)
            cycle = (room.get("game") or {}).get("cycle") or {}
            if (
                cycle.get("status") in {"running", "waiting_for_roll"}
                and cycle["status"] not in tested
            ):
                self.blocked_switch(cycle["status"])
                tested.add(cycle["status"])
            for check in self.request("GET", self.prefix + "/checks"):
                if (
                    check["status"] != "pending"
                    or cycle.get("status") != "waiting_for_roll"
                    or check["id"] != cycle.get("state", {}).get("pending_check_id")
                ):
                    continue
                stage = (check.get("settlement") or {}).get("stage", "initial")
                key = (check["id"], stage)
                if key in handled:
                    continue
                suffix = "/choice" if stage == "choice" else "/roll"
                self.request(
                    "POST",
                    self.prefix + f"/checks/{check['id']}" + suffix,
                    {"operation": "accept"} if stage == "choice" else {},
                    player=True,
                )
                handled.add(key)
            if cycle.get("status") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        record = {
            "text": text,
            "request": request,
            "cycle": cycle,
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "checks": self.request("GET", self.prefix + "/checks"),
            "events": self.request(
                "GET", self.prefix + f"/events?after_seq={before['revision']}&limit=200"
            ),
            "before": before,
            "after": self.request("GET", self.prefix),
        }
        records = (
            self.result.setdefault("recoveries", [])
            if retry or continuing
            else self.result["turns"]
        )
        records.append(record)
        label = "recovery" if retry or continuing else "turn"
        write(self.directory / f"{label}-{len(records)}.json", record)
        self.persist()
        print(
            f"{label} {len(records)}: {cycle.get('status')}, {record['elapsed_seconds']} s",
            flush=True,
        )
        assert cycle.get("status") == "completed", "Live turn did not complete; failure preserved"
        self.result.pop("inflight", None)
        self.persist()

    def export(self):
        if not self.prefix:
            return
        runs = self.request("GET", self.prefix + "/agent-runs")
        debug = [self.request("GET", self.prefix + "/agent-runs/" + r["id"]) for r in runs]
        write(self.directory / "agent-debug.json", debug)
        write(self.directory / "final-room.json", self.request("GET", self.prefix))
        write(self.directory / "all-checks.json", self.request("GET", self.prefix + "/checks"))
        with sqlite3.connect(self.directory / "game.db") as db:
            calls = [
                json.loads(r[0])
                for r in db.execute("SELECT document FROM agent_model_calls ORDER BY rowid")
            ]
        write(self.directory / "model-calls.json", calls)
        self.result["game_calls"] = len(calls)
        self.result["game_usage"] = {
            k: sum((c.get("token_usage") or {}).get(k) or 0 for c in calls)
            for k in ("input", "output", "total")
        }

    def verify_game(self):
        self.export()
        self.persist()
        verified = verify(self.directory, require_switch=False)
        write(self.directory / "verified-game.json", verified)
        self.result["real_api"]["status"] = "game_verified"

    def run_check(self, args):
        kernel = ctypes.windll.kernel32
        # Detach from any shared console before sending a broadcast Ctrl+C.
        kernel.FreeConsole()
        assert kernel.AllocConsole()
        ctypes.windll.user32.ShowWindow(kernel.GetConsoleWindow(), 0)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self._console_handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)(lambda _: True)
        assert kernel.SetConsoleCtrlHandler(self._console_handler, True)
        try:
            self.launch()
            if args.resume and args.live and self.prefix:
                current = self.request("GET", self.prefix)["game"]["cycle"]
                if current["status"] in {"failed", "waiting_for_roll"}:
                    self.turn(
                        self.result.get("inflight", {}).get("text", "通过正常接口恢复未完成回合"),
                        retry=current["status"] == "failed",
                        continuing=True,
                    )
            self.browser()
            if args.switch_only:
                self.switch_checks()
                self.result["status"] = "switch_checks_completed"
                return
            if args.live:
                if not self.result["trials"] and not self.trial("openai-minimal"):
                    self.result["real_api"]["status"] = "blocked"
                    self.result["status"] = "blocked_at_minimal_trial"
                    return
                self.result["real_api"]["status"] = "connectivity_passed"
                if not self.prefix:
                    self.setup()
                if args.finish:
                    self.finish_missing()
                    self.result["status"] = "targeted_checks_completed"
                    return
                iid = next(
                    i["instance_id"]
                    for i in self.request("GET", self.prefix)["inventory"]
                    if i["holder_name"] == "周衡" and i["title"] == "小刀"
                )
                actions = [
                    "我环顾钟楼广场，只观察眼前公开可见的环境。",
                    "我问管理员林守时：钟楼什么时候停摆的？然后对沈岚说：请说说你的观察，先不要独自行动。",
                    "我进入维修间，先停在入口观察工作台。",
                    "我冒着碰落零件和弄伤手的风险，仔细搜索工作台背面和抽屉积灰覆盖的细节；请KP根据现场条件裁定侦查检定，找不到也接受结果。",
                    "我把自己持有的小刀放在脚边地上。",
                    f"我拾回刚才放在脚边、实例编号为{iid}的那把小刀。",
                    f"我再次拾取编号{iid}的小刀；如果已经持有，只确认现状。",
                    "请只回顾工作台调查的实际检定结果，以及我现在持有哪些物品，不执行新行动。",
                ]
                for text in actions[len(self.result["turns"]) :]:
                    self.turn(text)
                self.result["summary_rebuild"] = self.request(
                    "POST", self.prefix + "/summary-rebuild", {}
                )
                self.restore()
                self.turn("恢复后请只回顾此前检定结果、当前位置和我现在持有的物品，不进行新行动。")
                self.verify_game()
                self.switch_checks()
            else:
                self.stop()
                self.launch()
                assert self.request("GET", "/model/config")["api_key_set"]
            self.result["status"] = "completed"
        except Exception as error:
            self.result.update(status="failed", failure=str(error))
            self.result.setdefault("failures", []).append(
                {"launcher": self.run, "error": str(error)}
            )
            self.persist()
            raise
        finally:
            try:
                if self.launcher and self.prefix:
                    self.export()
            finally:
                if self.page and self.page.cdp:
                    self.page.cdp.close()
                self.stop()
                for _, process in reversed(self.processes):
                    SmokeCheck.stop(process)
                for log in self.logs:
                    log.close()
                self.persist()
                self.http.close()
                for path in self.directory.glob("*.json"):
                    if path.name not in {"session.json", "host-model-settings.json"}:
                        assert not any(
                            secret in path.read_text(encoding="utf-8") for secret in self.secrets
                        ), "Secret in evidence"
                for path in self.directory.glob("*.log"):
                    raw = path.read_bytes()
                    assert not any(
                        secret.encode(encoding) in raw
                        for secret in self.secrets
                        for encoding in ("utf-8", "utf-16-le")
                    ), "Secret in log"

    def finish_missing(self):
        room = self.request("GET", self.prefix)
        initial = json.loads((self.directory / "initial-room.json").read_text(encoding="utf-8"))
        item = next(
            i for i in initial["inventory"] if i["holder_name"] == "周衡" and i["title"] == "小刀"
        )
        iid = item["instance_id"]
        actions = [
            "我冒着碰落零件和弄伤手的风险，仔细搜索工作台背面和抽屉积灰覆盖的细节；请KP根据现场条件裁定侦查检定，找不到也接受结果。",
            "我把自己持有的小刀放在脚边地上。",
            f"我拾回刚才放在脚边、实例编号为{iid}的那把小刀。",
            f"我再次拾取编号{iid}的小刀；如果已经持有，只确认现状。",
            "请只回顾刚才工作台检定的实际结果和我现在持有的物品，不执行新行动。",
        ]
        start = len(self.result["turns"])
        self.result.setdefault("targeted_runs", []).append(
            {"start_turn": start + 1, "instance_id": iid}
        )
        if self.request("GET", self.prefix + "/checks"):
            # Continue the original proposed check after a runner interruption;
            # never resubmit or reroll an already settled investigation.
            actions = actions[1:]
        dropped = any(
            iid
            in t.get("after", {})
            .get("session_state", {})
            .get("module_runtime", {})
            .get("dropped_items", {})
            for t in self.result["turns"]
        )
        if dropped:
            actions = [a for a in actions if not a.startswith("我把自己持有")]
            if any(
                i["instance_id"] == iid and i["holder_name"] == "周衡" for i in room["inventory"]
            ):
                actions = [a for a in actions if not a.startswith("我拾回")]
        if any(
            t.get("text", "").startswith("我再次拾取")
            and t.get("cycle", {}).get("status") == "completed"
            for t in [*self.result["turns"], *self.result.get("recoveries", [])]
            if iid in t.get("text", "")
        ):
            actions = [a for a in actions if not a.startswith("我再次拾取")]
        for action in actions:
            self.turn(action)
        self.result["summary_rebuild"] = self.request("POST", self.prefix + "/summary-rebuild", {})
        self.restore()
        self.turn("恢复后请只回顾刚才的检定结果、我持有的小刀和当前位置，不执行新行动。")
        self.verify_game()
        self.result["targeted_runs"][-1]["end_turn"] = len(self.result["turns"])

    def switch_checks(self):
        local = self.request("GET", "/model/ollama-models")
        assert "qwen3:8b" in local["models"], "Installed Ollama model unavailable; not verified"
        before = self.request("GET", self.prefix) if self.prefix else None
        checks = self.request("GET", self.prefix + "/checks") if self.prefix else None
        ollama = {
            "provider": "ollama",
            "model": "qwen3:8b",
            "base_url": "http://127.0.0.1:11434/v1/",
        }
        for name, config in (
            ("ollama-before", ollama),
            ("openai-switch", self.api),
            ("ollama-after", ollama),
        ):
            if any(t["name"] == name and t["state"] == "available" for t in self.result["trials"]):
                continue
            self.ui_save(config)
            self.page.click("试运行已保存模型")
            wait_for(lambda: bool(self.request("GET", "/model/status")["busy"]), 10)
            blocked = self.http.post(self.frontend_url + "/api/model/config", json=self.api)
            # A short local trial can finish between the busy read and this POST.
            assert blocked.status_code in {200, 409}
            wait_for(lambda: self.page.contains("试运行结束，结果见下方。"), 300)
            trial = json.loads(
                self.page.evaluate(
                    "document.querySelector('section[aria-labelledby=model-heading] "
                    "details pre').textContent"
                )
            )
            write(self.directory / f"trial-{name}.json", trial)
            self.result["trials"].append(
                {
                    "name": name,
                    "ui": True,
                    "generation_switch_blocked": blocked.status_code == 409,
                    "switch_probe_status": blocked.status_code,
                    **trial,
                }
            )
            self.persist()
            assert trial["state"] == "available", trial
            print(f"UI switch {name}: available", flush=True)
        self.ui_save(self.api)
        self.page.command("Page.reload")
        wait_for(lambda: self.page.contains("OPENAI_API_KEY"))
        wait_for(lambda: not self.page.contains("读取中"))
        self.page.evaluate("document.querySelector('#model-heading').scrollIntoView()")
        screenshot = self.page.command("Page.captureScreenshot", {"captureBeyondViewport": True})
        (self.directory / "settings-verified.png").write_bytes(base64.b64decode(screenshot["data"]))
        if before:
            after = self.request("GET", self.prefix)
            comparison = {
                "session_state": before["session_state"] == after["session_state"],
                "inventory": before["inventory"] == after["inventory"],
                "checks": checks == self.request("GET", self.prefix + "/checks"),
            }
            assert all(comparison.values())
            self.result["switch_state_comparison"] = comparison
        self.result["switching"] = "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--switch-only",
        action="store_true",
        help="UI generation switching (real API calls possible), without new game actions",
    )
    parser.add_argument(
        "--finish", action="store_true", help="Recheck missing dice/items in the existing room"
    )
    parser.add_argument("--backend-port", type=int, default=8026)
    parser.add_argument("--frontend-port", type=int, default=5175)
    arguments = parser.parse_args()
    if arguments.finish and not (arguments.resume and arguments.live):
        parser.error("--finish requires --resume --live")
    if arguments.resume and not (arguments.live or arguments.switch_only):
        parser.error("--resume requires --live or --switch-only")
    if arguments.resume and arguments.live and not arguments.finish:
        parser.error("Use --resume --live --finish to preserve already completed actions")
    ApiCheck(arguments).run_check(arguments)
