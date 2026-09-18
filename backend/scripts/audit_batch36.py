"""Export public play and private execution evidence from the isolated database."""

import hashlib
import json
import sqlite3
import sys
from datetime import datetime

from scripts.play_batch36 import BASE, clean, write


def decode(row):
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, str) and value[:1] in {"{", "["}:
            try:
                result[key] = json.loads(value)
            except ValueError:
                pass
    return clean(result)


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def audit(directory):
    database = sqlite3.connect(f"file:{(directory / 'game.db').as_posix()}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    tables = {
        "agent_cycles": "cycles",
        "agent_runs": "agent-runs",
        "agent_model_calls": "model-calls",
        "agent_action_plans": "cycle-audits",
        "agent_tool_receipts": "tool-receipts",
        "pending_checks": "checks",
    }
    private = directory / "private-audit"
    private.mkdir(exist_ok=True)
    data = {}
    provenance_path = directory / "fixture-provenance.json"
    provenance = (
        json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
    )
    start_seq = provenance.get("initial_max_seq", 0)
    new_cycle_ids = {
        row[0]
        for row in database.execute(
            "select id from agent_cycles where json_extract(state, '$.triggering_event_seq') > ?",
            (start_seq,),
        )
    }
    new_run_ids = {
        row[0]
        for row in database.execute("select id,cycle_id from agent_runs")
        if row[1] in new_cycle_ids
    }
    for table, name in tables.items():
        data[name] = [decode(r) for r in database.execute("select * from " + table)]
        if start_seq:
            data[name] = [
                r
                for r in data[name]
                if (
                    r.get("cycle_id", r.get("id")) in new_cycle_ids
                    or r.get("run_id") in new_run_ids
                )
            ]
        write(private / (name + ".json"), data[name])
    events = [decode(r) for r in database.execute("select * from room_events order by seq")]
    events = [e for e in events if e["seq"] > start_seq]
    public = [e for e in events if e["visibility"] == "public"]
    write(private / "host-events.json", events)
    with (directory / "public-events.jsonl").open("w", encoding="utf-8") as stream:
        for event in public:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    names = {}
    for member in database.execute("select * from room_members"):
        names[member["id"]] = member["display_name"]
    lines = [
        "# 第36批《常暗之厢》完整公开团录",
        "",
        "本局通过正常 HTTP API 创建角色、分配与冻结；KP 和队友实际调用本机 Ollama qwen3:8b，",
        "上下文配置 8192。Codex 扮演普通玩家。调试者读过私有模组与审计，因此不宣称严格盲测。",
        "单测使用的替身未接入本局；保留原骰，所有修复前的错误、失败和重复发言均保留。",
        "完整公共事件另见 public-events.jsonl；私有计划、生成原文、修正和回退见 private-audit/。",
        "",
    ]
    if start_seq:
        lines[2:6] = [
            "本短场景使用历史数据库的独立副本，经正常存档加载和HTTP提交执行。",
            "初始状态及原始哈希见 fixture-provenance.json；本团录只列副本中新发生的事件。",
            "KP与队友使用真实 Ollama qwen3:8b，8192上下文，未使用模型替身或改写原骰。",
            "这不是新局自然通关证据。",
        ]
    for event in public:
        p = event["payload"]
        kind = event["type"]
        if kind in {"agent.status", "agent.cycle_status", "agent.cycle_changed"} and p.get(
            "status"
        ) not in {
            "failed",
            "waiting_for_roll",
            "waiting_for_host_review",
            "waiting_for_review",
            "cancelled",
        }:
            continue
        if not (
            p.get("text")
            or kind.startswith(
                (
                    "action.",
                    "check.",
                    "scene.",
                    "entity.",
                    "clue.",
                    "module.",
                    "game.",
                    "snapshot.",
                    "agent.cycle",
                    "sanity.",
                    "resource.",
                    "injury.",
                    "combat.",
                )
            )
        ):
            continue
        speaker = p.get("actor_name") or names.get(event.get("actor_member_id"), "系统")
        lines += [f"## seq {event['seq']} · {speaker} · {kind}", "", event["occurred_at"], ""]
        if p.get("text"):
            lines += [p["text"], ""]
        elif kind == "scene.updated":
            lines += [f"转场：{p.get('scene_title', '')}", p.get("scene_summary", ""), ""]
        elif kind in {"entity.revealed", "clue.revealed"}:
            lines += [f"公开：{p.get('title', '')}", p.get("public_summary", p.get("text", "")), ""]
        elif kind.startswith("check."):
            lines += [p.get("display_text", p.get("reason", "检定事件")), ""]
            lines += ["```json", json.dumps(p, ensure_ascii=False, indent=2), "```", ""]
        else:
            lines += ["```json", json.dumps(p, ensure_ascii=False, indent=2), "```", ""]
        if p.get("safe_fallback"):
            lines += ["（本条为校验失败后的安全回退，私有审计保留生成与拒绝原因。）", ""]
    operations = directory / "operations.jsonl"
    if operations.exists():
        lines += [
            "## 保存与恢复操作元数据",
            "",
            "包含正常保存恢复和阻断现场保存；只列操作元数据，不混入私有游戏状态。",
            "",
        ]
        for line in operations.read_text(encoding="utf-8").splitlines():
            op = json.loads(line)
            if op["method"] == "POST" and (
                "/snapshots" in op["path"] or op["path"].endswith(("/pause", "/resume"))
            ):
                name = (op.get("body") or {}).get("name", "")
                lines += [f"- {op['time']} · {op['path']} · {name} · HTTP {op['status']}"]
        lines.append("")
    (directory / "session-full.md").write_text("\n".join(lines), encoding="utf-8")
    calls = [r["document"] for r in data["model-calls"]]
    cycle_metrics = []
    for cycle in data["cycles"]:
        runs = [r for r in data["agent-runs"] if r["cycle_id"] == cycle["id"]]
        ids = {r["id"] for r in runs}
        records = [r["document"] for r in data["model-calls"] if r["run_id"] in ids]
        wall = (
            (stamp(cycle["finished_at"]) - stamp(cycle["created_at"])).total_seconds() * 1000
            if cycle["finished_at"]
            else None
        )
        phases = [
            e
            for e in events
            if e["type"] == "agent.cycle_changed"
            and e["payload"].get("cycle_id", e["payload"].get("id")) == cycle["id"]
        ]
        waiting = 0
        phase_ms = {}
        wait_ms = {}
        for before, after in zip(phases, phases[1:]):
            elapsed = (
                stamp(after["occurred_at"]) - stamp(before["occurred_at"])
            ).total_seconds() * 1000
            node = before["payload"].get("current_node", "unknown")
            phase_ms[node] = phase_ms.get(node, 0) + elapsed
            if before["payload"].get("status", "").startswith("waiting"):
                waiting += elapsed
                status = before["payload"]["status"]
                wait_ms[status] = wait_ms.get(status, 0) + elapsed
        model = sum(c.get("model_elapsed_ms", 0) for c in records)
        validation = sum(c.get("validation_ms", 0) for c in records)
        queue = sum(c.get("queue_wait_ms", 0) for c in records)
        cycle_metrics.append(
            {
                "id": cycle["id"],
                "origin": cycle["state"].get("origin"),
                "related_player_cycle_id": cycle["state"].get("related_player_cycle_id"),
                "triggering_event_seq": cycle["state"].get("triggering_event_seq"),
                "status": cycle["status"],
                "wall_ms": round(wall) if wall is not None else None,
                "calls": len(records),
                "model_ms": model,
                "validation_ms": validation,
                "model_queue_ms": queue,
                "waiting_ms": round(waiting),
                "wait_by_status_ms": {k: round(v) for k, v in wait_ms.items()},
                "phase_wall_ms": {k: round(v) for k, v in phase_ms.items()},
                "application_and_unclassified_ms": round(
                    wall - model - validation - queue - waiting
                )
                if wall is not None
                else None,
            }
        )
    write(
        directory / "timing-summary.json",
        {
            "calls": len(calls),
            "model_ms": sum(c.get("model_elapsed_ms", 0) for c in calls),
            "validation_ms": sum(c.get("validation_ms", 0) for c in calls),
            "model_queue_ms": sum(c.get("queue_wait_ms", 0) for c in calls),
            "errors": sum(bool(c.get("error_category")) for c in calls),
            "actual_prompt_tokens": [
                c["token_usage"]["input"]
                for c in calls
                if c.get("token_usage") and c["token_usage"].get("input")
            ],
            "actual_output_tokens": [
                c["token_usage"]["output"]
                for c in calls
                if c.get("token_usage") and c["token_usage"].get("output")
            ],
            "input_chars": [c.get("input_chars") for c in calls],
            "teammate_status_events": [
                e
                for e in events
                if e["type"]
                in {
                    "agent.teammate_decision",
                    "agent.teammate_generation_failed",
                    "agent.teammate_task_updated",
                }
            ],
            "cycles": cycle_metrics,
            "limitation": (
                "Residual includes application work, checkpointing, recovery pauses and "
                "unclassified scheduling; it is not model time."
            ),
        },
    )
    manifest = []
    for path in [
        directory / "session-full.md",
        directory / "public-events.jsonl",
        *private.glob("*.json"),
    ]:
        manifest.append(
            {
                "path": str(path.relative_to(directory)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    write(directory / "audit-manifest.json", manifest)
    print(
        json.dumps(
            {"public_events": len(public), "model_calls": len(calls), "cycles": len(cycle_metrics)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    destination = (BASE / sys.argv[1]).resolve()
    assert destination.is_relative_to(BASE.resolve()) and destination != BASE.resolve()
    audit(destination)
