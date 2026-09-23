"""Fixed, source-valid Paper Chase acceptance; only existing local Ollama.

Fresh directories only. Setup, history padding, evidence and actions use normal
HTTP operations. No injected events, dice, receipts or model substitutes.
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import validate_batch43 as streaming
from check_character_creation import BACKEND, ROOT, port_free, wait_for
from check_multiplayer import BrowserPage
from validate_batch43 import OBSERVER, StreamingCheck, read, write

OUTPUT = ROOT / "data/prepared/batch-45"
PACKAGE = ROOT / "data/prepared/zhuishuren/batch-40/package-reviewed.json"
BACKEND_PORT, FRONTEND_PORT = 8045, 5145
streaming.OUTPUT = OUTPUT
streaming.BACKEND_PORT, streaming.FRONTEND_PORT = BACKEND_PORT, FRONTEND_PORT

FIXED_ACTION = (
    "我环顾道格拉斯的书房，查看书架上的空档和通向屋外的窗户。"
    "托马斯早先估计少了多少本书，他知道具体书名吗？"
    "请把眼前的两项观察和早先的证词分句回答，明确哪些是托马斯的估计。"
    "艾琳，请根据这些旧证词说明下一步还需要核对什么，不要声称已经查明窃贼。"
)
SETUP_ACTIONS = [
    "托马斯，请告诉我叔叔何时失踪、藏书估计少了多少本，以及你是否知道具体书名。",
    "我前往道格拉斯的书房。",
]
TASK_ACTION = "艾琳，请根据托马斯早先的说法查看书房窗户，核对窗锁是否松动。"
CRITERIA = {
    "preparation": "unchanged reviewed Paper Chase package; current study; early commission public",
    "memory": "early source older than event window, at least two actual summary segments",
    "messages": "complete player statement and early six-books/unknown-titles evidence",
    "plan": "study/window/books target; no unrelated target or unsupported completion",
    "answer": "body answers both observations and estimates six books, titles unknown, attributed",
    "teammate": "next verification remains pending, no invented completion or culprit",
    "stream": (
        "two different members show multiple growing body prefixes; reconnect before model ends"
    ),
    "history": "both final bodies exactly match the persisted event once",
    "effects": "zero receipts cannot prove a nonzero operation executed once",
}


def streaming_checks(audits, narration, final, cycle_id):
    body = final["payload"]["text"]
    stream = final["payload"].get("stream_id")
    attempt = final["payload"].get("stream_attempt")
    calls = [
        c
        for c in narration
        if c.get("attempt") == attempt and c.get("cycle_id", cycle_id) == cycle_id
    ]
    # Ambiguous timing is not evidence of generation-time visibility.
    call = calls[0] if len(calls) == 1 else {}
    start, end = call.get("request_started_at", 0), call.get("model_finished_at", 0)
    growth, monotonic = [], []
    for audit in audits:
        rows = [
            o
            for o in audit["observations"]
            if o.get("cycle_id") == cycle_id
            and o.get("stream_id") == stream
            and o.get("attempt") == attempt
            and o.get("text")
            and start <= o["at"] < (end or 0)
        ]
        text = ""
        prefixes = []
        valid = True
        for row in sorted(rows, key=lambda r: r["at"]):
            if not body.startswith(row["text"]) or not row["text"].startswith(text):
                valid = False
            if row["text"].startswith(text) and len(row["text"]) > len(text):
                text = row["text"]
                prefixes.append(row)
        growth.append(prefixes)
        monotonic.append(valid)
    reconnect = any(
        f["type"] == "keeper.stream.snapshot"
        and f["at"] > audits[1].get("disconnected_at", float("inf"))
        and start <= f["at"] < (end or 0)
        and (f.get("data") or {}).get("cycle_id") == cycle_id
        and (f.get("data") or {}).get("stream_id") == stream
        and (f.get("data") or {}).get("attempt") == attempt
        and (f.get("data") or {}).get("status") == "responding"
        for f in audits[1]["frames"]
    )
    return {
        "two_members_multiple_growing_prefixes": all(monotonic)
        and all(len(p) >= 2 for p in growth),
        "prefixes_never_retracted": monotonic,
        "reconnect_during_generation": reconnect,
        "matched_attempt": attempt,
        "matched_model_finished_at": end,
        "accepted_prefixes": growth,
    }


def serve(directory):
    from app.config import Settings
    from app.models.settings import ModelSettings

    configured = Settings()
    ModelSettings(configured)
    # Protect the module-level default app too, before importing app.main.
    for name, filename in (
        ("DATABASE_URL", "bootstrap.db"),
        ("CHECKPOINT_DB_PATH", "bootstrap-checkpoint.db"),
        ("KNOWLEDGE_DB_PATH", "bootstrap-knowledge.db"),
        ("MODEL_SETTINGS_PATH", "bootstrap-model-settings.json"),
    ):
        os.environ[name] = ("sqlite+aiosqlite:///" if name == "DATABASE_URL" else "") + (
            directory / filename
        ).as_posix()
    endpoint = urlsplit(configured.model_base_url)
    if configured.model_provider != "ollama" or endpoint.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("Only the existing loopback Ollama configuration is authorized")
    settings = Settings(
        **{
            **configured.model_dump(),
            "host_admin_token": os.environ["BATCH43_HOST_TOKEN"],
            "database_url": "sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
            "checkpoint_db_path": directory / "checkpoint.db",
            "knowledge_db_path": directory / "knowledge.db",
            "model_settings_path": directory / "model-settings.json",
        }
    )
    write(
        directory / "effective-config.json",
        {
            key: getattr(settings, key)
            for key in (
                "model_provider",
                "model_name",
                "model_base_url",
                "model_context_limit",
                "model_output_limit",
                "model_output_mode",
                "model_think",
                "model_keep_alive",
                "model_temperature",
                "model_timeout_seconds",
                "agent_context_chars",
                "agent_event_window",
                "summary_event_threshold",
                "summary_context_threshold",
            )
        },
    )
    import uvicorn

    from app.main import create_app

    uvicorn.run(create_app(settings), host="127.0.0.1", port=BACKEND_PORT, log_level="warning")


class FixedCheck(StreamingCheck):
    def __init__(self, name):
        super().__init__(name)
        self.frontend_url = f"http://127.0.0.1:{FRONTEND_PORT}"
        self.http.timeout = 180
        self.compression_results = []
        self.result.update(
            question=FIXED_ACTION,
            criteria=CRITERIA,
            setup_actions=SETUP_ACTIONS,
            preparation_status="not_started",
        )
        write(
            self.directory / "fixed-case.json",
            {
                "question": FIXED_ACTION,
                "criteria": CRITERIA,
                "setup_actions": SETUP_ACTIONS,
                "separate_task_case": TASK_ACTION,
                "package": str(PACKAGE.relative_to(ROOT)),
                "package_sha256": hashlib.sha256(PACKAGE.read_bytes()).hexdigest(),
                "history_padding": "normal public chat; dialogue only, never source evidence",
                "compression_preparation": (
                    "three padded requests, then at most three ordinary rebuild requests "
                    "until the keeper has two distinct successful coverage endpoints"
                ),
            },
        )
        write(
            self.directory / "code-manifest.json",
            {
                str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for folder in (ROOT / "backend/app", ROOT / "backend/scripts")
                for path in folder.rglob("*.py")
            },
        )

    def setup(self):
        imported = self.request("POST", "/module-preparations/import", read(PACKAGE))
        pid = imported["preparation_id"]
        created = self.request("POST", "/rooms", {"name": "第45批 追书人固定验收"})
        self.prefix = "/rooms/" + created["room"]["id"]
        self.result.update(room_id=created["room"]["id"], preparation_id=pid)
        self.request("PATCH", self.prefix + "/module-preparation", {"preparation_id": pid})
        self.members = {}
        for role, name in (("player", "丹尼尔"), ("witness", "观察成员"), ("agent", "艾琳")):
            if role == "agent":
                room = self.request(
                    "POST",
                    self.prefix + "/members",
                    {
                        "display_name": name,
                        "controller_type": "agent",
                    },
                )["room"]
                member = next(m["id"] for m in room["members"] if m["controller_type"] == "agent")
            else:
                joined = self.request(
                    "POST",
                    "/rooms/join",
                    {
                        "invite_code": created["invite_code"],
                        "display_name": name,
                    },
                )
                self.auth[role] = joined["member_token"]
                member = joined["room"]["self_member_id"]
            self.members[role] = member
            card = self.request(
                "POST",
                "/characters/point-buy",
                {
                    "ruleset_id": "coc7-character-creation",
                    "name": name,
                    "age": 25,
                    "attributes": {
                        k: {"value": v}
                        for k, v in dict(
                            str=60,
                            con=60,
                            siz=60,
                            dex=60,
                            app=50,
                            int=60,
                            pow=50,
                            edu=60,
                        ).items()
                    },
                },
            )
            path = "/characters/" + card["id"]
            card = self.request(
                "PATCH",
                path,
                {
                    "version": card["version"],
                    "occupation": "professor",
                    "era": "1920s",
                    "selected_occupation_skills": [
                        "accounting",
                        "anthropology",
                        "archaeology",
                        "history",
                    ],
                    "occupation_skills": {"credit_rating": {"points": 20}},
                },
            )
            card = self.request("POST", path + "/finalize", {"version": card["version"]})
            self.cards[role] = card
            room = self.request(
                "POST",
                self.prefix + "/character-slots",
                {
                    "character_id": card["id"],
                },
            )["room"]
            slot = next(
                s["id"] for s in room["character_slots"] if s["source_character_id"] == card["id"]
            )
            self.request(
                "POST",
                self.prefix + "/character-assignments",
                {
                    "slot_id": slot,
                    "member_id": member,
                },
            )
            self.request(
                "POST",
                self.prefix + "/ready",
                {
                    "ready": True,
                    **({"member_id": member} if role == "agent" else {}),
                },
                actor="host" if role == "agent" else role,
            )
        for role, member in (
            ("keeper", created["room"]["host_member_id"]),
            ("investigator", self.members["agent"]),
        ):
            profile = self.request(
                "POST",
                "/agent-profiles",
                {
                    "role": role,
                    "name": "KP" if role == "keeper" else "艾琳",
                    "speaking_style": "用自然中文逐项回应玩家的问题，区分过去的证词与眼前观察。",
                    "goals": "准确调查；未执行的核对保持待办，不虚构执行结果。",
                },
            )
            if role == "keeper":
                self.keeper_profile_id = profile["id"]
            self.request(
                "POST",
                self.prefix + "/agent-bindings",
                {
                    "member_id": member,
                    "profile_id": profile["id"],
                },
            )
        self.request("POST", self.prefix + "/start")
        self.settle()
        write(
            self.directory / "initial-public-entities.json",
            self.request("GET", self.prefix + "/public-entities", actor="player"),
        )
        for index, text in enumerate(SETUP_ACTIONS):
            self.act(text)
            write(
                self.directory / f"setup-{index + 1}.json",
                {
                    "text": text,
                    "events": self.request("GET", self.prefix + "/logs", actor="player"),
                    "room": self.request("GET", self.prefix, actor="player"),
                },
            )
        # No fabricated clues: these messages only create an ordinary conversation
        # history longer than the configured window. Its purpose is recorded.
        configured = read(self.directory / "effective-config.json")
        for batch in range(3):
            for index in range(configured["agent_event_window"] // 2 + 2):
                self.request(
                    "POST",
                    self.prefix + "/messages",
                    {
                        "text": (
                            f"调查记录 {batch + 1}-{index + 1}：我仍在书房等候，暂不作新的判断。"
                        ),
                        "client_request_id": str(uuid4()),
                    },
                    actor="player",
                )
            summary = self.request("POST", self.prefix + "/summary-rebuild")
            self.compression_results.append(summary)
            write(self.directory / f"compression-{batch + 1}.json", summary)
        # The endpoint advances one reader per request. Reader order is not a
        # guarantee that three requests produced two keeper compressions.
        while self.keeper_compressions() < 2 and len(self.compression_results) < 6:
            summary = self.request("POST", self.prefix + "/summary-rebuild")
            self.compression_results.append(summary)
            write(self.directory / f"compression-{len(self.compression_results)}.json", summary)
        self.preflight(configured)
        self.open_windows()

    def open_windows(self):
        for role in ("player", "witness"):
            page = BrowserPage(self, role)
            self.pages.append(page)
            page.evaluate(
                """(() => {
                const key=%s, token=%s, original=Storage.prototype.getItem;
                Storage.prototype.getItem=function(name) {
                    return this===localStorage && name===key ? token : original.call(this,name);
                };
            })()"""
                % (json.dumps("coc.room." + self.result["room_id"]), json.dumps(self.auth[role]))
            )
            page.navigate("#/rooms/" + self.result["room_id"])
            wait_for(page.connected)
            page.evaluate(OBSERVER)
            page.evaluate("""(() => {
                window.publicDomAudit = [];
                const seen = new Set();
                new MutationObserver(() => {
                    if (!window.streamAudit.armed) return;
                    for (const node of document.querySelectorAll('[data-event-seq]')) {
                        const seq = Number(node.dataset.eventSeq);
                        const text = node.querySelector('p.preserve-lines')?.textContent || '';
                        if (!text || seen.has(seq)) continue;
                        seen.add(seq);
                        window.publicDomAudit.push({at:Date.now()/1000, seq, text});
                    }
                }).observe(document.body, {subtree:true, childList:true, characterData:true});
            })()""")

    def act(self, text, *, cycle_field=None):
        submitted = self.request(
            "POST",
            self.prefix + "/actions",
            {
                "text": text,
                "client_request_id": str(uuid4()),
            },
            actor="player",
        )
        if cycle_field:
            self.result[cycle_field] = submitted["room"]["game"]["cycle"]["id"]
        self.settle()
        return submitted

    def model_calls(self):
        with sqlite3.connect(
            f"file:{(self.directory / 'game.db').as_posix()}?mode=ro", uri=True
        ) as db:
            return [
                {**json.loads(document), "cycle_id": cycle_id}
                for document, cycle_id in db.execute(
                    "SELECT c.document,r.cycle_id FROM agent_model_calls c "
                    "JOIN agent_runs r ON c.run_id=r.id ORDER BY r.created_at,c.id"
                )
            ]

    def keeper_compressions(self):
        return len(
            {
                row["last_successful_summary_seq"]
                for summary in self.compression_results
                for row in summary
                if row["profile_id"] == self.keeper_profile_id
                and not row["stale"]
                and row["last_successful_summary_seq"] > 0
            }
        )

    def preflight(self, configured):
        room = self.request("GET", self.prefix, actor="player")
        entities = self.request("GET", self.prefix + "/public-entities", actor="player")
        events = self.request("GET", self.prefix + "/logs", actor="player")
        preparation = self.request("GET", "/module-preparations/" + self.result["preparation_id"])
        package = read(PACKAGE)
        from app.memory.events import story_events

        story, _ = story_events(events, include_initial_reveals=True)
        source = next(
            (
                e
                for e in story
                if e["type"] == "entity.revealed"
                and "估计少了六本" in json.dumps(e["payload"], ensure_ascii=False)
            ),
            None,
        )
        with sqlite3.connect(self.directory / "game.db") as db:
            segments = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT content FROM agent_memories WHERE kind='summary_segment' AND active=1"
                )
            ]
            binding = dict(
                zip(
                    [
                        c[0]
                        for c in db.execute(
                            "SELECT * FROM room_module_preparation_bindings LIMIT 1"
                        ).description
                    ],
                    db.execute("SELECT * FROM room_module_preparation_bindings LIMIT 1").fetchone(),
                )
            )
        checks = {
            "matching_preparation": (
                binding["preparation_id"] == self.result["preparation_id"]
                and binding["source_hash"] == package["knowledge"]["source"]["source_hash"]
                and binding["preparation_version"] == preparation["version"]
                and preparation["status"] == "approved"
            ),
            "current_study": room["session_state"].get("scene_title") == "道格拉斯的书房",
            "study_public": any(
                e["id"] == binding["current_scene"]
                and e["title"] == "道格拉斯的书房"
                and e.get("fact_scope") == "current_scene"
                and "空档" in e["public_summary"]
                and "窗户" in e["public_summary"]
                for e in entities
            ),
            "early_source_public": source is not None,
            "outside_window": bool(
                source and source["seq"] < story[-configured["agent_event_window"]]["seq"]
            ),
            "multiple_compressions": self.keeper_compressions() >= 2,
        }
        preflight = {
            "checks": checks,
            "binding": binding,
            "room": room,
            "public_entities": entities,
            "early_source": source,
            "segments": segments,
            "task_scope": "Main case requests advice, not execution; separate task case follows.",
            "status": "passed" if all(checks.values()) else "preparation_failed",
        }
        write(self.directory / "preflight.json", preflight)
        self.result["preparation_status"] = preflight["status"]
        if not all(checks.values()):
            raise AssertionError("验收准备失败，不调用固定验收问题: " + json.dumps(checks))

    def run(self):
        for port in (BACKEND_PORT, FRONTEND_PORT):
            port_free(port)
        self.start(
            [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)],
            BACKEND,
            "backend",
        )
        wait_for(
            lambda: self.http.get(f"http://127.0.0.1:{BACKEND_PORT}/api/health").is_success, 90
        )
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
        wait_for(lambda: self.http.get(self.frontend_url).is_success, 45)
        self.setup()
        for index, page in enumerate(self.pages):
            page.evaluate(
                "window.streamAudit.armed=true;window.streamAudit.reconnect="
                + ("true" if index else "false")
            )
        self.result["action_requested_at"] = time.time()
        submitted = self.act(FIXED_ACTION, cycle_field="cycle_id")
        cycle_id = submitted["room"]["game"]["cycle"]["id"]
        self.result["cycle_id"] = cycle_id
        final = self.persisted(cycle_id)
        if final is None:
            raise AssertionError("固定题回合结束但没有KP正文；检查澄清或拒绝事件")
        write(self.directory / "final-narration.json", final)
        audits = []
        for index, page in enumerate(self.pages):
            wait_for(page.connected, 30)
            selector = f'[data-event-seq="{final["seq"]}"] p.preserve-lines'
            wait_for(lambda: page.text_at(selector) == final["payload"]["text"], 30)
            audit = page.evaluate("window.streamAudit")
            audit["formal_observations"] = page.evaluate("window.publicDomAudit")
            audit["final_text"] = page.text_at(selector)
            audit["final_bubbles"] = page.evaluate(
                f"document.querySelectorAll('[data-event-seq=\"{final['seq']}\"]').length"
            )
            audits.append(audit)
            write(self.directory / f"member-{index + 1}-stream.json", audit)
            page.evaluate(
                f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block:'center'}})"
            )
            screenshot = page.command("Page.captureScreenshot", {"format": "png"})
            (self.directory / f"member-{index + 1}-final.png").write_bytes(
                base64.b64decode(screenshot["data"])
            )
        calls = self.model_calls()
        write(self.directory / "model-calls.json", calls)
        public = self.request("GET", self.prefix + "/logs", actor="player")
        write(self.directory / "public-events.json", public)
        write(
            self.directory / "current-receipts.json",
            [
                e
                for e in public
                if e["payload"].get("cycle_id") == cycle_id
                and e["type"]
                in {
                    "module.interaction",
                    "check.resolved",
                    "scene.updated",
                    "entity.revealed",
                    "clue.revealed",
                    "action.result",
                }
            ],
        )
        with sqlite3.connect(
            f"file:{(self.directory / 'game.db').as_posix()}?mode=ro", uri=True
        ) as db:
            plan = db.execute(
                "SELECT document FROM agent_action_plans WHERE cycle_id=?", (cycle_id,)
            ).fetchone()
        write(self.directory / "validated-plan.json", json.loads(plan[0]) if plan else None)
        write(
            self.directory / "behavior.json",
            self.request("GET", self.prefix + "/teammate-behavior"),
        )
        relevant = [
            c for c in calls if c.get("request_started_at", 0) >= self.result["action_requested_at"]
        ]
        write(self.directory / "fixed-turn-calls.json", relevant)
        narration = [
            c
            for c in relevant
            if c.get("schema") == "KeeperNarration" and c.get("cycle_id") == cycle_id
        ]
        observations = [
            [o for o in a["observations"] if o.get("cycle_id") == cycle_id and o.get("text")]
            for a in audits
        ]
        stream_result = streaming_checks(audits, narration, final, cycle_id)
        write(self.directory / "stream-assessment.json", stream_result)
        first = min((o["at"] for o in observations[0]), default=None)
        response_seqs = {
            e["seq"]
            for e in public
            if e["type"] in {"keeper.narration", "npc.spoke", "agent.spoke"}
            and e["payload"].get("cycle_id") == cycle_id
        }
        first_public = min(
            [
                o["at"]
                for o in audits[0]["formal_observations"]
                if o["seq"] in response_seqs and o["at"] >= self.result["action_requested_at"]
            ]
            + ([first] if first else []),
            default=None,
        )
        checks = {
            "full_request_in_narration": bool(narration)
            and all(
                FIXED_ACTION in json.dumps(c.get("transmitted_messages", []), ensure_ascii=False)
                for c in narration
            ),
            "early_evidence_in_final_messages": bool(narration)
            and all(
                "估计少了六本" in json.dumps(c.get("transmitted_messages", []), ensure_ascii=False)
                and "不知道具体书名"
                in json.dumps(c.get("transmitted_messages", []), ensure_ascii=False)
                for c in narration
            ),
            "multiple_body_sentences": len(
                [s for s in final["payload"]["text"].split("。") if s.strip()]
            )
            >= 2,
            **{
                key: stream_result[key]
                for key in ("two_members_multiple_growing_prefixes", "reconnect_during_generation")
            },
            "final_history_equal": all(
                a["final_text"] == final["payload"]["text"] and a["final_bubbles"] == 1
                for a in audits
            ),
            "no_fallback": not final["payload"].get("safe_fallback"),
        }
        self.result.update(
            checks=checks,
            final_text=final["payload"]["text"],
            first_dom_seconds=first - self.result["action_requested_at"] if first else None,
            first_public_dom_seconds=(
                first_public - self.result["action_requested_at"] if first_public else None
            ),
            browser_errors=[e for p in self.pages for e in p.exceptions],
            answer_accuracy="requires separate source/plan/receipt/body assessment",
            status="captured",
        )
        print(json.dumps(self.result, ensure_ascii=False), flush=True)
        write(self.directory / "main-captured-result.json", self.result)
        self.task_case()

    def capture_failure(self):
        """Keep all available evidence even when no narration was produced."""
        if not self.prefix or not (self.directory / "game.db").exists():
            return

        def preserve(name, value):
            if not (self.directory / name).exists():
                write(self.directory / name, value)

        calls = self.model_calls()
        preserve("model-calls.json", calls)
        events = self.request("GET", self.prefix + "/logs", actor="player")
        preserve("public-events.json", events)
        start = self.result.get("action_requested_at")
        if start is not None:
            preserve(
                "fixed-turn-calls.json",
                [c for c in calls if c.get("request_started_at", 0) >= start],
            )
        cycle = self.result.get("cycle_id")
        responses = {
            e["seq"]
            for e in events
            if e["payload"].get("cycle_id") == cycle
            and e["type"]
            in {"keeper.narration", "npc.spoke", "agent.spoke", "action.clarification_requested"}
        }
        for index, page in enumerate(self.pages):
            audit = page.evaluate("window.streamAudit || null") or {}
            audit["formal_observations"] = page.evaluate("window.publicDomAudit || []")
            preserve(f"failure-member-{index + 1}.json", audit)
            if index == 0 and start is not None and "first_public_dom_seconds" not in self.result:
                first = min(
                    (
                        o["at"]
                        for o in audit["formal_observations"]
                        if o["seq"] in responses and o["at"] >= start
                    ),
                    default=None,
                )
                self.result["first_public_dom_seconds"] = first - start if first else None
        if cycle:
            with sqlite3.connect(
                f"file:{(self.directory / 'game.db').as_posix()}?mode=ro", uri=True
            ) as db:
                plan = db.execute(
                    "SELECT document FROM agent_action_plans WHERE cycle_id=?", (cycle,)
                ).fetchone()
            preserve("validated-plan.json", json.loads(plan[0]) if plan else None)
        preserve("failure-public-responses.json", [e for e in events if e["seq"] in responses])
        preserve("failure-behavior.json", self.request("GET", self.prefix + "/teammate-behavior"))

    def task_case(self):
        """A separate normal delegation; advice is never labelled an executed task."""
        before = self.request("GET", self.prefix + "/logs", actor="player")
        start = time.time()
        result = {
            "request": TASK_ACTION,
            "submitted_at": start,
            "status": "submitted",
        }
        try:
            self.act(TASK_ACTION, cycle_field="task_cycle_id")
            result["status"] = "captured_requires_receipt_assessment"
        except Exception as error:
            result.update(status="failed", error=str(error))
            raise
        finally:
            events = self.request("GET", self.prefix + "/logs", actor="player")
            result.update(
                cycle_id=self.result.get("task_cycle_id"),
                events=[e for e in events if e["seq"] > before[-1]["seq"]],
                behavior=self.request("GET", self.prefix + "/teammate-behavior"),
                model_calls=[
                    c for c in self.model_calls() if c.get("request_started_at", 0) >= start
                ],
            )
            write(self.directory / "task-case.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name")
    parser.add_argument("--serve", type=Path)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return
    if not args.name:
        parser.error("--name is required")
    check = FixedCheck(args.name)
    try:
        check.run()
    except Exception as error:
        check.result.update(error=str(error), status="failed")
        try:
            check.capture_failure()
        except Exception as capture_error:
            check.result["capture_error"] = str(capture_error)
        raise
    finally:
        check.close()


if __name__ == "__main__":
    main()
