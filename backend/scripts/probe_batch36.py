"""Independent historical fixtures through normal HTTP routes and real Ollama.

Each case gets its own database backup and normal snapshot restoration. Original
receipts and dice are preserved; no game tools or model phases are bypassed.
"""

import argparse
import hashlib
import json
import secrets
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from scripts.audit_batch36 import audit
from scripts.play_batch36 import BASE, ROOT, Session, write

SOURCE = ROOT / "data/prepared/changan/batch-35/run-20260916T234100Z"
CASES = {
    "opinion": (
        "question-binding-blocker-save.json",
        [
            "陈拓，你知道拉杆是做什么的吗？",
        ],
    ),
    "opinion-variant": (
        "question-binding-blocker-save.json",
        [
            "陈拓，照面板上的说明，你认为先用哪根杆比较稳妥？为什么？",
        ],
    ),
    "delegation": (
        "question-binding-blocker-save.json",
        [
            "陈拓，能帮我检查一下操作面板吗？",
            "有什么地方仍然没法确定？",
        ],
    ),
    "division": (
        "npc-context-save.json",
        [
            "周岚，照看乘务员，陈拓，帮我找黑包，我去门口看看。",
        ],
    ),
    "division-variant": (
        "npc-context-save.json",
        [
            "请周岚照顾乘务员；陈拓，能帮我检查一下附近有没有黑包吗？我在门口观察。",
        ],
    ),
    "npc": (
        "teammate-context-save.json",
        [
            "乘务员先生，是什么咬伤了您？司机现在在哪里？",
            "乘务员，纸条说第三个箱子藏着钥匙，这就是您的黑包吗？驾驶室该怎么走？",
        ],
    ),
    "observation": (
        "mixed-control-authority-save.json",
        [
            "陈拓，帮我留意拉杆和车的反应。",
            "我先等一下，听听大家还有什么判断。",
        ],
    ),
    "ending-dialogue": (
        None,
        [
            "我只是看看自己有没有受伤。那种被咬的感觉太真实了，我现在只想抱着包坐一会儿，等手不再发抖。",
        ],
    ),
}


def settings(directory, host):
    return Settings(
        _env_file=None,
        host_admin_token=host,
        data_dir=ROOT / "data",
        database_url="sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
        checkpoint_db_path=directory / "checkpoint.db",
        knowledge_db_path=directory / "knowledge.db",
        model_settings_path=directory / "model-settings.json",
        model_provider="ollama",
        model_name="qwen3:8b",
        model_base_url="http://127.0.0.1:11434/v1/",
        model_api_key="ollama",
        openai_api_key="",
        model_context_limit=8192,
        model_output_limit=900,
        model_timeout_seconds=240,
        model_think=False,
    )


def run_case(directory, name, *, source=SOURCE, case=None):
    snapshot_file, actions = case or CASES[name]
    if directory.exists():
        raise ValueError("Never overwrite an earlier probe")
    directory.mkdir(parents=True)
    record_version(directory)
    hashes = {}
    for filename in ("game.db", "knowledge.db", "checkpoint.db"):
        path = source / filename
        hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        with (
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as src,
            sqlite3.connect(directory / filename) as dst,
        ):
            src.backup(dst)
    shutil.copyfile(source / "session.json", directory / "session.json")
    host = secrets.token_urlsafe(32)
    write(directory / "private-auth.json", {"host": host})
    saved = (
        json.loads((source / snapshot_file).read_text(encoding="utf-8")) if snapshot_file else None
    )
    snapshot = saved.get("snapshot", saved) if saved else None
    with sqlite3.connect(directory / "game.db") as db:
        initial_seq = db.execute("select max(seq) from room_events").fetchone()[0]
    write(
        directory / "fixture-provenance.json",
        {
            "kind": "historical_database_copy_then_normal_snapshot_load",
            "source": str(source.relative_to(ROOT)),
            "source_sha256": hashes,
            "snapshot": snapshot,
            "initial_max_seq": initial_seq,
            "actions": actions,
            "dice_or_world_state_rewritten": False,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    session = Session(directory)
    session.http.close()
    started = time.monotonic()
    outcomes = []
    effective = settings(directory, host)
    write(
        directory / "effective-config.json",
        {
            key: str(getattr(effective, key))
            for key in (
                "database_url",
                "checkpoint_db_path",
                "knowledge_db_path",
                "model_settings_path",
                "model_name",
                "model_context_limit",
                "model_output_limit",
                "model_think",
            )
        },
    )
    with TestClient(create_app(effective)) as http:
        session.http = http
        if snapshot:
            if session.req("GET", session.prefix, player=True)["status"] == "running":
                session.req("POST", session.prefix + "/pause")
            session.req("POST", session.prefix + f"/snapshots/{snapshot['id']}/load")
            session.req("POST", session.prefix + "/resume")
        write(
            directory / "initial-public-state.json", session.req("GET", session.prefix, player=True)
        )
        session.state["seen_seq"] = initial_seq
        session.save()
        for text in actions:
            step = time.monotonic()
            try:
                session.req(
                    "POST",
                    session.prefix + "/actions",
                    {"text": text, "client_request_id": str(uuid4())},
                    player=True,
                )
            except RuntimeError as error:
                outcomes.append(
                    {
                        "input": text,
                        "submission_error": str(error),
                        "elapsed_ms": round((time.monotonic() - step) * 1000),
                    }
                )
                break
            while True:
                session.poll()
                cycle = session.req("GET", session.prefix + "/agent-cycle")
                if cycle.get("status") not in {"running", "queued", "waiting_for_roll"}:
                    break
                if time.monotonic() - step > 360:
                    break
            outcomes.append(
                {
                    "input": text,
                    "elapsed_ms": round((time.monotonic() - step) * 1000),
                    "cycle": cycle,
                }
            )
            if cycle.get("status") != "completed":
                break
        write(
            directory / "probe-result.json",
            {
                "case": name,
                "steps": outcomes,
                "wall_ms": round((time.monotonic() - started) * 1000),
                "context_limit": 8192,
            },
        )
        session.observe()
    audit(directory)


def record_version(directory):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    diff = subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT)
    (directory / "code.diff").write_bytes(diff)
    paths = [
        *list((ROOT / "backend/app").rglob("*.py")),
        *list((ROOT / "backend/scripts").glob("*batch36.py")),
        ROOT / "backend/tests/test_batch36_collaboration.py",
    ]
    write(
        directory / "code-version.json",
        {
            "head": head,
            "diff_sha256": hashlib.sha256(diff).hexdigest(),
            "files": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
            },
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("case", choices=CASES)
    args = parser.parse_args()
    directory = (BASE / args.run / args.case).resolve()
    assert directory.is_relative_to(BASE.resolve())
    run_case(directory, args.case)
