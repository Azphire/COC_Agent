"""Short serial local-model acceptance, reusing batch-10 HTTP/browser helpers."""

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from check_character_creation import BACKEND, ROOT, TEST_HOST, wait_for
from check_sanity import SanityCheck


def serve(directory):
    import uvicorn

    from app.config import Settings
    from app.main import create_app

    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / ".cache/batch-11").resolve()):
        raise ValueError("Batch 11 isolation required")
    settings = Settings(
        host_admin_token=TEST_HOST,
        data_dir=directory / "data",
        database_url=f"sqlite+aiosqlite:///{(directory / 'game.db').as_posix()}",
        knowledge_db_path=directory / "knowledge.db",
        checkpoint_db_path=directory / "checkpoint.db",
    )
    if settings.model_provider != "ollama" or urlparse(settings.model_base_url).hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("Existing local Ollama only")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8000, log_level="warning")


class Batch11Check(SanityCheck):
    def __init__(self):
        super().__init__(original=True, batch=11)
        self.report["batch"] = 11
        self.report["host_interventions"].append(
            "事先批准 Clicker action_target 自动首次遭遇；仅冻结行动者。"
            "另由主机配置车厢声音的困难辨听任务（普通聆听检定），用于检定后处理验收。"
        )

    def start_backend(self):
        self.backend = self.start(
            [sys.executable, str(Path(__file__).resolve()), "--serve", str(self.directory)],
            BACKEND,
            f"backend-{len(self.processes)}",
        )
        wait_for(lambda: self.http.get("http://127.0.0.1:8000/api/health").is_success)

    def request(self, method, path, body=None):
        if path.endswith("/sanity/encounters"):
            raise AssertionError(
                "Automatic SAN missing: host supplementation forbidden in batch 11"
            )
        if path == "/module-entities" and body:
            body = dict(body)
            if body.get("sanity_effects"):
                body["sanity_effects"] = [
                    {**e, "automation": "automatic", "repeat": "first_only"}
                    for e in body["sanity_effects"]
                ]
            if body.get("title") == "车厢内的声音":
                body.update(
                    type="location",
                    initial_visibility="hidden",
                    reveal_conditions={
                        "access_policy": "requires_check",
                        "successful_check": {
                            "kind": "skill",
                            "name": "listen",
                            "difficulty": "regular",
                        },
                    },
                )
        if body:
            body = {
                k: v.replace("第十批", "第十一批") if isinstance(v, str) else v
                for k, v in body.items()
            }
        result = super().request(method, path, body)
        if path == "/module-entities" and body and body.get("title") == "车厢内的声音":
            self.sound_entity = result["id"]
        return result

    def turn(self, text, target=None):
        if not getattr(self, "sound_visible", False):
            self.request("POST", self.prefix + f"/entities/{self.sound_entity}/reveal")
            self.sound_visible = True
            self.report["host_interventions"].append(
                "主机确认可听到喘息声；辨听细节仍按批准检定条件。"
            )
        if getattr(self, "luck_done", False):
            return super().turn(text, target)
        room = self.request("GET", self.prefix)
        self.request(
            "PATCH",
            self.prefix + "/check-rules",
            {
                "luck_spending": True,
                "expected_revision": room["revision"],
            },
        )
        event, cycle = super().turn(
            "我冒着暴露位置的风险，靠近车厢内的声音，仔细辨听喘息声中隐藏的细节。"
            "按主机批准的条件申请普通聆听检定。",
            target,
        )
        assert cycle["status"] == "waiting_for_roll", cycle
        check = next(
            c
            for c in self.request("GET", self.prefix + "/checks")
            if c["status"] == "pending" and not c.get("sanity")
        )
        base = "http://127.0.0.1:8000/api" + self.prefix + f"/checks/{check['id']}"
        response = self.player_http.post(base + "/roll", json={})
        assert response.is_success, response.text
        check = next(
            c for c in self.request("GET", self.prefix + "/checks") if c["id"] == check["id"]
        )
        self.report["ordinary_raw"] = check
        assert check.get("options", {}).get("luck"), (
            "No natural eligible Luck branch; do not alter dice"
        )
        self.request("POST", self.prefix + "/pause")
        snapshot = self.request(
            "POST", self.prefix + "/snapshots", {"name": "待选择幸运，重启续算"}
        )["snapshot"]
        self.stop(self.backend)
        self.start_backend()
        self.request("POST", self.prefix + f"/snapshots/{snapshot['id']}/load")
        self.request("POST", self.prefix + "/resume")
        choice = {"operation": "luck", "spend": 1}
        first = self.player_http.post(base + "/choice", json=choice)
        assert first.is_success, first.text
        second = self.player_http.post(base + "/choice", json=choice)
        assert second.is_success, second.text
        self.report["ordinary_final"] = first.json()["check"]
        cycle = self.wait_completed()
        assert cycle["status"] == "completed", cycle
        self.request("POST", self.prefix + "/pause")
        paid = self.request("POST", self.prefix + "/snapshots", {"name": "已扣幸运，保留原骰点"})[
            "snapshot"
        ]
        loaded = self.request("POST", self.prefix + f"/snapshots/{paid['id']}/load")["room"]
        slot = check["slot_id"]
        assert (
            loaded["session_state"]["characters"][slot]["luck"]
            == first.json()["check"]["settlement"]["luck_after"]
        )
        self.request("POST", self.prefix + "/resume")
        self.report["luck_restart_snapshot"] = snapshot["id"]
        self.report["luck_paid_snapshot"] = paid["id"]
        self.luck_done = True
        return event, cycle

    def export(self):
        super().export()
        if hasattr(self, "prefix"):
            runs = self.request("GET", self.prefix + "/agent-runs")
            (self.directory / "model-call-summary.json").write_text(
                json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8"
            )


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        check = Batch11Check()
        print("DIRECTORY=" + str(check.directory), flush=True)
        try:
            check.run()
        finally:
            try:
                check.export()
            finally:
                check.close()
