"""Isolated batch-8 three-browser acceptance. Only --real calls local qwen3:8b."""

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from check_character_creation import BACKEND, ROOT, TEST_HOST, port_free, wait_for
from check_module_navigation import NavigationCheck
from check_multiplayer import BrowserPage


def fake_response(messages, kwargs):
    c = json.loads(messages[-1]["content"])
    schema = kwargs["response_schema"].__name__
    if schema == "KeeperPlan":
        ids = c["action_identifiers"]
        text = c["triggering_action"]["payload"]["text"]
        kind = (
            "unknown"
            if "那个" in text
            else "move"
            if "进入" in text
            else "converse"
            if "交谈" in text
            else "investigate"
            if "检定" in text
            else "observe"
        )
        intent = {
            "type": kind,
            "actor_member_id": ids["actor_member_id"],
            "actor_character_slot_id": ids["actor_character_slot_id"],
            "evidence_quote": text,
            "confidence": 1,
        }
        plan = {
            "plan_id": ids["plan_id"],
            "cycle_id": ids["cycle_id"],
            "current_scene_id": ids["current_scene_id"],
            "parsed_intent": intent,
            "expected_navigation_revision": ids["expected_navigation_revision"],
        }
        if kind == "move":
            t = c["approved_exits"][0]
            intent["target_id"] = t["target_scene_node_id"]
            plan["proposed_transition_id"] = t["transition_id"]
        elif kind == "converse":
            intent["target_id"] = next(e["id"] for e in c["current_targets"] if e["type"] == "npc")
        elif kind == "investigate":
            risk = "风险" in text
            entity = next((e for e in c.get("check_requirements", []) if e["title"] in text), None)
            target = entity["entity_id"] if entity else ids["current_scene_id"]
            intent["target_id"] = target
            plan["proposed_check"] = {
                "target_member_id": ids["actor_member_id"],
                "kind": "attribute" if risk else "skill",
                "name": "dex" if risk else "spot_hidden",
                "reason": "调查当前目标",
                "necessity": "required",
                "target_entity_id": target,
                "basis_entity_id": target if entity else None,
                "clue_id": target if entity else None,
                "uncertainty": "能否在挑战中完成动作",
                "success_effect": "完成动作",
                "failure_consequence": "无法完成动作",
                "rule_topic_id": "coc7.skill_check" if risk else None,
                "risk_quote": text if risk else "",
            }
            if entity:
                plan["proposed_reveal_entity_ids"] = [target]
        return plan
    if schema == "KeeperNarration":
        claims = c["PUBLIC_CLAIM_OPTIONS"][-1:]
        result = {
            "public_narration": "\n".join(x["statement"] for x in claims),
            "grounded_claims": claims,
        }
        if c.get("conversation_target"):
            npc = next(e for e in c["public_entities"] if e["id"] == c["conversation_target"])
            result["npc_speech"] = {"entity_id": npc["id"], "text": npc["public_summary"]}
        checks = [e for e in c["public_tool_results"]["events"] if e["type"] == "check.resolved"]
        if checks:
            result["check_result_reference"] = checks[-1]["payload"]["id"]
        return result
    if schema == "TeammateDecision":
        return {
            "mode": "pass",
            "related_player_action_seq": c["triggering_action"]["seq"],
            "confidence": 1,
        }
    return {"content": "记录公开场景、交谈和服务端检定，未证实的内容仍是推测。"}


def serve(directory, real):
    import httpx
    import uvicorn

    from app.agents.model import FakeModelAdapter
    from app.config import Settings
    from app.main import create_app
    from app.models.ollama import OllamaAgentAdapter

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
    actual_adapter = (
        OllamaAgentAdapter(settings) if real else FakeModelAdapter(responder=fake_response)
    )

    app.state.agent_model_adapter = actual_adapter
    if not real:

        def reject(*args, **kwargs):
            raise AssertionError("Fake acceptance cannot access the network")

        httpx.AsyncHTTPTransport.handle_async_request = reject
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


