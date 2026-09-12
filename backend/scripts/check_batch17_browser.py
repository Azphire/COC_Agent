"""Browser verification of the frozen batch17 room; separate from model acceptance."""

import base64
import shutil
import time

import httpx
from check_batch17 import DIRECTORY
from check_character_creation import SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage
from module_package import ROOT, read, write


def main():
    owner = SmokeCheck.__new__(SmokeCheck)
    owner.directory = DIRECTORY / "browser"
    owner.directory.mkdir(parents=True, exist_ok=True)
    owner.logs, owner.processes = [], []
    owner.http = httpx.Client(trust_env=False, timeout=10)
    page = None
    result = {"status": "failed", "checks": [], "supplement_user_approved": False}
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
        prep = read(DIRECTORY / "load-info.json")["preparation_id"]
        page.navigate("#/preparations")
        wait_for(
            lambda: page.evaluate(
                f"!!document.querySelector('#preparation-select option[value=\"{prep}\"]')"
            )
        )
        page.fill("#preparation-select", prep)
        wait_for(lambda: page.contains("243 个原文块"))
        result["checks"].append("source coverage and pending numeric supplement visible")
        page.screenshot("preparation.png")
        live = read(DIRECTORY / "session.json")["prefix"].rsplit("/", 1)[-1]
        page.navigate("#/rooms/" + live)
        wait_for(lambda: page.contains("调查板") and page.contains("战斗与伤势"))
        for selector, filename in [
            ("[data-testid=investigation-board]", "holders.png"),
            ("[data-testid=module-navigation]", "navigation.png"),
        ]:
            page.evaluate(f"document.querySelector('{selector}').scrollIntoView()")
            time.sleep(0.2)
            shot = page.command(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (owner.directory / filename).write_bytes(base64.b64decode(shot["data"]))
        text = page.evaluate("document.body.innerText")
        result["holder_display"] = "持有者：" in text
        result["checks"] += [
            "live room navigation",
            "investigation board",
            "combat and treatment panel",
        ]
        page.evaluate(
            "Array.from(document.querySelectorAll('h2')).find(x=>x.textContent==='战斗与伤势').scrollIntoView()"
        )
        page.evaluate(
            "Array.from(document.querySelectorAll('.combat-panel details'))"
            ".find(x=>x.querySelector('summary')?.textContent==='治疗伤势').open=true"
        )
        assert page.evaluate(
            "!!document.querySelector('select[aria-label=治疗行动者]') && "
            "!!document.querySelector('select[aria-label=治疗对象]')"
        )
        result["treatment_entry_opened"] = True
        shot = page.command(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
        )
        (owner.directory / "combat-treatment.png").write_bytes(base64.b64decode(shot["data"]))
        result.update(
            status="passed", room_id=live, preparation_id=prep, browser_errors=page.exceptions
        )
        assert not page.exceptions
    except Exception as error:
        result["error"] = str(error)
        if page:
            page.screenshot("failed.png")
        raise
    finally:
        write(owner.directory / "result.json", result)
        if page and page.cdp:
            page.cdp.close()
        for _, process in reversed(owner.processes):
            owner.stop(process)
        for log in owner.logs:
            log.close()
        owner.http.close()


if __name__ == "__main__":
    main()
