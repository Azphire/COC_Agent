"""Two real Chrome identities: card -> file -> submission -> equipment combat.

Isolated database and deterministic model only; real server dice, no success retry.
Run from the repo root with a fresh run name. Uses ports 8028 and 5188.
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

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app

    def reject(*_, **__):
        raise AssertionError("Batch 29 must not contact an external HTTP service")

    httpx.HTTPTransport.handle_request = reject
    httpx.AsyncHTTPTransport.handle_async_request = reject

    def respond(messages, kwargs):
        schema = kwargs["response_schema"].__name__
        context = json.loads(messages[-1]["content"])
        if schema.endswith("CombatDecision"):
            own = context["own"]
            if context["automatic"]:
                result = dict(
                    operation="pass",
                    reason="原创训练对象等待",
                    target_id=None,
                    weapon_id=None,
                    range_band="base",
                )
            else:
                weapon = next(w for w in own["weapons"] if w.get("template_id") == "rifle_22")
                assert weapon["skill"] == "firearms_rifle_shotgun"
                assert own["skills"][weapon["skill"]] == 65
                assert weapon["ammo"] == 3 and weapon["reserve"] == 8
                assert weapon["id"] != weapon["template_id"]
                target = next(
                    k
                    for k, v in context["combat"]["participants"].items()
                    if v["label"] == "训练目标"
                )
                result = dict(
                    operation="attack",
                    reason="用实际持有步枪单发射击训练目标",
                    target_id=target,
                    weapon_id=weapon["id"],
                    range_band="long",
                )
                (directory / "combat-context.json").write_text(
                    json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        elif schema == "CombatNarration":
            result = dict(text="本次训练行动已按战斗结果结算。")
        else:
            raise AssertionError(f"Unexpected model schema: {schema}")
        with (directory / "fixture-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(schema=schema, kind="deterministic fixture")) + "\n")
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
        model_name="batch29-deterministic-fixture",
        model_base_url="http://127.0.0.1:1/v1/",
        model_api_key="",
        openai_api_key="",
    )
    app = create_app(settings)
    app.state.agent_model_adapter = FakeModelAdapter(responder=respond)
    uvicorn.run(app, host="127.0.0.1", port=8028, log_level="warning")


class Batch29UI(Batch28UI):
    def __init__(self, run_name):
        root = (ROOT / "data/prepared/changan/batch-29").resolve()
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
            network="same host loopback",
            platform=sys.platform,
            remote_device_acceptance="not_run",
            dice="actual server dice, one shot",
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
        host.fill("#character-name", "第29批步枪调查员")
        host.fill("#creation-mode", "point_buy")
        host.click("创建购点草稿")
        wait_for(lambda: host.contains("职业与技能"))
        card = host.current()
        for key, value in dict(
            str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60
        ).items():
            host.fill(f"#attribute-{key}", value)
        host.fill("#occupation", "firefighter")
        host.fill("#occupation-attribute", "str")
        host.fill("#occupation_skills-credit_rating", 9)
        host.evaluate("document.querySelector('#specialization-firearms_rifle_shotgun').click()")
        host.fill("#interest_skills-firearms_rifle_shotgun", 40)
        host.fill('[aria-label="装备目录"]', "rifle_22")
        host.click("添加装备")
        host.fill('[aria-label="初始已装弹1"]', 3)
        host.fill('[aria-label="初始备弹1"]', 8)
        host.save_draft(card["version"])
        card = host.current()
        assert card["validation"]["valid"], card["validation"]
        assert card["equipment"][0]["initial_ammo"] == 3
        host.capture('[aria-label="装备目录"]', "created-equipment.png")
        host.click("最终确认")
        wait_for(lambda: host.contains("角色已最终确认。"))
        host.click("导出 JSON")
        file = host.directory / f"character-{card['id']}.json"
        wait_for(file.exists)
        document = json.loads(file.read_text(encoding="utf-8"))
        assert document["character"]["status"] == "finalized"
        assert document["character"]["equipment"] == card["equipment"]
        host.navigate("#/rooms")
        wait_for(lambda: host.contains("创建房间"))
        host.fill("#room-name", "第29批装备完整链路")
        host.click("创建房间")
        wait_for(host.connected)
        rid = host.evaluate("location.hash.split('/').at(-1)")
        prefix = f"/rooms/{rid}"
        player = Page(self, "player")
        self.pages.append(player)
        player.fill("#join-invite", host.text_at('[data-testid="invite-code"]'))
        player.fill("#join-name", "装备玩家")
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
        assert player.contains("初始已装弹 3／备弹 8（每件）")
        player.capture('[data-testid="submission-preview"]', "preview.png")
        player.click("提交角色")
        wait_for(lambda: host.contains("预览 · 装备玩家"))
        host.click("预览 · 装备玩家")
        wait_for(lambda: host.contains("接受并分配"))
        host.click("接受并分配")
        wait_for(lambda: player.contains("已接受"))
        accepted = self.request("GET", prefix)
        slot = accepted["character_slots"][0]
        assert slot["character_snapshot"]["equipment"] == card["equipment"]
        self.request("POST", prefix + "/module", {"module_id": "stopped-clock"})
        profile = self.request("POST", "/agent-profiles", {"role": "keeper", "name": "确定性KP"})
        self.request(
            "POST",
            prefix + "/agent-bindings",
            {"member_id": accepted["host_member_id"], "profile_id": profile["id"]},
        )
        player.click("准备 · 装备玩家")
        host.click("开始游戏")
        wait_for(lambda: player.text_at('[data-testid="room-status"]') == "运行中")
        room = self.request("GET", prefix)
        gun = next(
            w
            for w in room["session_state"]["characters"][slot["id"]]["weapons"]
            if w.get("template_id") == "rifle_22"
        )
        assert (gun["ammo"], gun["reserve"]) == (3, 8)
        assert any(i["instance_id"] == gun["id"] for i in room["inventory"])
        # Real host form uses the same server catalogue and a specific sword skill.
        host.evaluate(
            "[...document.querySelectorAll('summary')].find(e=>e.textContent==='主机：准备基础战斗数据').click()"
        )
        host.fill(".combat-panel form input:not([type])", "训练目标")
        for index, value in [(0, 100), (1, 1), (2, 50), (3, 0), (4, 0)]:
            host.evaluate(
                f"document.querySelectorAll('.combat-panel form input[type=number]')[{index}]"
                f".id='npc-field-{index}'"
            )
            host.fill(f"#npc-field-{index}", value)
        host.fill('[aria-label="准备武器"]', "sword_medium")
        host.fill('[aria-label="NPC武器技能"]', 20)
        host.fill('.combat-panel form input[maxlength="300"]', "第29批原创隔离训练资料")
        host.capture(".combat-panel form", "npc-preparation.png")
        host.click("保存战斗资料")
        wait_for(
            lambda: any(
                p["label"] == "训练目标"
                for p in self.request("GET", prefix)["combat"]["participants"].values()
            )
        )
        npc = next(
            p
            for p in self.request("GET", prefix)["combat"]["participants"].values()
            if p["label"] == "训练目标"
        )
        assert any(
            w.get("template_id") == "sword_medium" and w["skill"] == "fighting_sword"
            for w in npc["weapons"]
        )
        player.fill("#agent-action", "我用手里的.22栓式步枪向训练目标开一枪，距离是两倍基础射程。")
        player.click("发送")
        wait_for(
            lambda: (
                (self.request("GET", prefix)["combat"].get("pending") or {}).get("stage")
                == "attack_roll"
            ),
            45,
        )
        player.click("确认掷骰")
        wait_for(lambda: self.request("GET", prefix)["combat"]["pending"] is None, 45)
        wait_for(
            lambda: self.request("GET", prefix)["combat"]["current_actor_id"] == slot["member_id"],
            45,
        )
        wait_for(lambda: self.request("GET", prefix + "/agent-cycle")["status"] == "completed", 45)
        room = self.request("GET", prefix)
        action = next(
            a
            for a in room["session_state"]["combat"]["actions"].values()
            if a["operation"] == "attack"
        )
        assert action["weapon"]["id"] == gun["id"] and action["range_band"] == "long"
        assert action["bases"]["attack"]["value"] == 65
        assert action["bases"]["attack"]["difficulty"] == "hard"
        fired = next(
            w
            for w in room["combat"]["participants"][slot["member_id"]]["weapons"]
            if w["id"] == gun["id"]
        )
        assert (fired["ammo"], fired["reserve"]) == (2, 8)
        player.fill('[aria-label="行动武器"]', gun["id"])
        player.fill('[aria-label="射击距离"]', "long")
        player.capture(".combat-panel", "shot-and-range.png")
        player.click("装填")
        wait_for(
            lambda: any(
                w["id"] == gun["id"] and w["ammo"] == 4 and w["reserve"] == 6
                for w in self.request("GET", prefix)["combat"]["participants"][slot["member_id"]][
                    "weapons"
                ]
            ),
            45,
        )
        wait_for(lambda: self.request("GET", prefix + "/agent-cycle")["status"] == "completed", 45)
        player.capture(".combat-panel", "reloaded.png")
        host.click("确认战斗结束")
        self.report.update(
            status="passed",
            room_id=rid,
            card=card["id"],
            initial_weapon=gun,
            shot=action,
            final_weapon=next(
                w
                for w in self.request("GET", prefix)["session_state"]["characters"][slot["id"]][
                    "weapons"
                ]
                if w["id"] == gun["id"]
            ),
            browser=host.command("Browser.getVersion")["product"],
            fixture_calls=len((self.directory / "fixture-calls.jsonl").read_text().splitlines()),
            chain="UI create/finalize/download/upload/preview/accept/start/attack/roll/reload",
        )
        for page in self.pages:
            assert not page.exceptions, page.exceptions


if __name__ == "__main__":
    if sys.argv[1] == "--serve":
        sys.path.insert(0, str(BACKEND))
        serve(Path(sys.argv[2]))
    else:
        check = Batch29UI(sys.argv[1])
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
