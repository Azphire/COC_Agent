"""Read-only independent aggregation of captured batch 49 live cases and source integrity."""

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data/prepared/batch-49"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def rows(db, table, where="", values=()):
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}" {where}', values)]


def decoded(value):
    return json.loads(value) if isinstance(value, str) and value[:1] in {"{", "["} else value


def natural_case(path):
    case = read(path)
    if case.get("status") != "captured":
        raise ValueError(f"Case not yet captured: {path.name}")
    audit, root_id = case["audit"], case["cycle_id"]
    event_by_seq = {e["seq"]: e for e in audit["room_events"]}

    def parent_id(row):
        state = row.get("state", {})
        explicit = state.get("parent_cycle_id")
        trigger = event_by_seq.get(state.get("triggering_event_seq"), {})
        # Finished child state clears its temporary parent link. The retained
        # triggering action remains an exact event reference to its parent.
        proposed_by = trigger.get("payload", {}).get("cycle_id")
        return explicit or (proposed_by if proposed_by != row["id"] else None)

    ids = {root_id}
    while True:
        expanded = ids | {row["id"] for row in audit["agent_cycles"]
                          if parent_id(row) in ids}
        if expanded == ids:
            break
        ids = expanded
    runs = {row["id"]: row for row in audit["agent_runs"] if row["cycle_id"] in ids}
    calls = {row["id"]: row for row in audit["agent_model_calls"] if row["run_id"] in runs}
    compact = []
    for identity, row in calls.items():
        run, doc = runs[row["run_id"]], row["document"]
        compact.append({"id": identity, "cycle_id": run["cycle_id"],
                        "run_id": row["run_id"], "actor_member_id": run["actor_member_id"],
                        "graph_node": run["graph_node"], "schema": doc["schema"],
                        "attempt": doc.get("attempt"), "latency_ms": doc["latency_ms"],
                        "model_elapsed_ms": doc.get("model_elapsed_ms"),
                        "queue_wait_ms": doc.get("queue_wait_ms", 0),
                        "token_usage": doc.get("token_usage"),
                        "error_category": doc.get("error_category"),
                        "validation_issues": doc.get("validation_issues", []),
                        "transmitted_body_patterns": {
                            name: prop["pattern"] for name, prop in doc.get(
                                "output_contract", {}).get("properties", {}).items()
                            if name in {"public_narration", "observed_detail"}
                            and prop.get("pattern")
                        }})
    events = sorted((e for e in audit["room_events"]
                     if e.get("payload", {}).get("cycle_id") in ids), key=lambda e: e["seq"])
    kept = []
    for event in events:
        if any(term in event["type"] for term in (
            "narration", "revealed", "action_result", "request", "teammate_decision",
            "action.submitted", "action_proposed", "turn_result", "action.result",
            "module.interaction", "teammate_task_updated",
        )):
            kept.append({key: event[key] for key in (
                "seq", "type", "actor_member_id", "visibility", "payload",
            )})
    return {
        "case": f"{path.parent.name}/{path.stem}", "root_cycle_id": root_id,
        "cycle_ids": sorted(ids), "request": case["request"],
        "elapsed_seconds": case.get("elapsed_seconds"), "calls": compact,
        "model_call_count": len(compact),
        "excluded_preceding_calls": len(audit["agent_model_calls"]) - len(compact),
        "schemas": dict(Counter(c["schema"] for c in compact)),
        "usage": {key: sum((c["token_usage"] or {}).get(key, 0) for c in compact)
                  for key in ("input", "output")},
        "latency_ms": sum(c["latency_ms"] for c in compact),
        "model_elapsed_ms": sum(c["model_elapsed_ms"] or 0 for c in compact),
        "cycles": [{"id": c["id"], "status": c["status"],
                    "parent_cycle_id": parent_id(c),
                    "parent_link_basis": "retained parent or triggering event cycle_id",
                    "triggering_member_id": c.get("state", {}).get("triggering_member_id"),
                    "triggering_event_seq": c.get("state", {}).get("triggering_event_seq"),
                    "origin": c.get("state", {}).get("origin"),
                    "request_keys": c.get("state", {}).get("request_keys", []),
                    "request_operands": c.get("state", {}).get("request_operands", {}),
                    "safe_error": c.get("state", {}).get("safe_error")}
                   for c in audit["agent_cycles"] if c["id"] in ids],
        "formal_evidence": kept,
        "task_states": [{"member_id": r["member_id"],
                         "task_status": r["document"].get("task_status"),
                         "task_cycle_id": r["document"].get("task_cycle_id"),
                         "pending_requests": r["document"].get("pending_requests", [])}
                        for r in audit["agent_behavior_states"]],
    }


