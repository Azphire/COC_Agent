"""Real Chrome UI + normal service check, deterministic model fixture, zero external calls.

Run from repo root with a fresh run name. Only ports 8028/5188 and a new data directory.
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

    sys.path.insert(0, str(BACKEND / "tests"))
    from test_action_adjudication import modern_response

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app

    def reject(*_, **__):
        raise AssertionError("Batch 28 must not contact an external model or HTTP server")

    httpx.HTTPTransport.handle_request = reject
    httpx.AsyncHTTPTransport.handle_async_request = reject

    def respond(messages, kwargs):
        result = modern_response(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            context = json.loads(messages[-1]["content"])
            actor = next(
                c
                for c in context["characters"]
                if c["member_id"] == context["action_identifiers"]["actor_member_id"]
            )
            key = next(k for k, v in actor["custom_skill_names"].items() if v == "生存（湿地）")
            assert actor["skill_values"][key] == 60
            (directory / "keeper-context.json").write_text(
                json.dumps(
                    dict(kind="deterministic fixture", actor=actor), ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            result["proposed_check"]["name"] = key
        with (directory / "fixture-calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"schema": kwargs["response_schema"].__name__, "type": "deterministic"})
                + "\n"
            )
        return result

    settings = Settings(
        _env_file=None,
        host_admin_token=TEST_HOST,
        data_dir=directory,
        database_url="sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
        checkpoint_db_path=directory / "checkpoint.db",
        knowledge_db_path=directory / "knowledge.db",
        model_settings_path=directory / "model-settings.json",
        model_provider="ollama",
        model_name="batch28-deterministic-fixture",
        model_base_url="http://127.0.0.1:1/v1/",
        model_api_key="",
        openai_api_key="",
    )
    app = create_app(settings)
    app.state.agent_model_adapter = FakeModelAdapter(responder=respond)
    uvicorn.run(app, host="127.0.0.1", port=8028, log_level="warning")


class Page(BrowserPage):
    def current(self):
        character_id = self.evaluate(
            "document.querySelector('[data-testid=character-id]')?.textContent"
        )
        return self.http.get(f"http://127.0.0.1:5188/api/characters/{character_id}").json()

    def choose(self, selector, keys):
        self.evaluate(
            """(() => {
          const select = document.querySelector(%s);
          for (const option of select.options) option.selected = %s.includes(option.value);
          select.dispatchEvent(new Event('change', {bubbles:true}));
        })()"""
            % (json.dumps(selector), json.dumps(keys))
        )

    def capture(self, selector, filename):
        self.evaluate(f"document.querySelector({json.dumps(selector)}).scrollIntoView()")
        result = self.command("Page.captureScreenshot", {"format": "png"})
        (self.directory / filename).write_bytes(base64.b64decode(result["data"]))


class Batch28UI(SmokeCheck):
    def __init__(self, run_name):
        root = (ROOT / "data/prepared/changan/batch-28").resolve()
        self.directory = (root / run_name).resolve()
        assert self.directory.is_relative_to(root) and self.directory != root
        self.directory.mkdir(parents=True, exist_ok=False)
        self.processes, self.logs, self.pages = [], [], []
        self.cdp = None
        self.http = httpx.Client(
            trust_env=False, timeout=15, headers={"Authorization": f"Bearer {TEST_HOST}"}
        )
        self.frontend_url = "http://127.0.0.1:5188"
        self.report = dict(
            model="deterministic fixture",
            external_model_calls=0,
            remote_device_acceptance="not_run",
            network="same host loopback",
            platform=sys.platform,
        )

    def start(self, command, cwd, name):
        log = (self.directory / f"{name}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "COC_BACKEND_PORT": "8028",
            "COC_FRONTEND_PORT": "5188",
            "MODEL_API_KEY": "",
            "OPENAI_API_KEY": "",
        }
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.processes.append((name, proc))
        return proc

    def request(self, method, path, body=None):
        response = self.http.request(method, self.frontend_url + "/api" + path, json=body)
        assert response.is_success, f"{response.status_code}: {response.text}"
        return response.json()

    def run(self):
        for port in (8028, 5188):
            port_free(port)
        self.start(
            [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)],
            ROOT,
            "backend",
        )
        wait_for(lambda: self.http.get("http://127.0.0.1:8028/api/health").status_code == 200)
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
        host.navigate("#/create")
        wait_for(lambda: host.evaluate("!!document.querySelector('#character-name')"))
        host.fill("#character-name", "第28批湿地调查员")
        host.fill("#creation-mode", "point_buy")
        host.click("创建购点草稿")
        wait_for(lambda: host.contains("职业与技能"))
        card = host.current()
        for key, value in dict(
            str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60
        ).items():
            host.fill(f"#attribute-{key}", value)
        host.fill("#occupation", "outdoorsman")
        host.fill("#occupation-attribute", "str")
        # All six selectable categories appear; add three new ones through actual UI.
        for group, name in [("pilot", "飞艇"), ("survival", "湿地"), ("lore", "梦境史")]:
            host.fill("#custom-skill-group", group)
            host.fill("#custom-skill-name", name)
            host.click("添加专业")
        ids = host.evaluate(
            "[...document.querySelectorAll('[id^=interest_skills-custom_]')].map(e=>e.id.replace('interest_skills-',''))"
        )
        assert len(ids) == 3
        pilot, survival, lore = ids
        host.choose("#group-firearms", ["handgun"])
        host.choose("#group-survival", [survival])
        host.fill("#occupation_skills-credit_rating", 9)
        host.fill("#occupation_skills-" + survival, 50)
        host.fill("#interest_skills-" + pilot, 30)
        host.fill("#interest_skills-" + lore, 20)
        host.save_draft(card["version"])
        card = host.current()
        assert {i["code"] for i in card["validation"]["issues"]} == {"keeper_approval"}
        host.evaluate(
            "[...document.querySelectorAll('[aria-label^=KP允许]')].forEach(e=>e.click())"
        )
        host.save_draft(card["version"])
        card = host.current()
        assert card["validation"]["valid"], card["validation"]
        assert [card["skill_values"][k] for k in ids] == [31, 60, 21]
        host.capture("#custom-skill-name", "created-specialties.png")
        host.click("最终确认")
        wait_for(lambda: host.contains("角色已最终确认。"))
        host.click("导出 JSON")
        file = host.directory / f"character-{card['id']}.json"
        wait_for(file.exists)
        document = json.loads(file.read_text(encoding="utf-8"))
        assert document["character"]["status"] == "finalized"
        self.report["character_created_confirmed_exported"] = True
        self.report["custom_ids"] = ids
        host.navigate("#/rooms")
        wait_for(lambda: host.contains("创建房间"))
        host.fill("#room-name", "第28批专业链路确定性验证")
        host.click("创建房间")
        wait_for(host.connected)
        rid = host.evaluate("location.hash.split('/').at(-1)")
        prefix = f"/rooms/{rid}"
        player = Page(self, "player")
        self.pages.append(player)
        player.fill("#join-invite", host.text_at('[data-testid="invite-code"]'))
        player.fill("#join-name", "另一玩家")
        player.click("加入房间")
        wait_for(player.connected)
        assert player.evaluate("sessionStorage.getItem('coc.host')") is None
        root = player.command("DOM.getDocument")["root"]["nodeId"]
        node = player.command(
            "DOM.querySelector",
            {"nodeId": root, "selector": '[data-testid="character-submissions"] input[type=file]'},
        )["nodeId"]
        player.command("DOM.setFileInputFiles", {"nodeId": node, "files": [str(file)]})
        wait_for(lambda: player.contains("服务端已重算"))
        for label in ["驾驶（飞艇）：31", "生存（湿地）：60", "学问（梦境史）：21"]:
            assert player.contains(label)
        assert player.contains("待本房间 KP 引入")
        player.capture('[data-testid="character-submissions"]', "preview.png")
        player.click("提交角色")
        wait_for(lambda: host.contains("预览 · 另一玩家"))
        host.click("预览 · 另一玩家")
        wait_for(lambda: host.contains("接受并分配同时核准"))
        host.capture('[data-testid="character-submissions"]', "pending.png")
        host.click("接受并分配")
        wait_for(lambda: player.contains("已接受"))
        accepted = self.request("GET", prefix)
        slot = accepted["character_slots"][0]
        snapshot = slot["character_snapshot"]
        assert snapshot["custom_specializations"] == card["custom_specializations"]
        assert snapshot["skill_values"] == card["skill_values"]
        runtime = accepted["session_state"]["characters"][slot["id"]]
        for stat in ["hp", "mp", "san", "luck"]:
            assert runtime[stat] == snapshot["derived_values"][stat]
        assert [r["dice"] for r in snapshot["roll_records"]] == [
            r["dice"] for r in card["roll_records"]
        ]
        self.report["room_submission_accepted_assigned"] = True
        self.report["runtime"] = runtime
        self.report["room_id"] = rid
        self.request("POST", prefix + "/module", {"module_id": "stopped-clock"})
        profile = self.request(
            "POST", "/agent-profiles", {"role": "keeper", "name": "确定性KP夹具"}
        )
        self.request(
            "POST",
            prefix + "/agent-bindings",
            {"member_id": accepted["host_member_id"], "profile_id": profile["id"]},
        )
        player.click("准备 · 另一玩家")
        wait_for(lambda: player.contains("取消准备 · 另一玩家"))
        host.click("开始游戏")
        wait_for(lambda: player.text_at('[data-testid="room-status"]') == "运行中")
        wait_for(lambda: player.evaluate("!!document.querySelector('#agent-action')"))
        player.fill(
            "#agent-action", "我冒着误判地面而陷入泥潭的危险辨认湿地落脚点，进行生存湿地检定。"
        )
        player.click("发送")
        wait_for(lambda: bool(self.request("GET", prefix + "/checks")), 45)
        check = self.request("GET", prefix + "/checks")[0]
        assert check["value"] == 60 and check["display_name"] == "生存（湿地）"
        wait_for(lambda: player.contains("掷骰查看原结果"))
        wait_for(
            lambda: self.request("GET", prefix + "/agent-cycle")["status"] == "waiting_for_roll", 45
        )
        player.click("掷骰查看原结果")
        wait_for(lambda: self.request("GET", prefix + "/checks")[0].get("dice"), 30)
        check = self.request("GET", prefix + "/checks")[0]
        if (
            check["status"] == "pending"
            and (check.get("settlement") or {}).get("stage") == "choice"
        ):
            player.click("接受原结果")
        wait_for(lambda: self.request("GET", prefix + "/checks")[0]["status"] == "resolved", 30)
        wait_for(lambda: self.request("GET", prefix + "/agent-cycle")["status"] == "completed", 45)
        context = json.loads((self.directory / "keeper-context.json").read_text(encoding="utf-8"))
        assert context["actor"]["custom_skill_names"][survival] == "生存（湿地）"
        assert context["actor"]["skill_values"][survival] == 60
        player.screenshot("resolved-check.png")
        self.report["check"] = self.request("GET", prefix + "/checks")[0]
        self.report["fixture_calls"] = len(
            (self.directory / "fixture-calls.jsonl").read_text().splitlines()
        )
        self.report["ordinary_check_service_chain"] = "passed (deterministic model fixture)"
        self.report["browser"] = host.command("Browser.getVersion")["product"]
        for page in self.pages:
            assert not page.exceptions, page.exceptions
        self.report["status"] = "passed"

    def close(self):
        for page in self.pages:
            if page.cdp:
                page.cdp.close()
        super().close()
        for port in (8028, 5188):
            port_free(port)


if __name__ == "__main__":
    if sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        check = Batch28UI(sys.argv[1])
        try:
            check.run()
        except Exception:
            check.report["status"] = "failed"
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
