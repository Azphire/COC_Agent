"""Two complete card workflows through Chrome; isolated DB, no model generation."""

import json
import shutil
import sys

import httpx
from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage


def main():
    owner = SmokeCheck.__new__(SmokeCheck)
    owner.directory = ROOT / "data/prepared/changan/batch-21" / sys.argv[1]
    owner.directory.mkdir(parents=True, exist_ok=False)
    owner.processes, owner.logs = [], []
    owner.http = httpx.Client(
        trust_env=False, timeout=10, headers={"Authorization": "Bearer " + TEST_HOST}
    )
    page = None
    result = {
        "status": "failed",
        "model": "catalogue fixture only; no model generation",
        "cards": [],
    }
    try:
        for port in (8000, 5173):
            port_free(port)
        command = [
            sys.executable,
            str(BACKEND / "scripts/check_character_creation.py"),
            "--serve",
            str(owner.directory / "game.db"),
        ]
        backend = owner.start(command, BACKEND, "backend")
        wait_for(lambda: owner.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
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
        page = BrowserPage(owner, "chrome")
        page.evaluate(
            f"sessionStorage.setItem('coc.host',{json.dumps(TEST_HOST)}); location.reload()"
        )
        wait_for(lambda: page.contains("创建房间"))
        for mode, occupation in [("random", "doctor"), ("point_buy", "musician")]:
            page.navigate("#/create")
            wait_for(lambda: page.contains("随机生成整组属性并保存草稿"))
            page.fill("#character-name", "第二十一批" + occupation)
            if mode == "point_buy":
                page.fill("#creation-mode", mode)
                page.click("创建购点草稿")
            else:
                page.click("随机生成整组属性并保存草稿")
            wait_for(lambda: page.contains("掷骰记录"))
            original = page.current()
            if mode == "point_buy":
                for key, value in dict(
                    str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60
                ).items():
                    page.fill("#attribute-" + key, value)
            page.fill("#character-era", "modern")
            page.fill("#occupation", occupation)
            groups = (
                {"academic": ["history", "psychoanalysis"]}
                if occupation == "doctor"
                else {
                    "instrument": ["art_violin"],
                    "social": ["charm"],
                    "personal": ["language_french", "history", "photography", "spot_hidden"],
                }
            )
            if occupation == "musician":
                page.fill("#occupation-attribute", "pow")
            for key, choices in groups.items():
                page.evaluate(
                    """(() => { const e=document.querySelector(%s); const values=%s;
                    for(const o of e.options)o.selected=values.includes(o.value);
                    e.dispatchEvent(new Event('change',{bubbles:true})); })()"""
                    % (json.dumps("#group-" + key), json.dumps(choices))
                )
            page.evaluate("document.querySelector('#specialization-science_physics').click()")
            page.fill("#occupation_skills-credit_rating", 30)
            page.fill("#interest_skills-science_physics", 40)
            for key, value in dict(
                appearance="旧外套",
                beliefs="求实",
                people="姐姐",
                places="故乡",
                possessions="怀表",
                traits="谨慎",
            ).items():
                page.fill("#background-" + key, value)
            page.fill("#key-connection", "people")
            page.click("添加资产明细")
            page.fill('[aria-label="资产说明1"]', "银行存款")
            page.fill('[aria-label="资产估值1"]', 100)
            page.fill('[aria-label="装备目录"]', "knife")
            page.click("添加装备")
            page.fill('[aria-label="装备目录"]', "")
            page.click("添加装备")
            page.fill('[aria-label="装备名称2"]', "家书")
            page.save_draft(original["version"])
            saved = page.current()
            assert saved["validation"]["valid"], saved["validation"]
            assert saved["roll_records"] == original["roll_records"]
            page.command("Page.reload")
            wait_for(lambda: page.contains("编辑角色草稿"))
            assert page.current() == saved
            page.click("最终确认")
            wait_for(lambda: page.contains("角色卡 · 已最终确认"))
            finalized = page.current()
            page.click("导出 JSON")
            wait_for(
                lambda: page.evaluate(
                    "document.querySelector('#character-json').value.length > 100"
                )
            )
            exported = json.loads(page.evaluate("document.querySelector('#character-json').value"))
            assert exported["character"] == finalized
            (owner.directory / f"{occupation}-export.json").write_text(
                json.dumps(exported, ensure_ascii=False, indent=2), "utf8"
            )
            page.screenshot(f"{occupation}-confirmed.png")
            page.click("导入为新草稿")
            wait_for(lambda: page.contains("导入自：") and page.contains("编辑角色草稿"))
            imported = page.current()
            for field in (
                "background",
                "equipment",
                "asset_details",
                "finances",
                "skill_values",
                "attributes",
                "effective_attributes",
            ):
                assert imported[field] == finalized[field]
            assert [r["dice"] for r in imported["roll_records"]] == [
                r["dice"] for r in original["roll_records"]
            ]
            result["cards"].append(
                dict(
                    mode=mode,
                    occupation=occupation,
                    id=finalized["id"],
                    imported_id=imported["id"],
                    skill=imported["skill_values"]["science_physics"],
                )
            )
            print(mode, occupation, "create/save/reload/finalize/export/import passed", flush=True)
        owner.stop(backend)
        owner.start(command, BACKEND, "backend-restarted")
        wait_for(lambda: owner.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
        page.command("Page.reload")
        wait_for(lambda: page.contains("导入自："))
        assert page.current() == imported
        page.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False},
        )
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot("mobile.png")
        assert not page.exceptions, page.exceptions
        result.update(status="passed", process_restart=True, browser_errors=page.exceptions)
    except Exception as error:
        result["error"] = str(error)
        if page:
            page.screenshot("failed.png")
        raise
    finally:
        if page and page.cdp:
            page.cdp.close()
        for _, process in reversed(owner.processes):
            owner.stop(process)
        for log in owner.logs:
            log.close()
        owner.http.close()
        (owner.directory / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), "utf8"
        )


if __name__ == "__main__":
    main()
