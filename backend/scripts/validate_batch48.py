"""Isolated browser acceptance; all new seats/cards/bindings are made by the wizard."""

import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, wait_for

DIRECTORY = ROOT / "data/prepared/batch-48/real-01"
SOURCE = ROOT / "data/prepared/changan/batch-34/run-20260916T211255Z"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def prepare():
    DIRECTORY.mkdir(parents=True, exist_ok=False)
    protected = [
        ROOT / ".env",
        ROOT / "data/game.db",
        ROOT / "data/host-model-settings.json",
        ROOT / "data/prepared/changan/batch-21/package-approved.json",
        ROOT / "data/modules/常暗之厢/常暗之厢 2-3.doc",
        ROOT / "data/modules/追书人/追书人 1-2.pdf",
    ]
    for name in ("game.db", "checkpoint.db", "knowledge.db"):
        original, target = SOURCE / name, DIRECTORY / name
        protected.extend([original, Path(str(original) + "-wal")])
        with sqlite3.connect(f"file:{original.as_posix()}?mode=ro", uri=True) as source:
            with sqlite3.connect(target) as destination:
                source.backup(destination)
    config = ROOT / "data/host-model-settings.json"
    if config.is_file():
        shutil.copy2(config, DIRECTORY / "model-settings.json")
    write(DIRECTORY / "source-protection-before.json", {str(p): file_hash(p) for p in protected})
    write(
        DIRECTORY / "fixture-origin.json",
        {
            "source": str(SOURCE),
            "backup": "SQLite read-only backup",
            "new_characters": 0,
            "new_seats": 0,
            "new_bindings": 0,
            "retained": "all prior cards, profiles, approved preparation, rooms and history",
            "scenario": "常暗之厢",
            "recommended_players": [2, 3],
            "source_block": "block_03a6a77938c3f5922720cdc206523cf0",
            "source_page": 1,
            "excluded": "追书人 PDF p.4 supports one investigator, two with adjustments",
        },
    )


def serve():
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        host_admin_token=TEST_HOST,
        data_dir=ROOT / "data",
        database_url=f"sqlite+aiosqlite:///{(DIRECTORY / 'game.db').as_posix()}",
        checkpoint_db_path=DIRECTORY / "checkpoint.db",
        knowledge_db_path=DIRECTORY / "knowledge.db",
        model_settings_path=DIRECTORY / "model-settings.json",
    )
    write(
        DIRECTORY / "effective-config.json",
        {
            key: getattr(settings, key)
            for key in (
                "model_provider",
                "model_name",
                "model_context_limit",
                "model_output_limit",
                "agent_max_calls",
                "agent_context_chars",
                "model_timeout_seconds",
            )
        },
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8148, log_level="warning")


def start_services():
    processes = []
    commands = [
        ("backend", [sys.executable, str(Path(__file__).resolve()), "--serve"], BACKEND, {}),
        (
            "frontend",
            [shutil.which("node"), str(ROOT / "frontend/node_modules/vite/bin/vite.js")],
            ROOT / "frontend",
            {"COC_BACKEND_PORT": "8148", "COC_FRONTEND_PORT": "5248"},
        ),
    ]
    for name, command, cwd, extra in commands:
        with (DIRECTORY / f"{name}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUTF8": "1", **extra},
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            processes.append({"name": name, "pid": process.pid})
    write(DIRECTORY / "service-pids.json", processes)
    with httpx.Client(trust_env=False, timeout=5) as client:
        wait_for(lambda: client.get("http://127.0.0.1:8148/api/health").status_code == 200)
        wait_for(lambda: client.get("http://127.0.0.1:5248").status_code == 200)
    print("Services ready: 8148 / 5248", flush=True)


def start_browser(name):
    directory = DIRECTORY / name
    directory.mkdir(exist_ok=True)
    chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    with (directory / "chrome.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                str(chrome),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-extensions",
                "--disable-component-update",
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={directory / 'profile'}",
                "--window-size=1440,1000",
                "about:blank",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    write(directory / "process.json", {"pid": process.pid})
    wait_for(lambda: (directory / "profile/DevToolsActivePort").is_file())
    port = (directory / "profile/DevToolsActivePort").read_text().splitlines()[0]
    write(directory / "port.json", {"port": port})
    browser = connect_browser(name)
    browser.command("Page.navigate", {"url": "http://127.0.0.1:5248/"})
    browser.cdp.close()
    print(f"Browser ready: {name}", flush=True)


def connect_browser(name):
    from websockets.sync.client import connect

    browser = object.__new__(SmokeCheck)
    browser.directory = DIRECTORY / name
    browser.request_id, browser.requests, browser.exceptions = 0, [], []
    browser.http = httpx.Client(trust_env=False, timeout=5)
    port = json.loads((browser.directory / "port.json").read_text())["port"]
    targets = browser.http.get(f"http://127.0.0.1:{port}/json").json()
    target = next(t for t in targets if t["type"] == "page")
    browser.cdp = connect(
        target["webSocketDebuggerUrl"],
        proxy=None,
        open_timeout=5,
        ping_interval=None,
        max_size=64 * 1024 * 1024,
    )
    for method in ("Page.enable", "Network.enable", "Runtime.enable"):
        browser.command(method)
    return browser


def browser_operation(args):
    browser = connect_browser(args.browser)
    try:
        if args.click:
            browser.click(args.click)
        if args.follow:
            expression = (
                "[...document.querySelectorAll('a')].find(a => a.textContent.trim() === "
                + json.dumps(args.follow)
                + ")"
            )
            wait_for(lambda: browser.evaluate(f"!!({expression})"))
            browser.evaluate(f"({expression}).click()")
        if args.fill:
            browser.fill(args.fill, args.value)
        if args.click or args.fill or args.follow:
            with (browser.directory / "operations.jsonl").open("a", encoding="utf-8") as log:
                log.write(
                    json.dumps(
                        {
                            "at": time.time(),
                            "click": args.click,
                            "follow": args.follow,
                            "fill": args.fill,
                            "value": "[test host credential]"
                            if args.fill == "#host-key"
                            else args.value,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        if args.eval_file:
            result = browser.evaluate(Path(args.eval_file).read_text(encoding="utf-8"))
            print(json.dumps(result, ensure_ascii=False))
        if args.text:
            print(browser.evaluate("document.body.innerText"))
        if args.screenshot:
            result = browser.command(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": False,
                },
            )
            (browser.directory / args.screenshot).write_bytes(base64.b64decode(result["data"]))
        if args.capture:
            snapshot = browser.evaluate("({text:document.body.innerText, url:location.href})")
            write(
                browser.directory / args.capture,
                {**snapshot, "at": time.time(), "exceptions": browser.exceptions},
            )
    finally:
        browser.cdp.close()
        browser.http.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--new-browser")
    parser.add_argument("--browser", default="host")
    parser.add_argument("--eval-file")
    parser.add_argument("--text", action="store_true")
    parser.add_argument("--screenshot")
    parser.add_argument("--capture")
    parser.add_argument("--click")
    parser.add_argument("--follow")
    parser.add_argument("--fill")
    parser.add_argument("--value", default="")
    args = parser.parse_args()
    if args.serve:
        serve()
    elif args.prepare:
        prepare()
    elif args.start:
        start_services()
    elif args.new_browser:
        start_browser(args.new_browser)
    else:
        browser_operation(args)
