"""Read-only browser verification of the isolated batch 18 player room."""

import base64
import json
import shutil
import sys

import httpx
from check_batch18 import DIRECTORY
from check_character_creation import ROOT, SmokeCheck, wait_for
from module_package import read, write
from websockets.sync.client import connect


def inspect(label):
    browser = SmokeCheck.__new__(SmokeCheck)
    browser.directory = DIRECTORY / "browser" / label
    browser.directory.mkdir(parents=True, exist_ok=False)
    browser.processes, browser.logs = [], []
    browser.http = httpx.Client(trust_env=False, timeout=8)
    browser.cdp, browser.request_id = None, 0
    browser.requests, browser.exceptions = [], []

    def screenshot(name):
        browser.evaluate(
            "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"
        )
        result = browser.command(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
        )
        (browser.directory / name).write_bytes(base64.b64decode(result["data"]))

    try:
        vite = (ROOT / "frontend/node_modules/vite/dist/node/index.js").as_uri()
        script = (
            f"import {{createServer}} from {json.dumps(vite)};"
            "const server=await createServer({"
            f"root:{json.dumps(str(ROOT / 'frontend'))},configFile:false,"
            "server:{host:'127.0.0.1',port:5188,strictPort:true,proxy:{"
            "'/api':{target:'http://127.0.0.1:8018'},"
            "'/ws':{target:'ws://127.0.0.1:8018',ws:true}}}});await server.listen();"
        )
        try:
            frontend_running = browser.http.get("http://127.0.0.1:5188").status_code == 200
        except httpx.HTTPError:
            frontend_running = False
        if not frontend_running:
            browser.start([shutil.which("node"), "--input-type=module", "-e", script], ROOT, "vite")
        wait_for(lambda: browser.http.get("http://127.0.0.1:5188").status_code == 200)
        profile = browser.directory / "profile"
        browser.start(
            [
                "C:/Program Files/Google/Chrome/Application/chrome.exe",
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--in-process-gpu",
                "--disable-software-rasterizer",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--window-size=1365,1100",
                "about:blank",
            ],
            ROOT,
            "chrome",
        )
        devtools = profile / "DevToolsActivePort"
        wait_for(devtools.exists)
        port = devtools.read_text().splitlines()[0]
        page = next(
            p
            for p in browser.http.get(f"http://127.0.0.1:{port}/json").json()
            if p["type"] == "page"
        )
        browser.cdp = connect(page["webSocketDebuggerUrl"], proxy=None, ping_interval=None)
        for method in ("Page.enable", "Runtime.enable", "Network.enable"):
            browser.command(method)
        session = read(DIRECTORY / "session.json")
        rid = session["prefix"].rsplit("/", 1)[-1]
        browser.command(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    f"localStorage.setItem({json.dumps('coc.room.' + rid)}, "
                    f"{json.dumps(session['token'])});"
                )
            },
        )
        browser.command("Page.navigate", {"url": f"http://127.0.0.1:5188/#/rooms/{rid}"})
        wait_for(
            lambda: browser.evaluate(
                "document.querySelector('[data-testid=scene-title]')?.textContent"
            )
        )
        content = browser.evaluate("document.body.innerText")
        (browser.directory / "visible.txt").write_text(content, encoding="utf-8")
        screenshot("room.png")
        captures = {}
        for name, selector in (
            ("messages", "[data-testid=timeline]"),
            ("items", "[data-testid=investigation-board]"),
            ("current-scene", "[data-testid=scene-title]"),
            ("combat-treatment", ".combat-panel"),
        ):
            captures[name] = browser.evaluate(
                "(()=>{const e=document.querySelector(" + json.dumps(selector) + ");"
                "if(!e)return false; e.scrollTop=e.scrollHeight;"
                "e.scrollIntoView({block:'center'});return true;})()"
            )
            if captures[name]:
                screenshot(name + ".png")
        pending = browser.evaluate(
            "(()=>{const e=[...document.querySelectorAll('button')].find(b=>"
            "/掷骰|确认当前阶段|进行SAN|闪避|反击/.test(b.textContent)&&!b.disabled);"
            "if(!e)return null;e.scrollIntoView({block:'center'});return e.textContent;})()"
        )
        if pending:
            screenshot("pending-choice.png")
        write(
            browser.directory / "verification.json",
            {
                "room_id": rid,
                "browser_errors": browser.exceptions,
                "scene": browser.evaluate(
                    "document.querySelector('[data-testid=scene-title]')?.textContent"
                ),
                "read_only": True,
                "captures": captures,
                "enabled_rule_choice": pending,
                "public_messages_visible": bool(
                    browser.evaluate("document.querySelector('[data-testid=timeline]')?.innerText")
                ),
                "holder_visible": "持有" in content,
                "check_or_sanity_visible": "检定" in content or "SAN" in content,
                "combat_or_treatment_visible": "治疗" in content or "战斗" in content,
            },
        )
        print(browser.directory / "verification.json")
    finally:
        if browser.cdp:
            browser.cdp.close()
        for _, process in reversed(browser.processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for log in browser.logs:
            log.close()
        browser.http.close()


if __name__ == "__main__":
    inspect(sys.argv[1])
