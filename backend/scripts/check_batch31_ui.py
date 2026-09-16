"""Real Chrome short flow with isolated HTTP/WS/SQLite and deterministic KP.

Card creation/setup uses normal HTTP; acceptance, restrictions and healthy action
are exercised in the browser. No environment file or external model transport.
"""

import json
import shutil
import sys
from pathlib import Path
from uuid import uuid4

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
        raise AssertionError("Batch31 forbids external HTTP/model calls")

    httpx.HTTPTransport.handle_request = reject
    httpx.AsyncHTTPTransport.handle_async_request = reject

    def respond(messages, kwargs):
        result = modern_response(messages, kwargs)
        with (directory / "fixture-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"schema": kwargs["response_schema"].__name__}) + "\n")
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
        model_name="batch31-deterministic-fixture",
        model_base_url="http://127.0.0.1:1/v1/",
        model_api_key="",
        openai_api_key="",
    )
    app = create_app(settings)
    app.state.agent_model_adapter = FakeModelAdapter(responder=respond)
    uvicorn.run(app, host="127.0.0.1", port=8028, log_level="warning")


class Batch31UI(Batch28UI):
    def __init__(self, run_name):
        root = (ROOT / "data/prepared/changan/batch-31").resolve()
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
            external_model_calls=0,
            model="deterministic fixture",
            network="same host loopback",
            remote_device_acceptance="not_run",
        )

    def card(self, name, mythos):
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
        path = f"/characters/{card['id']}"
        card = self.request(
            "PATCH",
            path,
            {
                "version": card["version"],
                "occupation": "occultist",
                "occupation_skills": {"credit_rating": {"points": 20}},
                "occupation_group_choices": {
                    "social": ["persuade"],
                    "language": ["language_latin"],
                    "personal": ["cthulhu_mythos"],
                },
                "initial_mythos_proposal": {
                    "source": "occultist",
                    "value": mythos,
                    "reason": "隔离SAN初始化验收",
                },
                "approve_occupation_exceptions": ["initial_mythos"],
            },
        )
        self.request("POST", path + "/finalize", {"version": card["version"]})
        return self.request("GET", path + "/export")

    @staticmethod
    def disabled(page, label):
        return page.evaluate(
            "[...document.querySelectorAll('button')].find(e=>e.textContent==="
            + json.dumps(label)
            + ")?.disabled"
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
        host.fill("#room-name", "第31批SAN0与自主资格")
        host.click("创建房间")
        wait_for(host.connected)
        rid = host.evaluate("location.hash.split('/').at(-1)")
        prefix = f"/rooms/{rid}"
        self.report["room_id"] = rid
        player = Page(self, "player")
        self.pages.append(player)
        player.fill("#join-invite", host.text_at('[data-testid="invite-code"]'))
        player.fill("#join-name", "SAN0玩家")
        player.click("加入房间")
        wait_for(player.connected)
        document = self.card("SAN0神秘学家", 99)
        file = self.directory / "zero-character.json"
        file.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        root = player.command("DOM.getDocument")["root"]["nodeId"]
        node = player.command(
            "DOM.querySelector",
            {"nodeId": root, "selector": '[data-testid="character-submissions"] input[type=file]'},
        )["nodeId"]
        player.command("DOM.setFileInputFiles", {"nodeId": node, "files": [str(file)]})
        wait_for(lambda: player.contains("初始 SAN 为 0"))
        assert player.contains("永久疯狂")
        player.capture('[data-testid="submission-preview"]', "zero-preview.png")
        player.click("提交角色")
        wait_for(lambda: host.contains("预览 · SAN0玩家"))
        host.click("预览 · SAN0玩家")
        wait_for(lambda: host.contains("初始 SAN 为 0"))
        host.evaluate("document.querySelector('#review-approve-initial_mythos').click()")
        host.click("接受并分配")
        wait_for(lambda: player.contains("已接受"))
        room = self.request("GET", prefix)
        zero = room["character_slots"][0]
        current = room["session_state"]["characters"][zero["id"]]
        assert (current["san"], current["sanity"]["kind"]) == (0, "permanent")
        self.report["zero_runtime"] = current
        healthy = self.card("正常调查员", 7)["character"]
        member = self.request(
            "POST", prefix + "/members", {"display_name": "正常调查员", "controller_type": "human"}
        )["room"]["members"][-1]
        room = self.request("POST", prefix + "/character-slots", {"character_id": healthy["id"]})[
            "room"
        ]
        slot = next(
            s for s in room["character_slots"] if s["character_snapshot"]["id"] == healthy["id"]
        )
        self.request(
            "POST",
            prefix + "/character-assignments",
            {"slot_id": slot["id"], "member_id": member["id"]},
        )
        self.request("POST", prefix + "/module", {"module_id": "stopped-clock"})
        kp = self.request("POST", "/agent-profiles", {"role": "keeper", "name": "隔离KP"})
        self.request(
            "POST",
            prefix + "/agent-bindings",
            {"member_id": room["host_member_id"], "profile_id": kp["id"]},
        )
        self.request("POST", prefix + "/ready", {"member_id": member["id"], "ready": True})
        player.click("准备 · SAN0玩家")
        wait_for(lambda: player.contains("取消准备 · SAN0玩家"))
        host.click("开始游戏")
        wait_for(lambda: player.text_at('[data-testid="room-status"]') == "运行中")
        for mid in (zero["member_id"], member["id"]):
            room = self.request("GET", prefix)
            self.request(
                "POST",
                prefix + "/combat/setup",
                {
                    "expected_revision": room["revision"],
                    "member_id": mid,
                    "reason": "隔离战斗面板验收",
                    "stats_public": True,
                    "team": "opposition" if mid == zero["member_id"] else "investigators",
                },
            )
        self.request(
            "POST",
            prefix + "/combat/damage",
            {
                "target_id": member["id"],
                "amount": 2,
                "reason": "隔离测试：提供合法急救目标",
                "client_request_id": str(uuid4()),
            },
        )
        wait_for(lambda: player.contains("SAN 为 0：永久疯狂，不能自主行动"))
        player.fill("#agent-action", "我自行检查公告")
        assert self.disabled(player, "发送")
        player.capture(".runtime-cards", "zero-runtime.png")
        # The panel's autonomous treatment path was another direct combat API bypass.
        player.evaluate(
            "[...document.querySelectorAll('summary')].find(e=>e.textContent==='治疗伤势').click()"
        )
        player.fill('[aria-label="治疗行动者"]', zero["member_id"])
        player.fill('[aria-label="治疗对象"]', member["id"])
        assert self.disabled(player, "急救") and self.disabled(player, "医学治疗")
        player.capture(".combat-panel", "zero-combat-disabled.png")
        before = self.request("GET", prefix)
        denied = self.http.post(
            self.frontend_url + "/api" + prefix + "/combat/action",
            json={
                "actor_id": zero["member_id"],
                "target_id": member["id"],
                "operation": "attack",
                "weapon_id": "unarmed",
                "reason": "直接请求绕过尝试",
                "turn_key": 0,
                "client_request_id": str(uuid4()),
            },
        )
        # Remote player's browser fetch carries its own member identity.
        request_body = {
            "actor_id": zero["member_id"],
            "target_id": member["id"],
            "operation": "attack",
            "weapon_id": "unarmed",
            "turn_key": 0,
            "reason": "浏览器直接请求绕过尝试",
            "client_request_id": str(uuid4()),
        }
        result = player.evaluate(
            """(async () => {
          const response = await fetch(%s, {method:'POST', headers: {
            'Content-Type':'application/json',
            Authorization:'Bearer ' + localStorage.getItem(%s)
          }, body: JSON.stringify(%s)});
          return {status:response.status, body:await response.json()};
        })()"""
            % (
                json.dumps("/api" + prefix + "/combat/action"),
                json.dumps("coc.room." + rid),
                json.dumps(request_body),
            )
        )
        assert result["status"] == 409 and "自主" in result["body"]["detail"]["message"]
        self.report["player_direct_combat_rejection"] = result
        self.report["host_cannot_impersonate_remote_http"] = denied.status_code
        assert denied.status_code == 403
        assert self.request("GET", prefix)["revision"] == before["revision"]
        # A normal investigator can still take an ordinary action in the real UI.
        host.fill("#agent-action-actor", member["id"])
        host.fill("#agent-action", "我冒着误判风险仔细检查现场，请进行侦查检定。")
        wait_for(lambda: self.disabled(host, "发送") is False)
        host.click("发送")
        wait_for(lambda: bool(self.request("GET", prefix + "/checks")), 45)
        wait_for(
            lambda: self.request("GET", prefix + "/agent-cycle")["status"] == "waiting_for_roll", 45
        )
        host.click("掷骰查看原结果")
        wait_for(lambda: self.request("GET", prefix + "/checks")[0].get("dice"), 30)
        check = self.request("GET", prefix + "/checks")[0]
        if (
            check["status"] == "pending"
            and (check.get("settlement") or {}).get("stage") == "choice"
        ):
            host.click("接受原结果")
        wait_for(lambda: self.request("GET", prefix + "/agent-cycle")["status"] == "completed", 45)
        host.screenshot("healthy-action.png")
        self.report["healthy_check"] = self.request("GET", prefix + "/checks")[0]
        # An active attack can still target the restricted investigator. They
        # cannot choose a fresh defense; only the bounded host continuation is shown.
        room = self.request("GET", prefix)
        self.request(
            "POST",
            prefix + "/combat/action",
            {
                "actor_id": member["id"],
                "target_id": zero["member_id"],
                "operation": "attack",
                "weapon_id": "unarmed",
                "reason": "隔离测试：受限角色作为伤害目标",
                "turn_key": room["combat"]["turn_key"],
                "client_request_id": str(uuid4()),
            },
        )
        wait_for(lambda: self.request("GET", prefix)["combat"]["pending"] is not None)
        wait_for(lambda: player.contains("主机可处理此等待阶段"))
        assert self.disabled(player, "闪避") is None and self.disabled(player, "反击") is None
        player.capture(".combat-panel", "zero-active-combat.png")
        host.fill('[aria-label="受限阶段处理原因"]', "SAN0角色不能自行防御，继续原攻击结算")
        host.click("确认受限阶段处理")
        wait_for(lambda: self.request("GET", prefix)["combat"]["pending"]["stage"] == "attack_roll")
        host.click("确认掷骰")
        wait_for(lambda: self.request("GET", prefix + "/agent-cycle")["status"] == "completed", 45)
        assert self.request("GET", prefix)["combat"]["pending"] is None
        self.report["combat_result"] = [
            e["payload"]
            for e in self.request("GET", prefix + "/events")["events"]
            if e["type"] == "combat.resolved"
        ][-1]
        host.capture(".combat-panel", "combat-settled.png")
        self.report["browser"] = host.command("Browser.getVersion")["product"]
        self.report["fixture_calls"] = len(
            (self.directory / "fixture-calls.jsonl").read_text().splitlines()
        )
        self.report["status"] = "passed"
        for page in self.pages:
            assert not page.exceptions, page.exceptions


if __name__ == "__main__":
    if sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        check = Batch31UI(sys.argv[1])
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
