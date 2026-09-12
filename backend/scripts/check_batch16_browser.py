"""Read/operate the batch16 isolated app with real Chrome, never the user's DB."""

import base64
import shutil
import time

import httpx
from check_batch16 import DIRECTORY, ROOT, read, write
from check_character_creation import SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage


def capture_viewport(page, filename):
    # The older shared screenshot helper deliberately scrolls to the page top.
    result = page.command(
        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
    )
    (page.directory / filename).write_bytes(base64.b64decode(result["data"]))


def main():
    owner = SmokeCheck.__new__(SmokeCheck)
    owner.directory = DIRECTORY / "browser"
    owner.directory.mkdir(parents=True, exist_ok=True)
    owner.logs, owner.processes = [], []
    owner.http = httpx.Client(
        trust_env=False, timeout=10, headers={"Authorization": "Bearer local-package-review"}
    )
    page = None
    try:
        port_free(5173)
        owner.start(
            [
                shutil.which("node"),
                str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
            ],
            ROOT / "frontend",
            "frontend",
        )
        wait_for(lambda: owner.http.get("http://127.0.0.1:5173").status_code == 200)
        page = BrowserPage(owner, "host")
        page.evaluate(
            "sessionStorage.setItem('coc.host','local-package-review'); location.reload()"
        )
        wait_for(lambda: page.contains("创建房间"))
        page.navigate("#/preparations")
        prep = read(DIRECTORY / "load-info.json")["preparation_id"]
        wait_for(
            lambda: page.evaluate(
                f"!!document.querySelector('#preparation-select option[value=\"{prep}\"]')"
            )
        )
        page.fill("#preparation-select", prep)
        wait_for(lambda: page.contains("导入时全文覆盖校对"))
        assert page.contains("243 个原文块") and page.contains("7 个可玩场景")
        page.evaluate(
            "document.querySelector('[data-testid=preparation-coverage]').scrollIntoView()"
        )
        time.sleep(0.2)
        capture_viewport(page, "preparation.png")
        page.navigate("#/rooms")
        wait_for(lambda: page.contains("创建房间"))
        page.fill("#room-name", "第十六批 · 浏览器绑定验收")
        page.click("创建房间")
        wait_for(lambda: page.evaluate("location.hash.startsWith('#/rooms/')"))
        room_id = page.evaluate("location.hash.slice(8)")
        wait_for(lambda: page.contains("绑定准备版本"))
        wait_for(
            lambda: page.evaluate(
                f"!!document.querySelector('#room-preparation option[value=\"{prep}\"]')"
            )
        )
        selectors = page.evaluate(
            "Array.from(document.querySelectorAll('select')).map(x=>({id:x.id,options:Array.from(x.options).map(o=>({value:o.value,text:o.text}))}))"
        )
        select = next(s for s in selectors if any(o["value"] == prep for o in s["options"]))
        assert select["id"], "binding select needs a stable accessible selector"
        page.fill("#" + select["id"], prep)
        page.click("绑定准备版本")
        wait_for(lambda: page.contains("当前场景导航"))
        assert page.contains("调查板") and page.contains("战斗与伤势")
        assert page.evaluate(r"/活动 NPC\s*0/.test(document.body.innerText)")
        page.evaluate("document.querySelector('[data-testid=module-navigation]').scrollIntoView()")
        time.sleep(0.2)
        capture_viewport(page, "bound-room.png")
        live = read(DIRECTORY / "session.json")["prefix"].rsplit("/", 1)[-1]
        page.navigate("#/rooms/" + live)
        wait_for(lambda: page.contains("当前场景导航"))
        page.evaluate(
            "document.querySelector('[data-testid=module-navigation] details').open=true; "
            "document.querySelector('[data-testid=module-navigation]').scrollIntoView()"
        )
        wait_for(lambda: page.contains("结局 A"))
        assert page.evaluate(
            "document.querySelector('[data-testid=module-navigation] form button').disabled"
        )
        time.sleep(0.2)
        capture_viewport(page, "live-room.png")
        page.evaluate(
            "document.querySelector('[data-testid=investigation-board]').scrollIntoView()"
        )
        time.sleep(0.2)
        capture_viewport(page, "investigation-board.png")
        page.evaluate(
            "Array.from(document.querySelectorAll('h2')).find(x=>x.textContent==='战斗与伤势').scrollIntoView()"
        )
        time.sleep(0.2)
        capture_viewport(page, "combat-panel.png")
        assert not page.exceptions, page.exceptions
        write(
            owner.directory / "result.json",
            {
                "status": "passed",
                "preparation_id": prep,
                "new_room_id": room_id,
                "checks": [
                    "coverage UI",
                    "new room binding",
                    "navigation",
                    "investigation board",
                    "combat panel",
                ],
                "browser_errors": [],
            },
        )
        print("Browser verification passed")
    except Exception:
        if page:
            page.screenshot("failed.png")
            write(
                owner.directory / "failure.json",
                {"text": page.evaluate("document.body.innerText"), "exceptions": page.exceptions},
            )
        raise
    finally:
        if page and page.cdp:
            page.cdp.close()
        for _, process in reversed(owner.processes):
            owner.stop(process)
        for log in owner.logs:
            log.close()
        owner.http.close()


if __name__ == "__main__":
    main()
