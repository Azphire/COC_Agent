"""Resumable formal-package short game; local Ollama by default, original dice only.

python backend/scripts/check_batch25.py ollama-c1 --live --provider ollama
Use --resume for the same journal; --retry-failed explicitly resumes its original cycle.
No product model manager or alternate inference path is introduced.
"""

import argparse
import ctypes
import hashlib
import ipaddress
import json
import os
import signal
import sqlite3
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from check_batch23_api import HOST, ApiCheck
from check_batch25_evidence import (
    BATCH,
    ROOT,
    audit,
    comparison,
    database_evidence,
    owned_item,
    read,
    turn_response,
)

from app.config import Settings
from app.models.credentials import resolve_credential
from app.models.settings import ModelConfiguration, ModelSettings

PACKAGE = ROOT / "data/prepared/changan/batch-21/package-approved.json"
PACKAGE_HASH = "93dee39824e41a0d9544fc5e869845c0ffcaeb43dd54603aec1eae840372ddd4"


def write(path, value):
    """Atomically persist request IDs and stages before any effectful operation."""
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
    ) as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(file.name, path)


def select_model(provider, model=None, base_url=None, output_mode=None, authorized=False):
    settings = Settings()
    manager = ModelSettings(settings)
    selected = manager.configurations.get(provider)
    if provider == "openai" and authorized and not selected:
        previous = read(ROOT / "data/prepared/changan/batch-24/live-c4/result.json")
        saved = previous["launcher"][-1]["configuration"]
        selected = ModelConfiguration(
            **{k: saved[k] for k in ("provider", "model", "base_url", "output_mode")}
        )
    if not selected:
        raise ValueError(f"No existing {provider} configuration")
    selected = selected.model_copy(
        update={
            k: v
            for k, v in dict(model=model, base_url=base_url, output_mode=output_mode).items()
            if v is not None
        }
    )
    url = urlsplit(selected.base_url)
    if provider == "ollama":
        # Literal loopback only: no DNS, proxies or redirects to an external endpoint.
        if not ipaddress.ip_address(url.hostname).is_loopback or url.scheme != "http":
            raise ValueError("Ollama must use a literal local loopback HTTP address")
    elif not authorized or selected.base_url.rstrip("/") != "https://api.openai.com/v1":
        raise ValueError("Formal OpenAI game requires specific user destination/data authorization")
    return settings, selected, resolve_credential(settings, selected)


