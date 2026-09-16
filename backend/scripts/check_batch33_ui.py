"""Real Chrome: mythos package creation, local consent, export, room consent and check.

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
        raise AssertionError("Batch 33 must not contact an external HTTP service")

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
            assert (
                actor["skill_values"]["cthulhu_mythos"]
                == actor["initial_mythos_sources"][0]["value"]
            )
            assert actor["known_spells"][0]["casting_status"] == "unsupported"
            assert actor["experience_effects"]["approved"]
            assert 35 <= actor["runtime"]["san"] <= 44
            assert actor["runtime"]["san_max"] == 99 - actor["skill_values"]["cthulhu_mythos"]
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
        model_name="batch33-deterministic-fixture",
        model_base_url="http://127.0.0.1:1/v1/",
        model_api_key="",
        openai_api_key="",
    )
    app = create_app(settings)
    app.state.agent_model_adapter = FakeModelAdapter(responder=respond)
    uvicorn.run(app, host="127.0.0.1", port=8028, log_level="warning")


class Batch33UI(Batch28UI):
    def __init__(self, run_name):
        root = (ROOT / "data/prepared/changan/batch-33").resolve()
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
        host.fill("#character-name", "第33批战场老兵")
        host.fill("#creation-mode", "point_buy")
        host.click("创建购点草稿")
        wait_for(lambda: host.contains("职业与技能"))
        card = host.current()
        for key, value in dict(
            str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60
        ).items():
            host.fill(f"#attribute-{key}", value)
        host.fill("#occupation", "doctor")
        host.choose("#group-academic", ["history", "law"])
        host.fill("#occupation_skills-credit_rating", 30)
        host.fill("#experience-package", "mythos")
        host.fill("#experience-history", "与KP讨论的古籍研究经历，相信书中记载")
        host.fill("#mythos-method", "suggested_roll")
        host.fill("#mythos-background-0", "研究经历留下的旧疤痕")
        host.fill("#mythos-background-1", "梦中出现的怪异存在")
        host.evaluate("document.querySelector('#mythos-add-spell').click()")
        host.fill("#mythos-spell-name-0", "旧印开光术")
        host.fill(
            "#mythos-spell-source-0", "规则书1907 PDF219–220／印刷218–219；KP许可的初始已知记录"
        )
        host.save_draft(card["version"])
        card = host.current()
        assert {i["code"] for i in card["validation"]["issues"]} == {"keeper_approval"}
        assert 35 <= card["derived_values"]["san"] <= 44
        assert 6 <= card["skill_values"]["cthulhu_mythos"] <= 15
        assert not card["known_spells"]
        self.report["creation_rolls"] = card["experience_rolls"]
        assert host.evaluate("document.querySelector('#finalize-character').disabled")
        host.capture('[data-testid="experience-packages"]', "pending-creation.png")
        host.evaluate("document.querySelector('#approve-experience').click()")
        host.save_draft(card["version"])
        card = host.current()
        assert card["validation"]["valid"] and card["experience_approvals"]
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
        host.fill("#room-name", "第33批经历包确定性验证")
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
        assert player.contains("神话经历包") and player.contains("旧印开光术")
        assert player.contains("尚不支持施放")
        player.capture('[data-testid="submission-preview"]', "player-preview.png")
        player.click("提交角色")
        wait_for(lambda: host.contains("预览 · 另一玩家"))
        host.click("预览 · 另一玩家")
        wait_for(lambda: host.evaluate("!!document.querySelector('#review-approve-experience')"))
        assert host.evaluate(
            "[...document.querySelectorAll('button')].find(e=>e.textContent==='接受并分配').disabled"
        )
        host.capture('[data-testid="experience-summary"]', "host-consent.png")
        host.evaluate("document.querySelector('#review-approve-experience').click()")
        host.click("接受并分配")
        wait_for(lambda: player.contains("已接受"))
        accepted = self.request("GET", prefix)
        slot = accepted["character_slots"][0]
        snapshot = slot["character_snapshot"]
        assert snapshot["skill_values"] == card["skill_values"]
        assert snapshot["experience"]["package"] == "mythos" and snapshot["experience_approvals"]
        assert (
            snapshot["experience_rolls"]["mythos"]["dice"]
            == card["experience_rolls"]["mythos"]["dice"]
        )
        runtime = accepted["session_state"]["characters"][slot["id"]]
        assert (runtime["san"], runtime["san_max"], runtime["sanity"]["mythos_gain"]) == (
            card["derived_values"]["san"],
            99 - card["initial_mythos"],
            0,
        )
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
            "#agent-action", "我冒着误判而错失机会的风险分析现场神话知识，请进行克苏鲁神话检定。"
        )
        player.click("发送")
        wait_for(lambda: bool(self.request("GET", prefix + "/checks")), 45)
        check = self.request("GET", prefix + "/checks")[0]
        assert check["value"] == card["initial_mythos"] and check["display_name"] == "克苏鲁神话"
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
        assert snapshot["known_spells"][0]["name"] == "旧印开光术"
        assert runtime["sanity"]["belief"] == "believer"
        self.request("POST", prefix + "/pause")
        checkpoint = self.request("POST", prefix + "/snapshots", {"name": "第33批状态与已知法术"})[
            "snapshot"
        ]
        for _ in range(2):
            self.request("POST", prefix + f"/snapshots/{checkpoint['id']}/load")
            restored = self.request("GET", prefix)
            assert (
                restored["character_slots"][0]["character_snapshot"]["known_spells"]
                == snapshot["known_spells"]
            )
            assert (
                restored["session_state"]["characters"][slot["id"]]["sanity"] == runtime["sanity"]
            )
        self.report["restored_known_spells"] = snapshot["known_spells"]
        self.report["snapshot_id"] = checkpoint["id"]
        self.report["status"] = "passed"


if __name__ == "__main__":
    if sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        check = Batch33UI(sys.argv[1])
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
