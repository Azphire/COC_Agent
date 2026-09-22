"""Small, serial real-model probes through the normal character and room HTTP APIs."""

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

from scripts.prepare_batch41 import QUEUE, ROOT, digest, read, write

os.environ["COC_PLAY_BASE"] = "data/prepared/batch-41"
os.environ["COC_PLAY_PORT"] = "8041"
from scripts import play_batch36, play_batch39  # noqa: E402


class Session(play_batch39.Session):
    def card(
        self,
        name,
        occupation,
        groups,
        priority,
        *,
        era="modern",
        age=29,
        equipment=None,
        occupation_attribute=None,
    ):
        cards = self.state.setdefault("cards", {})
        if name not in cards:
            attributes = dict(str=50, con=60, siz=55, dex=60, app=55, int=65, pow=60, edu=55)
            card = self.req(
                "POST",
                "/characters/point-buy",
                {
                    "ruleset_id": "coc7-character-creation",
                    "name": name,
                    "age": age,
                    "attributes": {k: {"value": v} for k, v in attributes.items()},
                },
            )
            cards[name] = card["id"]
            self.save()
            write(self.directory / (name + "-creation.json"), card)
        card = self.req("GET", "/characters/" + cards[name])
        if card["status"] == "finalized":
            return card
        path = "/characters/" + card["id"]
        card = self.req(
            "PATCH",
            path,
            {
                "version": card["version"],
                "occupation": occupation,
                "occupation_skills": {},
                "interest_skills": {},
                "era": era,
                "equipment": equipment or [],
                **({"age_deductions": {"str": 5}} if 15 <= age <= 19 else {}),
                "occupation_group_choices": groups,
                "occupation_attribute": occupation_attribute,
                "selected_specializations": [
                    s
                    for v in groups.values()
                    for s in v
                    if s.startswith(("language_", "art_", "science_"))
                ],
            },
        )
        rules = self.req("GET", "/character-rulesets/coc7-character-creation")
        definition = next(o for o in rules["occupations"] if o["key"] == occupation)
        credit = definition.get("credit_rating_minimum") or 0
        allocations = {"credit_rating": {"points": credit}}
        remaining = card["remaining_points"]["occupation"] - credit
        for target in (50, 60, 70):
            for skill in dict.fromkeys([*priority, *card["effective_occupation_skills"]]):
                if skill == "credit_rating" or skill not in card["effective_occupation_skills"]:
                    continue
                old = allocations.get(skill, {}).get("points", 0)
                amount = min(
                    remaining, max(0, target - card["skill_base_values"].get(skill, 0) - old)
                )
                if amount:
                    allocations[skill] = {"points": old + amount}
                    remaining -= amount
        interest = {}
        remaining = card["remaining_points"]["interest"]
        for skill in ("spot_hidden", "listen", "dodge", "first_aid", "stealth", "persuade"):
            if skill in card["effective_occupation_skills"]:
                continue
            amount = min(remaining, max(0, 50 - card["skill_base_values"].get(skill, 0)))
            if amount:
                interest[skill] = {"points": amount}
                remaining -= amount
        card = self.req(
            "PATCH",
            path,
            {
                "version": card["version"],
                "occupation_skills": allocations,
                "interest_skills": interest,
            },
        )
        write(self.directory / (name + "-draft.json"), card)
        card = self.req("POST", path + "/finalize", {"version": card["version"]})
        write(self.directory / (name + "-frozen.json"), card)
        return card

    def settle(self):
        until = time.monotonic() + 720
        while time.monotonic() < until:
            self.poll()
            room = self.req("GET", self.prefix, player=True)
            cycle = room.get("game", {}).get("cycle") or {}
            status = cycle.get("status")
            if status not in {"running", "queued", "waiting_for_roll"}:
                if status in {"failed", "waiting_for_review"}:
                    raise ValueError(
                        f"Actual model cycle stopped at {status}; preserve room for review"
                    )
                return
        raise TimeoutError("Probe still active after bounded wait; retained room and logs")


