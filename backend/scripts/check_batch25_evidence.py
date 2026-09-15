"""Read-only batch 25 audit. No server, inference, state repair or dice replay."""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
BATCH = ROOT / "data/prepared/changan/batch-25"
STATE_KEYS = (
    "characters",
    "module_runtime",
    "combat",
    "time_receipts",
    "game_minute",
    "game_round",
    "sanity_day",
)


def read(path):
    return json.loads(Path(path).read_text("utf-8-sig"))


def summary_body(content):
    """Navigation wraps retained summary prose with current scene metadata."""
    try:
        value = json.loads(content)
    except ValueError:
        return content
    if (
        isinstance(value, dict)
        and set(value) == {"content", "current_scene_node_id", "heading_path"}
        and isinstance(value["content"], str)
    ):
        return value["content"]
    return content


def preserved_summaries(expected, saved, active):
    return (
        bool(expected)
        and expected == saved
        and {key: summary_body(value) for key, value in expected.items()}
        == {key: summary_body(value) for key, value in active.items()}
    )


def database_evidence(path, room_id):
    """Use SQLite read-only mode, scoped to this room, retaining rowid call boundaries."""
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        events = [
            dict(seq=s, type=t, payload=json.loads(p), client_request_id=q, actor_member_id=a)
            for s, t, p, q, a in db.execute(
                "SELECT seq,type,payload,client_request_id,actor_member_id "
                "FROM room_events WHERE room_id=? ORDER BY seq",
                (room_id,),
            )
        ]
        calls = [
            dict(rowid=n, **json.loads(d))
            for n, d in db.execute(
                "SELECT c.rowid,c.document FROM agent_model_calls c "
                "JOIN agent_runs r ON r.id=c.run_id WHERE r.room_id=? ORDER BY c.rowid",
                (room_id,),
            )
        ]
        cycles = [
            dict(id=i, status=s, state=json.loads(d))
            for i, s, d in db.execute(
                "SELECT id,status,state FROM agent_cycles WHERE room_id=?", (room_id,)
            )
        ]
        plans = {
            i: json.loads(d)
            for i, d in db.execute(
                "SELECT cycle_id,document FROM agent_action_plans WHERE room_id=?", (room_id,)
            )
        }
        receipts = [
            dict(id=i, run_id=r, result=json.loads(d))
            for i, r, d in db.execute(
                "SELECT id,run_id,result FROM agent_tool_receipts WHERE room_id=?", (room_id,)
            )
        ]
    return dict(events=events, calls=calls, cycles=cycles, plans=plans, receipts=receipts)


def comparison(before, after, before_checks, after_checks):
    return {
        **{
            k: k in before["session_state"]
            and k in after["session_state"]
            and before["session_state"][k] == after["session_state"][k]
            for k in STATE_KEYS
        },
        "checks": before_checks == after_checks,
        "inventory": before["inventory"] == after["inventory"],
    }


def turn_response(step, events):
    """A completed cycle is insufficient: require its submitted request and actual reply."""
    cycle = step.get("cycle") or {}
    start = step.get("before", {}).get("revision", 0)
    end = step.get("after", {}).get("revision", start)
    submitted = [
        e
        for e in events
        if start < e["seq"] <= end
        and e["type"] == "action.submitted"
        and e.get("client_request_id") == step["request"]["client_request_id"]
    ]
    replies = [
        e
        for e in events
        if start < e["seq"] <= end
        and e["type"] == "keeper.narration"
        and e["payload"].get("cycle_id") == cycle.get("id")
        and e["payload"].get("text", "").strip()
    ]
    return cycle.get("status") == "completed" and len(submitted) == 1 and bool(replies)


