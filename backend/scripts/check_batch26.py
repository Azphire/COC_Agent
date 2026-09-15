"""One natural local observation of the approved package; no summary/restore long run."""

import argparse
import hashlib
import json

from check_batch25 import PACKAGE, Batch25, read, write
from check_batch25_evidence import ROOT, database_evidence, turn_response


def audit_discovery(directory):
    result = read(directory / "result.json")
    room = read(directory / "final-room.json")
    evidence = database_evidence(result["database_path"], room["id"])
    step = result.get("steps", {}).get(result.get("acceptance_step", "observe"), {})
    baseline = result.get("baseline", {})
    events = [e for e in evidence["events"] if e["seq"] > step.get("before", {}).get("revision", 0)]
    calls = [c for c in evidence["calls"] if c["rowid"] > baseline.get("call_rowid", 0)]
    ids = result["entity_ids"]
    reveals = [e for e in events if e["type"] == "entity.revealed"]
    public = read(directory / "public-entities.json")
    plan = (step.get("plan") or {}).get("plan", {})
    rows = dict(
        actual_response=bool(step) and turn_response(step, events),
        selected_map=plan.get("focus", {}).get("action_target_id") == ids["train_map"],
        map_reveal=sum(e["payload"].get("id") == ids["train_map"] for e in reveals) == 1,
        no_unrelated_reveal=all(e["payload"].get("id") == ids["train_map"] for e in reveals),
        no_check=not read(directory / "all-checks.json"),
        public_map=ids["train_map"] in {e["id"] for e in public},
        hidden_detail=ids["map_erased"] not in {e["id"] for e in public},
        inventory_unchanged=bool(step) and step["before"]["inventory"] == room["inventory"],
        local_model=bool(calls)
        and all(c.get("provider") == "ollama" and c.get("token_usage") for c in calls),
        original_package=hashlib.sha256(PACKAGE.read_bytes()).hexdigest()
        == result["package_sha256"],
    )
    return dict(
        status="passed" if all(rows.values()) else "failed",
        rows=rows,
        room_id=room["id"],
        preparation_id=result["preparation_id"],
        natural_inputs=sum(
            e["type"] == "action.submitted" and e["seq"] > baseline.get("event_seq", 0)
            for e in evidence["events"]
        ),
        acceptance_inputs=sum(e["type"] == "action.submitted" for e in events),
        model_calls=len(calls),
        tokens={
            k: sum((c.get("token_usage") or {}).get(k) or 0 for c in calls)
            for k in ("input", "output")
        },
        total_token_basis="input + output",
        configuration=result["configuration"],
    )


class Batch26(Batch25):
    batch_directory = ROOT / "data/prepared/changan/batch-26"
    audit_report = staticmethod(audit_discovery)

    def advance(self):
        stage = (
            "observe_after_fix"
            if self.args.after_fix
            else self.result.get("acceptance_step", "observe")
        )
        if self.args.after_fix:
            assert self.result["steps"]["observe"]["status"] == "completed"
            assert not self.request("GET", self.prefix + "/checks")
        self.result["acceptance_step"] = stage
        self.persist()
        self.step(stage, "我沿着车门旁的墙面逐一检查图示和标识，看看上面画了什么。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--after-fix", action="store_true")
    parser.add_argument("--backend-port", type=int, default=8027)
    parser.add_argument("--frontend-port", type=int, default=5176)
    parser.set_defaults(
        provider="ollama",
        model=None,
        base_url=None,
        output_mode=None,
        authorize_openai_formal_context=False,
        source_batch24=False,
        refresh_summary=False,
    )
    args = parser.parse_args()
    if args.after_fix and not (args.resume and args.live):
        parser.error("--after-fix requires --resume --live and retains the completed first attempt")
    if args.audit_only:
        directory = (Batch26.batch_directory / args.run).resolve()
        assert directory.is_relative_to(Batch26.batch_directory.resolve())
        report = audit_discovery(directory)
        write(directory / "verified-evidence.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report["status"] == "passed" else 2)
    runner = Batch26(args)
    runner.run_batch()
    raise SystemExit(0 if runner.result.get("acceptance") == "passed" else 2)
