"""Real Chrome: occultist creation, local consent, export, room consent and check.

Uses two isolated browser identities, new SQLite/checkpoints, deterministic model
responses, real unseeded server dice, and blocks all external model HTTP.
"""

import json
import shutil
import sys
from pathlib import Path

import httpx
from check_batch28_ui import Batch28UI, Page
from check_character_creation import BACKEND, ROOT, TEST_HOST, port_free, wait_for


def serve(directory):
    import uvicorn

    sys.path.insert(0, str(BACKEND / "tests"))
    from test_action_adjudication import modern_response

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app

    def reject(*_, **__):
        raise AssertionError("Batch 30 must not contact an external HTTP service")

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
            assert actor["skill_values"]["cthulhu_mythos"] == 15
            assert (actor["runtime"]["san"], actor["runtime"]["san_max"]) == (84, 84)
            (directory / "keeper-context.json").write_text(
                json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["proposed_check"]["name"] = "cthulhu_mythos"
        with (directory / "fixture-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    dict(schema=kwargs["response_schema"].__name__, kind="deterministic fixture")
                )
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
        model_name="batch30-deterministic-fixture",
        model_base_url="http://127.0.0.1:1/v1/",
        model_api_key="",
        openai_api_key="",
    )
    app = create_app(settings)
    app.state.agent_model_adapter = FakeModelAdapter(responder=respond)
    uvicorn.run(app, host="127.0.0.1", port=8028, log_level="warning")


class Batch30UI(Batch28UI):
    def __init__(self, run_name):
        root = (ROOT / "data/prepared/changan/batch-30").resolve()
        self.directory = (root / run_name).resolve()
        assert self.directory.is_relative_to(root) and self.directory != root
        self.directory.mkdir(parents=True, exist_ok=False)
        self.processes, self.logs, self.pages = [], [], []
        self.cdp = None
        self.http = httpx.Client(
            trust_env=False, timeout=20, headers={"Authorization": f"Bearer {TEST_HOST}"}
        )
        self.frontend_url = "http://127.0.0.1:5188"
        self.report = dict(
            model="deterministic fixture",
            external_model_calls=0,
            network="same host loopback",
            remote_device_acceptance="not_run",
        )

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
        host.fill("#character-name", "第30批神秘学家")
        host.fill("#creation-mode", "point_buy")
        host.click("创建购点草稿")
        wait_for(lambda: host.contains("职业与技能"))
        card = host.current()
        for key, value in dict(
            str=20, con=60, siz=60, dex=60, app=50, int=60, pow=90, edu=60
        ).items():
            host.fill(f"#attribute-{key}", value)
        host.fill("#occupation", "occultist")
        host.choose("#group-social", ["persuade"])
        host.choose("#group-language", ["language_latin"])
        host.evaluate("document.querySelector('#initial-mythos-enabled').click()")
        host.fill("#initial-mythos-value", 15)
        host.fill("#initial-mythos-reason", "KP确认角色从既往神秘研究获得的初始知识")
        host.fill("#occupation_skills-credit_rating", 20)
        assert host.evaluate("document.querySelector('#occupation_skills-cthulhu_mythos').disabled")
        assert host.evaluate("document.querySelector('#interest_skills-cthulhu_mythos').disabled")
        host.save_draft(card["version"])
        card = host.current()
        assert {i["code"] for i in card["validation"]["issues"]} == {"keeper_approval"}
        assert (
            card["initial_mythos"],
            card["derived_values"]["san"],
            card["derived_values"]["san_max"],
        ) == (15, 84, 84)
        assert host.evaluate("document.querySelector('#finalize-character').disabled")
        host.capture('[data-testid="occupation-exceptions"]', "pending-creation.png")
        host.evaluate("document.querySelector('#approve-initial_mythos').click()")
        host.save_draft(card["version"])
        card = host.current()
        assert card["validation"]["valid"] and card["occupation_exception_approvals"]
        host.click("最终确认")
        wait_for(lambda: host.contains("角色已最终确认。"))
        host.click("导出 JSON")
        file = host.directory / f"character-{card['id']}.json"
        wait_for(file.exists)
        document = json.loads(file.read_text(encoding="utf-8"))
        assert document["character"]["status"] == "finalized"
        self.report["character_id"] = card["id"]
        host.navigate("#/rooms")
        wait_for(lambda: host.contains("创建房间"))
        host.fill("#room-name", "第30批职业特例确定性验证")
        host.click("创建房间")
        wait_for(host.connected)
        rid = host.evaluate("location.hash.split('/').at(-1)")
        prefix = f"/rooms/{rid}"
        self.report["room_id"] = rid
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
        assert player.contains("候选效果，待 KP 明确核准")
        assert player.contains("初始克苏鲁神话 15") and player.contains("90 → 84")
        player.capture('[data-testid="submission-preview"]', "player-preview.png")
        player.click("提交角色")
        wait_for(lambda: host.contains("预览 · 另一玩家"))
        host.click("预览 · 另一玩家")
        wait_for(
            lambda: host.evaluate("!!document.querySelector('#review-approve-initial_mythos')")
        )
        assert host.evaluate(
            "[...document.querySelectorAll('button')].find(e=>e.textContent==='接受并分配').disabled"
        )
        host.capture('[data-testid="occupation-exception-summary"]', "host-consent.png")
        host.evaluate("document.querySelector('#review-approve-initial_mythos').click()")
        host.click("接受并分配")
        wait_for(lambda: player.contains("已接受"))
        accepted = self.request("GET", prefix)
        slot = accepted["character_slots"][0]
        snapshot = slot["character_snapshot"]
        assert snapshot["skill_values"] == card["skill_values"]
        assert snapshot["initial_mythos"] == 15 and snapshot["occupation_exception_approvals"]
        runtime = accepted["session_state"]["characters"][slot["id"]]
        assert (runtime["san"], runtime["san_max"], runtime["sanity"]["mythos_gain"]) == (84, 84, 0)
        assert [r["dice"] for r in snapshot["roll_records"]] == [
            r["dice"] for r in card["roll_records"]
        ]
        self.report["initial_runtime"] = runtime
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
            "#agent-action", "我冒着误判而错失机会的风险辨认现场的怪异线索，请进行克苏鲁神话检定。"
        )
        player.click("发送")
        wait_for(lambda: bool(self.request("GET", prefix + "/checks")), 45)
        check = self.request("GET", prefix + "/checks")[0]
        assert check["value"] == 15 and check["display_name"] == "克苏鲁神话"
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
        player.screenshot("mythos-check.png")
        self.report["check"] = self.request("GET", prefix + "/checks")[0]
        self.report["fixture_calls"] = len(
            (self.directory / "fixture-calls.jsonl").read_text().splitlines()
        )
        self.report["browser"] = host.command("Browser.getVersion")["product"]
        for page in self.pages:
            assert not page.exceptions, page.exceptions
        self.report["status"] = "passed"


if __name__ == "__main__":
    if sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        check = Batch30UI(sys.argv[1])
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