def check_result(check, events, before_roll, target):
    fixed = [
        e
        for e in events
        if e["type"] == "check.dice_fixed" and e["payload"].get("check_id") == check["id"]
    ]
    settled = [
        e for e in events if e["type"] == "check.resolved" and e["payload"].get("id") == check["id"]
    ]
    requested = [
        e
        for e in events
        if e["type"] == "check.requested" and e["payload"].get("id") == check["id"]
    ]
    original = (check.get("settlement") or {}).get("original_dice")
    actions = (before_roll.get("plan") or {}).get("validation", {}).get("approved_actions", [])
    tools = [a["tool"] for a in actions]
    bound = (
        before_roll.get("check", {}).get("clue_id") == target
        and before_roll.get("check", {}).get("policy_target_id") == target
        and any(
            t["name"] == "request_skill_check" and t.get("arguments", {}).get("clue_id") == target
            for t in tools
        )
        and any(
            t["name"] in {"reveal_clue", "reveal_entity"}
            and target in t.get("arguments", {}).values()
            for t in tools
        )
    )
    dice_ok = bool(
        original
        and len(fixed) == len(settled) == len(requested) == 1
        and requested[0]["seq"]
        <= before_roll.get("room", {}).get("revision", 0)
        < fixed[0]["seq"]
        < settled[0]["seq"]
        and original == fixed[0]["payload"]["dice"] == check.get("dice")
        and original.get("roll_record", {}).get("source") == "system"
        and check.get("result") == settled[0]["payload"].get("result")
        and check.get("status") == "resolved"
    )
    reveals = [
        e
        for e in events
        if e["type"] in {"entity.revealed", "clue.revealed"}
        and (e["payload"].get("id") or e["payload"].get("clue_id")) == target
    ]
    passed = (check.get("result") or {}).get("passed")
    reveal_ok = (
        (len(reveals) == 1 and bool(settled) and reveals[0]["seq"] > settled[0]["seq"])
        if passed
        else not reveals
    )
    cycle_id = before_roll.get("cycle", {}).get("id")
    unrelated = [
        e["seq"]
        for e in events
        if cycle_id
        and e["type"] in {"entity.revealed", "clue.revealed"}
        and e["payload"].get("cycle_id") == cycle_id
        and (e["payload"].get("id") or e["payload"].get("clue_id")) != target
    ]
    return dict(
        binding=bool(bound),
        dice=dice_ok,
        reveal=bool(reveal_ok) and not unrelated,
        passed=passed,
        check_id=check["id"],
        roll_id=(original or {}).get("roll_record", {}).get("id"),
        result=check.get("result"),
        fixed_seq=[e["seq"] for e in fixed],
        resolved_seq=[e["seq"] for e in settled],
        reveal_seq=[e["seq"] for e in reveals],
        unrelated_reveal_seq=unrelated,
    )


def owned_item(room, item_id, holder_id, events):
    items = [
        i
        for i in room.get("inventory", [])
        if i["item_id"] == item_id and i["holder_id"] == holder_id
    ]
    receipts = [
        e
        for e in events
        if e["type"] == "module.interaction_receipt"
        and e["payload"].get("entity_id") == item_id
        and e["payload"].get("interaction_id") == "take"
    ]
    valid = len(items) == len(receipts) == 1 and (
        receipts[0]["payload"].get("inventory", {}).get(items[0]["instance_id"]) == holder_id
    )
    return bool(valid), items, receipts


def continued_action(step, events, destination):
    """A recall reply, even to action-like text, is not an executed continuation."""
    plan = (step.get("plan") or {}).get("plan", {})
    moves = [
        e
        for e in events
        if e["type"] == "scene.updated"
        and step["before"]["revision"] < e["seq"] <= step["after"]["revision"]
    ]
    return bool(
        plan.get("focus", {}).get("action")
        and plan.get("parsed_intent", {}).get("type") == "move"
        and destination
        and len(moves) == 1
        and moves[0]["payload"].get("scene_title") == destination
        and step["after"]["session_state"]["scene_title"] == destination
    )


def formal_retest_status(config, calls, status, blocked=False):
    """A provider selection or successful transport alone is not a formal retest."""
    if config.get("provider") != "openai":
        return "not_run"
    if not calls:
        return "blocked" if blocked else "not_run"
    from urllib.parse import urlsplit

    url = urlsplit(config.get("base_url", ""))
    valid = (
        url.scheme == "https"
        and url.netloc == "api.openai.com"
        and url.path.rstrip("/") == "/v1"
        and not url.query
        and not url.fragment
        and all(
            c.get("provider") == "openai"
            and c.get("model") == config.get("model")
            and c.get("response_model")
            and c.get("token_usage")
            for c in calls
        )
    )
    return status if valid else "failed"


