"""Recheck the unchanged Batch 45 question from an isolated real preparation.

Copy coherent SQLite snapshots read-only, including source WAL contents. Member
replacement, reassignment, cancellation and the fresh submission use normal APIs.
No evidence, result, character statistic or game-state row is injected.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3

from validate_batch45 import FIXED_ACTION, OUTPUT, FixedCheck, read, write


class ResumeCheck(FixedCheck):
    def __init__(self, name, source):
        source = source.resolve()
        assert source.is_relative_to(OUTPUT.resolve()) and source != OUTPUT.resolve()
        original = read(source / "acceptance.json")
        assert original["preparation_status"] == "passed"
        assert original["question"] == FIXED_ACTION
        with sqlite3.connect(f"file:{(source / 'game.db').as_posix()}?mode=ro", uri=True) as db:
            cycle_id, status, raw = db.execute(
                "SELECT id,status,state FROM agent_cycles ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            state = json.loads(raw)
            assert status == "failed" and state["call_count"] == state["tool_count"] == 0
            assert not db.execute(
                "SELECT 1 FROM room_events WHERE type LIKE 'handout.%' LIMIT 1"
            ).fetchone(), "Member replacement requires a fixture with no private handout history"
        super().__init__(name)
        self.source, self.original, self.failed_cycle = source, original, cycle_id
        provenance = {
            "source_directory": str(source),
            "source_cycle": cycle_id,
            "method": "SQLite read-only backup including WAL contents",
            "databases": {},
        }
        for filename in ("game.db", "checkpoint.db", "knowledge.db"):
            path = source / filename
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            wal = source / (filename + "-wal")
            wal_before = hashlib.sha256(wal.read_bytes()).hexdigest() if wal.exists() else None
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as old:
                with sqlite3.connect(self.directory / filename) as new:
                    old.backup(new)
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            assert before == after
            wal_after = hashlib.sha256(wal.read_bytes()).hexdigest() if wal.exists() else None
            assert wal_before == wal_after
            provenance["databases"][filename] = {
                "original_sha256": before,
                "original_wal_sha256": wal_before,
                "unchanged": True,
            }
        if (source / "model-settings.json").exists():
            shutil.copy2(source / "model-settings.json", self.directory / "model-settings.json")
        write(self.directory / "clone-provenance.json", provenance)
        fixed = read(self.directory / "fixed-case.json")
        fixed.update(
            setup_mode="reuse real preparation; normal new-member submission",
            reused_preparation_from=str(source),
            scope="New cycle and new member credentials; original history and cards retained",
        )
        write(self.directory / "fixed-case.json", fixed)
        self.result.update(fixture_origin=str(source), submission_mode="fresh normal player action")

    def setup(self):
        self.result.update(
            room_id=self.original["room_id"], preparation_id=self.original["preparation_id"]
        )
        self.prefix = "/rooms/" + self.original["room_id"]
        configured = read(self.directory / "effective-config.json")
        assert configured == read(self.source / "effective-config.json")
        room = self.request("GET", self.prefix)
        old_members = {
            m["display_name"]: m
            for m in room["members"]
            if m["active"] and m["role"] == "player" and m["controller_type"] == "human"
        }
        assert set(old_members) == {"丹尼尔", "观察成员"}
        assert not room.get("handouts", {}).get("assignments")
        assert all(
            not s["character_snapshot"].get("module_handout") for s in room["character_slots"]
        )
        slots = {s["member_id"]: s["id"] for s in room["character_slots"] if s["member_id"]}
        self.request("POST", self.prefix + "/pause")
        self.request("POST", self.prefix + "/agent-cycle/cancel")
        for member in old_members.values():
            self.request("PATCH", self.prefix + "/members/" + member["id"], {"active": False})
        invite = self.request("POST", self.prefix + "/invite/rotate")["invite_code"]
        self.members, self.cards = {}, {}
        for role, name in (("player", "丹尼尔"), ("witness", "观察成员")):
            joined = self.request(
                "POST", "/rooms/join", {"invite_code": invite, "display_name": name}
            )
            self.auth[role] = joined["member_token"]
            member = joined["room"]["self_member_id"]
            self.members[role], self.cards[role] = member, {}
            self.request(
                "POST",
                self.prefix + "/character-assignments",
                {"slot_id": slots[old_members[name]["id"]], "member_id": member},
            )
            self.request("POST", self.prefix + "/ready", {"ready": True}, actor=role)
        with sqlite3.connect(
            f"file:{(self.directory / 'game.db').as_posix()}?mode=ro", uri=True
        ) as db:
            self.keeper_profile_id = next(
                pid
                for pid, raw in db.execute("SELECT id,document FROM agent_profiles")
                if json.loads(raw)["role"] == "keeper"
            )
        for path in sorted(self.source.glob("compression-*.json")):
            summary = read(path)
            self.compression_results.append(summary)
            write(self.directory / path.name, summary)
        self.request("POST", self.prefix + "/resume")
        write(
            self.directory / "fixture-adjustment-events.json",
            self.request("GET", self.prefix + "/logs", actor="player"),
        )
        self.preflight(configured)
        self.open_windows()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    check = ResumeCheck(args.name, OUTPUT / args.source)
    try:
        check.run()
    except Exception as error:
        check.result.update(error=str(error), status="failed")
        try:
            check.capture_failure()
        except Exception as capture_error:
            check.result["capture_error"] = str(capture_error)
        raise
    finally:
        check.close()


if __name__ == "__main__":
    main()
