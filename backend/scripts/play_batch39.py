"""Batch 39 isolated real-model probes and normal API play, reusing batch 36 tools."""

import argparse
import difflib
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / os.environ.get("COC_PLAY_BASE", "data/prepared/changan/batch-39")
PORT = int(os.environ.get("COC_PLAY_PORT", "8039"))
# Protect the default application constructed at import as well as the active app.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (BASE / "bootstrap.db").as_posix()
os.environ["CHECKPOINT_DB_PATH"] = str(BASE / "bootstrap-checkpoint.db")
os.environ["KNOWLEDGE_DB_PATH"] = str(BASE / "bootstrap-knowledge.db")
os.environ["MODEL_SETTINGS_PATH"] = str(BASE / "bootstrap-model-settings.json")

from scripts import audit_batch36, play_batch36, probe_batch36  # noqa: E402

play_batch36.URL = f"http://127.0.0.1:{PORT}/api"


class Session(play_batch36.Session):
    def req(self, method, path, body=None, player=False):
        if method == "POST" and path == "/rooms":
            body = {**body, "name": getattr(self, "room_name", "第39批 常暗之厢 本地真实局")}
        return super().req(method, path, body, player)

    def poll(self):
        until = time.monotonic() + 45
        while time.monotonic() < until:
            room = self.req("GET", self.prefix, player=True)
            cycle = room.get("game", {}).get("cycle") or {}
            if cycle.get("status") not in {"running", "queued", "waiting_for_roll"}:
                break
            # A request event precedes the graph's wait checkpoint. Only act
            # when the original current cycle is actually ready for the roll.
            if cycle.get("status") == "waiting_for_roll":
                pending = (room.get("combat") or {}).get("pending") or {}
                stage = pending.get("stage", "")
                if pending.get("participant_id") == self.state["player_id"] and (
                    stage.endswith("_roll") or stage.endswith("_choice")
                ):
                    # The same player buttons as the UI: roll the original,
                    # then accept it. No luck spending or restricted resolution.
                    self.req("POST", self.prefix + "/combat/step", {
                        "action_id": pending["id"], "stage": stage,
                        "operation": "roll" if stage.endswith("_roll") else "accept",
                    }, player=True)
                    continue
                for check in self.req("GET", self.prefix + "/checks"):
                    if check["status"] != "pending":
                        continue
                    player = check.get("target_member_id") == self.state["player_id"]
                    cid = check["id"]
                    stage = (check.get("settlement") or {}).get("stage")
                    if stage in {"choice", "awaiting_choice"}:
                        self.req("POST", self.prefix + f"/checks/{cid}/choice",
                                 {"operation": "accept"}, player=player)
                    elif check.get("sanity"):
                        stage = check["sanity"]["stage"]
                        if stage in {"san", "loss", "int", "duration"}:
                            self.req("POST", self.prefix + f"/sanity/checks/{cid}/roll",
                                     {"expected_stage": stage}, player=player)
                    else:
                        self.req("POST", self.prefix + f"/checks/{cid}/roll", {}, player=player)
            time.sleep(1)
        self.observe()


def version(directory):
    probe_batch36.record_version_original(directory)
    path = directory / "code-version.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for folder in ("scripts", "tests"):
        for file in (ROOT / "backend" / folder).glob("*batch*.py"):
            data["files"][str(file.relative_to(ROOT))] = hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
    # git diff omits new, untracked regression/driver files. Include their exact
    # patch as well as their hashes without staging or changing the index.
    diff = (directory / "code.diff").read_bytes()
    untracked = set(subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--",
         "backend/app", "backend/scripts", "backend/tests"],
        cwd=ROOT, text=True,
    ).splitlines())
    for name in data["files"]:
        if name.replace("\\", "/") in untracked:
            contents = (ROOT / name).read_text(encoding="utf-8").splitlines(keepends=True)
            diff += "".join(difflib.unified_diff(
                [], contents, fromfile="/dev/null", tofile="b/" + name,
            )).encode("utf-8")
    (directory / "code.diff").write_bytes(diff)
    data["diff_sha256"] = hashlib.sha256(diff).hexdigest()
    play_batch36.write(path, data)


