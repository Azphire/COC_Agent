"""Real local-model streaming acceptance in two independent Chrome sessions.

Run from backend: python scripts/validate_batch43.py --name run-01
Uses only fresh batch-43 databases, reviewed local sources, and loopback services.
Fault injection belongs to the deterministic tests, never this real-model run.
"""

import argparse
import base64
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from check_character_creation import BACKEND, ROOT, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage

BATCH42 = ROOT / "data/prepared/zhihulu/batch-42"
ORIGINALS = ROOT / "data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/short-run-01"
OUTPUT = ROOT / "data/prepared/zhihulu/batch-43"
BACKEND_PORT, FRONTEND_PORT = 8043, 5143


def read(path):
    return json.loads(path.read_text("utf-8"))


def write(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", "utf-8")


def serve(directory):
    import uvicorn

    from app.config import Settings
    from app.main import create_app
    from app.models.settings import ModelSettings

    configured = Settings()
    ModelSettings(configured)
    endpoint = urlsplit(configured.model_base_url)
    if configured.model_provider != "ollama" or endpoint.hostname not in {
        "127.0.0.1", "localhost", "::1",
    }:
        raise ValueError("Real acceptance requires the existing local Ollama endpoint")
    settings = Settings(**{
        **configured.model_dump(),
        "host_admin_token": os.environ["BATCH43_HOST_TOKEN"],
        "database_url": "sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
        "checkpoint_db_path": directory / "checkpoint.db",
        "knowledge_db_path": directory / "knowledge.db",
        "model_settings_path": directory / "model-settings.json",
    })
    write(directory / "effective-config.json", {
        "provider": settings.model_provider, "model": settings.model_name,
        "base_url": settings.model_base_url,
        "mode": "real model; no substituted generation, dice or action receipts",
    })
    uvicorn.run(create_app(settings), host="127.0.0.1", port=BACKEND_PORT, log_level="warning")


# Mutation timestamps are recorded in the browser at the DOM update, not when
# Python happens to poll it. Reconnect is triggered only after visible text.
OBSERVER = r"""(() => {
  window.streamAudit = { armed: false, reconnect: false, observations: [], frames: [] };
  const seen = new Map();
  const audit = () => {
    const state = window.streamAudit;
    if (!state.armed) return;
    for (const node of document.querySelectorAll('[data-testid="keeper-stream"]')) {
      const text = node.querySelector('p.preserve-lines')?.textContent || '';
      const key = node.dataset.streamId + ':' + node.dataset.streamIndex + ':' + text;
      if (seen.has(key)) continue;
      seen.set(key, true);
      state.observations.push({at:Date.now()/1000, cycle_id:node.dataset.cycleId,
        stream_id:node.dataset.streamId, index:Number(node.dataset.streamIndex),
        attempt:Number(node.dataset.streamAttempt), text, status:node.dataset.streamStatus});
      if (text && state.reconnect && !state.disconnected_at) {
        state.disconnected_at = Date.now()/1000;
        window.roomSockets.at(-1)?.close();
      }
    }
  };
  new MutationObserver(audit).observe(document.body, {
    subtree:true, childList:true, characterData:true, attributes:true,
    attributeFilter:['data-stream-index', 'data-stream-status']
  });
  const observeSocket = socket => {
    socket.addEventListener('message', event => {
        const frame = JSON.parse(event.data);
        if (window.streamAudit.armed &&
            (frame.type.startsWith('keeper.stream.') || frame.type === 'room.synced')) {
          window.streamAudit.frames.push({at:Date.now()/1000, ...frame});
        }
    });
  };
  for (const socket of window.roomSockets || []) observeSocket(socket);
  const OriginalSocket = window.WebSocket;
  window.WebSocket = class extends OriginalSocket {
    constructor(...args) {
      super(...args);
      if (String(args[0]).includes('/ws/rooms/')) observeSocket(this);
    }
  };
})()"""


class StreamingCheck(SmokeCheck):
    def __init__(self, name):
        root = OUTPUT.resolve()
        self.directory = (root / name).resolve()
        assert self.directory.is_relative_to(root) and self.directory != root
        self.directory.mkdir(parents=True, exist_ok=False)
        self.processes, self.logs, self.pages = [], [], []
        self.host_token = secrets.token_urlsafe(32)
        self.auth = {"host": self.host_token}
        self.http = httpx.Client(trust_env=False, timeout=60)
        self.frontend_url = f"http://127.0.0.1:{FRONTEND_PORT}"
        self.result = {"status": "failed", "mode": "real_local_ollama", "model_substitute": False}
        self.prefix = ""
        self.cards = {}

    def start(self, command, cwd, name):
        log = (self.directory / f"{name}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        process = subprocess.Popen(
            command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                 "BATCH43_HOST_TOKEN": self.host_token,
                 "COC_BACKEND_PORT": str(BACKEND_PORT),
                 "COC_FRONTEND_PORT": str(FRONTEND_PORT)},
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.processes.append((name, process))
        return process

    def request(self, method, path, body=None, actor="host"):
        response = self.http.request(
            method, f"http://127.0.0.1:{BACKEND_PORT}/api" + path, json=body,
            headers={"Authorization": "Bearer " + self.auth[actor]},
        )
        assert response.is_success, f"{method} {path}: {response.status_code} {response.text[:500]}"
        if "ndjson" in response.headers.get("content-type", ""):
            return [json.loads(line) for line in response.text.splitlines() if line]
        return response.json()

    def setup(self):
        imported = self.request("POST", "/module-preparations/import",
                                read(BATCH42 / "package-reviewed.json"))
        pid = imported["preparation_id"]
        created = self.request("POST", "/rooms", {"name": "第43批 KP真实流式验收"})
        room_id = created["room"]["id"]
        self.prefix = "/rooms/" + room_id
        self.result.update(room_id=room_id, preparation_id=pid)
        self.request("PATCH", self.prefix + "/module-preparation", {"preparation_id": pid})
        for ho, name in (("HO1", "林月"), ("HO3", "周晟")):
            original = read(ORIGINALS / (name + "-frozen.json"))
            card = self.request("POST", "/characters/point-buy", {
                "ruleset_id": original["ruleset_id"], "name": name, "age": 19,
                "attributes": original["attributes"],
            })
            path = "/characters/" + card["id"]
            patch = {k: original[k] for k in (
                "occupation", "occupation_group_choices", "selected_specializations",
                "occupation_skills", "interest_skills", "age_deductions", "era",
            )}
            patch.update(
                version=card["version"],
                module_handout={"preparation_id": pid, "handout_id": ho,
                                "attribute_allocations": {}},
                background={**original["background"],
                            "appearance": "19岁女生" if ho == "HO1" else "19岁男生"},
            )
            card = self.request("PATCH", path, patch)
            card = self.request("PATCH", path,
                                {"version": card["version"], "approve_module_handout": True})
            card = self.request("POST", path + "/finalize", {"version": card["version"]})
            self.cards[ho] = card
            joined = self.request("POST", "/rooms/join", {
                "invite_code": created["invite_code"], "display_name": name,
            })
            self.auth[ho] = joined["member_token"]
            member_id = joined["room"]["self_member_id"]
            view = self.request("POST", self.prefix + "/character-slots",
                                {"character_id": card["id"]})["room"]
            slot_id = next(s["id"] for s in view["character_slots"]
                           if s["source_character_id"] == card["id"])
            self.request("POST", self.prefix + "/character-assignments",
                         {"slot_id": slot_id, "member_id": member_id})
            self.request("POST", self.prefix + "/handout-assignments", {
                "handout_id": ho, "slot_id": slot_id, "member_id": member_id,
                "client_request_id": str(uuid4()),
            })
            self.request("POST", self.prefix + "/ready", {"ready": True}, actor=ho)
            page = BrowserPage(self, ho)
            self.pages.append(page)
            # RoomSession normally reads localStorage. Supply its credential
            # through an in-memory getter so Chrome never writes member tokens
            # into the retained test profile. HTTP/WS auth remains unchanged.
            page.evaluate("""(() => {
                const key = %s, token = %s, original = Storage.prototype.getItem;
                Storage.prototype.getItem = function(name) {
                  return this === localStorage && name === key
                    ? token : original.call(this, name);
                };
            })()""" % (
                json.dumps("coc.room." + room_id), json.dumps(self.auth[ho]),
            ))
            page.navigate("#/rooms/" + room_id)
            wait_for(page.connected)
            page.evaluate(OBSERVER)
        profile = self.request("POST", "/agent-profiles", {
            "role": "keeper", "name": "KP", "background": "现代中国生日宴，19岁学生",
            "personality": "准确，自然，认真回应玩家眼前的观察",
            "goals": "只依据已公开事实和实际回执叙述。",
            "speaking_style": "中文。观察回应按玩家要求写三个以句号结尾的完整句子，"
                              "依次描述场所、人物、眼前动作；三句全部写入公开KP正文，"
                              "不能只放附带细节；只依公开来源与回执。",
        })
        self.request("POST", self.prefix + "/agent-bindings", {
            "member_id": created["room"]["host_member_id"], "profile_id": profile["id"],
        })
        self.request("POST", self.prefix + "/start")
        self.settle()

    def settle(self, timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            view = self.request("GET", self.prefix)
            cycle = (view.get("game") or {}).get("cycle") or {}
            status = cycle.get("status")
            if status not in {"running", "queued", "queued_action", "waiting_for_roll"}:
                assert status not in {"failed", "waiting_for_review"}, cycle
                return cycle
            if status == "waiting_for_roll":
                # Follow the ordinary choice API with original random dice;
                # never retry an action in pursuit of a more convenient result.
                for check in self.request("GET", self.prefix + "/checks"):
                    if check["status"] != "pending":
                        continue
                    actor = next((ho for ho in self.cards if
                                  self.request("GET", self.prefix, actor=ho)["self_member_id"]
                                  == check["target_member_id"]), "host")
                    stage = (check.get("settlement") or {}).get("stage")
                    choice = stage in {"choice", "awaiting_choice"}
                    self.request("POST", self.prefix + "/checks/" + check["id"]
                                 + ("/choice" if choice else "/roll"),
                                 {"operation": "accept"} if choice else {}, actor=actor)
            time.sleep(0.1)
        raise TimeoutError("The original real-model action did not finish")

    def persisted(self, cycle_id):
        database = self.directory / "game.db"
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(room_events)")}
            if not columns:
                return None
            for seq, payload, occurred_at in db.execute(
                "SELECT seq,payload,occurred_at FROM room_events "
                "WHERE room_id=? AND type='keeper.narration' ORDER BY seq DESC",
                (self.result["room_id"],),
            ):
                data = json.loads(payload)
                if data.get("cycle_id") == cycle_id and not data.get("check_invitation"):
                    return {"seq": seq, "payload": data, "occurred_at": occurred_at,
                            "observed_persisted_at": time.time()}
        return None

    def model_calls(self):
        database = self.directory / "game.db"
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
            return [json.loads(r[0]) for r in db.execute("SELECT document FROM agent_model_calls")]

    def privacy(self):
        checks = []
        for ho, page in zip(self.cards, self.pages):
            view = self.request("GET", self.prefix, actor=ho)
            assert [a["handout_id"] for a in view["handouts"]["assignments"]] == [ho]
            channels = {
                "http": view,
                "events": self.request("GET", self.prefix + "/events", actor=ho),
                "export": self.request("GET", self.prefix + "/logs", actor=ho),
            }
            for kind, data in channels.items():
                encoded = json.dumps(data, ensure_ascii=False)
                for other, card in self.cards.items():
                    if other != ho:
                        secret = card["module_handout"]["definition"]["text"]
                        assert json.dumps(secret, ensure_ascii=False)[1:-1] not in encoded
                checks.append({"member": ho, "channel": kind, "other_ho_absent": True})
            # Return booleans only. Repeated snapshots can exceed CDP's 1 MiB
            # frame budget, and private HO data does not belong in this audit.
            assert page.evaluate("window.roomFrames.every(f => f.type !== 'room.snapshot' "
                                 "|| f.data.handouts.available.length === 0)")
            for other, card in self.cards.items():
                if other == ho:
                    continue
                secret = card["module_handout"]["definition"]["text"]
                assert page.evaluate("!JSON.stringify(window.roomFrames).includes(" +
                                     json.dumps(json.dumps(secret, ensure_ascii=False)[1:-1]) + ")")
            checks.append({"member": ho, "channel": "ws", "other_ho_absent": True})
        return checks

    def run(self):
        for port in (BACKEND_PORT, FRONTEND_PORT):
            port_free(port)
        self.start([sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)],
                   BACKEND, "backend")
        wait_for(lambda: self.http.get(f"http://127.0.0.1:{BACKEND_PORT}/api/health").is_success,
                 90)
        self.start([shutil.which("node"), str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                    "--host", "127.0.0.1"], ROOT / "frontend", "frontend")
        wait_for(lambda: self.http.get(self.frontend_url).is_success, 45)
        self.setup()
        for index, page in enumerate(self.pages):
            page.evaluate("window.streamAudit.armed=true; window.streamAudit.reconnect="
                          + ("true" if index else "false"))
        client_request_id = str(uuid4())
        action_text = (
            "我环顾别墅前院（公开正文请依次描述眼前环境、宾客和佣人并写成三个"
            "以句号结尾的完整句子且只描述眼前的公开事实而不回顾晚宴安排）。"
        )
        self.result["action_requested_at"] = time.time()
        submitted = self.request("POST", self.prefix + "/actions", {
            "text": action_text,
            "client_request_id": client_request_id,
        }, actor="HO1")
        cycle_id = ((submitted.get("room", {}).get("game") or {}).get("cycle") or {}).get("id")
        if not cycle_id:
            cycle_id = self.request("GET", self.prefix)["game"]["cycle"]["id"]
        self.result["cycle_id"] = cycle_id
        final = wait_for(lambda: self.persisted(cycle_id), 600)
        cycle = self.settle()
        write(self.directory / "final-narration.json", final)
        for index, page in enumerate(self.pages):
            wait_for(page.connected, 30)
            selector = f'[data-event-seq="{final["seq"]}"] p.preserve-lines'
            wait_for(lambda: page.text_at(selector) == final["payload"]["text"], 30)
            audit = page.evaluate("window.streamAudit")
            write(self.directory / f"member-{index + 1}-stream.json", audit)
            assert page.evaluate(
                f'document.querySelectorAll(\'[data-event-seq="{final["seq"]}"]\').length'
            ) == 1
            assert not page.evaluate("document.querySelector('[data-testid=keeper-stream]') "
                                     "?.dataset.cycleId === " + json.dumps(cycle_id))
            page.evaluate(f"document.querySelector({json.dumps(selector)})"
                          ".scrollIntoView({block:'center'})")
            screenshot = page.command("Page.captureScreenshot", {"format": "png"})
            (self.directory / f"member-{index + 1}-final.png").write_bytes(
                base64.b64decode(screenshot["data"])
            )
        calls = self.model_calls()
        keys = ("provider", "model", "schema", "attempt", "error_category",
                "request_started_at", "first_chunk_at", "model_finished_at",
                "first_validated_segment_at", "latency_ms", "token_usage")
        write(self.directory / "model-timings.json", [{k: c.get(k) for k in keys} for c in calls])
        audits = [page.evaluate("window.streamAudit") for page in self.pages]
        visible = [r for r in audits[0]["observations"] if r.get("cycle_id") == cycle_id
                   and r.get("text")]
        self.result["privacy"] = self.privacy()
        self.result["browser_errors"] = [e for page in self.pages for e in page.exceptions]
        self.result["final_event_seq"] = final["seq"]
        self.result["final_persisted_at"] = final["observed_persisted_at"]
        self.result["persistence_time_precision"] = "first SQLite-visible row, polling <= 100 ms"
        self.result["final_matches_two_member_history"] = True
        assert visible, "No validated draft reached the real browser before settlement"
        first = min(r["at"] for r in visible)
        narrated = [c for c in calls if c.get("schema") == "KeeperNarration"
                    and c.get("request_started_at", 0) >= self.result["action_requested_at"]]
        self.result["narration_received_full_user_text"] = bool(narrated) and all(
            any(action_text in message.get("content", "")
                for message in call.get("input_messages", []))
            for call in narrated
        )
        assert narrated and all(c.get("model_finished_at") for c in narrated), (
            "Narration timing metadata missing; see model-timings.json"
        )
        first_visible = min(visible, key=lambda r: r["at"])
        first_call = next(c for c in narrated if c["attempt"] == first_visible["attempt"])
        self.result.update(
            narration_request_started_at=min(c["request_started_at"] for c in narrated),
            displayed_attempt=first_visible["attempt"],
            request_started_at=first_call["request_started_at"],
            first_model_chunk_at=first_call.get("first_chunk_at"),
            first_displayed_at=first,
            model_finished_at=first_call["model_finished_at"],
            first_display_latency_ms=round((first - first_call["request_started_at"]) * 1000),
            first_display_before_completion=first < first_call["model_finished_at"],
            reconnect_at=audits[1].get("disconnected_at"),
        )
        assert self.result["first_display_before_completion"], self.result
        assert audits[1].get("disconnected_at"), "Second browser never disconnected midstream"
        snapshots = [f for f in audits[1]["frames"] if f["type"] == "keeper.stream.snapshot"
                     and f["at"] > audits[1]["disconnected_at"]]
        self.result["reconnected_current_stream_snapshots"] = len(snapshots)
        assert snapshots, "Reconnect completed only after the model; current-stream replay untested"
        actions = self.request("GET", self.prefix + "/logs", actor="HO1")
        actual = [e for e in actions if e["type"] == "action.submitted"
                  and e.get("client_request_id") == client_request_id]
        assert len(actual) == 1, actual
        settlements = [e for e in actions if e["payload"].get("cycle_id") == cycle_id
                       and e["type"] in {"module.interaction", "check.resolved", "scene.updated"}]
        settlement_keys = [(e["type"], e["payload"].get("id"),
                            e["payload"].get("source_event_seq"),
                            e["payload"].get("operation"),
                            e["payload"].get("target_id")) for e in settlements]
        assert len(settlement_keys) == len(set(settlement_keys))
        write(self.directory / "settlement-events.json", settlements)
        self.result.update(action_submissions=len(actual), cycle_status=cycle["status"],
                           settlement_events=len(settlements), duplicate_settlements=False)
        assert not self.result["browser_errors"]
        assert len([s for s in final["payload"]["text"].split("。") if s.strip()]) >= 2, (
            "Final real narration has fewer than two complete sentences"
        )
        assert not final["payload"].get("safe_fallback"), (
            "Real model ended in deterministic fallback"
        )
        self.result["status"] = "passed"
        print(json.dumps(self.result, ensure_ascii=False), flush=True)

    def close(self):
        for page in self.pages:
            try:
                # Credentials are not part of acceptance artifacts.
                page.evaluate("localStorage.clear(); sessionStorage.clear()")
            except Exception as error:
                self.result.setdefault("cleanup_errors", []).append(type(error).__name__)
            finally:
                if page.cdp:
                    try:
                        page.cdp.close()
                    except Exception as error:
                        self.result.setdefault("cleanup_errors", []).append(type(error).__name__)
        for _, process in reversed(self.processes):
            self.stop(process)
        for log in self.logs:
            log.close()
        self.http.close()
        write(self.directory / "acceptance.json", self.result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="run-01")
    parser.add_argument("--serve", type=Path)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments.serve)
    else:
        check = StreamingCheck(arguments.name)
        try:
            check.run()
        except Exception as error:
            check.result["error"] = str(error)
            raise
        finally:
            check.close()
