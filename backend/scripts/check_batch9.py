"""Batch 9 acceptance using the existing three-browser/CDP helpers."""

import argparse
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_check_policy import AdjudicationCheck
from check_check_policy import fake_response as previous_fake
from check_module_navigation import NavigationCheck
from check_multiplayer import BrowserPage


def fake_response(messages, kwargs):
    context = json.loads(messages[-1]["content"])
    result = previous_fake(messages, kwargs)
    if (
        kwargs["response_schema"].__name__ == "KeeperPlan"
        and "回顾" in context["triggering_action"]["payload"]["text"]
    ):
        result["parsed_intent"]["type"] = "recall"
        target = next(
            (e for e in context.get("known_targets", []) if e["fact_scope"] == "historical"), None
        )
        result["parsed_intent"]["target_id"] = target["id"] if target else None
    if kwargs["response_schema"].__name__ == "KeeperNarration":
        claims = context["PUBLIC_CLAIM_OPTIONS"][:1]
        result.update(
            public_narration="\n".join(c["statement"] for c in claims), grounded_claims=claims
        )
    return result


def serve(directory, real):
    import httpx
    import uvicorn

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app
    from app.models.ollama import OllamaAgentAdapter

    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / ".cache/batch-9").resolve()):
        raise ValueError("isolated batch 9 directory required")
    options = {} if real else {"_env_file": None}
    settings = Settings(
        **options,
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
        raise ValueError("Existing local Ollama configuration required")
    app = create_app(settings)
    app.state.agent_model_adapter = (
        OllamaAgentAdapter(settings) if real else FakeModelAdapter(responder=fake_response)
    )
    if not real:

        def reject(*args, **kwargs):
            raise AssertionError("Fake acceptance cannot access external HTTP")

        httpx.AsyncHTTPTransport.handle_async_request = reject
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


class Batch9Check(AdjudicationCheck):
    def __init__(self, real, config):
        NavigationCheck.__init__(self, real, config, "batch-9/real" if real else "batch-9/fake")
        if real:
            for source in ("rules", "modules/常暗之厢"):
                shutil.copytree(ROOT / "data" / source, self.directory / "data" / source)

    def start_backend(self):
        command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)]
        if self.real:
            command.append("--real")
        self.backend = self.start(command, BACKEND, "backend-" + str(len(self.processes)))
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)

    def run(self):
        self.start_services()
        prep_id = self.setup()
        host = self.host_page("host")
        host.navigate("#/preparations")
        wait_for(
            lambda: host.evaluate(
                f"!!document.querySelector('#preparation-select option[value=\"{prep_id}\"]')"
            )
        )
        host.fill("#preparation-select", prep_id)
        wait_for(lambda: host.contains("结构已批准"))
        self.capture(host, "[data-testid=module-structure]", "structure.png")
        player, second = [BrowserPage(self, name) for name in ("player-a", "player-b")]
        self.pages.extend([player, second])
        created = self.request("POST", "/rooms", {"name": "第九批规则与事实验收"})
        room_id = created["room"]["id"]
        self.report["room_id"] = room_id
        prefix = "/rooms/" + room_id
        host.navigate("#/rooms/" + room_id)
        wait_for(host.connected)
        for page, name in ((player, "玩家A"), (second, "玩家B")):
            page.fill("#join-invite", created["invite_code"])
            page.fill("#join-name", name)
            page.click("加入房间")
            wait_for(page.connected)
        self.request(
            "POST", prefix + "/members", {"display_name": "AI队友", "controller_type": "agent"}
        )
        room = self.request("GET", prefix)
        for name in ("玩家A", "玩家B", "AI队友"):
            member = next(m for m in room["members"] if m["display_name"] == name)
            character = self.make_character(name + "调查员")
            result = self.request(
                "POST", prefix + "/character-slots", {"character_id": character["id"]}
            )
            slot = next(
                s
                for s in result["room"]["character_slots"]
                if s["source_character_id"] == character["id"]
            )
            self.request(
                "POST",
                prefix + "/character-assignments",
                {"member_id": member["id"], "slot_id": slot["id"]},
            )
            if name == "AI队友":
                teammate_id = member["id"]
                self.request("POST", prefix + "/ready", {"ready": True, "member_id": teammate_id})
        player.click("准备 · 玩家A")
        second.click("准备 · 玩家B")
        self.request("PATCH", prefix + "/module-preparation", {"preparation_id": prep_id})
        if self.real:
            refs = [
                {"source_id": s["source_id"], "source_hash": s["source_hash"]}
                for s in self.request("GET", "/knowledge/sources")
                if s["kind"] != "module" and s["chunk_count"] > 0
            ]
            self.request(
                "PATCH",
                prefix + "/knowledge",
                {
                    "enabled": True,
                    "rules": refs,
                    "module": {
                        "source_id": self.source["source_id"],
                        "source_hash": self.source["source_hash"],
                    },
                },
            )
        for role, member in (("keeper", room["host_member_id"]), ("investigator", teammate_id)):
            profile = self.request(
                "POST",
                "/agent-profiles",
                {"role": role, "name": "本地KP" if role == "keeper" else "谨慎的同伴"},
            )
            self.request(
                "POST",
                prefix + "/agent-bindings",
                {"member_id": member, "profile_id": profile["id"]},
            )
        self.request("POST", prefix + "/start")
        self.report["gpu_before"] = self.gpu()
        self.finish_session(prefix, room_id, host, player, second)

    def finish_session(self, prefix, room_id, host, player, second):
        self.report["cycles"] = []
        for question in (
            "奖励骰和惩罚骰怎么使用？",
            "困难成功与极难成功有什么区别？",
            "请解释大成功和大失败的规则。",
        ):
            self.play_turn(prefix, player, host, question, "rule_question")
        self.play_turn(prefix, player, host, "我环顾当前明显可见的环境，确认眼前有什么。")
        if self.real:
            automatic = [
                e
                for e in self.request("GET", prefix + "/host-entities")
                if e["type"] == "clue"
                and not e.get("reveal_conditions", {}).get("successful_check")
                and e.get("reveal_conditions", {}).get("access_policy") in {None, "automatic"}
            ]
            if automatic:
                clue = next((e for e in automatic if "便签" in e["title"]), automatic[0])
                self.play_turn(
                    prefix, player, host, f"我查看眼前的{clue['title']}，阅读其已经允许公开的内容。"
                )
        title = self.report["check_entity_title"]
        self.play_turn(
            prefix,
            player,
            host,
            f"我留在原地，仔细调查{title}的模糊细节，请按它配置的侦查检定处理。",
        )
        assert self.report["checks"] == 1
        assert self.report["transitions"] == 0
        # A failed genuine die roll stays failed. The already public scene remains
        # a legitimate historical fact, so acceptance never forces a successful roll.
        known = self.request("GET", prefix + "/public-entities")
        self.report["before_transition_entities"] = known
        self.play_turn(
            prefix,
            player,
            host,
            "我现在离开这里，进入前方车厢。" if self.real else "我现在进入前方通道。",
        )
        nav = self.request("GET", prefix + "/module-navigation")
        assert nav["current_scene_node_id"] == self.report["second_node_id"]
        current = self.request("GET", prefix + "/public-entities")
        historic = next(
            (e for e in current if e["fact_scope"] == "historical" and e["type"] == "clue"), None
        )
        self.report["historical_target"] = historic
        self.play_turn(
            prefix,
            player,
            host,
            f"我回顾之前获知的{historic['title']}，只复述旧信息。"
            if historic
            else "我回顾之前获知的旧线索，只复述已经公开的信息。",
        )
        saved = self.request("POST", prefix + "/snapshots", {"name": "第九批规则与事实存档"})[
            "snapshot"
        ]
        self.request("POST", prefix + "/pause")
        self.stop(self.backend)
        wait_for(lambda: port_free(8000) is None)
        self.start_backend()
        self.request("POST", prefix + f"/snapshots/{saved['id']}/load", {})
        assert self.request("GET", prefix + "/public-entities") == current
        self.request("POST", prefix + "/resume")
        for page in self.pages:
            page.command("Page.reload")
            wait_for(page.connected)
        self.play_turn(prefix, second, host, "我查看当前环境，确认所在位置。")
        self.verify(prefix, room_id)
        self.report.update(
            browser_contexts=3,
            save_restart_load=True,
            rule_gameplay_side_effects=0,
            premature_transitions=0,
            passed=True,
        )
        assert self.report["checks"] == 1 and self.report["transitions"] == 1
        self.dump(prefix)

    def play_turn(self, prefix, player, host, text, category="investigation", target=None):
        previous = self.request("GET", prefix + "/agent-cycle")
        baseline = self.request("GET", prefix + "/events")["events"]
        player.fill("#agent-request-category", category)
        player.fill("#agent-action", text)
        started = time.monotonic()
        if target:
            # The public target ID is part of the supported actions API. Use the
            # player's existing browser identity and retain the same event sync.
            result = player.evaluate(
                "fetch('/api" + prefix + "/actions', {method:'POST', headers:{"
                "'Content-Type':'application/json', Authorization:'Bearer '+localStorage.getItem("
                + json.dumps("coc.room." + self.report["room_id"])
                + ")}, body:JSON.stringify("
                + json.dumps(
                    {
                        "text": text,
                        "category": category,
                        "target_entity_id": target,
                        "client_request_id": str(uuid4()),
                    },
                    ensure_ascii=False,
                )
                + ")})"
                ".then(async r=>({status:r.status,body:await r.json()}))"
            )
            assert result["status"] == 200, result
        else:
            player.click("提交规则问题" if category == "rule_question" else "提交行动")
        cycle = self.settle(prefix, previous_id=previous["id"] if previous else None)
        cycle = self.handle_waits(prefix, cycle, host, player, {"review_decision": "reject"})
        self.report["cycles"].append(
            {
                "id": cycle["id"],
                "action": text,
                "category": category,
                "seconds": round(time.monotonic() - started, 3),
                "status": cycle["status"],
                "state": cycle.get("state", {}),
            }
        )
        self.dump(prefix)
        assert cycle["status"] == "completed" and not cycle.get("requires_clarification"), cycle
        self.check_players(prefix, self.report["room_id"], cycle)
        if category == "rule_question":
            events = self.request("GET", prefix + "/events")["events"][len(baseline) :]
            assert {e["type"] for e in events} <= {
                "rules.question",
                "rules.answered",
                "agent.cycle_changed",
            }
            assert any(e["type"] == "rules.answered" for e in events)
            assert cycle["state"]["call_count"] == 0
        print("TURN=" + json.dumps(self.report["cycles"][-1], ensure_ascii=False), flush=True)
        return cycle

    def check_players(self, prefix, room_id, cycle):
        super().check_players(prefix, room_id, cycle)
        host_public = [
            e
            for e in self.request("GET", prefix + "/events")["events"]
            if e["visibility"] == "public"
        ]
        for page in self.pages[1:]:
            auth = (
                "{headers:{Authorization:'Bearer '+localStorage.getItem("
                + json.dumps("coc.room." + room_id)
                + ")}}"
            )
            data = page.evaluate(f"fetch('/api{prefix}/events',{auth}).then(r=>r.json())")
            assert [e for e in data["events"] if e["visibility"] == "public"] == host_public
        last_text = next(
            e["payload"]["text"]
            for e in reversed(host_public)
            if e["type"] in {"rules.answered", "keeper.narration"}
        )
        for page in self.pages:
            wait_for(lambda: page.contains(last_text[:30]))
        self.report["host_and_two_players_public_sync"] = True

    def dump(self, prefix):
        events = self.request("GET", prefix + "/events")["events"]
        runs = self.request("GET", prefix + "/agent-runs")
        plans, retrievals = [], []
        for run in runs:
            retrievals.extend(self.request("GET", prefix + f"/agent-runs/{run['id']}/retrievals"))
            if run["graph_node"] == "plan_keeper_action":
                plans.append(self.request("GET", prefix + f"/cycles/{run['cycle_id']}/validation"))
        for name, value in (
            ("events", events),
            ("runs", runs),
            ("plans", plans),
            ("retrievals", retrievals),
        ):
            (self.directory / f"{name}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        public = ["# PUBLIC", "", "合成乘客为主机测试人物，非原模组人物。", ""]
        for e in events:
            if e["visibility"] != "public":
                continue
            p = e["payload"]
            if e["type"] in {
                "action.submitted",
                "action.clarification_requested",
                "keeper.narration",
                "npc.spoke",
                "agent.spoke",
                "agent.action_proposed",
                "rules.question",
                "rules.answered",
                "check.resolved",
                "scene.updated",
                "entity.revealed",
            }:
                content = (
                    p.get("text")
                    or p.get("question")
                    or p.get("display_text")
                    or p.get("scene_summary")
                    or p.get("public_summary")
                )
                public.append(f"- #{e['seq']} [{e['type']}] {content}")
                for c in p.get("citations", []):
                    public.append(
                        f"  - 来源：{c['source_title']} / {c.get('edition')} / "
                        f"{c.get('source_version', c['source_hash'])} / p.{c.get('physical_page')}"
                    )
        calls = [c for r in runs for c in r["model_calls"]]
        self.report.update(
            model_calls=len(calls),
            model_latency_ms=sum(c["latency_ms"] for c in calls),
            model_stages=dict(Counter(r["graph_node"] for r in runs for c in r["model_calls"])),
            checks=sum(e["type"] == "check.resolved" for e in events),
            transitions=sum(e["type"] == "module.scene_transition" for e in events),
            rule_answers=sum(e["type"] == "rules.answered" for e in events),
            summary_rebuilds=sum(e["type"] == "agent.summary_rebuilt" for e in events),
            summary_failures=sum(e["type"] == "agent.summary_stale" for e in events),
            summary_selections=[
                c["state"].get("summary_selection") for c in self.report.get("cycles", [])
            ],
        )
        debug = (
            "# HOST_DEBUG\n\n```json\n"
            + json.dumps(self.report, ensure_ascii=False, indent=2)
            + "\n```\n"
        )
        (self.directory / "session.md").write_text(
            "\n".join(public) + "\n\n" + debug, encoding="utf-8"
        )
        (self.directory / "report.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--continue-session", type=Path)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.real)
        return
    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    if args.continue_session:
        continue_session(args.continue_session, config)
        return
    check = Batch9Check(args.real, config)
    print("DIRECTORY=" + str(check.directory.relative_to(ROOT)), flush=True)
    try:
        check.run()
    finally:
        check.close()
    for port in (8000, 5173):
        port_free(port)


