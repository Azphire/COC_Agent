"""Batch 42 real Chrome UI: three HO cards and two isolated member identities.

No model generation. Uses a fresh directory and ports 8042/5142; original sources
are read from data/, all mutable databases remain under the browser run directory.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage


def serve(directory):
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    def reject(*_, **__):
        raise AssertionError("Browser-only verification must not contact a model")

    httpx.HTTPTransport.handle_request = reject
    httpx.AsyncHTTPTransport.handle_async_request = reject
    settings = Settings(
        _env_file=None,
        host_admin_token=TEST_HOST,
        data_dir=ROOT / "data",
        database_url="sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
        checkpoint_db_path=directory / "checkpoint.db",
        knowledge_db_path=directory / "knowledge.db",
        model_settings_path=directory / "model-settings.json",
        model_provider="ollama",
        model_name="browser-no-generation",
        model_base_url="http://127.0.0.1:1/v1/",
        model_api_key="",
        openai_api_key="",
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8042, log_level="warning")


class Page(BrowserPage):
    def current(self):
        identifier = self.text_at('[data-testid="character-id"]')
        return self.http.get(f"http://127.0.0.1:5142/api/characters/{identifier}").json()

    def choose(self, selector, values):
        self.evaluate(
            """(() => {
          const e = document.querySelector(%s);
          for (const o of e.options) o.selected = %s.includes(o.value);
          e.dispatchEvent(new Event('change', {bubbles:true}));
        })()"""
            % (json.dumps(selector), json.dumps(values))
        )

    def capture(self, selector, name):
        self.evaluate(f"document.querySelector({json.dumps(selector)}).scrollIntoView()")
        screenshot = self.command("Page.captureScreenshot", {"format": "png"})
        (self.directory / name).write_bytes(base64.b64decode(screenshot["data"]))


class BrowserCheck(SmokeCheck):
    def __init__(self, run_name):
        root = (ROOT / "data/prepared/zhihulu/batch-42/browser").resolve()
        self.directory = (root / run_name).resolve()
        assert self.directory.is_relative_to(root) and self.directory != root
        self.directory.mkdir(parents=True, exist_ok=False)
        self.processes, self.logs, self.pages = [], [], []
        self.http = httpx.Client(
            trust_env=False, timeout=30, headers={"Authorization": f"Bearer {TEST_HOST}"}
        )
        self.frontend_url = "http://127.0.0.1:5142"
        self.result = {
            "status": "failed",
            "model_calls": 0,
            "network": "same-host loopback",
            "cards": [],
        }

    def start(self, command, cwd, name):
        log = (self.directory / f"{name}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "COC_BACKEND_PORT": "8042",
                "COC_FRONTEND_PORT": "5142",
                "MODEL_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.processes.append((name, process))
        return process

    def get(self, path):
        response = self.http.get(self.frontend_url + "/api" + path)
        assert response.is_success, response.text
        return response.json()

    def run(self):
        for port in (8042, 5142):
            port_free(port)
        self.start(
            [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)],
            BACKEND,
            "backend",
        )
        wait_for(lambda: self.http.get("http://127.0.0.1:8042/api/health").status_code == 200)
        self.start(
            [shutil.which("node"), str(ROOT / "frontend/node_modules/vite/bin/vite.js")],
            ROOT / "frontend",
            "frontend",
        )
        wait_for(lambda: self.http.get(self.frontend_url).status_code == 200)
        host = Page(self, "host")
        self.pages.append(host)
        host.fill("#host-key", TEST_HOST)
        host.click("解锁主机")
        wait_for(lambda: host.contains("创建房间"))
        host.navigate("#/preparations")
        wait_for(lambda: host.contains("导入已准备模组"))
        root = host.command("DOM.getDocument")["root"]["nodeId"]
        node = host.command(
            "DOM.querySelector", {"nodeId": root, "selector": "#preparation-package"}
        )["nodeId"]
        host.command(
            "DOM.setFileInputFiles",
            {
                "nodeId": node,
                "files": [str(ROOT / "data/prepared/zhihulu/batch-42/package-reviewed.json")],
            },
        )
        host.click("导入准备包")
        wait_for(lambda: host.contains("模组专属 HO 与建卡要求"), 150)
        prep = self.get("/module-preparations")[0]
        self.result["preparation_id"] = prep["id"]
        host.capture('[data-testid="preparation-handouts"]', "preparation-handouts.png")
        print("Source-reviewed package imported through UI", flush=True)
        for ho in ("HO1", "HO2", "HO3"):
            host.navigate("#/create")
            wait_for(lambda: host.contains("随机生成整组属性并保存草稿"))
            host.fill("#character-name", f"浏览器-{ho}")
            host.fill("#character-age", 19)
            host.fill("#creation-mode", "point_buy")
            host.click("创建购点草稿")
            wait_for(lambda: host.contains("模组专属 HO 角色调整"))
            original = host.current()
            for key, value in dict(
                str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60
            ).items():
                host.fill("#attribute-" + key, value)
            host.fill("#age-deduction-str", 5)
            host.fill("#character-era", "modern")
            host.fill("#occupation", "student")
            for key, choices in {
                "language": ["own_language"],
                "academic": ["history", "psychology", "law"],
                "personal": ["spot_hidden", "stealth"],
            }.items():
                host.choose("#group-" + key, choices)
            host.fill("#occupation_skills-credit_rating", 5)
            host.fill("#occupation_skills-psychology", 10)
            host.fill("#interest_skills-stealth", 15)
            for key in ("appearance", "beliefs", "people", "places", "possessions", "traits"):
                host.fill("#background-" + key, "按 HO 创建的学生背景")
            host.fill("#background-appearance", "19 岁男学生" if ho == "HO3" else "19 岁女学生")
            host.fill("#key-connection", "people")
            wait_for(
                lambda: host.evaluate(
                    "!!document.querySelector('#character-handout-preparation "
                    f'option[value="{prep["id"]}"]\')'
                )
            )
            host.fill("#character-handout-preparation", prep["id"])
            wait_for(
                lambda: host.evaluate(
                    f"!!document.querySelector('#character-handout-selection option[value={ho}]')"
                )
            )
            host.fill("#character-handout-selection", ho)
            if ho == "HO2":
                for key in ("str", "pow", "dex"):
                    host.fill("#handout-attribute-" + key, 10)
            host.save_draft(original["version"])
            preview = host.current()
            assert [i["code"] for i in preview["validation"]["issues"]] == ["keeper_approval"], (
                preview["validation"]
            )
            assert host.evaluate("document.querySelector('#finalize-character').disabled")
            host.capture('[data-testid="module-handout-summary"]', ho + "-preview.png")
            host.evaluate("document.querySelector('#approve-module-handout').click()")
            host.save_draft(preview["version"])
            approved = host.current()
            assert approved["validation"]["valid"], approved["validation"]
            host.click("最终确认")
            wait_for(lambda: host.contains("角色卡 · 已最终确认"))
            card = host.current()
            assert (
                card["module_handout_approval"] and card["roll_records"] == original["roll_records"]
            )
            assert card["attributes"]["str"]["value"] == 60
            if ho == "HO2":
                assert card["effective_attributes"]["str"] == 65
                assert card["effective_attributes"]["pow"] == 60
                assert card["effective_attributes"]["dex"] == 70
                assert card["derived_values"]["mp"] == 12
                assert card["derived_values"]["san"] == 60
                assert card["skill_values"]["dodge"] == 35
                assert card["skill_values"]["credit_rating"] == 5
            else:
                assert card["skill_values"]["credit_rating"] == 35
                skill, expected = ("psychology", 50) if ho == "HO1" else ("stealth", 65)
                assert card["skill_values"][skill] == expected
            host.command("Page.reload")
            wait_for(lambda: host.contains("角色卡 · 已最终确认"))
            wait_for(lambda: host.current() == card)
            self.result["cards"].append(card)
            print(ho + " creation/preview/approval/freeze/reload through UI passed", flush=True)
        host.navigate("#/rooms")
        wait_for(lambda: host.contains("创建房间"))
        host.fill("#room-name", "第42批 浏览器 HO 定向分配")
        host.click("创建房间")
        wait_for(host.connected)
        room_id = host.evaluate("location.hash.split('/').pop()")
        prefix = "/rooms/" + room_id
        self.result["room_id"] = room_id
        invite = host.text_at('[data-testid="invite-code"]')
        for name in ("成员甲", "成员乙"):
            player = Page(self, name)
            self.pages.append(player)
            player.fill("#join-invite", invite)
            player.fill("#join-name", name)
            player.click("加入房间")
            wait_for(player.connected)
        host.fill("#member-name", "AI 预留席位")
        host.fill("#member-controller", "agent")
        host.click("添加席位")
        wait_for(lambda: host.contains("AI 预留席位"))
        for card in self.result["cards"]:
            host.fill("#publish-character", card["id"])
            host.click("发布角色")
            wait_for(
                lambda: (
                    len(self.get(prefix)["character_slots"]) == self.result["cards"].index(card) + 1
                )
            )
        room = self.get(prefix)
        members = [
            next(m for m in room["members"] if m["display_name"] == name)
            for name in ("成员甲", "成员乙", "AI 预留席位")
        ]
        slots = [
            next(s for s in room["character_slots"] if s["public_summary"]["name"] == card["name"])
            for card in self.result["cards"]
        ]
        for slot, member in zip(slots, members):
            host.fill(f'[aria-label="分配 {slot["public_summary"]["name"]}"]', member["id"])
            host.click("分配 · " + slot["public_summary"]["name"])
            wait_for(
                lambda: (
                    next(s for s in self.get(prefix)["character_slots"] if s["id"] == slot["id"])[
                        "member_id"
                    ]
                    == member["id"]
                )
            )
        host.fill("#room-preparation", prep["id"])
        host.click("绑定准备版本")
        wait_for(
            lambda: host.evaluate(
                "document.querySelectorAll('#handout-selection option').length===4"
            )
        )
        for number, (slot, member) in enumerate(zip(slots, members), 1):
            host.fill("#handout-selection", "HO" + str(number))
            host.fill("#handout-recipient", slot["id"])
            host.click("分配给 " + member["display_name"])
            wait_for(lambda: len(self.get(prefix)["handouts"]["assignments"]) == number)
        host.capture('[data-testid="room-handouts"]', "host-assignments.png")
        for index, player in enumerate(self.pages[1:], 1):
            wait_for(
                lambda: player.evaluate(
                    "document.querySelectorAll('[data-testid=assigned-handout]').length===1"
                )
            )
            own = "HO" + str(index)
            assigned = player.evaluate(
                "[...document.querySelectorAll('[data-testid=assigned-handout]')].map(e=>e.dataset.handoutId)"
            )
            assert assigned == [own], assigned
            frames = player.evaluate("window.roomFrames")
            for frame in frames:
                if frame["type"] == "room.snapshot":
                    view = frame["data"]
                    assert not view["handouts"]["available"]
                    assert all(
                        item["handout_id"] == own and item["member_id"] == view["self_member_id"]
                        for item in view["handouts"]["assignments"]
                    )
                if frame["type"] == "room.event" and frame["data"]["type"] == "handout.assigned":
                    assert frame["data"]["payload"]["assignment"]["handout_id"] == own
            for other in prep["handouts"]:
                if other["id"] != own:
                    assert other["text"] not in json.dumps(frames, ensure_ascii=False)
            assert not player.evaluate("!!document.querySelector('#handout-selection')")
            player.capture('[data-testid="room-handouts"]', "own-handout.png")
            player.command(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 390,
                    "height": 844,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            assert player.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            player.capture('[data-testid="room-handouts"]', "own-handout-mobile.png")
        self.result.update(
            status="passed",
            two_member_browser_isolation=True,
            browser_errors=[e for page in self.pages for e in page.exceptions],
        )
        assert not self.result["browser_errors"], self.result["browser_errors"]
        print(
            "Host UI assigned all three HOs; "
            "two member browser identities each received only their own HO",
            flush=True,
        )

    def close(self):
        for page in self.pages:
            if page.cdp:
                page.cdp.close()
        for _, process in reversed(self.processes):
            self.stop(process)
        for log in self.logs:
            log.close()
        self.http.close()
        (self.directory / "result.json").write_text(
            json.dumps(self.result, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    if sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        check = BrowserCheck(sys.argv[1])
        try:
            check.run()
        except Exception as error:
            check.result["error"] = str(error)
            for page in check.pages:
                page.screenshot("failed.png")
            raise
        finally:
            check.close()
