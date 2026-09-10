"""Batch 10: local Ollama, isolated databases, real HTTP/browser, restart while waiting."""

import json
import shutil
import sqlite3
import sys
import time
from base64 import b64decode
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage, MultiplayerCheck
from check_preparation import PreparationCheck


def capture_panel(page, selector, filename):
    page.evaluate(
        f"document.querySelector({json.dumps(selector)}).scrollIntoView({{behavior:'instant',block:'start'}})"
    )
    result = page.command(
        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
    )
    (page.directory / filename).write_bytes(b64decode(result["data"]))


def serve(directory):
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / ".cache/batch-10").resolve()):
        raise ValueError("Batch 10 isolation required")
    settings = Settings(
        host_admin_token=TEST_HOST,
        data_dir=directory / "data",
        database_url=f"sqlite+aiosqlite:///{(directory / 'game.db').as_posix()}",
        knowledge_db_path=directory / "knowledge.db",
        checkpoint_db_path=directory / "checkpoint.db",
    )
    if settings.model_provider != "ollama" or urlparse(settings.model_base_url).hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("Existing local Ollama only")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8000, log_level="warning")


class SanityCheck(MultiplayerCheck):
    @staticmethod
    def stop(process):
        SmokeCheck.stop(process)

    def start(self, command, cwd, name):
        process = super().start(command, cwd, name)
        print(f"STARTED={name}:{process.pid}", flush=True)
        return process

    def __init__(self, original=False, batch=10):
        self.original = original
        SmokeCheck.__init__(self, artifact_prefix=f"batch-{batch}/real")
        self.pages = []
        self.http.timeout = 180
        self.report = {
            "passed": False,
            "host_interventions": [],
            "turns": [],
            "origin": "host_authored_test",
        }
        if original:
            self.report["origin"] = "module_excerpt"
            self.report["host_interventions"].append(
                "局部验收从2号车厢入口开始；主机设定已携光源、先前已见过7号车厢尸体，本次仅结算首次目睹Clicker。不是从开场连续跑到这里。"
            )
        config = json.loads(
            (ROOT / ".cache/batch-6/real-config.json").read_text(encoding="utf-8-sig")
        )
        for source, dest in [
            (config["game_database"], "game.db"),
            (config["knowledge_database"], "knowledge.db"),
        ]:
            with sqlite3.connect(
                (ROOT / source).resolve().as_uri() + "?mode=ro", uri=True
            ) as original:
                with sqlite3.connect(self.directory / dest) as target:
                    original.backup(target)
        for folder in ("rules", "modules/常暗之厢"):
            shutil.copytree(ROOT / "data" / folder, self.directory / "data" / folder)
        module = self.directory / "data/modules/第十批隔离验收"
        module.mkdir(parents=True)
        (module / "测试事件.md").write_text(
            "# 隔离测试室\n这是第十批的人工测试事件，不属于常暗之厢原剧情。"
            "调查员在一间测试室内，桌面有写着日期的登记册，角落有一块盖布。"
            "登记册可直接阅读。主机揭示盖布下的血肉模糊尸体后，目睹者进行 SAN 1/1D4+1。"
            "损失依据：本地第七版1907规则书 PDF131（书页130）的理智损失范例。\n",
            encoding="utf-8",
        )

    def start_backend(self):
        self.backend = self.start(
            [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)],
            BACKEND,
            f"backend-{len(self.processes)}",
        )
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").is_success)

    def wait_cycle(self):
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            cycle = self.request("GET", self.prefix + "/agent-cycle")
            if cycle and cycle["status"] in {
                "completed",
                "failed",
                "waiting_for_roll",
                "waiting_for_review",
            }:
                time.sleep(0.5)
                return cycle
            time.sleep(0.2)
        raise AssertionError("Local encounter exceeded 360 seconds")

    def wait_completed(self):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            cycle = self.request("GET", self.prefix + "/agent-cycle")
            if cycle and cycle["status"] in {"completed", "failed"}:
                return cycle
            time.sleep(0.2)
        raise AssertionError("Local settlement narration exceeded 150 seconds")

    def turn(self, text, target=None):
        start = time.monotonic()
        response = self.player_http.post(
            "http://127.0.0.1:8000/api" + self.prefix + "/actions",
            json={"text": text, "target_entity_id": target, "client_request_id": str(uuid4())},
        )
        assert response.is_success, response.text
        cycle = self.wait_cycle()
        self.report["turns"].append(
            {"text": text, "seconds": round(time.monotonic() - start, 3), "cycle": cycle}
        )
        print(
            "TURN="
            + json.dumps(
                {
                    "text": text,
                    "status": cycle["status"],
                    "seconds": self.report["turns"][-1]["seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return response.json()["event"], cycle

    def run(self):
        import httpx

        from app.rules.sanity import RULE_SOURCE

        for port in (8000, 5173):
            port_free(port)
        self.start_backend()
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
        wait_for(lambda: self.http.get("http://127.0.0.1:5173").is_success)
        status = self.request("GET", "/model/status")
        self.report["model_status"] = status
        self.request("POST", "/knowledge/index", {"kind": "modules"})
        source = next(
            s
            for s in self.request("GET", "/knowledge/sources")
            if s["title"] == ("常暗之厢" if self.original else "第十批隔离验收")
        )
        prep = self.request(
            "POST",
            "/module-preparations",
            {
                **{k: source[k] for k in ("source_id", "source_hash")},
                "display_title": "第十批常暗之厢局部SAN验收"
                if self.original
                else "第十批隔离测试事件",
            },
        )
        entities = {}
        templates = [
            ("scene", "scene", "隔离测试室", "这是人工验收场景。桌面有登记册，角落有盖布。"),
            ("note", "clue", "登记册", "登记册上写着今天的值班日期。"),
            ("corpse", "clue", "盖布下的尸体（隔离测试）", "盖布下是一具血肉模糊的恐怖尸体。"),
        ]
        if self.original:
            templates = [
                (
                    "scene",
                    "scene",
                    "2号车厢入口（局部验收起点）",
                    "你们站在2号车厢入口，前方昏暗，能听到喘息声。",
                ),
                ("note", "clue", "车厢内的声音", "车厢中段有喘息声。"),
                (
                    "corpse",
                    "npc",
                    "Clicker",
                    "照明中可以看到车厢中段的怪物没有眼睛（上半部头颅）。",
                ),
            ]
        for key, kind, title, summary in templates:
            body = {
                "preparation_id": prep["id"],
                "type": kind,
                "title": title,
                "public_summary": summary,
                "tags": [] if self.original else ["host_authored_test"],
                "source_pages": [10] if self.original else [],
                "initial_visibility": "hidden" if key == "corpse" else "revealed",
            }
            if key == "corpse":
                body["sanity_effects"] = [
                    {
                        "id": "corpse-san",
                        "encounter": "目睹血肉模糊的恐怖尸体",
                        "trigger": "action_target",
                        "success_loss": "1",
                        "failure_loss": "1d4+1",
                        "source": RULE_SOURCE,
                        "page": 131,
                        "basis": "§8.1理智损失范例；本遭遇是明确标注的隔离测试事件",
                        "visibility": "public",
                    }
                ]
                if self.original:
                    body["sanity_effects"][0].update(
                        encounter="首次目睹Clicker",
                        failure_loss="1d6",
                        source="module:" + source["source_hash"],
                        page=10,
                        basis="本地常暗之厢Word页10，<2号车厢>：目睹Clicker SAN1/1d6；"
                        "主机已记录本次局部起点前置条件。",
                    )
            entity = self.request("POST", "/module-entities", body)
            self.request("POST", f"/module-entities/{entity['id']}/approve")
            entities[key] = entity
        self.request(
            "PATCH",
            f"/module-preparations/{prep['id']}",
            {"initial_scene_entity_id": entities["scene"]["id"]},
        )
        self.request("POST", f"/module-preparations/{prep['id']}/approve")
        path = f"/module-preparations/{prep['id']}/structure"
        structure = self.request("POST", path + "/build")
        node = next(
            (n for n in structure["nodes"] if n["node_id"] != structure["root_node_id"]),
            structure["nodes"][0],
        )
        if self.original:
            node = next(n for n in structure["nodes"] if "2号车厢" in n["title"])
            for other in structure["nodes"]:
                if other["node_id"] not in {node["node_id"], structure["root_node_id"]}:
                    self.request("PATCH", path + "/nodes/" + other["node_id"], {"included": False})
        self.request(
            "PATCH",
            path + "/nodes/" + node["node_id"],
            {
                "approved_type": "scene",
                "initial_scene": True,
                "public_title": entities["scene"]["title"],
                "public_summary": entities["scene"]["public_summary"],
            },
        )
        for e in entities.values():
            self.request(
                "POST",
                path + "/entity-bindings",
                {
                    "entity_id": e["id"],
                    "node_id": node["node_id"],
                    "source_hash": source["source_hash"],
                },
            )
        self.request("POST", path + "/approve", {})
        created = self.request(
            "POST",
            "/rooms",
            {
                "name": "第十批 常暗之厢SAN局部验收"
                if self.original
                else "第十批 SAN 本机短遭遇（隔离测试）"
            },
        )
        self.prefix = "/rooms/" + created["room"]["id"]
        self.report["room_id"], self.report["preparation_id"] = created["room"]["id"], prep["id"]
        joined = self.request(
            "POST",
            "/rooms/join",
            {"invite_code": created["invite_code"], "display_name": "真人调查员"},
        )
        player_id = joined["room"]["self_member_id"]
        self.player_http = httpx.Client(
            trust_env=False,
            headers={"Authorization": "Bearer " + joined["member_token"]},
            timeout=180,
        )
        card = self.make_character("SAN验收调查员")
        room = self.request("POST", self.prefix + "/character-slots", {"character_id": card["id"]})[
            "room"
        ]
        slot = room["character_slots"][0]["id"]
        self.request(
            "POST",
            self.prefix + "/character-assignments",
            {"slot_id": slot, "member_id": player_id},
        )
        self.player_http.post(
            "http://127.0.0.1:8000/api" + self.prefix + "/ready", json={"ready": True}
        ).raise_for_status()
        self.request("PATCH", self.prefix + "/module-preparation", {"preparation_id": prep["id"]})
        rules = [
            s
            for s in self.request("GET", "/knowledge/sources")
            if s["kind"] == "rulebook" and "1907" in s["title"]
        ]
        self.request(
            "PATCH",
            self.prefix + "/knowledge",
            {
                "rules": [{k: s[k] for k in ("source_id", "source_hash")} for s in rules],
                "module": {k: source[k] for k in ("source_id", "source_hash")},
            },
        )
        kp = self.request(
            "POST",
            "/agent-profiles",
            {"role": "keeper", "name": "本机KP", "background": "按批准的遭遇处理SAN。"},
        )
        self.request(
            "POST",
            self.prefix + "/agent-bindings",
            {"profile_id": kp["id"], "member_id": room["host_member_id"]},
        )
        host = PreparationCheck.host_page(self, "host")
        player = BrowserPage(self, "player")
        self.pages.append(player)
        player.evaluate(
            "localStorage.setItem("
            + json.dumps("coc.room." + room["id"])
            + ","
            + json.dumps(joined["member_token"])
            + ")"
        )
        # Use the same credential format as the room UI's join flow.
        player.navigate("#/rooms/" + room["id"])
        # The API member is used for authoritative roll/permission checks; the browser
        # is authenticated via the stored room credential below after inspecting its shape.
        host.navigate("#/rooms/" + room["id"])
        self.request("POST", self.prefix + "/start")
        event, cycle = self.turn(
            "我确认已经能听到的车厢声音，暂时停留在入口。"
            if self.original
            else "我阅读桌面登记册上已经可见的日期。",
            entities["note"]["id"],
        )
        assert cycle["status"] == "completed", cycle
        self.request("POST", self.prefix + f"/entities/{entities['corpse']['id']}/reveal")
        self.report["host_interventions"].append(
            "主机按原模组确认光源照到Clicker，公开其可见外形。"
            if self.original
            else "主机揭示明确标注的测试尸体；非原模组剧情。"
        )
        event, cycle = self.turn(
            "我用已有光源照向车厢中段，首次目睹没有眼睛的Clicker。请按已批准的SAN遭遇处理。"
            if self.original
            else "我掀开盖布，目睹血肉模糊的尸体。请按已批准的SAN遭遇处理。",
            entities["corpse"]["id"],
        )
        if cycle["status"] != "waiting_for_roll":
            if cycle["status"] != "completed":
                self.request("POST", self.prefix + "/agent-cycle/cancel")
            self.request(
                "POST",
                self.prefix + "/sanity/encounters",
                {
                    "target_member_id": player_id,
                    "entity_id": entities["corpse"]["id"],
                    "effect_id": "corpse-san",
                    "source_event_seq": event["seq"],
                },
            )
            self.report["host_interventions"].append(
                "KP未创建SAN检定，由主机按同一已批准效果及实际遭遇事件补发。"
            )
        check = next(
            c
            for c in self.request("GET", self.prefix + "/checks")
            if c.get("sanity") and c["status"] == "pending"
        )
        self.report["pending_check_id"] = check["id"]
        self.request("POST", self.prefix + "/pause")
        snapshot = self.request("POST", self.prefix + "/snapshots", {"name": "待SAN掷骰重启"})[
            "snapshot"
        ]
        self.stop(self.backend)
        self.start_backend()
        loaded = self.request("POST", self.prefix + f"/snapshots/{snapshot['id']}/load")["room"]
        assert loaded["session_state"]["characters"][slot]["san"] == 50
        self.request("POST", self.prefix + "/resume")
        for _ in range(6):
            check = next(
                c for c in self.request("GET", self.prefix + "/checks") if c["id"] == check["id"]
            )
            if check["status"] == "resolved":
                break
            stage = check["sanity"]["stage"]
            if stage == "symptom":
                current = self.request("GET", self.prefix)
                self.request(
                    "POST",
                    self.prefix + "/sanity/manage",
                    {
                        "operation": "symptom",
                        "slot_id": slot,
                        "expected_revision": current["revision"],
                        "reason": "主机按即时症状表选择，实测不修改骰点",
                        "symptom": "偏执：怀疑有人监视",
                        "mode": "realtime",
                    },
                )
                self.report["host_interventions"].append("实际骰点触发疯狂，主机选择偏执症状。")
            else:
                response = self.player_http.post(
                    "http://127.0.0.1:8000/api"
                    + self.prefix
                    + f"/sanity/checks/{check['id']}/roll",
                    json={"expected_stage": stage},
                )
                assert response.is_success, response.text
        cycle = self.wait_completed()
        assert cycle["status"] == "completed", cycle
        current = self.request("GET", self.prefix)
        own = self.player_http.get("http://127.0.0.1:8000/api" + self.prefix).json()
        assert (
            own["session_state"]["characters"][slot] == current["session_state"]["characters"][slot]
        )
        assert 0 < current["session_state"]["characters"][slot]["san"] < 50
        assert (
            self.player_http.post(
                "http://127.0.0.1:8000/api" + self.prefix + "/resources/correct",
                json={
                    "slot_id": slot,
                    "resource": "san",
                    "value": 50,
                    "reason": "unauthorized",
                    "expected_revision": current["revision"],
                },
            ).status_code
            == 403
        )
        self.report["runtime"] = current["session_state"]["characters"][slot]
        self.report["check"] = check
        if current["session_state"]["characters"][slot]["sanity"]["phase"] == "bout":
            sanity = current["session_state"]["characters"][slot]["sanity"]
            self.request(
                "POST",
                self.prefix + "/sanity/manage",
                {
                    "operation": "advance",
                    "expected_revision": current["revision"],
                    "reason": "主机明确推进发作持续轮数",
                    "round": sanity["bout_end_round"],
                },
            )
            current = self.request("GET", self.prefix)
            self.request(
                "POST",
                self.prefix + "/sanity/manage",
                {
                    "operation": "end_bout",
                    "expected_revision": current["revision"],
                    "slot_id": slot,
                    "reason": "已到发作结束轮数",
                },
            )
        _, cycle = self.turn(
            "我回顾刚才已经听见的喘息声，停留在入口整理思绪。"
            if self.original
            else "我回顾刚才登记册上已读到的日期，整理思绪后继续调查。",
            entities["note"]["id"],
        )
        assert cycle["status"] == "completed", cycle
        host.navigate("#/rooms/" + room["id"])
        wait_for(lambda: host.contains("理智与疯狂"))
        capture_panel(host, "[data-sanity-slot]", "host-sanity.png")
        player.navigate("#/rooms/" + room["id"])
        wait_for(lambda: player.contains("理智与疯狂"))
        capture_panel(player, "[data-sanity-slot]", "player-sanity.png")
        self.report["passed"] = True
        self.export()

    def export(self):
        if not hasattr(self, "prefix"):
            return
        for name, path in [("runs", "/agent-runs"), ("events", "/events"), ("room", "")]:
            response = self.http.get("http://127.0.0.1:8000/api" + self.prefix + path)
            if response.is_success:
                (self.directory / (name + ".json")).write_text(
                    json.dumps(response.json(), ensure_ascii=False, indent=2), encoding="utf-8"
                )
        if hasattr(self, "player_http"):
            public = self.player_http.get(
                "http://127.0.0.1:8000/api" + self.prefix + "/logs?format=markdown"
            ).text
            host = self.http.get(
                "http://127.0.0.1:8000/api" + self.prefix + "/logs?format=markdown"
            ).text
            (self.directory / "PUBLIC.md").write_text(public, encoding="utf-8")
            (self.directory / "HOST_DEBUG.md").write_text(host, encoding="utf-8")
        (self.directory / "report.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        check = SanityCheck(original="--original" in sys.argv)
        print("DIRECTORY=" + str(check.directory), flush=True)
        try:
            check.run()
        finally:
            try:
                check.export()
            finally:
                check.close()
