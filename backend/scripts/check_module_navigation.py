"""Isolated three-browser ModuleIR acceptance; no commercial module fixtures in Git.

Fake: python scripts/check_module_navigation.py
Real: python scripts/check_module_navigation.py --real --config ../.cache/batch-6/real-config.json
Real configuration supplies an existing preparation DB and explicitly reviewed scene selections.
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage
from check_preparation import PreparationCheck


def fake_response(messages, kwargs):
    c = json.loads(messages[-1]["content"])
    if c.get("phase") == "summary":
        return {"content": "当前位置及公开实体以导航和调查板为准。"}
    if c.get("phase") == "narrate_publicly":
        e = max(c["public_entities"], key=lambda e: e["revealed_event_seq"] or 0)
        return {
            "claims": [
                {
                    "claim_id": "public",
                    "category": "module_fact",
                    "statement": e["public_summary"],
                    "entity_ids": [e["id"]],
                }
            ]
        }
    if c["role"] == "investigator":
        return {
            "tools": [
                {"name": "get_public_scene", "arguments": {}},
                {"name": "propose_action", "arguments": {"text": "我观察当前场景。"}},
            ]
        }
    if c["phase"] == "resolve_keeper_response":
        return {"tools": []}
    action = c["triggering_action"]
    text = action["payload"]["text"]
    if "检定" in text:
        return {
            "tools": [
                {
                    "name": "request_skill_check",
                    "arguments": {
                        "target_member_id": action["actor_member_id"],
                        "name": "spot_hidden",
                        "reason": "观察",
                    },
                }
            ]
        }
    if "进入" in text:
        t = c["module"]["outgoing_transitions"][0]
        return {
            "tools": [
                {
                    "name": "transition_scene",
                    "arguments": {
                        "target_scene_node_id": t["target_scene_node_id"],
                        "expected_revision": c["module_context_audit"]["navigation_revision"],
                        "request_id": "fake-transition",
                    },
                }
            ]
        }
    if "公告" in text:
        e = next(e for e in c["module"]["approved_entities"] if e["type"] == "clue")
        return {"tools": [{"name": "reveal_entity", "arguments": {"entity_id": e["id"]}}]}
    return {"tools": []}


def serve(directory, real):
    import httpx
    import uvicorn

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app

    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / ".cache").resolve()):
        raise ValueError("isolated_directory_required")
    settings = Settings(
        _env_file=None,
        host_admin_token=TEST_HOST,
        data_dir=ROOT / "data" if real else directory / "data",
        database_url=f"sqlite+aiosqlite:///{(directory / 'game.db').as_posix()}",
        knowledge_db_path=directory / "knowledge.db",
        checkpoint_db_path=directory / "checkpoint.db",
        model_provider="ollama",
        model_name="qwen3:8b",
        model_base_url="http://127.0.0.1:11434/v1/",
        model_context_limit=8192,
        model_output_limit=1100,
        model_timeout_seconds=240,
    )
    app = create_app(settings)
    if not real:
        app.state.agent_model_adapter = FakeModelAdapter(responder=fake_response)

        def reject(*args, **kwargs):
            raise AssertionError("Fake acceptance cannot access the network")

        httpx.AsyncHTTPTransport.handle_async_request = reject
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


class NavigationCheck(PreparationCheck):
    def start(self, command, cwd, name):
        process = super().start(command, cwd, name)
        (self.directory / "processes.json").write_text(
            json.dumps(
                [{"name": n, "pid": p.pid, "args": p.args} for n, p in self.processes],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return process

    def __init__(self, real, config):
        SmokeCheck.__init__(self, artifact_prefix="batch6-real" if real else "batch6-fake")
        self.real, self.config, self.pages, self.backend = real, config, [], None
        self.report = {"mode": "local qwen3:8b" if real else "Fake Model", "passed": False}
        self.http.timeout = 180
        if real:
            for source, target in (
                (ROOT / config["game_database"], self.directory / "game.db"),
                (ROOT / config["knowledge_database"], self.directory / "knowledge.db"),
            ):
                with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original:
                    with sqlite3.connect(target) as copy:
                        original.backup(copy)
        else:
            folder = self.directory / "data/modules/navigation"
            folder.mkdir(parents=True)
            (folder / "module.md").write_text(
                "# 候车厅\n候车厅里有公告和一位站务员。OPENING_KEEPER_MARKER\n"
                "# 通道\n通道里很安静。FUTURE_KEEPER_MARKER\n",
                encoding="utf-8",
            )

    def start_backend(self):
        command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)]
        if self.real:
            command.append("--real")
        self.backend = self.start(command, BACKEND, "backend-" + str(len(self.processes)))
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)

    def setup(self):
        if not self.real:
            self.request("POST", "/knowledge/index", {"kind": "modules"})
        sources = self.request("GET", "/knowledge/sources")
        source = next(
            s for s in sources if s["title"] == self.config.get("source_title", "navigation")
        )
        self.source = source
        prep_id = self.config.get("preparation_id")
        if not prep_id:
            prep_id = self.request(
                "POST",
                "/module-preparations",
                {
                    "source_id": source["source_id"],
                    "source_hash": source["source_hash"],
                    "display_title": "结构导航验收",
                },
            )["id"]
            for kind, title, summary in (
                ("scene", "候车厅", "你们来到候车厅。"),
                ("npc", "站务员", "一位站务员站在附近。"),
                ("clue", "公告", "公告提醒旅客保留车票。"),
            ):
                e = self.request(
                    "POST",
                    "/module-entities",
                    {
                        "preparation_id": prep_id,
                        "type": kind,
                        "title": title,
                        "public_summary": summary,
                    },
                )
                self.request("POST", f"/module-entities/{e['id']}/approve")
                if kind == "scene":
                    initial_entity = e["id"]
            self.request(
                "PATCH",
                f"/module-preparations/{prep_id}",
                {"initial_scene_entity_id": initial_entity, "required_entity_ids": []},
            )
        entities = self.request("GET", f"/module-preparations/{prep_id}/entities")
        initial = next(e for e in entities if e["type"] == "scene" and e["status"] == "approved")
        second = self.request(
            "POST",
            "/module-entities",
            {
                "preparation_id": prep_id,
                "type": "scene",
                "title": self.config.get("second_scene_title", "通道"),
                "public_summary": self.config.get("second_scene_summary", "你们进入安静的通道。"),
            },
        )
        self.request("POST", f"/module-entities/{second['id']}/approve")
        self.request("POST", f"/module-preparations/{prep_id}/approve")
        prefix = f"/module-preparations/{prep_id}/structure"
        built = self.request("POST", prefix + "/build")
        nodes = [n for n in built["nodes"] if n["node_id"] != built["root_node_id"]]
        first_id = self.config.get("initial_node_id", nodes[0]["node_id"])
        second_id = self.config.get("second_node_id", nodes[1]["node_id"])
        for node in built["nodes"]:
            if node["node_id"] not in {first_id, second_id, built["root_node_id"]}:
                self.request("PATCH", prefix + f"/nodes/{node['node_id']}", {"included": False})
        for node_id, entity, is_initial in ((first_id, initial, True), (second_id, second, False)):
            self.request(
                "PATCH",
                prefix + f"/nodes/{node_id}",
                {
                    "approved_type": "scene",
                    "public_title": entity["title"],
                    "public_summary": entity["public_summary"],
                    "initial_scene": is_initial,
                },
            )
        entities = self.request("GET", f"/module-preparations/{prep_id}/entities")
        approved = [e for e in entities if e["status"] == "approved"]
        for entity in approved:
            self.request(
                "POST",
                prefix + "/entity-bindings",
                {
                    "entity_id": entity["id"],
                    "node_id": second_id if entity["id"] == second["id"] else first_id,
                    "source_hash": source["source_hash"],
                },
            )
        transition = self.request(
            "POST",
            prefix + "/transitions",
            {"source_scene_node_id": first_id, "target_scene_node_id": second_id, "approved": True},
        )
        snapshot = self.request("POST", prefix + "/approve", {})
        self.report.update(
            preparation_id=prep_id,
            structure_snapshot_id=snapshot["snapshot_id"],
            structure_version=snapshot["structure_version"],
            node_count=built["node_count"],
            block_count=built["block_count"],
            warning_count=len(built["warnings"]),
            entity_bindings=len(snapshot["entity_bindings"]),
            npc_bindings=sum(e["type"] == "npc" for e in approved),
            approved_transitions=1,
            initial_node_id=first_id,
            second_node_id=second_id,
            extraction_method=built["extraction_method"],
            transition_id=transition["transition_id"],
        )
        return prep_id

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
        created = self.request("POST", "/rooms", {"name": "第六批结构导航验收"})
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
        actions = self.config.get(
            "actions", ["我阅读公告。", "请进行一次侦查检定。", "我进入前方通道。"]
        )
        for action in actions:
            previous = self.request("GET", prefix + "/agent-cycle")
            player.fill("#agent-action", action)
            player.click("提交行动")
            cycle = self.settle(prefix, previous_id=previous["id"] if previous else None)
            cycle = self.handle_waits(prefix, cycle, host, player, {"review_decision": "reject"})
            assert cycle["status"] == "completed", cycle
            print("CYCLE_COMPLETED=" + cycle["id"], flush=True)
        self.verify(prefix, room_id)
        nav = self.request("GET", prefix + "/module-navigation")
        self.report["navigation_before_restart"] = {
            k: nav[k]
            for k in ("current_scene_node_id", "visited_scene_node_ids", "navigation_revision")
        }
        self.report["human_actions"] = len(actions)
        fallback = self.request(
            "POST",
            prefix + "/module-search",
            {"query": self.config.get("fallback_query", "通道"), "scope": "global"},
        )
        self.report["host_fallback_results"] = len(fallback["result"]["evidence"])
        snapshot = self.request("POST", prefix + "/snapshots", {"name": "结构导航存档"})["snapshot"]
        self.request("POST", prefix + "/pause")
        self.stop(self.backend)
        wait_for(lambda: port_free(8000) is None)
        self.start_backend()
        self.request("POST", prefix + f"/snapshots/{snapshot['id']}/load", {})
        self.request("POST", prefix + "/resume")
        for page in self.pages:
            page.command("Page.reload")
            wait_for(page.connected)
        restored = self.request("GET", prefix + "/module-navigation")
        assert all(restored[k] == v for k, v in self.report["navigation_before_restart"].items())
        previous = self.request("GET", prefix + "/agent-cycle")
        player.fill("#agent-action", "我确认当前所在的场景，继续观察。")
        player.click("提交行动")
        cycle = self.settle(prefix, previous_id=previous["id"])
        assert (
            self.handle_waits(prefix, cycle, host, player, {"review_decision": "reject"})["status"]
            == "completed"
        )
        self.verify(prefix, room_id)
        self.audit(prefix, room_id)
        self.report.update(browser_contexts=3, save_restart_load=True)
        assert self.report["kp_responses"] >= 3 and self.report["teammate_actions"] >= 2
        assert self.report["checks"] >= 1 and self.report["public_entity_count"] >= 2
        assert self.report["scene_transitions"] >= 1, (
            "Real model did not execute the approved transition"
        )
        assert self.report["ordinary_global_module_searches"] == 0
        assert self.report["host_fallback_results"] > 0
        self.report["passed"] = True

    def audit(self, prefix, room_id):
        runs = self.request("GET", prefix + "/agent-runs")
        events = self.request("GET", prefix + "/events")["events"]
        self.report["scene_transitions"] = sum(
            e["type"] == "module.scene_transition" for e in events
        )
        retrievals = [
            entry
            for run in runs
            for entry in self.request("GET", prefix + f"/agent-runs/{run['id']}/retrievals")
        ]
        self.report["ordinary_global_module_searches"] = sum(
            r["source_filters"]["kind"] == "module" for r in retrievals
        )
        self.report["rule_retrievals"] = sum(
            r["source_filters"]["kind"] == "rules" for r in retrievals
        )
        modes = Counter(
            r["context"].get("module_context_audit", {}).get("context_mode")
            for r in runs
            if r["graph_node"] == "keeper_decide"
        )
        self.report["context_modes"] = dict(modes)
        publics = []
        for page in self.pages[1:]:
            for endpoint in ("module-navigation", "module-context-debug"):
                status = page.evaluate(
                    f"fetch('/api{prefix}/{endpoint}', {{headers: {{Authorization: 'Bearer ' + "
                    f"localStorage.getItem('coc.room.{room_id}')}}}}).then(r => r.status)"
                )
                assert status == 403
            public = page.evaluate(
                f"fetch('/api{prefix}/current-scene', {{headers: {{Authorization: 'Bearer ' + "
                f"localStorage.getItem('coc.room.{room_id}')}}}}).then(r => r.json())"
            )
            publics.append(public)
            assert "node_" not in json.dumps(public)
            assert page.evaluate(
                "!JSON.stringify(window.roomFrames).includes('node_') && "
                "!JSON.stringify(window.roomFrames).includes('keeper_summary')"
            )
        assert publics[0] == publics[1]
        public_lines, debug_lines = ["# PUBLIC", ""], ["# HOST_DEBUG", ""]
        for event in events:
            data = event["payload"]
            if event["visibility"] == "public":
                text = data.get("content") or data.get("text") or data.get("scene_title")
                role = (
                    "KP"
                    if event["type"] == "keeper.narration"
                    else "AI队友：谨慎的同伴"
                    if event["type"] in {"agent.spoke", "agent.action_proposed"}
                    else "检定"
                    if event["type"] == "check.resolved"
                    else "场景"
                )
                if text:
                    public_lines.append(f"- #{event['seq']} [{role}] {str(text)[:700]}")
                elif event["type"] == "check.resolved":
                    result = data["result"]
                    public_lines.append(
                        f"- #{event['seq']} [检定] {data['name']} "
                        f"骰点={result['total']} 目标={result['threshold']} "
                        f"结果={result['level']} passed={result['passed']}"
                    )
                elif event["type"] == "entity.revealed":
                    public_lines.append(f"- #{event['seq']} [已公开实体] {data.get('title', '')}")
            if event["type"] == "module.context_selected":
                for key in (
                    "current_scene_node_id",
                    "heading_path",
                    "selected_node_ids",
                    "selected_block_ids",
                    "context_mode",
                ):
                    label = {
                        "current_scene_node_id": "current_scene",
                        "selected_node_ids": "selected_nodes",
                        "selected_block_ids": "selected_blocks",
                    }.get(key, key)
                    debug_lines.append(f"- [MODULE] {label}={data.get(key)}")
            if event["type"] == "module.scene_transition":
                debug_lines.append(f"- [MODULE] transition={data['source']} -> {data['target']}")
            if event["type"] == "review.requested" and data.get("navigation_request"):
                debug_lines.append("- [MODULE] transition_waiting_host_review")
            if event["type"] == "module.global_search":
                debug_lines.append(
                    f"- [MODULE] context_mode=global_fallback "
                    f"reason={data['reason']} results={data['result_count']}"
                )
        for run in runs:
            if run["graph_node"] == "keeper_decide":
                audit = run["context"].get("module_context_audit", {})
                debug_lines.append(
                    f"- [MODULE] cycle={run['cycle_id']} "
                    f"context_mode={audit.get('context_mode')} "
                    f"budget_used={audit.get('budget_used')} "
                    f"omitted_blocks={audit.get('omitted_block_count')}"
                )
        (self.directory / "session.md").write_text(
            "\n".join(public_lines + [""] + debug_lines), encoding="utf-8"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--serve", type=Path)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.real)
        return
    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    if args.real and not config:
        raise ValueError("Real acceptance requires a reviewed local configuration")
    check = NavigationCheck(args.real, config)
    print("DIRECTORY=" + str(check.directory.relative_to(ROOT)), flush=True)
    try:
        check.run()
    finally:
        check.close()


if __name__ == "__main__":
    main()