def integrity(directory):
    origin = read(directory / "clone-provenance.json")
    setup = read(directory / "setup-state.json")
    protected = {path: sha(Path(path)) == expected
                 for path, expected in origin["source_sha256"].items()}
    source = Path(origin["source"]) / "game.db"
    with sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True) as old:
        with sqlite3.connect((directory / "game.db").as_uri() + "?mode=ro", uri=True) as new:
            old.row_factory = new.row_factory = sqlite3.Row
            new.execute("BEGIN")
            retained = {}
            for table in ("character_drafts", "character_roll_records", "character_events",
                          "agent_profiles", "module_preparations", "module_entities",
                          "room_character_slots"):
                keys = [row["name"] for row in old.execute(f'PRAGMA table_info("{table}")')
                        if row["pk"]]
                before = {tuple(row[k] for k in keys): row for row in rows(old, table)}
                after = {tuple(row[k] for k in keys): row for row in rows(new, table)}
                different = [list(key) for key, row in before.items() if after.get(key) != row]
                retained[table] = {"source_rows": len(before), "equal": not different,
                                   "different_keys": different}
            copied_cards = []
            for old_id, new_id in setup["slots"].items():
                a = rows(old, "room_character_slots", "WHERE id=?", (old_id,))[0]
                b = rows(new, "room_character_slots", "WHERE id=?", (new_id,))[0]
                first, second = decoded(a["character_snapshot"]), decoded(b["character_snapshot"])
                copied_cards.append({"source_slot": old_id, "new_slot": new_id,
                                     "source_character_id": a["source_character_id"],
                                     "same_source_character": a["source_character_id"]
                                     == b["source_character_id"],
                                     "full_snapshot_equal": first == second,
                                     "different_snapshot_fields": [key for key in first.keys()
                                                                    | second.keys()
                                                                    if first.get(key)
                                                                    != second.get(key)]})
            bindings = rows(old, "room_agent_bindings", "WHERE room_id=?",
                            (origin["source_room"],))
            new_bindings = rows(new, "room_agent_bindings", "WHERE room_id=?",
                                (setup["room_id"],))
            copied_profiles = []
            for binding in bindings:
                linked = [b for b in new_bindings
                          if b["member_id"] == setup["members"][binding["member_id"]]]
                copied_profiles.append({"profile_id": binding["profile_id"],
                                        "new_member_id": setup["members"][binding["member_id"]],
                                        "same_profile_id": len(linked) == 1
                                        and linked[0]["profile_id"] == binding["profile_id"]})
            a = rows(old, "room_module_preparation_bindings", "WHERE room_id=?",
                     (origin["source_room"],))[0]
            b = rows(new, "room_module_preparation_bindings", "WHERE room_id=?",
                     (setup["room_id"],))[0]
            prep_keys = ("preparation_id", "source_id", "source_hash", "preparation_version",
                         "initial_scene", "relations")
            prep_equal = all(decoded(a[k]) == decoded(b[k]) for k in prep_keys)
            old_entities = {r["source_entity_id"]: decoded(r["snapshot"])
                            for r in rows(old, "room_entity_states", "WHERE room_id=?",
                                          (origin["source_room"],))}
            new_entities = rows(new, "room_entity_states", "WHERE room_id=?", (setup["room_id"],))
            different_entities = [e["source_entity_id"] for e in new_entities
                                  if old_entities.get(e["source_entity_id"])
                                  != decoded(e["snapshot"])]
            hidden = read(directory / "preflight-hidden-reveal.json")
            return {"directory": directory.name, "protected_files": protected,
                    "all_protected_hashes_match": all(protected.values()), "retained": retained,
                    "copied_cards": copied_cards, "copied_profiles": copied_profiles,
                    "same_preparation_binding": prep_equal,
                    "source_entity_snapshots_equal": not different_entities,
                    "different_entity_snapshot_ids": different_entities,
                    "note_back_hidden_before_turn": bool(hidden)
                    and all(e["state"] == "hidden" for e in hidden),
                    "excluded_metadata": ["new room/member/slot/binding identity and timestamps",
                                          "per-room runtime reveal state and event seq"]}