def audit(directory):
    audit_batch36.audit(directory)
    from scripts.audit_batch38 import audit_quality

    audit_quality(directory)
    path = directory / "session-full.md"
    batch = "第40批" if "batch-40" in str(BASE) else "第39批"
    transcript = path.read_text(encoding="utf-8").replace("第36批", batch)
    binding_path = directory / "binding-verification.json"
    if binding_path.exists():
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        title = binding.get("title")
        if title:
            transcript = transcript.replace("《常暗之厢》", f"《{title}》", 1)
    path.write_text(transcript, encoding="utf-8")
    manifest = directory / "audit-manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not any(e["path"] == "interaction-metrics.json" for e in data):
        data.append({"path": "interaction-metrics.json"})
    data.append({"path": "private-audit/behavior-state.json"})
    for entry in data:
        entry["sha256"] = hashlib.sha256((directory / entry["path"]).read_bytes()).hexdigest()
    play_batch36.write(manifest, data)


def receipt_state(directory):
    with sqlite3.connect(f"file:{(directory / 'game.db').as_posix()}?mode=ro", uri=True) as db:
        return {
            table: db.execute("select * from " + table + " order by id").fetchall()
            for table in ("agent_tool_receipts", "pending_checks")
        }


probe_batch36.record_version_original = probe_batch36.record_version
probe_batch36.record_version = version
probe_batch36.audit = audit
probe_batch36.Session = Session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "probe",
            "serve",
            "init",
            "act",
            "poll",
            "observe",
            "restore",
            "save",
            "export",
            "version",
        ],
    )
    parser.add_argument("directory")
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()
    directory = (BASE / args.directory).resolve()
    assert directory.is_relative_to(BASE) and directory != BASE
    if args.command == "probe":
        source, snapshot, *actions = args.args
        probe_batch36.run_case(
            directory,
            args.directory,
            source=ROOT / source,
            case=(None if snapshot == "-" else snapshot, actions),
        )
        return
    directory.mkdir(parents=True, exist_ok=True)
    if args.command == "serve":
        play_batch36.serve(directory, port=PORT)
        return
    if args.command == "version":
        version(directory)
        return
    if args.command == "export":
        audit(directory)
        return
    session = Session(directory)
    try:
        if args.command == "init":
            version(directory)
            config = json.loads(Path(args.args[0]).read_text(encoding="utf-8")) if args.args else {}
            session.setup(**config)
        elif args.command == "act":
            session.req(
                "POST",
                session.prefix + "/actions",
                {"text": args.args[0], "client_request_id": str(uuid4())},
                player=True,
            )
            session.poll()
        elif args.command == "restore":
            before = session.req("GET", session.prefix + "/checks")
            receipts = receipt_state(directory)
            behavior = session.req("GET", session.prefix + "/teammate-behavior")
            saved = session.req("POST", session.prefix + "/snapshots", {"name": "第39批途中保存"})
            play_batch36.write(directory / "natural-save.json", saved)
            session.req("POST", session.prefix + "/pause")
            snapshot = saved.get("snapshot", saved)
            session.req("POST", session.prefix + f"/snapshots/{snapshot['id']}/load")
            session.req("POST", session.prefix + "/resume")
            after = session.req("GET", session.prefix + "/checks")
            same_receipts = receipts == receipt_state(directory)
            settled = next(
                (c for c in reversed(after) if c["status"] == "resolved" and not c.get("sanity")),
                None,
            )
            replay = None
            if settled:
                try:
                    session.req(
                        "POST",
                        session.prefix + f"/checks/{settled['id']}/roll",
                        {},
                        player=settled.get("target_member_id") == session.state["player_id"],
                    )
                    replay = "idempotent_response"
                except RuntimeError as error:
                    replay = str(error)
            replay_unchanged = after == session.req("GET", session.prefix + "/checks")
            replay_unchanged = replay_unchanged and receipts == receipt_state(directory)
            after_behavior = session.req("GET", session.prefix + "/teammate-behavior")
            play_batch36.write(
                directory / "restore-verification.json",
                {
                    "before_checks": before,
                    "after_checks": after,
                    "equal_checks": before == after,
                    "equal_receipts": same_receipts,
                    "settled_roll_replay": replay,
                    "replay_unchanged": replay_unchanged,
                    "before_behavior": behavior,
                    "after_behavior": after_behavior,
                    "equal_behavior": behavior == after_behavior,
                },
            )
            assert before == after and same_receipts and replay_unchanged
            assert behavior == after_behavior
            session.observe()
        elif args.command == "save":
            name = args.args[0] if args.args else "第39批阻断现场"
            saved = session.req("POST", session.prefix + "/snapshots", {"name": name})
            play_batch36.write(directory / ("save-" + str(uuid4()) + ".json"), saved)
            session.observe()
        else:
            getattr(session, args.command)()
    finally:
        session.http.close()


if __name__ == "__main__":
    main()
