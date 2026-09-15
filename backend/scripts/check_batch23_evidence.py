"""Read-only audit of an isolated batch-23 game; never starts a model or rolls dice."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def usage(calls):
    return {
        key: sum((c.get("token_usage") or {}).get(key) or 0 for c in calls)
        for key in ("input", "output", "total")
    }


def verify(directory, require_switch=True):
    directory = Path(directory).resolve()
    result = read(directory / "result.json")
    room = read(directory / "final-room.json")
    initial = read(directory / "initial-room.json")
    checks = read(directory / "all-checks.json")
    calls = read(directory / "model-calls.json")
    with sqlite3.connect((directory / "game.db").as_uri() + "?mode=ro", uri=True) as db:
        events = [
            {"seq": seq, "type": kind, "payload": json.loads(payload)}
            for seq, kind, payload in db.execute(
                "SELECT seq,type,payload FROM room_events WHERE room_id=? ORDER BY seq",
                (room["id"],),
            )
        ]
        cycles = [
            {"status": status, "state": json.loads(state)}
            for status, state in db.execute(
                "SELECT status,state FROM agent_cycles WHERE room_id=?", (room["id"],)
            )
        ]
    kinds = Counter(e["type"] for e in events)
    assert kinds["action.submitted"] >= 5
    assert cycles and all(c["status"] == "completed" for c in cycles)
    assert max(c["state"].get("call_count", 0) for c in cycles) <= 12
    schemas = Counter(c["schema"] for c in calls)
    assert {"KeeperPlan", "KeeperNarration", "TeammateDecision", "SummaryOutput"} <= schemas.keys()
    assert all(c["provider"] == "openai" and c["attempt"] in (1, 2) for c in calls)
    assert all(c.get("request_id") and c.get("response_model") for c in calls)
    assert all(c.get("token_usage") and c["latency_ms"] >= 0 for c in calls)
    assert kinds["npc.spoke"] and kinds["keeper.narration"] and kinds["agent.summary_rebuilt"]
    assert checks and all(c["status"] == "resolved" for c in checks)
    dice = []
    for check in checks:
        fixed = [
            e
            for e in events
            if e["type"] == "check.dice_fixed" and e["payload"]["check_id"] == check["id"]
        ]
        settled = [
            e for e in events if e["type"] == "check.resolved" and e["payload"]["id"] == check["id"]
        ]
        assert len(fixed) == len(settled) == 1
        original = check["settlement"]["original_dice"]
        assert original == fixed[0]["payload"]["dice"] == check["dice"]
        assert original["roll_record"]["source"] == "system"
        dice.append(
            {
                "check_id": check["id"],
                "roll_id": original["roll_record"]["id"],
                "result": check["result"],
                "fixed_seq": fixed[0]["seq"],
                "resolved_seq": settled[0]["seq"],
                "original_preserved": True,
                "luck_spent": check["settlement"]["luck_spent"],
                "push_requested": check["settlement"]["push_requested"],
            }
        )

    initial_items = {i["instance_id"]: i for i in initial["inventory"]}
    final_items = {i["instance_id"]: i for i in room["inventory"]}
    assert len(final_items) == len(room["inventory"]) == len(initial_items)
    assert initial_items == final_items
    transfers = [e for e in events if e["type"] == "module.interaction_receipt"]
    drops = [e for e in transfers if e["payload"]["interaction_id"] == "drop"]
    pickups = [e for e in transfers if e["payload"]["interaction_id"] == "pickup"]
    assert len(drops) == len(pickups) == 1, (
        "Require one actual drop/pickup and no duplicate receipt"
    )
    drop, pickup = drops[0], pickups[0]
    assert drop["payload"]["entity_id"] == pickup["payload"]["entity_id"]
    iid = next(
        i for i, item in initial_items.items() if item["item_id"] == drop["payload"]["entity_id"]
    )
    assert iid not in drop["payload"]["inventory"]
    assert pickup["payload"]["inventory"][iid] == initial_items[iid]["holder_id"]
    all_records = [*result["turns"], *result.get("recoveries", [])]
    drop_turn = next(
        t
        for t in all_records
        if t["after"]["session_state"]["module_runtime"].get("dropped_items", {}).get(iid)
        and t["before"]["revision"] < drop["seq"] <= t["after"]["revision"]
    )
    assert iid not in {i["instance_id"] for i in drop_turn["after"]["inventory"]}

    def weapon_count(snapshot, instance_id):
        return sum(
            w["quantity"]
            for character in snapshot["session_state"]["characters"].values()
            for w in character["weapons"]
            if w["id"] == instance_id
        )

    assert weapon_count(initial, iid) == weapon_count(room, iid) == 1
    assert weapon_count(drop_turn["after"], iid) == 0
    for item_id in initial_items.keys() - {iid}:
        assert weapon_count(initial, item_id) == weapon_count(drop_turn["after"], item_id)
    repeated = [
        t
        for t in all_records
        if iid in t["text"] and "再次拾取" in t["text"] and t["cycle"]["status"] == "completed"
    ]
    assert repeated
    assert all(t["before"]["inventory"] == t["after"]["inventory"] for t in repeated)

    protected = (
        "characters",
        "module_runtime",
        "combat",
        "time_receipts",
        "game_minute",
        "game_round",
        "sanity_day",
    )
    recalls = [
        t for t in all_records if "只回顾" in t["text"] and t["cycle"]["status"] == "completed"
    ]
    assert len(recalls) >= 2
    for turn in recalls:
        assert turn["before"]["inventory"] == turn["after"]["inventory"]
        for key in protected:
            assert turn["before"]["session_state"][key] == turn["after"]["session_state"][key], key
        affected = [
            e for e in events if turn["before"]["revision"] < e["seq"] <= turn["after"]["revision"]
        ]
        assert not any(
            e["type"]
            in {
                "check.requested",
                "check.dice_fixed",
                "check.resolved",
                "module.interaction_receipt",
                "scene.updated",
            }
            for e in affected
        )
    last_recall = max(recalls, key=lambda t: t["after"]["revision"])
    recalled_text = "\n".join(
        e["payload"].get("text", "")
        for e in events
        if e["type"] == "keeper.narration"
        and last_recall["before"]["revision"] < e["seq"] <= last_recall["after"]["revision"]
    )
    assert checks[-1]["display_text"] in recalled_text
    assert all(
        f"{i['holder_name']}当前持有：{i['title']}" in recalled_text for i in room["inventory"]
    )
    assert any(
        e["type"] == "agent.summary_rebuilt" and e["seq"] < last_recall["before"]["revision"]
        for e in events
    )
    assert result["restore"] and all(result["restore"].values())
    assert result.get("summary_rebuild") and all(not s["stale"] for s in result["summary_rebuild"])
    blocks = {b["stage"] for b in result["switch_blocks"] if b["http_status"] == 409}
    assert {"running", "waiting_for_roll"} <= blocks
    if require_switch:
        assert result["switching"] == "passed" and all(result["switch_state_comparison"].values())
        for name in ("ollama-before", "openai-switch", "ollama-after"):
            assert any(
                t["name"] == name and t["state"] == "available" and t.get("ui")
                for t in result["trials"]
            )

    trials = [c for t in result["trials"] for c in t["calls"] if c["provider"] == "openai"]
    summary = {
        "status": "passed",
        "room_id": room["id"],
        "actual_player_inputs": kinds["action.submitted"],
        "recorded_turn_files": len(result["turns"]),
        "normal_recoveries": len(result.get("recoveries", [])),
        "cycles": len(cycles),
        "max_cycle_calls": max(c["state"].get("call_count", 0) for c in cycles),
        "game_calls": len(calls),
        "game_usage": usage(calls),
        "schemas": dict(schemas),
        "format_repair_calls": sum(c["attempt"] == 2 for c in calls),
        "api_trial_calls": len(trials),
        "api_total_calls": len(calls) + len(trials),
        "api_total_usage": usage([*calls, *trials]),
        "response_models": sorted({c["response_model"] for c in [*calls, *trials]}),
        "dice": dice,
        "item_instance_id": iid,
        "drop_receipt_seq": drop["seq"],
        "pickup_receipt_seq": pickup["seq"],
        "inventory_and_weapon_parity": True,
        "readonly_recalls": len(recalls),
        "post_summary_recall_matches_check_and_holders": True,
        "summary_coverage": [e for e in events if e["type"] == "agent.summary_rebuilt"],
        "restore": result["restore"],
        "switching": result.get("switching"),
        "launcher_verified_stops": sum(
            bool(x.get("ctrl_c") and x.get("ports_released")) for x in result["launcher"]
        ),
        "event_counts": dict(kinds),
    }
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    evidence = verify(args.directory)
    output = args.directory / "verified-evidence.json"
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: evidence[k]
                for k in ("status", "actual_player_inputs", "api_total_calls", "api_total_usage")
            },
            ensure_ascii=False,
        )
    )