def continue_session(directory, config):
    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / ".cache/batch-9").resolve()):
        raise ValueError("isolated directory required")
    check = Batch9Check.__new__(Batch9Check)
    SmokeCheck.__init__(check, "batch-9/continuation")
    check.directory, check.config, check.real = directory, config, True
    check.pages, check.backend = [], None
    check.report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    (directory / f"before-continuation-{uuid4().hex[:8]}-report.json").write_text(
        json.dumps(check.report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    check.report["passed"] = False
    prefix = "/rooms/" + check.report["room_id"]
    try:
        check.start_services()
        host = check.host_page("continued-host")
        players = [BrowserPage(check, name) for name in ("player-a", "player-b")]
        check.pages.extend(players)
        for page in check.pages:
            page.navigate("#/rooms/" + check.report["room_id"])
            wait_for(page.connected)
        if check.request("GET", prefix)["status"] == "running":
            check.request("POST", prefix + "/pause")
        before = check.request("GET", prefix + "/public-entities")
        saved = check.request("POST", prefix + "/snapshots", {"name": "第九批补结后存档"})[
            "snapshot"
        ]
        check.stop(check.backend)
        wait_for(lambda: port_free(8000) is None)
        check.start_backend()
        check.request("POST", prefix + f"/snapshots/{saved['id']}/load", {})
        assert check.request("GET", prefix + "/public-entities") == before
        check.request("POST", prefix + "/resume")
        for page in check.pages:
            page.command("Page.reload")
            wait_for(page.connected)
        note = next(
            e
            for e in check.request("GET", prefix + "/public-entities")
            if e["title"] == "便签正面文字"
        )
        cycle = check.play_turn(
            prefix,
            players[0],
            host,
            "我回顾先前获知的便签正面文字，只复述已公开的旧信息。",
            target=note["id"],
        )
        events = check.request("GET", prefix + "/events")["events"]
        narration = next(
            e["payload"]
            for e in events
            if e["type"] == "keeper.narration" and e["payload"].get("cycle_id") == cycle["id"]
        )
        assert "先前获知（当前位置未确认）" in narration["text"], narration
        assert note["public_summary"][:30] in narration["text"]
        assert (
            narration["text"].removeprefix("你回顾了已经获知的信息。\n")
            == "先前获知（当前位置未确认）：" + note["public_summary"][:200]
        )
        check.verify(prefix, check.report["room_id"])
        check.report.update(
            passed=True,
            explicit_historical_recall=True,
            host_note_settlement="../host-note-settlement.json",
            browser_contexts=3,
            save_restart_load=True,
            rule_gameplay_side_effects=0,
            premature_transitions=0,
        )
        check.dump(prefix)
    finally:
        check.close()


if __name__ == "__main__":
    main()