def config(slug):
    police = (
        "陈若宁",
        "police_officer",
        {"firearms": ["handgun"], "social": ["persuade"], "transport": ["drive_auto"]},
        ["psychology", "spot_hidden", "law", "persuade"],
        "耐心记录报案，不急于下结论",
    )
    reporter = (
        "陆明",
        "journalist",
        {"art": ["photography"], "social": ["persuade"], "personal": ["listen", "law"]},
        ["library_use", "psychology", "persuade"],
        "重视事实与证据",
    )
    if slug == "xipula":
        return {
            "investigators": [police],
            "background": "2024年美国金斯波特警察",
            "character_options": {police[0]: {"occupation_attribute": "dex"}},
        }, [
            "我请报案女士坐下，拿笔记下她的姓名，再询问那五个人的外貌以及事情发生的具体地点。",
            "临分别前，我问奥利文能否给我留下联系方式，方便后续联络。",
        ]
    if slug == "changmingdeng":
        return {
            "investigators": [reporter],
            "era": "1920s",
            "background": "1925年中国，受外国古物爱好者委托寻访长明灯的记者",
        }, [
            "我沿大路走到眼前的茶馆，走进棚下，在桌旁坐下。",
            "我向招呼客人的老板娘打招呼，客气地说明自己是来访的记者，想请教长明灯的传说，以及最近村里与灯有关的事情。",
        ]
    if slug == "zhufeigangqin":
        priest = (
            "安娜修女",
            "clergy",
            {"language": ["language_latin"], "social": ["persuade"], "personal": ["first_aid"]},
            ["psychology", "listen", "persuade"],
            "善于倾听，温和克制",
        )
        escort = (
            "艾琳",
            "prostitute",
            {
                "art_craft": ["art_acting"],
                "social": ["charm", "persuade"],
                "personal": ["spot_hidden"],
            },
            ["charm", "psychology", "spot_hidden"],
            "独立机敏，注意周围人的反应",
        )
        driver = (
            "马克",
            "taxi_driver",
            {"personal": ["first_aid"]},
            ["drive_auto", "listen", "spot_hidden"],
            "务实谨慎的出租车司机",
        )
        return {
            "investigators": [priest, escort, driver, reporter],
            "era": "modern",
            "background": (
                "1973年欧洲小镇，四人分别以修女、应召女郎、出租车司机、记者身份受邀看演出；"
                "没有智能手机"
            ),
        }, ["我在座位上听报幕，确认今天演奏者的姓名和将要演奏的曲名，然后看看舞台的布置。"]
    if slug == "zhihulu":
        investigators = [
            (
                name,
                "student",
                {
                    "language": ["language_english"],
                    "academic": ["history", "psychology", "persuade"],
                    "personal": ["spot_hidden", "stealth"],
                },
                ["psychology", "persuade", "listen"],
                personality,
            )
            for name, personality in [
                ("林月", "HO1，19岁女生，主人家的千金"),
                ("许安", "HO2，19岁女生，林月的室友"),
                ("周晟", "HO3，19岁男生，富裕家庭的学生"),
            ]
        ]
        return {
            "investigators": investigators,
            "background": "现代中国，三名19岁学生参加生日宴",
            "character_options": {p[0]: {"age": 19} for p in investigators},
        }, ["我走到前院一位正在招呼客人的佣人旁，礼貌地问今晚晚宴在哪里举行，之后还有什么安排。"]
    raise ValueError("This source requires a non-CoC ruleset; use the native-rules blocker probe")


def verify_native_block(entry):
    from scripts.validate_batch41 import client_for, ok

    out = ROOT / entry["output_directory"]
    with client_for(out / "validation-final") as client:
        rules = ok(client.get("/api/character-rulesets"))
        wanted = "cthulhu-rising-brp" if entry["module_id"] == "muxingemeng" else "yaoling-d6"
        response = client.post(
            "/api/characters/point-buy",
            json={"ruleset_id": wanted, "name": "原生规则兼容性探针", "age": 29},
        )
        assert response.status_code in {404, 422}
        result = {
            "status": "blocked_native_rules",
            "source_hash": entry["source_hash"],
            "package_binding": read(out / "validation-final/validation.json"),
            "available_rulesets": [
                {"id": r["id"], "edition": r.get("edition"), "enabled": r.get("enabled")}
                for r in rules
            ],
            "requested_ruleset": wanted,
            "status_code": response.status_code,
            "reason": response.json(),
            "real_model_play": "not_started_with_invalid_cards",
            "recovery": "原规则完整卡/规则资料及对应规则适配后再使用本包建立新测试房间",
        }
        write(out / "short-validation.json", result)
        return result