class Batch25(ApiCheck):
    batch_directory = BATCH
    audit_report = staticmethod(audit)

    def persist(self):
        write(self.directory / "result.json", self.result)

    def __init__(self, args):
        self.args = args
        batch = self.batch_directory.resolve()
        self.directory = (batch / args.run).resolve()
        assert self.directory.is_relative_to(batch) and self.directory != batch
        self.directory.mkdir(parents=True, exist_ok=args.resume)
        assert hashlib.sha256(PACKAGE.read_bytes()).hexdigest() == PACKAGE_HASH
        initial, selected, credential = select_model(
            args.provider,
            args.model,
            args.base_url,
            args.output_mode,
            args.authorize_openai_formal_context,
        )
        self.api = selected.model_dump(mode="json", exclude={"api_key", "api_key_source"})
        self.api["api_key"] = ""
        self.port, self.front = args.backend_port, args.frontend_port
        self.frontend_url = f"http://127.0.0.1:{self.front}"
        self.processes, self.logs = [], []
        self.launcher = self.page = self.prefix = self.player = None
        self.run = 0
        self.http = httpx.Client(
            trust_env=False, timeout=600, headers={"Authorization": "Bearer " + HOST}
        )
        source = ROOT / "data/prepared/changan/batch-24/live-c4" if args.source_batch24 else None
        if source and args.provider != "openai":
            raise ValueError("The existing batch-24 room is reserved for authorized OpenAI retest")
        self.storage = source or self.directory
        self.database_path = self.storage / "game.db"
        if source and not args.resume:
            for filename in ("game.db", "checkpoint.db"):
                with sqlite3.connect((source / filename).as_uri() + "?mode=ro", uri=True) as src:
                    with sqlite3.connect(self.directory / ("before-" + filename)) as dst:
                        src.backup(dst)
        self.result = dict(
            configuration={**selected.public(credential)},
            package_sha256=PACKAGE_HASH,
            database_path=str(self.database_path),
            turns=[],
            steps={},
            launcher=[],
            trials=[],
            status="not_run",
            baseline={},
        )
        if args.resume:
            self.result = read(self.directory / "result.json")
            if any(
                self.result["configuration"][k] != getattr(selected, k)
                for k in ("provider", "model", "base_url", "output_mode")
            ):
                raise ValueError("Resume model configuration must match the recorded run")
            if self.result["database_path"] != str(self.database_path):
                raise ValueError("Resume must retain the same database/source-batch24 option")
            write(self.directory / f"result-before-resume-{time.time_ns()}.json", self.result)
            self.run = len(self.result["launcher"])
        if args.resume or source:
            session = read((self.directory if args.resume else source) / "session.json")
            self.prefix, self.player = session["prefix"], session["token"]
            if source:
                assert self.prefix == "/rooms/d11069af-bc06-4c80-836f-4497be6eb0b8"
        self.env = {
            **os.environ,
            "DATA_DIR": str(ROOT / "data"),
            "DATABASE_URL": "sqlite+aiosqlite:///" + self.database_path.as_posix(),
            "CHECKPOINT_DB_PATH": str(self.storage / "checkpoint.db"),
            "KNOWLEDGE_DB_PATH": str(self.storage / "knowledge.db"),
            "MODEL_SETTINGS_PATH": str(self.directory / "host-model-settings.json"),
            "APP_PORT": str(self.port),
            "APP_HOST": "127.0.0.1",
            "HOST_ADMIN_TOKEN": HOST,
            "MODEL_PROVIDER": selected.provider,
            "MODEL_NAME": selected.model,
            "MODEL_BASE_URL": selected.base_url,
            "MODEL_OUTPUT_MODE": selected.output_mode,
            "MODEL_API_KEY": credential.key.get_secret_value(),
            "MODEL_TIMEOUT_SECONDS": "240",
            "PYTHONUTF8": "1",
            "NO_PROXY": "127.0.0.1,::1,localhost",
        }
        self.secrets = [
            s
            for s in (initial.openai_api_key.get_secret_value(), credential.key.get_secret_value())
            if s and s != "ollama"
        ]
        url = urlsplit(selected.base_url)
        self.result["transport"] = dict(
            endpoint=f"{url.scheme}://{url.netloc}/api/chat"
            if selected.provider == "ollama"
            else selected.base_url.rstrip("/") + "/chat/completions",
            effective_output="json_schema"
            if selected.provider == "ollama"
            else selected.output_mode,
            note="OllamaAgentAdapter sends native format schema; saved setting retained",
        )
        self.persist()

    def evidence(self):
        return database_evidence(self.database_path, self.prefix.split("/")[-1])

    def setup(self):
        imported = self.request("POST", "/module-preparations/import", read(PACKAGE))
        self.result.update(
            preparation_id=imported["preparation_id"], entity_ids=imported["entity_ids"]
        )
        created = self.request("POST", "/rooms", {"name": "第二十五批 Ollama 正式包短测"})
        self.prefix = "/rooms/" + created["room"]["id"]
        joined = self.request(
            "POST", "/rooms/join", {"invite_code": created["invite_code"], "display_name": "周衡"}
        )
        self.player = joined["member_token"]
        self.result["player_id"] = joined["room"]["self_member_id"]
        write(self.directory / "session.json", {"prefix": self.prefix, "token": self.player})
        self.persist()
        card = self.request(
            "POST",
            "/characters/point-buy",
            {
                "ruleset_id": "coc7-character-creation",
                "name": "周衡",
                "age": 25,
                "attributes": {
                    k: {"value": v}
                    for k, v in dict(
                        str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60
                    ).items()
                },
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
        room = self.request("POST", self.prefix + "/character-slots", {"character_id": card["id"]})[
            "room"
        ]
        self.request(
            "POST",
            self.prefix + "/character-assignments",
            {"slot_id": room["character_slots"][0]["id"], "member_id": self.result["player_id"]},
        )
        profile = self.request("POST", "/agent-profiles", {"role": "keeper", "name": "KP"})
        self.request(
            "POST",
            self.prefix + "/agent-bindings",
            {"member_id": room["host_member_id"], "profile_id": profile["id"]},
        )
        self.request(
            "PATCH",
            self.prefix + "/module-preparation",
            {"preparation_id": imported["preparation_id"]},
        )
        self.request("POST", self.prefix + "/ready", {"ready": True}, player=True)
        self.request("POST", self.prefix + "/start")
        self.persist()

    def capture_step(self, name, step):
        step["after"] = self.request("GET", self.prefix)
        step["checks"] = self.request("GET", self.prefix + "/checks")
        raw = self.evidence()
        step["events"] = [
            e
            for e in raw["events"]
            if step["before"]["revision"] < e["seq"] <= step["after"]["revision"]
        ]
        step["event_range"] = [step["before"]["revision"] + 1, step["after"]["revision"]]
        step["plan"] = raw["plans"].get(step.get("cycle", {}).get("id"))
        step["tool_receipts"] = raw["receipts"]
        step["roll_ids"] = [
            c["dice"]["roll_record"]["id"]
            for c in step["checks"]
            if (c.get("dice") or {}).get("roll_record")
        ]
        write(self.directory / f"step-{name}.json", step)
        self.persist()

    def step(self, name, text):
        step = self.result["steps"].get(name)
        if step and step.get("status") == "completed":
            if not turn_response(step, self.evidence()["events"]):
                raise RuntimeError("Completed stage has no matching response; refusing replay")
            return step
        if not step:
            step = dict(
                request={"text": text, "client_request_id": str(uuid4())},
                before=self.request("GET", self.prefix),
                before_checks=self.request("GET", self.prefix + "/checks"),
                status="pending",
                attempts=[],
            )
            self.result["steps"][name] = step
            self.capture_step(name, step)  # Commit original request before sending it.
        raw = self.evidence()
        submitted = [
            e
            for e in raw["events"]
            if e.get("client_request_id") == step["request"]["client_request_id"]
            and e["type"] == "action.submitted"
        ]
        if not submitted:
            step["submit_receipt"] = self.request(
                "POST", self.prefix + "/actions", step["request"], player=True
            )
        current = self.request("GET", self.prefix)["game"].get("cycle") or {}
        if step.get("cycle") and current.get("id") != step["cycle"].get("id"):
            raise RuntimeError("Original cycle changed; refuse to submit/replay the action")
        if current.get("status") == "failed" and self.args.retry_failed:
            step["attempts"].append(dict(cycle=current, operation="retry_original"))
            self.capture_step(name, step)
            self.request("POST", self.prefix + "/agent-cycle/retry", {})
        started = time.monotonic()
        last_status = None
        while time.monotonic() - started < 600:
            room = self.request("GET", self.prefix)
            cycle = room["game"].get("cycle") or {}
            step["cycle"] = cycle
            if cycle.get("status") != last_status:
                print(f"{name}: {cycle.get('status')} {cycle.get('id')}", flush=True)
                last_status = cycle.get("status")
                self.capture_step(name, step)
            for check in self.request("GET", self.prefix + "/checks"):
                if check["status"] != "pending" or check["id"] != cycle.get("state", {}).get(
                    "pending_check_id"
                ):
                    continue
                if cycle.get("status") != "waiting_for_roll":
                    continue
                stage = (check.get("settlement") or {}).get("stage", "initial")
                sanity = check.get("sanity")
                if sanity:
                    stage = sanity["stage"]
                path = self.directory / f"before-roll-{check['id']}-{stage}.json"
                if not path.exists():
                    write(
                        path,
                        dict(
                            check=check,
                            room=room,
                            cycle=cycle,
                            plan=self.evidence()["plans"].get(cycle["id"]),
                        ),
                    )
                if sanity:
                    endpoint = f"/sanity/checks/{check['id']}/roll"
                    body = {"expected_stage": stage}
                else:
                    endpoint = f"/checks/{check['id']}/" + (
                        "choice" if stage == "choice" else "roll"
                    )
                    body = {"operation": "accept"} if stage == "choice" else {}
                response = self.request("POST", self.prefix + endpoint, body, player=True)
                write(self.directory / f"roll-receipt-{check['id']}-{stage}.json", response)
                self.capture_step(name, step)
            if cycle.get("status") in {"completed", "failed", "cancelled", "waiting_for_review"}:
                break
            time.sleep(0.5)
        self.capture_step(name, step)
        step["status"] = (
            "completed"
            if turn_response(step, self.evidence()["events"])
            else (
                "failed"
                if step["cycle"].get("status") in {"completed", "failed", "cancelled"}
                else "pending"
            )
        )
        self.capture_step(name, step)
        if step["status"] != "completed":
            raise RuntimeError(f"{name}: {step['status']}; original request/cycle preserved")
        return step

    def public(self):
        board = self.request("GET", self.prefix + "/public-entities", player=True)
        room = self.request("GET", self.prefix, player=True)
        write(self.directory / "public-entities.json", board)
        write(self.directory / "player-inventory.json", room["inventory"])
        return board

    def summarize(self):
        summary = self.result.setdefault("summary", {})
        if summary.get("after_seq") and not summary.get("force_rebuild"):
            # This stage is evidence of the pre-save moment. A later move can
            # wrap the live summary with navigation metadata; do not overwrite it.
            completed_cursor = max(
                (
                    m.get("coverage_end") or 0
                    for m in summary.get("memories", [])
                    if m["kind"] == "summary" and m.get("active", True)
                ),
                default=0,
            )
            if completed_cursor >= summary.get("target_seq", float("inf")):
                return
        if "before_seq" not in summary:
            summary["before_seq"] = self.request("GET", self.prefix)["revision"]
            summary["target_seq"] = max(
                e["seq"]
                for e in self.evidence()["events"]
                if e["type"]
                in {
                    "check.resolved",
                    "entity.revealed",
                    "scene.updated",
                    "module.interaction_receipt",
                }
            )
            self.persist()
        # The endpoint advances one bounded source window, not the whole history.
        # Continue its cursor; never infer coverage from HTTP success or stale=False.
        target = summary.setdefault("target_seq", self.result["steps"]["take"]["after"]["revision"])
        for attempt in range(7):
            memories = self.request("GET", self.prefix + "/memories")
            cursor = max(
                (
                    m.get("coverage_end") or 0
                    for m in memories
                    if m["kind"] == "summary" and m.get("active", True)
                ),
                default=0,
            )
            summary["memories"] = memories
            if cursor >= target and not summary.get("force_rebuild"):
                break
            if attempt == 6:
                raise RuntimeError("Summary catch-up budget reached; resume the same cursor")
            receipt = self.request("POST", self.prefix + "/summary-rebuild", {})
            summary.setdefault("attempts", []).append(dict(before_cursor=cursor, receipt=receipt))
            write(
                self.directory / f"summary-attempt-{len(summary['attempts'])}.json",
                summary["attempts"][-1],
            )
            self.persist()
            if (
                max((r.get("last_successful_summary_seq") or 0 for r in receipt), default=0)
                <= cursor
            ):
                raise RuntimeError("Summary cursor did not advance; failure retained")
            summary.pop("force_rebuild", None)
            self.persist()
        summary["after_seq"] = self.request("GET", self.prefix)["revision"]
        write(self.directory / "summary.json", summary)
        self.persist()

    def refresh_summary_acceptance(self):
        """Archive an incomplete acceptance round; repeat only its read/restore stages."""
        folder = (
            self.directory / f"summary-refresh-{len(self.result.get('summary_refreshes', [])) + 1}"
        )
        folder.mkdir()
        for name in ("summary", "restore", "step-recall", "step-continue", "verified-evidence"):
            path = self.directory / (name + ".json")
            if path.exists():
                write(folder / path.name, read(path))
        record = {k: self.result.pop(k, None) for k in ("summary", "restore_record")}
        names = {"recall", "continue", self.result.pop("continuation_stage", "continue")}
        record["steps"] = {k: self.result["steps"].pop(k, None) for k in names}
        self.result.pop("continuation_destination", None)
        write(folder / "stages.json", record)
        self.result.setdefault("summary_refreshes", []).append(str(folder))
        self.result["summary"] = {"force_rebuild": True}
        self.persist()

    def restore_normal(self):
        record = self.result.setdefault("restore_record", {})
        if record.get("after"):
            if not all(
                comparison(
                    record["before"],
                    record["after"],
                    record["before_checks"],
                    record["after_checks"],
                ).values()
            ):
                raise RuntimeError("Saved restore comparison failed")
            if self.request("GET", self.prefix)["status"] == "paused":
                self.request("POST", self.prefix + "/resume")
            return
        if not record:
            record.update(
                before=self.request("GET", self.prefix),
                before_checks=self.request("GET", self.prefix + "/checks"),
            )
            self.persist()
        if "snapshot" not in record:
            record["snapshot"] = self.request(
                "POST", self.prefix + "/snapshots", {"name": "batch25 original settled state"}
            )["snapshot"]
            self.persist()
        if not record.get("stop_launcher"):
            self.request("POST", self.prefix + "/pause")
            record["stop_launcher"] = self.run
            self.persist()
            self.stop()
            self.launch()
        # A prior process interrupted between load and journalling must not load twice.
        loaded = [
            e
            for e in self.evidence()["events"]
            if e["seq"] > record["before"]["revision"] and e["type"] == "snapshot.loaded"
        ]
        record["load_receipt"] = (
            loaded[-1]
            if loaded
            else self.request("POST", self.prefix + f"/snapshots/{record['snapshot']['id']}/load")
        )
        record.update(
            after=self.request("GET", self.prefix),
            after_checks=self.request("GET", self.prefix + "/checks"),
        )
        record["comparison"] = comparison(
            record["before"], record["after"], record["before_checks"], record["after_checks"]
        )
        write(self.directory / "restore.json", record)
        self.persist()
        assert all(record["comparison"].values()), record["comparison"]
        self.request("POST", self.prefix + "/resume")

    def advance(self):
        ids = self.result["entity_ids"]
        board = self.public()
        room = self.request("GET", self.prefix)
        if room["session_state"]["scene_title"] not in {"6号车厢", "5号车厢"}:
            raise RuntimeError("Inspect current scene before selecting another natural action")
        if not self.result.get("investigation_key"):
            if room["session_state"]["scene_title"] == "6号车厢":
                if ids["train_map"] not in {e["id"] for e in board}:
                    self.step("observe", "我环顾车厢，看看门边和墙上能看见什么。")
                if ids["train_map"] not in {e["id"] for e in self.public()}:
                    self.step("orient_map", "我走到车门边，仔细看看墙上有没有路线图或车厢标识。")
                if ids["train_map"] not in {e["id"] for e in self.public()}:
                    self.step(
                        "inspect_wall", "我沿着车门旁的墙面逐一检查图示和标识，看看上面画了什么。"
                    )
            visible = {e["id"] for e in self.public()}
            target = "map_erased" if ids["train_map"] in visible else "newspaper"
            if self.request("GET", self.prefix + "/checks"):
                raise RuntimeError("Review existing checks before selecting an investigation")
            self.result["investigation_key"] = target
            self.result["route_selection"] = dict(
                before=self.request("GET", self.prefix),
                public_ids=sorted(visible),
                reason="Use observed map or approved adjacent seats. No investigation dice yet.",
            )
            self.persist()
        target = self.result["investigation_key"]
        if target == "newspaper":
            self.step("examine", "我把门上的便签揭下来，翻过来看背面的文字。")
            self.step("move_out", "我从6号车厢向前进入相邻的5号车厢。")
            if "investigate" not in self.result["steps"]:
                assert self.request("GET", self.prefix)["session_state"]["scene_title"] == "5号车厢"
            self.step("investigate", "我仔细翻找座位和座位下方，看看有没有遗留的东西。")
        else:
            self.step("investigate", "我仔细查看电车示意图上看不清楚的那一部分。")
        checks = self.request("GET", self.prefix + "/checks")
        if not any(c.get("clue_id") == ids[target] and c["status"] == "resolved" for c in checks):
            raise RuntimeError(
                "Investigation cycle completed without the bound result; do not replay"
            )
        if target == "newspaper":
            self.step("return", "我回到刚才的6号车厢，准备带上门边那张便签。")
        else:
            self.step("examine", "我把门上的便签揭下来，翻过来看背面的文字。")
        self.step("take", "我把刚才翻看的那张便签收进口袋里，带在身上。")
        room = self.request("GET", self.prefix)
        if not owned_item(
            room, ids["note_front"], self.result["player_id"], self.evidence()["events"]
        )[0]:
            raise RuntimeError(
                "Discovered object is not an owned instance; do not mark possession passed"
            )
        self.public()
        self.summarize()
        self.restore_normal()
        self.step("recall", "回顾刚才实际调查到的信息、检定结果、现在的位置和我持有的东西。")
        if not self.result.get("continuation_stage"):
            prior = self.result["steps"].get("continue")
            self.result["continuation_stage"] = "continue_move" if prior else "continue"
            current = self.request("GET", self.prefix)["session_state"]["scene_title"]
            self.result["continuation_destination"] = (
                "5号车厢" if current == "6号车厢" else "6号车厢"
            )
            self.persist()
        destination = self.result["continuation_destination"]
        self.step(self.result["continuation_stage"], f"我带着便签，走进相邻的{destination}。")
        self.public()

    def export(self):
        if not self.prefix:
            return
        super().export()
        raw = self.evidence()
        write(self.directory / "events.json", raw["events"])
        write(self.directory / "database-evidence.json", raw)
        self.public()

    def run_batch(self):
        kernel = ctypes.windll.kernel32
        kernel.FreeConsole()
        assert kernel.AllocConsole()
        ctypes.windll.user32.ShowWindow(kernel.GetConsoleWindow(), 0)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self._console_handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)(lambda _: True)
        assert kernel.SetConsoleCtrlHandler(self._console_handler, True)
        try:
            if self.args.provider == "ollama":
                parsed = urlsplit(self.api["base_url"])
                response = self.http.get(f"{parsed.scheme}://{parsed.netloc}/api/tags")
                response.raise_for_status()
                assert self.api["model"] in [m["name"] for m in response.json()["models"]]
                write(self.directory / "local-models.json", response.json())
            self.launch()
            current = self.request("GET", "/model/config")
            assert all(
                current[k] == self.api[k] for k in ("provider", "model", "base_url", "output_mode")
            )
            if not self.prefix:
                self.setup()
            room = self.request("GET", self.prefix)
            if self.args.source_batch24:
                assert room["game"]["preparation"]["id"] == "1b38f4b7-3287-457e-9ae6-f906906cf1f4"
                with sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True) as db:
                    prep = json.loads(
                        db.execute(
                            "SELECT document FROM module_preparations WHERE id=?",
                            (room["game"]["preparation"]["id"],),
                        ).fetchone()[0]
                    )
                self.result.update(
                    entity_ids=prep["package_entity_ids"],
                    preparation_id=room["game"]["preparation"]["id"],
                    player_id=self.request("GET", self.prefix, player=True)["self_member_id"],
                )
                write(
                    self.directory / "session.json", {"prefix": self.prefix, "token": self.player}
                )
            if not self.result["baseline"]:
                raw = self.evidence()
                self.result["baseline"] = dict(
                    event_seq=room["revision"],
                    call_rowid=max([c["rowid"] for c in raw["calls"]], default=0),
                )
                write(self.directory / "initial-room.json", room)
                write(self.directory / "baseline-evidence.json", raw)
                self.result["hidden_facts"] = [
                    dict(id=self.result["entity_ids"][e["key"]], text=e["fields"]["public_summary"])
                    for e in read(PACKAGE)["entities"]
                    if e["fields"]["initial_visibility"] == "hidden"
                ]
                self.persist()
            if self.args.live:
                if self.args.refresh_summary:
                    self.refresh_summary_acceptance()
                self.advance()
            self.result["status"] = "awaiting_audit" if self.args.live else "not_run"
            self.result.pop("error", None)
        except Exception as error:
            self.result.update(status="failed", error=str(error))
            self.result.setdefault("failures", []).append(dict(launcher=self.run, error=str(error)))
            raise
        finally:
            try:
                if self.launcher and self.prefix:
                    self.export()
            finally:
                self.persist()
                self.stop()
                for log in self.logs:
                    log.close()
                self.http.close()
                if (self.directory / "final-room.json").exists():
                    report = self.audit_report(self.directory)
                    write(self.directory / "verified-evidence.json", report)
                    self.result["acceptance"] = report["status"]
                    self.result["game_usage"] = report["tokens"]
                    self.result["usage_total_basis"] = report["total_token_basis"]
                    if report["status"] == "passed":
                        self.result["status"] = "completed"
                    elif self.result["status"] == "awaiting_audit":
                        self.result["status"] = (
                            "failed" if report["status"] == "failed" else "pending"
                        )
                    self.persist()
                    print(
                        json.dumps(
                            {
                                k: report[k]
                                for k in ("status", "natural_inputs", "model_calls", "tokens")
                            }
                        ),
                        flush=True,
                    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--output-mode", choices=["json_object", "json_schema"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--refresh-summary",
        action="store_true",
        help="Archive incomplete summary/restore/recall evidence and recheck only those stages",
    )
    parser.add_argument("--source-batch24", action="store_true")
    parser.add_argument(
        "--authorize-openai-formal-context",
        action="store_true",
        help="Only after user explicitly authorizes necessary module context, "
        "isolated room state and actions to https://api.openai.com/v1/",
    )
    parser.add_argument("--backend-port", type=int, default=8026)
    parser.add_argument("--frontend-port", type=int, default=5175)
    args = parser.parse_args()
    if args.refresh_summary and not (args.resume and args.live):
        parser.error("--refresh-summary requires --resume --live")
    runner = Batch25(args)
    runner.run_batch()
    raise SystemExit(2 if args.live and runner.result.get("acceptance") != "passed" else 0)