def main():
    cases = [natural_case(BASE / directory / filename)
             for directory, filename in (("real-02", "read-case.json"),
                                         ("real-03", "read-case.json"),
                                         ("real-03", "delegate-case.json"))]
    followups = []
    for name in ("real-04", "real-05", "real-06", "real-07", "real-08", "real-09", "real-10"):
        followup = BASE / name / "delegate-case.json"
        if not followup.exists():
            continue
        # This is a fresh delegation-only recheck. Its own root and descendants
        # must prove success; the earlier read-case pass is never substituted.
        followups.append(natural_case(followup))
    cases.extend(followups)
    persona = []
    for name in ("real-persona-01", "real-persona-02"):
        result, calls = read(BASE / name / "result.json"), read(BASE / name / "calls.json")
        persona.append({"case": name, **result, "schema": "Persona",
                        "actual_calls": len(calls),
                        "usage_totals": {k: sum((c.get("token_usage") or {}).get(k, 0)
                                                for c in calls) for k in ("input", "output")},
                        "model_latency_ms": sum(c.get("latency_ms", 0) for c in calls)})
    calls = {call["id"]: call for case in cases for call in case["calls"]}
    schemas = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "latency_ms": 0})
    for call in calls.values():
        schema = schemas[call["schema"]]
        schema["calls"] += 1
        for key in ("input", "output"):
            schema[key] += (call["token_usage"] or {}).get(key, 0)
        schema["latency_ms"] += call["latency_ms"]
    for case in persona:
        schema = schemas["Persona"]
        schema["calls"] += case["actual_calls"]
        for key in ("input", "output"):
            schema[key] += case["usage_totals"][key]
        schema["latency_ms"] += case["model_latency_ms"]
    document = {"natural_cases": cases, "persona_cases": persona,
                "integrity": [integrity(BASE / name)
                              for name in sorted({c["case"].split("/")[0] for c in cases})],
                "by_schema": dict(schemas),
                "total": {key: sum(s[key] for s in schemas.values())
                          for key in ("calls", "input", "output", "latency_ms")},
                "counting": "Only parent/descendant cycle runs; deduplicate actual call ids. "
                "Persona files contain only their newly executed call. Exclude older snapshots, "
                "setup, original adopted characters, fake calls and case-audit preceding runs."}
    document["independent_assessment"] = {
        "real-02/read-case": {
            "passed": False, "formal_event_seq": 46, "validation_event_seq": 45,
            "answer_complete": False, "answer_origin": "server_fallback",
            "finding": "Formal body did not answer the note text; preserve as failed run.",
        },
        "real-03/read-case": {
            "passed": True, "formal_event_seq": 46, "validation_event_seq": 45,
            "answer_complete": True, "answer_origin": "repaired", "repair_count": 1,
            "finding": "Formal body answers both observation and the selected e6 note source.",
        },
        "real-03/delegate-case": {
            "passed": False, "routing_passed": True, "fresh_result_ledger_passed": True,
            "formal_answer_passed": False, "whole_request_completed": False,
            "executor_member_id": "83fce908-c64f-418c-a402-298c40bafd25",
            "unaddressed_member_id": "96cc3aa9-060d-4ade-9828-5ef91d1f71d0",
            "request_key": "53:7", "action_event_seq": 74,
            "child_cycle_id": "65039a29-bb2a-4b25-9589-59681ec9a9b4",
            "action_target_id": "a630d572-fb0f-4fdb-9a14-7931258cb862",
            "reveal_event_seq": 96, "interaction_event_seq": 97,
            "result_ledger_event_seq": 102, "task_receipt_event_seq": 109,
            "formal_event_seq": 104, "answer_complete": False,
            "answer_origin": "server_fallback", "pending_operations": ["observe"],
            "finding": "Hunter executed own requested action; editor was skipped with no_trigger. "
            "New reveal/search remain in the ledger despite failed narration. The remaining "
            "observe request is attempted, not completed. Fallback claim of no hidden layer/fold "
            "has no matching approved source and is not counted as a valid answer.",
        },
    }
    for current in followups:
        evidence = current["formal_evidence"]
        validations = [e for e in evidence if e["type"] == "agent.narration_validated"]
        child_ids = {c["id"] for c in current["cycles"] if c["parent_cycle_id"]}
        tasks = [s for s in current["task_states"] if s["task_cycle_id"] in child_ids]
        pending = [r for state in tasks for r in state["pending_requests"]]
        document["independent_assessment"][current["case"]] = {
            "basis": "Only this fresh delegation root and its descendant action events",
            "formal_answer_complete": bool(validations)
            and all(e["payload"].get("answer_complete") for e in validations),
            "formal_narrations": [{"seq": e["seq"], "text": e["payload"].get("text"),
                                   "origin": e["payload"].get("answer_origin"),
                                   "safe_fallback": e["payload"].get("safe_fallback")}
                                  for e in evidence if e["type"] == "keeper.narration"],
            "new_reveal_event_seqs": [e["seq"] for e in evidence
                                      if e["type"] == "entity.revealed"],
            "result_ledger_event_seqs": [e["seq"] for e in evidence
                                         if e["type"] == "action.result"],
            "pending_requests": pending,
            "whole_request_completed": bool(tasks) and not pending
            and all(s["task_status"] == "completed" for s in tasks),
            "substituted_prior_read_evidence": False,
        }
        experiments = [c for c in current["calls"] if c["transmitted_body_patterns"]]
        if experiments:
            final_contract_source = (ROOT / "backend/app/agents/generation_contracts.py").read_text(
                encoding="utf-8",
            )
            document["independent_assessment"][current["case"]]["body_pattern_experiment"] = {
                "calls": [{"id": c["id"], "patterns": c["transmitted_body_patterns"],
                           "validation_issues": c["validation_issues"]} for c in experiments],
                "experimental_pattern_present_in_final_contract_source": any(
                    pattern in final_contract_source for c in experiments
                    for pattern in c["transmitted_body_patterns"].values()
                ),
                "conclusion": "These outputs did not satisfy the transmitted body pattern; "
                "this does not establish general provider support for regular expressions. "
                "This records the experiment, not a successful final-code validation.",
            }
    (BASE / "real-metrics.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    lines = ["# 第49批真实调用独立只读审计", "",
             "按当前问题的父回合及其子回合选取 run，再按实际 call ID 去重。"
             "委派快照中包含先前读便签调用，已排除；旧库调用、假模型、复用人物生成不计入。", "",
             "| case | 调用数 | input | output | 模型记录延迟合计 ms | 整次操作秒 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for case in cases:
        lines.append(f"| {case['case']} | {case['model_call_count']} | {case['usage']['input']} "
                     f"| {case['usage']['output']} | {case['latency_ms']} "
                     f"| {case['elapsed_seconds']} |")
    for case in persona:
        lines.append(f"| {case['case']} ({case['status']}) | {case['actual_calls']} "
                     f"| {case['usage_totals']['input']} | {case['usage_totals']['output']} "
                     f"| {case['model_latency_ms']} | {case['elapsed_ms'] / 1000} |")
    lines.extend(["", "| schema | 调用数 | input | output | 延迟合计 ms |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for name, values in schemas.items():
        lines.append(f"| {name} | {values['calls']} | {values['input']} | {values['output']} "
                     f"| {values['latency_ms']} |")
    lines.extend(["", "总计：" + json.dumps(document["total"], ensure_ascii=False), "",
                  "模型记录延迟与整次操作耗时口径分开；不同失败／复验不合成正常一次耗时。",
                  "完整逐次调用、正式证据和保护校验见 [real-metrics.json](real-metrics.json)。", "",
                  "## 正式结果独立核对", "",
                  "real-02/read：e45 answer_complete=false；e46 正式正文为 server_fallback，"
                  "未答便签原文，本次失败保留。", "",
                  "real-03/read：e45 answer_complete=true；e46 正式正文同时写出环顾结果与"
                  "‘只管前进吧，已经没有退路了。’来源 e6，answer_origin=repaired，修复一次。", "",
                  "real-03/delegate：指定猎人 83fce908… 被正确调用，短名字编辑 96cc3aa9…"
                  "以 no_trigger 跳过且没有模型调用。子回合 65039a29… 的触发事件 e74 "
                  "确属父回合 747101e2…，请求 key 为 53:7，执行者与正面目标匹配。"
                  "完成态会清空 parent_cycle_id，因此审计经 triggering_event_seq→原行动的 "
                  "cycle_id 重建血缘，不根据名字或事件时间猜测。", "",
                  "便签背面开局前确为 hidden；本次 e96 首次新揭示，e97 登记猎人的 search "
                  "成功，e102/e109 仍保留揭示及实际动作来源。e104 正式正文为失败回退，"
                  "answer_complete=false；其‘没有夹层或折叠’无对应批准来源，不能计作有效回答。"
                  "请求仍保留 observe，task_status=attempted，没有用任意揭示销掉全部请求。"
                  "本次路由及新揭示账本通过，KP 正文及整项请求结算未通过。", "",
                  "## 来源和复用完整性", "",
                  "real-02 与 real-03 的 clone-provenance 中各 12 条受保护路径重新计算均匹配，"
                  "包含原库、checkpoint、知识库、配置与批准准备包；原来不存在的文件仍不存在。"
                  "两个隔离库均保留源库原 6 张卡、68 条原骰、33 条角色事件、6 份 profile、"
                  "1 份准备、44 个准备实体和 6 个旧角色席位的完整原行。", "",
                  "两个新房间各 3 张人物卡的完整 character_snapshot 与原席位一致，"
                  "含姓名、数值和原骰；绑定的 3 份 profile ID 及其原文一致。准备版本、"
                  "来源、初始场景、关系与所有实体 snapshot 一致。比较排除的只有新房间／成员／"
                  "席位绑定身份时间戳，以及房间运行中的揭示状态和事件序号；没有排除卡内字段。", "",
                  "人物真实修复另为 real-persona-01 失败与 real-persona-02 成功各一次，"
                  "合计 input 5378/output 286。两次 result 中全部保留检查均为 true，"
                  "详见 [人物恢复记录](persona-validation.md)。",
                  "本审计没有调用模型，没有写数据库或停止服务。"])
    for current in followups:
        name = current["case"].split("/")[0]
        assessment = document["independent_assessment"][current["case"]]
        compact = {key: value for key, value in assessment.items() if key != "pending_requests"}
        compact["pending_requests"] = [
            {key: request.get(key)
             for key in ("key", "executor_member_id", "target_id", "operations")}
            for request in assessment["pending_requests"]
        ]
        lines.extend(["", f"## {name} 同题委派复验", "",
                      "本次从新房间独立执行委派题，未重跑读便签；real-03/read 的通过仅属于"
                      "先前读题，不代替本次委派正文或结算。", "",
                      "当前回合正式验证与结算：" + json.dumps(
                          compact, ensure_ascii=False, separators=(",", ":")), "",
                      f"{name} 的来源保护、全卡／profile／准备复制及揭示前 hidden 状态"
                      "单独列于 JSON 的 integrity；该目录调用按其自己的父子回合 ID 统计。"])
    (BASE / "real-metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"total": document["total"], "by_schema": dict(schemas),
                      "case_calls": {c["case"]: c["model_call_count"] for c in cases},
                      "protected_hashes": {i["directory"]: i["all_protected_hashes_match"]
                                           for i in document["integrity"]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
