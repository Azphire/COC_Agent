"""Real Chrome custom-specialization workflow against an isolated offline server."""

import base64
import json
import shutil
import sys

from check_character_creation import BACKEND, ROOT, TEST_HOST, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage


class CustomSkillsUI(SmokeCheck):
    def capture_custom(self, page, filename):
        page.evaluate(
            "document.querySelector('#custom-skill-name').closest('fieldset').scrollIntoView()"
        )
        result = page.command("Page.captureScreenshot", {"format": "png"})
        (page.directory / filename).write_bytes(base64.b64decode(result["data"]))

    def run(self):
        for port in (8000, 5173):
            port_free(port)
        self.start(
            [
                sys.executable,
                str(BACKEND / "scripts/check_character_creation.py"),
                "--serve",
                str(self.directory / "characters.db"),
            ],
            BACKEND,
            "backend",
        )
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").status_code == 200)
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
        wait_for(lambda: self.http.get("http://127.0.0.1:5173").status_code == 200)
        response = self.http.post(
            "http://127.0.0.1:8000/api/characters/point-buy",
            json={
                "ruleset_id": "coc7-character-creation",
                "name": "专业界面验证",
                "age": 25,
                "attributes": {
                    k: {"value": v}
                    for k, v in dict(
                        str=60, con=60, siz=60, dex=60, app=50, int=60, pow=50, edu=60
                    ).items()
                },
            },
        )
        response.raise_for_status()
        card = response.json()
        page = BrowserPage(self, "custom-skills")
        try:
            page.evaluate(f"sessionStorage.setItem('coc.host', {json.dumps(TEST_HOST)})")
            page.navigate("#/characters/" + card["id"])
            page.command("Page.reload")
            wait_for(lambda: page.contains("自定义专业"))
            page.fill("#occupation", "professor")
            names = [("language", "葡萄牙语"), ("art_craft", "陶艺"), ("science", "地球物理学")]
            for group, name in names:
                page.fill("#custom-skill-group", group)
                page.fill("#custom-skill-name", name)
                page.click("添加专业")
            wait_for(lambda: page.contains("删除 地球物理学"))
            page.fill("#custom-skill-name", "地球物理学")
            page.click("添加专业")
            assert page.contains("该专业已存在")
            ids = page.evaluate(
                "[...document.querySelectorAll('[id^=interest_skills-custom_]')]"
                ".map(e => e.id.replace('interest_skills-', ''))"
            )
            assert len(ids) == 3
            language, art, science = ids
            page.evaluate(
                """(() => {
                const select = document.querySelector('#group-language');
                for (const option of select.options) option.selected = option.value === %s;
                select.dispatchEvent(new Event('change', {bubbles:true}));
            })()"""
                % json.dumps(language)
            )
            page.evaluate("""(() => {
                const select = document.querySelector('#group-academic');
                const selected = ['history','biology','chemistry','occult'];
                for (const option of select.options)
                    option.selected = selected.includes(option.value);
                select.dispatchEvent(new Event('change', {bubbles:true}));
            })()""")
            for field, key, points in [
                ("occupation_skills", "credit_rating", 20),
                ("occupation_skills", language, 30),
                ("interest_skills", art, 20),
                ("interest_skills", science, 10),
            ]:
                page.fill(f"#{field}-{key}", points)
            page.save_draft(card["version"])
            saved = page.current()
            assert saved["validation"]["valid"], saved["validation"]
            assert [saved["skill_values"][k] for k in ids] == [31, 25, 11]
            self.capture_custom(page, "saved.png")
            page.fill('[aria-label="修改陶艺名称"]', "陶瓷制作")
            page.save_draft(saved["version"])
            renamed = page.current()
            assert renamed["custom_specializations"][1]["id"] == art
            page.click("删除 地球物理学")
            page.save_draft(renamed["version"])
            deleted = page.current()
            assert science not in deleted["skill_values"]
            assert (
                deleted["remaining_points"]["interest"]
                == saved["remaining_points"]["interest"] + 10
            )
            page.command("Page.reload")
            wait_for(lambda: page.contains("陶瓷制作"))
            assert not page.contains("删除 地球物理学")
            assert not page.exceptions, page.exceptions
            self.capture_custom(page, "reloaded.png")
            self.report.update(
                status="passed",
                character_id=card["id"],
                checks=[
                    "three groups",
                    "duplicate rejection",
                    "occupation choice",
                    "allocation",
                    "save",
                    "stable rename",
                    "deletion refunds",
                    "reload",
                ],
                custom_ids=ids,
            )
        except Exception:
            page.screenshot("failed.png")
            (self.directory / "failed-page.txt").write_text(
                page.evaluate("document.body.innerText"), encoding="utf-8"
            )
            raise
        finally:
            page.cdp.close()


if __name__ == "__main__":
    check = CustomSkillsUI("batch26")
    try:
        check.run()
    finally:
        check.close()