def audit(directory):
    directory = Path(directory).resolve()
    result = read(directory / "result.json")
    checks = read(directory / "all-checks.json") if (directory / "all-checks.json").exists() else []
    room = read(directory / "final-room.json") if (directory / "final-room.json").exists() else {}
    evidence = (
        database_evidence(result["database_path"], room["id"])
        if room
        else {"events": [], "calls": [], "cycles": [], "plans": {}, "receipts": []}
    )
    events = evidence["events"]
    rows = {}

    def item(name, condition=None, sources=(), missing="not_run", detail=None):
        rows[name] = dict(
            status=missing if condition is None else "passed" if condition else "failed",
            evidence=[str(s) for s in sources],
            detail=detail,
        )

    steps = result.get("steps", {})
    baseline = result.get("baseline", {})
    calls = [c for c in evidence["calls"] if c["rowid"] > baseline.get("call_rowid", 0)]
    new_events = [e for e in events if e["seq"] > baseline.get("event_seq", 0)]
    config = result["configuration"]
    from app.preparation.packages import translate_entity_refs
    from app.preparation.schemas import EntityFields

    package_path = ROOT / "data/prepared/changan/batch-21/package-approved.json"
    if room:
        package = read(package_path)
        matches = []
        with sqlite3.connect(Path(result["database_path"]).as_uri() + "?mode=ro", uri=True) as db:
            prep = json.loads(
                db.execute(
                    "SELECT document FROM module_preparations WHERE id=?",
                    (result["preparation_id"],),
                ).fetchone()[0]
            )
            for entry in package["entities"]:
                eid = prep["package_entity_ids"][entry["key"]]
                status, document = db.execute(
                    "SELECT status,document FROM module_entities WHERE id=?", (eid,)
                ).fetchone()
                expected = EntityFields.model_validate(
                    translate_entity_refs(entry["fields"], prep["package_entity_ids"])
                ).model_dump(mode="json")
                expected["tags"].append("package:" + entry["key"])
                actual = json.loads(document)
                matches.append(
                    status == "approved" and all(actual.get(k) == v for k, v in expected.items())
                )
        item(
            "approved_package",
            all(matches)
            and len(matches) == 44
            and prep["numeric_supplement"] == package["numeric_supplement"]
            and room["game"]["preparation"]["id"] == result["preparation_id"]
            and hashlib.sha256(package_path.read_bytes()).hexdigest() == result["package_sha256"],
            [package_path, "game.db#module_preparations", "game.db#module_entities"],
            detail={"matched_entities": sum(matches)},
        )
    item(
        "real_model",
        all(
            c.get("provider") == config["provider"]
            and c.get("model") == config["model"]
            and (c.get("response_model") or config["provider"] == "ollama")
            and c.get("token_usage")
            for c in calls
        )
        if calls
        else None,
        ["model-calls.json", "result.json#/configuration"],
    )
    inv = steps.get("investigate", {})
    target = result.get("entity_ids", {}).get(result.get("investigation_key", "map_erased"))
    selected = [c for c in checks if c.get("clue_id") == target] if target else []
    details = []
    if selected:
        for check in selected:
            p = directory / f"before-roll-{check['id']}-initial.json"
            details.append(check_result(check, events, read(p) if p.exists() else {}, target))
        for key, name in [
            ("binding", "pre_roll_binding"),
            ("dice", "original_result"),
            ("reveal", "corresponding_reveal"),
        ]:
            item(
                name,
                None
                if key != "binding" and details[0]["passed"] is None
                else len(details) == 1 and details[0][key],
                [
                    f"before-roll-{selected[0]['id']}-initial.json",
                    "all-checks.json",
                    "game.db#room_events",
                ],
                detail=details,
            )
        original_path = directory / f"before-roll-{selected[0]['id']}-initial.json"
        original = read(original_path) if original_path.exists() else {}
        approved = next(
            e["fields"]["reveal_conditions"]["successful_check"]
            for e in package["entities"]
            if result["entity_ids"][e["key"]] == target
        )
        item(
            "approved_check_condition",
            bool(original)
            and all(
                original.get("plan", {}).get("plan", {}).get("proposed_check", {}).get(k) == v
                for k, v in approved.items()
            ),
            [package_path, f"before-roll-{selected[0]['id']}-initial.json"],
            detail=approved,
        )
        for branch, value in [("success_branch", True), ("failure_branch", False)]:
            covered = [d for d in details if d["passed"] is value]
            item(
                branch,
                all(d["dice"] and d["reveal"] for d in covered) if covered else None,
                ["all-checks.json", "events.json"],
            )
    else:
        for name in ["pre_roll_binding", "original_result", "corresponding_reveal"]:
            item(
                name,
                False if inv.get("cycle", {}).get("status") == "completed" else None,
                ["step-investigate.json", "all-checks.json"],
                detail="No bound investigation result",
            )
        for name in ["success_branch", "failure_branch"]:
            item(name)
        item("approved_check_condition")
    public_path = directory / "public-entities.json"
    if public_path.exists() and room:
        board = read(public_path)
        revealed = {
            e["payload"].get("id") or e["payload"].get("clue_id")
            for e in events
            if e["type"] in {"entity.revealed", "clue.revealed"}
        }
        visible = {e["id"] for e in board}
        item(
            "public_board",
            visible <= revealed and (not details or (target in visible) == details[0]["passed"]),
            [public_path, "events.json"],
        )
    else:
        item("public_board")
    held, items, receipts = owned_item(
        room, result.get("entity_ids", {}).get("note_front"), result.get("player_id"), events
    )
    item(
        "actual_inventory",
        held if "take" in steps else None,
        ["final-room.json#/inventory", "events.json#module.interaction_receipt"],
        detail=dict(items=items, receipt_seqs=[e["seq"] for e in receipts]),
    )
    if "take" in steps and steps["take"].get("after"):
        take = steps["take"]
        item(
            "inventory_transition",
            held
            and not any(
                i["item_id"] == result["entity_ids"]["note_front"]
                for i in take["before"]["inventory"]
            )
            and receipts[0]["seq"] > take["before"]["revision"]
            and receipts[0]["seq"] <= take["after"]["revision"],
            ["step-take.json", "events.json"],
        )
    else:
        item("inventory_transition")
    summary = result.get("summary")
    if summary and "memories" in summary:
        text = "\n".join(
            m.get("content", "")
            for m in summary["memories"]
            if m.get("active", True) and m.get("kind") == "summary"
        )
        facts = [c.get("display_text", "") for c in selected]
        facts += [i["title"] for i in items]
        facts += [
            result.get("restore_record", {}).get("before", room)["session_state"]["scene_title"]
        ]
        board = (
            result.get("restore_record", {})
            .get("before", {})
            .get("game", {})
            .get("public_entities", read(public_path) if public_path.exists() else [])
        )
        facts += [e.get("public_summary", "") for e in board if e.get("type") != "scene"]
        hidden = result.get("hidden_facts", [])
        forbidden = [f["text"] for f in hidden if f["id"] not in {e["id"] for e in board}]
        item(
            "summary_facts",
            bool(text)
            and bool(selected)
            and held
            and all(f and f in text for f in facts)
            and not any(f in text for f in forbidden)
            and any(
                e["type"] == "agent.summary_rebuilt"
                and summary["before_seq"] < e["seq"] <= summary["after_seq"]
                for e in events
            ),
            ["summary.json", "events.json"],
            detail=dict(required=facts, forbidden=forbidden),
        )
    else:
        item("summary_facts")
    restore = result.get("restore_record", {})
    if restore.get("after"):
        comp = comparison(
            restore["before"], restore["after"], restore["before_checks"], restore["after_checks"]
        )
        stops = result.get("launcher", [])
        normal = any(
            s.get("start") == restore.get("stop_launcher")
            and s.get("ctrl_c")
            and s.get("ports_released")
            and s.get("exit_code") == 0
            for s in stops
        )
        item(
            "normal_restore",
            all(comp.values()) and normal and bool(restore.get("load_receipt")),
            ["restore.json", "result.json#/launcher"],
            detail=comp,
        )
        with sqlite3.connect(Path(result["database_path"]).as_uri() + "?mode=ro", uri=True) as db:
            saved = json.loads(
                db.execute(
                    "SELECT document FROM agent_save_states WHERE snapshot_id=?",
                    (restore["snapshot"]["id"],),
                ).fetchone()[0]
            )
            active = dict(
                db.execute(
                    "SELECT id,content FROM agent_memories "
                    "WHERE room_id=? AND active=1 AND kind='summary'",
                    (room["id"],),
                )
            )
        expected = {
            m["id"]: m["content"]
            for m in (summary or {}).get("memories", [])
            if m["kind"] == "summary" and m.get("active", True)
        }
        saved_summaries = {
            m["id"]: m["content"]
            for m in saved.get("memories", [])
            if m["kind"] == "summary" and m.get("active", True)
        }
        item(
            "restored_summary",
            preserved_summaries(expected, saved_summaries, active),
            ["summary.json", "game.db#agent_save_states", "game.db#agent_memories"],
            detail=dict(
                summary_ids=list(expected),
                saved_exact=expected == saved_summaries,
                active_exact=expected == active,
                comparison="Same IDs and exact prose; allow existing navigation metadata wrapper",
            ),
        )
    else:
        item("normal_restore")
        item("restored_summary")
    for name in ["recall", "continue"]:
        stage = result.get("continuation_stage", "continue") if name == "continue" else name
        step = steps.get(stage)
        condition = None
        if (
            step
            and restore.get("after")
            and step.get("cycle", {}).get("status") in {"completed", "failed", "cancelled"}
        ):
            condition = (
                step["before"]["revision"] >= restore["after"]["revision"]
                and turn_response(step, events)
                and step["before_checks"] == step.get("checks", [])
                and step["before"]["inventory"] == step.get("after", {}).get("inventory")
            )
            if name == "recall":
                condition = condition and all(
                    comparison(
                        step["before"],
                        step.get("after", step["before"]),
                        step["before_checks"],
                        step.get("checks", []),
                    ).values()
                )
                reply = "\n".join(
                    e["payload"].get("text", "")
                    for e in events
                    if e["type"] == "keeper.narration"
                    and e["payload"].get("cycle_id") == step.get("cycle", {}).get("id")
                )
                condition = (
                    condition
                    and bool(selected)
                    and held
                    and all(c["display_text"] in reply for c in selected)
                    and all(i["title"] in reply and i["holder_name"] in reply for i in items)
                )
            else:
                condition = condition and continued_action(
                    step, events, result.get("continuation_destination")
                )
        item(
            "post_restore_" + name, condition, [f"step-{stage}.json", "restore.json", "events.json"]
        )
    required = [
        v["status"]
        for k, v in rows.items()
        if k not in {"success_branch", "failure_branch", "openai_formal_retest"}
    ]
    status = (
        "passed"
        if all(s == "passed" for s in required)
        else (
            "failed"
            if "failed" in required
            else "blocked"
            if result.get("status") == "blocked"
            else "not_run"
        )
    )
    item(
        "openai_formal_retest",
        missing=formal_retest_status(config, calls, status, result.get("status") == "blocked"),
        sources=[
            "result.json#/configuration",
            "game.db#agent_model_calls",
            "verified-evidence.json#/rows",
        ],
        detail="Based on provider, destination, actual calls and formal scenario checks. "
        "Other providers are not_run; this audit does not grant external transmission permission.",
    )
    if config["provider"] == "openai":
        status = rows["openai_formal_retest"]["status"]
    return dict(
        status=status,
        rows=rows,
        configuration=config,
        transport=result.get("transport"),
        total_token_basis="input + output" if config["provider"] == "ollama" else "reported total",
        room_id=room.get("id"),
        preparation_id=result.get("preparation_id"),
        dice=details,
        natural_inputs=sum(e["type"] == "action.submitted" for e in new_events),
        model_calls=len(calls),
        tokens={
            k: sum(
                (
                    (c.get("token_usage") or {}).get(k)
                    if k != "total"
                    else (c.get("token_usage") or {}).get(
                        "total",
                        sum((c.get("token_usage") or {}).get(x) or 0 for x in ("input", "output")),
                    )
                )
                or 0
                for c in calls
            )
            for k in ("input", "output", "total")
        },
        event_counts=dict(Counter(e["type"] for e in new_events)),
        response_models=sorted({c["response_model"] for c in calls if c.get("response_model")}),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    args = parser.parse_args()
    directory = (BATCH / args.run).resolve()
    assert directory.is_relative_to(BATCH.resolve())
    report = audit(directory)
    (directory / "verified-evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "passed" else 2)