class AdjudicationCheck(NavigationCheck):
    def __init__(self, real, config):
        super().__init__(real, config, "batch8-real" if real else "batch8-fake")

    def start_backend(self):
        command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)]
        if self.real:
            command.append("--real")
        self.backend = self.start(command, BACKEND, "backend-" + str(len(self.processes)))
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)

    def request(self, method, path, body=None):
        if not self.real and path == "/module-entities" and body:
            if body.get("type") == "scene" and body.get("title") == "候车厅":
                body = {**body, "public_summary": "你们来到候车厅，眼前可以看到公告。"}
            if body.get("type") == "clue":
                body = {
                    **body,
                    "reveal_conditions": {
                        "access_policy": "requires_check",
                        "successful_check": {
                            "kind": "skill",
                            "name": "spot_hidden",
                            "difficulty": "regular",
                        },
                    },
                }
        if path == "/module-entities" and body and body.get("type") == "npc":
            body = {
                **body,
                "initial_visibility": "revealed",
                "tags": ["host_authored_test"],
                "keeper_summary": "BATCH8_NPC_PRIVATE_SENTINEL，不得公开。",
                "public_summary": "我也是刚醒来的乘客，只能确认我们目前还在这里。",
            }
        return super().request(method, path, body)

    def setup(self):
        if self.real:
            npc = self.request(
                "POST",
                "/module-entities",
                {
                    "preparation_id": self.config["preparation_id"],
                    "type": "npc",
                    "title": "同行乘客（测试人物）",
                    "public_summary": "测试",
                },
            )
            self.request("POST", f"/module-entities/{npc['id']}/approve")
        if self.real:
            entities = self.request(
                "GET", f"/module-preparations/{self.config['preparation_id']}/entities"
            )
            scene = next(e for e in entities if e["type"] == "scene" and e["status"] == "approved")
            clue = next(
                e
                for e in entities
                if e["reveal_conditions"].get("successful_check") and e["status"] == "approved"
            )
            self.request("POST", f"/module-entities/{scene['id']}/draft")
            self.request(
                "PATCH",
                f"/module-entities/{scene['id']}",
                {"public_summary": scene["public_summary"] + "门旁可见" + clue["title"] + "。"},
            )
            self.request("POST", f"/module-entities/{scene['id']}/approve")
            self.request("POST", f"/module-entities/{clue['id']}/draft")
            self.request(
                "PATCH",
                f"/module-entities/{clue['id']}",
                {
                    "reveal_conditions": {
                        **clue["reveal_conditions"],
                        "access_policy": "requires_check",
                    }
                },
            )
            self.request("POST", f"/module-entities/{clue['id']}/approve")
            self.report["check_entity_id"] = clue["id"]
            self.report["check_entity_title"] = clue["title"]
        prep = super().setup()
        if not self.real:
            self.report["check_entity_title"] = "公告"
        return prep

    def gpu(self):
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        return result.stdout.strip()

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
        created = self.request("POST", "/rooms", {"name": "第八批行动裁决验收"})
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
        investigation = (
            f"我留在原地，仔细调查{self.report['check_entity_title']}的模糊细节，"
            "请按它配置的侦查检定处理。"
        )
        actions = [
            "我环顾当前明显可见的环境，确认眼前有什么。",
            "我复核已经公开的当前环境信息，留在原地。",
            "我与眼前的测试乘客交谈，问他能确认什么。",
            investigation,
            investigation,
            "我在当前车厢原地做自身动作，冒着失去平衡的风险，在晃动中尝试站稳；"
            "成功就保持稳定，失败则无法完成这次动作。请进行敏捷属性检定。目标是自身动作，不涉及其他物件。",
            "我现在离开这里，进入前方车厢。" if self.real else "我现在进入前方通道。",
        ]
        self.report["cycles"] = []
        for index, action in enumerate(actions):
            previous = self.request("GET", prefix + "/agent-cycle")
            targets = self.request("GET", prefix)["game"].get("conversation_targets", [])
            if "交谈" in action:
                npc = next(t for t in targets if t["type"] == "npc")
                player.fill("#agent-conversation-target", npc["id"])
                self.report["npc_id"] = npc["id"]
            else:
                if targets:
                    player.fill("#agent-conversation-target", "")
            player.fill("#agent-action", action)
            start = time.monotonic()
            player.click("提交行动")
            cycle = self.settle(prefix, previous_id=previous["id"] if previous else None)
            cycle = self.handle_waits(prefix, cycle, host, player, {"review_decision": "reject"})
            assert cycle["status"] == "completed", cycle
            self.report["cycles"].append(
                {
                    "id": cycle["id"],
                    "seconds": round(time.monotonic() - start, 2),
                    "action": action,
                    "call_count": cycle["state"]["call_count"],
                    "check_id": cycle["state"].get("pending_check_id"),
                }
            )
            expected_check = index in {3, 5}
            assert bool(cycle["state"].get("pending_check_id")) == expected_check, self.request(
                "GET", prefix + f"/cycles/{cycle['id']}/validation"
            )
            self.report["gpu_latest"] = self.gpu()
            print("CYCLE_COMPLETED=" + cycle["id"] + " " + action, flush=True)
            nav = self.request("GET", prefix + "/module-navigation")
            if index < len(actions) - 1:
                assert nav["current_scene_node_id"] == self.report["initial_node_id"], (
                    "premature transition"
                )
            self.check_players(prefix, room_id, cycle)
        nav = self.request("GET", prefix + "/module-navigation")
        self.report["legal_transition"] = (
            nav["current_scene_node_id"] == self.report["second_node_id"]
        )
        assert self.report["legal_transition"]
        behavior = self.request("GET", prefix + "/teammate-behavior")
        saved = self.request("POST", prefix + "/snapshots", {"name": "第八批行为存档"})["snapshot"]
        self.request("POST", prefix + "/pause")
        self.stop(self.backend)
        wait_for(lambda: port_free(8000) is None)
        self.start_backend()
        self.request("POST", prefix + f"/snapshots/{saved['id']}/load", {})
        assert self.request("GET", prefix + "/teammate-behavior") == behavior
        self.request("POST", prefix + "/resume")
        for page in self.pages:
            page.command("Page.reload")
            wait_for(page.connected)
        previous = self.request("GET", prefix + "/agent-cycle")
        player.fill("#agent-action", "我查看当前环境，确认所在位置。")
        player.click("提交行动")
        cycle = self.settle(prefix, previous_id=previous["id"])
        cycle = self.handle_waits(prefix, cycle, host, player, {"review_decision": "reject"})
        self.report["cycles"].append({"id": cycle["id"], "action": "读档后继续观察"})
        self.check_players(prefix, room_id, cycle)
        self.verify(prefix, room_id)
        self.audit_actions(prefix)
        self.report.update(
            browser_contexts=3, save_restart_load=True, premature_transitions=0, passed=True
        )

    def check_players(self, prefix, room_id, cycle):
        publics = []
        for page in self.pages[1:]:
            auth = (
                "{headers:{Authorization:'Bearer '+localStorage.getItem("
                + json.dumps("coc.room." + room_id)
                + ")}}"
            )
            for endpoint in (
                f"cycles/{cycle['id']}/plan",
                f"cycles/{cycle['id']}/validation",
                "teammate-behavior",
                "summary-status",
            ):
                assert (
                    page.evaluate(f"fetch('/api{prefix}/{endpoint}',{auth}).then(r=>r.status)")
                    == 403
                )
            data = page.evaluate(f"fetch('/api{prefix}/events',{auth}).then(r=>r.json())")
            safe = json.dumps(data, ensure_ascii=False)
            assert (
                "BATCH8_NPC_PRIVATE_SENTINEL" not in safe
                and "rejected_actions" not in safe
                and "agent.teammate_decision" not in safe
            )
            publics.append([e for e in data["events"] if e["visibility"] == "public"])
            if cycle.get("requires_clarification"):
                wait_for(lambda: page.contains("需要澄清"))
        assert publics[0] == publics[1]

    def audit_actions(self, prefix):
        runs = self.request("GET", prefix + "/agent-runs")
        events = self.request("GET", prefix + "/events")["events"]
        plans = [
            self.request("GET", prefix + f"/cycles/{c['id']}/validation")
            for c in self.report["cycles"]
        ]
        public = ["# PUBLIC", ""]
        debug = ["# HOST_DEBUG", ""]
        for event in events:
            p = event["payload"]
            if event["visibility"] == "public":
                role = {
                    "action.submitted": "真人",
                    "keeper.narration": "KP",
                    "npc.spoke": "NPC",
                    "agent.spoke": "AI队友",
                    "agent.action_proposed": "AI队友",
                    "scene.updated": "场景",
                }.get(event["type"])
                if role:
                    public.append(
                        f"- #{event['seq']} [{role}] "
                        f"{p.get('text') or p.get('question') or p.get('scene_summary')}"
                    )
                elif event["type"] == "check.resolved":
                    public.append(f"- #{event['seq']} [检定] {p['display_text']}")
            for kind, label in [
                ("agent.narration_validated", "NARRATION"),
                ("agent.teammate_decision", "TEAMMATE"),
                ("agent.summary_stale", "SUMMARY"),
                ("agent.summary_rebuilt", "SUMMARY"),
                ("agent.context_supplemented", "CONTEXT"),
                ("module.scene_transition", "TRANSITION"),
            ]:
                if event["type"] == kind:
                    debug.append(f"- [{label}] " + json.dumps(p, ensure_ascii=False))
        for doc in plans:
            p, v = doc["plan"], doc["validation"]
            i = p["parsed_intent"]
            debug.extend(
                [
                    f"- [INTENT] type={i['type']} target={i.get('target_id')} "
                    f"quote={i['evidence_quote']}",
                    f"- [PLAN] proposed_tools={p['proposed_tool_calls']} "
                    f"transition={p.get('proposed_transition_id')}",
                    f"- [POLICY] approved={v['approved_actions']} rejected={v['rejected_actions']}",
                    "- [CHECK] " + json.dumps(v["check_decisions"], ensure_ascii=False),
                    f"- [CONTEXT] supplemented={doc['supplement_attempted']}",
                    f"- [RECOVERY] {doc['recoveries']}",
                    f"- [NPC] entity={i.get('target_id') if i['type'] == 'converse' else None}",
                ]
            )
        for run in runs:
            if run["graph_node"] in {
                "generate_keeper_narration",
                "decide_teammates",
                "repair_teammate_decision",
            }:
                assert "BATCH8_NPC_PRIVATE_SENTINEL" not in json.dumps(run["context"])
        decisions = [e["payload"] for e in events if e["type"] == "agent.teammate_decision"]
        retrievals = [
            r
            for run in runs
            for r in self.request("GET", prefix + f"/agent-runs/{run['id']}/retrievals")
        ]
        self.report.update(
            check_count=sum(e["type"] == "check.resolved" for e in events),
            check_rejections=sum(
                not c["allowed"] for p in plans for c in p["validation"]["check_decisions"]
            ),
            structured_rules=sum(
                r["source_filters"].get("mode") == "structured" for r in retrievals
            ),
            rule_rag=sum(
                r["source_filters"].get("mode") == "rag" and r["source_filters"]["kind"] == "rules"
                for r in retrievals
            ),
            narration_fallbacks=sum(
                bool(e["payload"].get("safe_fallback"))
                for e in events
                if e["type"] == "keeper.narration"
            ),
            teammate_skips=sum(bool(d.get("deterministically_skipped")) for d in decisions),
            total_cycles=len(plans),
            intent_distribution=dict(Counter(p["plan"]["parsed_intent"]["type"] for p in plans)),
            clarifications=sum(e["type"] == "action.clarification_requested" for e in events),
            policy_rejections=sum(len(p["validation"]["rejected_actions"]) for p in plans),
            transition_requests=sum(bool(p["plan"]["proposed_transition_id"]) for p in plans),
            actual_transitions=sum(e["type"] == "module.scene_transition" for e in events),
            npc_speeches=sum(e["type"] == "npc.spoke" for e in events),
            teammate_modes=dict(Counter(d["mode"] for d in decisions)),
            teammate_rejections=sum(len(d["rejections"]) for d in decisions),
            duplicate_candidates=sum(
                r["reason"]
                in {
                    "repeated_output",
                    "target_action_cooldown",
                    "repeats_player_action",
                    "empty_template",
                }
                for d in decisions
                for r in d["rejections"]
            ),
            supplements=sum(p["supplement_attempted"] for p in plans),
            recoveries=[r for p in plans for r in p["recoveries"]],
            tool_errors=dict(
                Counter(t.get("code") for r in runs for t in r["tool_results"] if not t.get("ok"))
            ),
            summary_failures=sum(e["type"] == "agent.summary_stale" for e in events),
            summary_rebuilds=sum(e["type"] == "agent.summary_rebuilt" for e in events),
            ordinary_global_module_searches=sum(
                r["source_filters"]["kind"] == "module" for r in retrievals
            ),
            model_call_count=sum(len(r["model_calls"]) for r in runs),
        )
        for cycle in self.report["cycles"]:
            calls = [c for r in runs if r["cycle_id"] == cycle["id"] for c in r["model_calls"]]
            debug.append(
                "- [CALLS] "
                + json.dumps(
                    {
                        **cycle,
                        "actual_calls": len(calls),
                        "model_latency_ms": sum(c["latency_ms"] for c in calls),
                    },
                    ensure_ascii=False,
                )
            )
        for retrieval in retrievals:
            debug.append(
                "- [RULE_SOURCE] "
                + json.dumps(
                    {
                        "query": retrieval["query"],
                        "filters": retrieval["source_filters"],
                        "injected_ids": retrieval["injected_ids"],
                    },
                    ensure_ascii=False,
                )
            )
        adopted = [
            e["payload"]["text"]
            for e in events
            if e["type"] in {"agent.spoke", "agent.action_proposed"}
        ]
        self.report["public_duplicate_outputs"] = len(adopted) - len(set(adopted))
        from app.agents.behavior import bigram_jaccard
        from app.config import Settings

        threshold = Settings(_env_file=None).teammate_similarity_threshold
        self.report["public_near_duplicate_pairs"] = sum(
            bigram_jaccard(text, previous) >= threshold
            for index, text in enumerate(adopted)
            for previous in adopted[max(0, index - 3) : index]
        )
        assert (
            self.report["public_duplicate_outputs"] == 0
            and self.report["public_near_duplicate_pairs"] == 0
            and self.report["ordinary_global_module_searches"] == 0
            and self.report["npc_speeches"] >= 1
        )
        public_text = "\n".join(public)
        assert "spot_hidden" not in public_text and "BATCH8_NPC_PRIVATE_SENTINEL" not in public_text
        self.report["public_internal_skill_leaks"] = 0
        (self.directory / "retrievals.json").write_text(
            json.dumps(retrievals, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.directory / "events.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.directory / "session.md").write_text(
            "\n".join(public + [""] + debug), encoding="utf-8"
        )
        (self.directory / "plans.json").write_text(
            json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8"
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
        raise ValueError("reviewed_real_configuration_required")
    check = AdjudicationCheck(args.real, config)
    print("DIRECTORY=" + str(check.directory.relative_to(ROOT)), flush=True)
    try:
        check.run()
    finally:
        check.close()
    for port in (8000, 5173):
        port_free(port)
    print("TEMPORARY_PORTS_RELEASED=8000,5173", flush=True)


if __name__ == "__main__":
    main()
