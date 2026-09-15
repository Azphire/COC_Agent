"""Batch 24 isolated UI import and short real API verification; never reset dice.

python backend/scripts/check_batch24.py live-c1 [--resume] [--live]
Reuses batch 23's launcher and credential resolution without its switching/trial suite.
"""

import argparse
import base64
import ctypes
import hashlib
import json
import signal
import time

from check_batch22 import write
from check_batch23_api import HOST, ApiCheck
from check_character_creation import ROOT, SmokeCheck, wait_for
from check_multiplayer import BrowserPage


class Batch24(ApiCheck):
    artifact_batch = "batch-24"

    def __init__(self, args):
        super().__init__(args)
        self.env["DATA_DIR"] = str(ROOT / "data")
        self.bundle = ROOT / "data/prepared/changan/batch-21/package-approved.json"
        assert hashlib.sha256(self.bundle.read_bytes()).hexdigest() == (
            "93dee39824e41a0d9544fc5e869845c0ffcaeb43dd54603aec1eae840372ddd4"
        )

    def blocked_switch(self, stage):
        pass  # Existing batch 23 evidence is reused; no switch or inference trial here.

    def browser(self):
        self.page = BrowserPage(self, f"browser-{self.run}")
        self.page.evaluate("location.hash='#/preparations'")
        wait_for(lambda: self.page.contains("解锁主机"))
        self.page.fill("#host-key", HOST)
        self.page.click("解锁主机")
        wait_for(lambda: self.page.contains("导入已准备模组"))

    def import_ui(self):
        page = self.page
        root = page.command("DOM.getDocument")["root"]["nodeId"]
        node = page.command(
            "DOM.querySelector", {"nodeId": root, "selector": "#preparation-package"}
        )["nodeId"]
        page.command("DOM.setFileInputFiles", {"nodeId": node, "files": [str(self.bundle)]})
        page.click("导入准备包")
        wait_for(lambda: page.contains("在房间的“批准版本”中选择此版本"), 150)
        preparations = self.request("GET", "/module-preparations")
        selected = page.evaluate("document.querySelector('#preparation-select').value")
        prep = next(p for p in preparations if p["id"] == selected)
        self.result["preparation_id"] = prep["id"]
        page.screenshot("import.png")
        page.evaluate("document.querySelector('[data-testid=preparation-status]').scrollIntoView()")
        time.sleep(0.3)
        screenshot = page.command("Page.captureScreenshot", {"format": "png"})
        (page.directory / "coverage.png").write_bytes(base64.b64decode(screenshot["data"]))
        page.click("导入准备包")
        wait_for(lambda: page.contains("已复用相同内容的版本"), 150)
        assert len(self.request("GET", "/module-preparations")) == len(preparations)
        page.command("Page.reload")
        time.sleep(0.8)
        wait_for(lambda: page.contains(prep["display_title"]), 25)
        self.result["import_ui"] = {
            "repeat_reused": True,
            "refresh_persisted": True,
            "preparation": prep,
        }
        self.persist()

    def setup(self):
        page = self.page
        page.evaluate("location.hash='#/rooms'")
        wait_for(lambda: page.contains("创建房间"))
        page.fill("#room-name", "第二十四批 正式包日常入口短测")
        page.click("创建房间")
        wait_for(lambda: "/rooms/" in (page.evaluate("location.hash") or ""))
        rid = page.evaluate("location.hash.split('/').pop()")
        self.prefix = "/rooms/" + rid
        room = self.request("GET", self.prefix)
        # Publish one ordinary point-buy character. Joining uses the invitation actually
        # displayed by the room UI; host and player credentials remain separate.
        wait_for(lambda: page.evaluate("!!document.querySelector('[data-testid=invite-code]')"))
        code = page.evaluate("document.querySelector('[data-testid=invite-code]').textContent")
        joined = self.request("POST", "/rooms/join", {"invite_code": code, "display_name": "周衡"})
        self.player = joined["member_token"]
        write(self.directory / "session.json", {"prefix": self.prefix, "token": self.player})
        values = dict(str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60)
        card = self.request(
            "POST",
            "/characters/point-buy",
            {
                "ruleset_id": "coc7-character-creation",
                "name": "周衡",
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
            },
        )
        card = self.request(
            "POST", f"/characters/{card['id']}/finalize", {"version": card["version"]}
        )
        published = self.request(
            "POST", self.prefix + "/character-slots", {"character_id": card["id"]}
        )["room"]
        self.request(
            "POST",
            self.prefix + "/character-assignments",
            {
                "slot_id": published["character_slots"][0]["id"],
                "member_id": joined["room"]["self_member_id"],
            },
        )
        profile = self.request("POST", "/agent-profiles", {"role": "keeper", "name": "KP"})
        self.request(
            "POST",
            self.prefix + "/agent-bindings",
            {"member_id": room["host_member_id"], "profile_id": profile["id"]},
        )
        self.request("POST", self.prefix + "/ready", {"ready": True}, player=True)
        page.command("Page.reload")
        time.sleep(0.8)
        wait_for(lambda: page.evaluate("!!document.querySelector('#room-preparation')"))
        page.fill("#room-preparation", self.result["preparation_id"])
        page.click("绑定准备版本")
        wait_for(lambda: self.request("GET", self.prefix).get("game", {}).get("preparation"))
        page.click("开始游戏")
        wait_for(lambda: self.request("GET", self.prefix)["status"] == "running")
        page.screenshot("started.png")
        write(self.directory / "session.json", {"prefix": self.prefix, "token": self.player})
        write(self.directory / "initial-room.json", self.request("GET", self.prefix))
        self.result["start_ui"] = True
        self.persist()

    def sanity_fixture(self):
        """Independent short fixture: host-established discovery; exact approved SAN effect.

        No model calls and no claim that the natural investigation check passed.
        """
        output = self.directory / "san-fixture.json"
        if output.exists():
            prior = json.loads(output.read_text("utf-8"))
            if prior.get("status") == "completed":
                return
            raise RuntimeError(
                "SAN fixture incomplete; inspect saved original check before resuming"
            )
        package = json.loads(self.bundle.read_text("utf-8-sig"))
        scene = next(e for e in package["entities"] if e["key"] == "s5")
        package["title"] = "第二十四批 SAN 短场景夹具（5号起始，独立于自然主线）"
        package["initial_node_id"] = scene["node_ids"][0]
        package["initial_entity_key"] = "s5"
        for node in package["nodes"]:
            node["patch"]["initial_scene"] = node["node_id"] == package["initial_node_id"]
        write(self.directory / "san-fixture-package.json", package)
        imported = self.request("POST", "/module-preparations/import", package)
        original = self.request("GET", self.prefix)
        created = self.request("POST", "/rooms", {"name": "Batch24 独立 SAN 夹具"})
        prefix = "/rooms/" + created["room"]["id"]
        member = self.request(
            "POST", prefix + "/members", {"display_name": "夹具观察者", "controller_type": "human"}
        )["room"]["members"][-1]
        source_card = original["character_slots"][0]["source_character_id"]
        slots = self.request("POST", prefix + "/character-slots", {"character_id": source_card})[
            "room"
        ]["character_slots"]
        self.request(
            "POST",
            prefix + "/character-assignments",
            {"slot_id": slots[0]["id"], "member_id": member["id"]},
        )
        self.request(
            "PATCH", prefix + "/module-preparation", {"preparation_id": imported["preparation_id"]}
        )
        self.request("POST", prefix + "/ready", {"ready": True, "member_id": member["id"]})
        self.request("POST", prefix + "/start")
        eid = imported["entity_ids"]["newspaper_date"]
        record = {
            "status": "started",
            "room_id": created["room"]["id"],
            "preparation_id": imported["preparation_id"],
            "source_package_sha256": hashlib.sha256(self.bundle.read_bytes()).hexdigest(),
            "fixture": (
                "5号起始；主机夹具建立已读出日期事实，不冒充自然调查成功；SAN 效果完全沿用批准包。"
            ),
            "entity_id": eid,
            "effect_id": "future_news",
            "natural_inputs": 0,
            "before": self.request("GET", prefix),
        }
        write(output, record)
        self.request("POST", prefix + f"/entities/{eid}/reveal")
        event = next(
            e
            for e in self.request("GET", prefix + "/events?limit=200")["events"]
            if e["type"] == "entity.revealed" and e["payload"]["id"] == eid
        )
        request = {
            "target_member_id": member["id"],
            "entity_id": eid,
            "effect_id": "future_news",
            "source_event_seq": event["seq"],
            "encounter_confirmed": True,
            "reason": record["fixture"],
        }
        check = self.request("POST", prefix + "/sanity/encounters", request)["check"]
        record.update(request=request, original_check=check)
        write(output, record)
        while check["sanity"]["stage"] != "done":
            check = self.request(
                "POST",
                prefix + f"/sanity/checks/{check['id']}/roll",
                {"expected_stage": check["sanity"]["stage"]},
            )["check"]
            record["settled_check"] = check
            write(output, record)
        repeat = self.request("POST", prefix + "/sanity/encounters", request)["check"]
        assert repeat == check
        before = self.request("GET", prefix)
        checks = self.request("GET", prefix + "/checks")
        saved = self.request("POST", prefix + "/snapshots", {"name": "SAN fixture settled"})[
            "snapshot"
        ]
        self.request("POST", prefix + "/pause")
        self.stop()
        self.launch()
        self.request("POST", prefix + f"/snapshots/{saved['id']}/load")
        after = self.request("GET", prefix)
        record["restore"] = {
            k: before["session_state"][k] == after["session_state"][k]
            for k in (
                "characters",
                "module_runtime",
                "combat",
                "time_receipts",
                "game_minute",
                "game_round",
                "sanity_day",
            )
        }
        record["restore"].update(
            checks=checks == self.request("GET", prefix + "/checks"),
            inventory=before["inventory"] == after["inventory"],
        )
        assert all(record["restore"].values())
        record.update(
            status="completed",
            after=after,
            checks=checks,
            main_room_unchanged=original["session_state"]
            == self.request("GET", self.prefix)["session_state"],
        )
        assert record["main_room_unchanged"]
        write(output, record)
        print("SAN fixture completed; original dice preserved; restore 9/9", flush=True)

    def run_batch(self, args):
        kernel = ctypes.windll.kernel32
        kernel.FreeConsole()
        assert kernel.AllocConsole()
        ctypes.windll.user32.ShowWindow(kernel.GetConsoleWindow(), 0)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self._console_handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)(lambda _: True)
        assert kernel.SetConsoleCtrlHandler(self._console_handler, True)
        try:
            self.launch()
            self.browser()
            if not self.result.get("preparation_id"):
                self.import_ui()
            elif not args.live:
                before = self.request("GET", self.prefix)
                self.import_ui()
                assert self.request("GET", self.prefix)["session_state"] == before["session_state"]
                self.result["restart_import_persisted"] = True
            if not self.prefix:
                self.setup()
            if args.san_fixture:
                self.sanity_fixture()
            if args.live:
                current = self.request("GET", self.prefix)["game"].get("cycle")
                if current and current["status"] in {"failed", "waiting_for_roll"}:
                    self.turn(
                        self.result.get("inflight", {}).get("text", "恢复原行动"),
                        retry=current["status"] == "failed",
                        continuing=True,
                    )
                actions = [
                    "我仔细查看电车示意图上被涂抹的部分。",
                    "我把门上的便签揭下来，翻过来看背面的文字。",
                    "我把已经查看的那张便签拿在手里。",
                    "我们从6号车厢进入5号车厢。",
                    "我仔细检查座位上的报纸，确认报头印刷的日期。",
                ]
                for action in actions:
                    if not any(t["text"] == action for t in self.result["turns"]):
                        self.turn(action)
                if not self.result.get("restores"):
                    self.request("POST", self.prefix + "/summary-rebuild")
                    self.restore()
                    assert any(
                        p["id"] == self.result["preparation_id"]
                        for p in self.request("GET", "/module-preparations")
                    )
                    self.turn("回顾我们实际调查到的信息、检定结果和我现在持有的东西。")
                self.page.evaluate(f"location.hash='#/rooms/{self.prefix.split('/')[-1]}'")
                wait_for(lambda: self.page.contains("调查"))
                self.page.screenshot("game-final.png")
            self.export()
            write(
                self.directory / "public-entities.json",
                self.request("GET", self.prefix + "/public-entities", player=True),
            )
            self.result["status"] = "completed" if args.live else "ui_completed"
        except Exception as error:
            self.result["status"] = "failed"
            self.result["error"] = str(error)
            if self.page:
                self.page.screenshot("failure.png")
            if self.prefix:
                self.export()
            raise
        finally:
            self.persist()
            self.stop()
            for _, process in self.processes:
                SmokeCheck.stop(process)
            self.http.close()
            for log in self.logs:
                log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--san-fixture", action="store_true")
    parser.add_argument("--backend-port", type=int, default=8026)
    parser.add_argument("--frontend-port", type=int, default=5175)
    Batch24(parser.parse_args()).run_batch(parser.parse_args())