def run(entry, run_name="short-run-01", actions_override=None):
    if entry["module_id"] in {"jinzhijiechu", "muxingemeng"}:
        return verify_native_block(entry)
    out = ROOT / entry["output_directory"]
    directory = out / run_name
    directory.mkdir(parents=True, exist_ok=True)
    old = out / "short-validation.json"
    if (
        old.exists()
        and read(old).get("status") in {"passed", "passed_public_probe_with_character_ruling"}
        and not actions_override
    ):
        if read(old)["package_file_sha256"] == digest(out / "package-reviewed.json"):
            return read(old)
    stdout = (directory / "server.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "scripts.play_batch41", "serve", "--directory", str(directory)],
        cwd=ROOT,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    session = None
    try:
        import httpx

        for _ in range(60):
            try:
                if httpx.get(
                    "http://127.0.0.1:8041/api/health", timeout=1, trust_env=False
                ).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        session = Session(directory)
        if session.state.get("preparation"):
            prior_binding = read(directory / "binding-verification.json")
            if prior_binding["package_file_sha256"] != digest(out / "package-reviewed.json"):
                raise ValueError("Existing room retains its prior package; choose a new --run-name")
        setup, actions = config(entry["module_id"])
        if actions_override:
            actions = actions_override
        setup.update(
            package_path=str(out / "package-reviewed.json"),
            room_name="第41批短验证 " + entry["title"],
        )
        if not session.state.get("preparation"):
            session.setup(**setup)
        session.settle()
        for action in actions:
            session.req(
                "POST",
                session.prefix + "/actions",
                {"text": action, "client_request_id": str(uuid4())},
                player=True,
            )
            session.settle()
        before = session.req("GET", session.prefix)
        saved = session.req("POST", session.prefix + "/snapshots", {"name": "第41批短验证保存"})
        write(directory / "natural-save.json", saved)
        session.req("POST", session.prefix + "/pause")
        snapshot = saved.get("snapshot", saved)
        session.req("POST", session.prefix + f"/snapshots/{snapshot['id']}/load")
        session.req("POST", session.prefix + "/resume")
        after = session.req("GET", session.prefix)
        assert before["session_state"] == after["session_state"]
        write(
            directory / "restore-verification.json",
            {
                "equal_session_state": True,
                "room_id": session.state["room_id"],
                "snapshot_id": snapshot["id"],
            },
        )
        session.observe()
        with sqlite3.connect((directory / "game.db").as_uri() + "?mode=ro", uri=True) as db:
            calls = [
                dict(zip(("id", "graph_node", "status", "provider", "model", "error_type"), row))
                for row in db.execute(
                    "SELECT id,graph_node,status,provider,model,error_type "
                    "FROM agent_runs WHERE room_id=?",
                    (session.state["room_id"],),
                )
            ]
        write(directory / "model-call-summary.json", calls)
        result = {
            "status": "awaiting_transcript_review",
            "source_hash": entry["source_hash"],
            "run_directory": str(directory.relative_to(ROOT)).replace("\\", "/"),
            "binding": read(directory / "binding-verification.json"),
            "real_model_calls": len(calls),
            "save_restore_equal": True,
            "actions": actions,
            "source_character_limits": setup,
        }
        write(out / "short-validation.json", result)
        write(directory / "short-validation.json", result)
        return result
    finally:
        if session:
            session.http.close()
        (directory / "stop-service").touch()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
        stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["run", "serve"])
    parser.add_argument("--module", action="append", default=[])
    parser.add_argument("--directory")
    parser.add_argument("--run-name", default="short-run-01")
    parser.add_argument("--action", action="append", help="Continue the chosen isolated room")
    args = parser.parse_args()
    if args.operation == "serve":
        play_batch36.serve(ROOT / args.directory, port=8041)
        return
    for entry in read(QUEUE / "preparation-inventory.json")["modules"]:
        if entry["decision"] == "skip_valid" or (
            args.module and entry["module_id"] not in args.module
        ):
            continue
        try:
            result = run(entry, args.run_name, args.action)
            print(entry["module_id"], result["status"], flush=True)
        except Exception as error:
            import traceback

            out = ROOT / entry["output_directory"]
            write(
                out / "short-validation.json",
                {
                    "status": "failed",
                    "error": str(error),
                    "run_directory": str((out / args.run_name).relative_to(ROOT)),
                },
            )
            (out / args.run_name / "failure.txt").write_text(traceback.format_exc(), "utf-8")
            print(entry["module_id"], "failed", str(error)[:400], flush=True)


if __name__ == "__main__":
    main()
