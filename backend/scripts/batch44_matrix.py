"""Fixed synthetic field matrix: source storage, recall, and gateway messages."""

import json

from sqlalchemy import select

from app.agents.action_runtime import (
    NARRATION_INSTRUCTION,
    PLAN_INSTRUCTION,
    TEAMMATE_INSTRUCTION,
    generation_prompt,
)
from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan, TeammateDecision
from app.agents.model import AgentModelClient, FakeModelAdapter
from app.memory.events import story_events
from app.memory.recall import select_memory, visible_tasks
from app.memory.service import memories
from app.persistence.room_models import RoomEvent
from app.rooms.handouts import private_handouts
from app.rooms.service import Identity


def at(value, path):
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def field_check(name, expected, actual):
    return {"field": name, "expected": expected, "actual": actual, "passed": actual == expected}


async def run_matrix(replay):
    from scripts.validate_batch44 import PASSWORD, PRIVATE, TASK

    svc, game, seq = replay.svc, replay.game, replay.seq
    adapter = FakeModelAdapter()
    gateway = AgentModelClient(svc.settings, adapter)
    cases = [
        (
            "password",
            "按青铜门口令核对密码盘。",
            [
                (
                    seq["password"],
                    {
                        "text": PASSWORD,
                        "epistemic": "authoritative_result",
                        "historical_only": True,
                    },
                )
            ],
        ),
        (
            "transfer",
            "回顾青铜钥匙的转交对象和数量。",
            [
                (
                    seq["transfer"],
                    {
                        "fields.from_member_id": game["player"],
                        "fields.to_member_id": game["agent"],
                        "fields.quantity": 1,
                        "fields.operation": "transfer",
                        "historical_only": True,
                    },
                )
            ],
        ),
        (
            "testimony",
            "回顾看门人关于青铜钥匙的原始说法与后来的更正。",
            [
                (
                    seq["testimony"],
                    {
                        "text": "青铜钥匙从未离开我手里。",
                        "epistemic": "attributed_testimony",
                        "speaker": "看门人",
                    },
                ),
                (
                    seq["correction"],
                    {
                        "text": "我先前说错了，青铜钥匙已经交给队友。",
                        "epistemic": "attributed_testimony",
                        "speaker": "看门人",
                    },
                ),
            ],
        ),
        (
            "check_outcomes",
            "回顾确认密码盘的侦查检定，区分历史成功与最近失败。",
            [
                (
                    seq["old-success"],
                    {
                        "fields.result.passed": True,
                        "fields.result.total": 17,
                        "fields.result.threshold": 55,
                        "source.turn": "historic-cycle",
                    },
                ),
                (
                    seq["new-failure"],
                    {
                        "fields.result.passed": False,
                        "fields.result.total": 83,
                        "fields.result.threshold": 55,
                        "source.turn": "current-cycle",
                    },
                ),
            ],
        ),
        (
            "resource",
            "回顾魔法值资源变化前后的数值。",
            [
                (
                    seq["resource"],
                    {
                        "fields.before.mp": 10,
                        "fields.after.mp": 7,
                        "fields.resource_changes.mp": -3,
                        "fields.target_member_id": game["agent"],
                    },
                )
            ],
        ),
        (
            "pending_task",
            "核对青铜门密码的任务仍待完成。",
            [
                (
                    seq["promise"],
                    {
                        "kind": "pending_task",
                        "text": TASK,
                        "status": "pending",
                        "epistemic": "requested_not_executed",
                        "historical_only": True,
                    },
                )
            ],
        ),
        ("long_source_tail", "查阅超长原始档案末尾的原文。", []),
    ]
    roles = [
        ("keeper", game["room"]["host_member_id"], True, False, KeeperPlan, PLAN_INSTRUCTION),
        (
            "public_narrator",
            game["room"]["host_member_id"],
            False,
            True,
            KeeperNarration,
            NARRATION_INSTRUCTION,
        ),
        ("teammate", game["agent"], False, False, TeammateDecision, TEAMMATE_INSTRUCTION),
    ]
    rows = []
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, replay.room_id)
        originals = {
            e.seq: e
            for e in await session.scalars(
                select(RoomEvent).where(
                    RoomEvent.room_id == room.id,
                )
            )
        }
        long_text = originals[seq["long-event"]].payload["content"]
        for role, actor, keeper, narrator, schema, instruction in roles:
            filtered = await svc.rooms.events(session, room, Identity(actor, keeper))
            if narrator:
                filtered = [e for e in filtered if e["visibility"] == "public"]
            events, _ = story_events(filtered, include_initial_reveals=True)
            by_seq = {e["seq"]: e for e in events}
            tasks = await visible_tasks(
                session, room.id, events, member_id=actor, keeper=keeper, narrator=narrator
            )
            profile = game["profiles"][0 if keeper else 1]["id"]
            summaries = await memories(session, room.id, profile, keeper)
            if narrator:
                summaries = [m for m in summaries if m.scope == "public"]
            for name, query, expectations in cases:
                selected, audit = select_memory(
                    events,
                    query,
                    tasks=(tasks if name == "pending_task" else []),
                    memories=summaries,
                    budget=4000,
                )
                context = {
                    "role": "keeper" if keeper else "investigator",
                    "phase": "plan_keeper_action" if keeper else "decide_teammates",
                    "events": [],
                    "characters": [],
                    "memory_evidence": selected,
                    "triggering_action": {
                        "seq": max(originals) + 1,
                        "actor_member_id": game["player"],
                        "payload": {"text": query},
                    },
                    "readonly_recall": True,
                }
                if narrator:
                    context["response_brief"] = {"historical_memory": selected}
                prompt = generation_prompt(context, schema)
                adapter.responses.append(
                    {
                        "plan_id": "matrix-plan",
                        "cycle_id": "matrix-cycle",
                        "current_scene_id": "square",
                        "parsed_intent": {
                            "type": "observe",
                            "actor_member_id": game["player"],
                            "actor_character_slot_id": "matrix-slot",
                            "evidence_quote": query,
                            "confidence": 1,
                        },
                    }
                    if keeper
                    else {"public_narration": "验收占位，不执行操作。"}
                    if narrator
                    else {
                        "mode": "speak",
                        "speech_text": "验收占位，不执行操作。",
                        "related_player_action_seq": max(originals) + 1,
                        "confidence": 1,
                    }
                )
                await gateway.generate(
                    [
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ],
                    response_schema=schema,
                    max_attempts=1,
                )
                call = gateway.calls[-1]
                transmitted = json.loads(call["transmitted_messages"][-1]["content"])
                final_records = transmitted.get("response_brief", {}).get(
                    "historical_memory", transmitted.get("memory_evidence", [])
                )
                stages = {"recall": selected, "actual_messages": final_records}
                checks, provenance = (
                    {stage: [] for stage in stages},
                    {stage: [] for stage in stages},
                )
                for stage, records in stages.items():
                    for source_seq, wanted in expectations:
                        candidates = [
                            r
                            for r in records
                            if r.get("source", {}).get("seq") == source_seq
                            and (wanted.get("kind") is None or r.get("kind") == wanted["kind"])
                        ]
                        record = candidates[0] if candidates else {}
                        for path, value in wanted.items():
                            checks[stage].append(
                                field_check(f"e{source_seq}:{path}", value, at(record, path))
                            )
                        source = record.get("source", {})
                        event = by_seq.get(source_seq, {})
                        provenance[stage].append(
                            bool(
                                record
                                and source.get("ref") == f"e{source_seq}"
                                and event
                                and source.get("visibility") == event["visibility"]
                                and source.get("time") == event.get("occurred_at")
                            )
                        )
                    if name == "long_source_tail":
                        record = next(
                            (
                                r
                                for r in records
                                if r.get("source", {}).get("seq") == seq["long-event"]
                                and "B44-LONG-END-8152" in r.get("text", "")
                            ),
                            {},
                        )
                        span = record.get("excerpt", {})
                        checks[stage].extend(
                            [
                                field_check(
                                    "tail_marker",
                                    True,
                                    "B44-LONG-END-8152" in record.get("text", ""),
                                ),
                                field_check(
                                    "complete_original_offset_end", len(long_text), span.get("end")
                                ),
                                field_check("explicit_partial", True, span.get("partial")),
                                field_check(
                                    "exact_source_slice",
                                    True,
                                    bool(record)
                                    and record.get("text")
                                    == long_text[span["start"] : span["end"]],
                                ),
                            ]
                        )
                        provenance[stage].append(
                            bool(record and record.get("source", {}).get("seq") in by_seq)
                        )
                # Raw source storage independently retains every designated value;
                # compressed segments are checked by the main replay acceptance.
                storage = []
                for source_seq, wanted in expectations:
                    source_event = originals[source_seq]
                    for path, value in wanted.items():
                        key = path.removeprefix("fields.")
                        if path.startswith("fields."):
                            actual = at(source_event.payload, key)
                        elif path == "source.turn":
                            actual = source_event.payload.get("cycle_id")
                        elif path == "text":
                            actual = source_event.payload.get(
                                "content"
                            ) or source_event.payload.get("text")
                            if name == "pending_task":
                                actual = next(
                                    (
                                        t["text"]
                                        for t in tasks
                                        if t.get("kind") == "pending_task"
                                        and t["source"].get("seq") == source_seq
                                    ),
                                    None,
                                )
                        elif path == "speaker":
                            actual = source_event.payload.get("actor_name")
                        else:
                            continue  # Projection labels are checked at recall/message stages.
                        storage.append(field_check(f"e{source_seq}:{path}", value, actual))
                if name == "long_source_tail":
                    storage.append(
                        field_check("tail_marker", True, long_text.endswith("B44-LONG-END-8152"))
                    )
                rows.append(
                    {
                        "case": name,
                        "reader": role,
                        "question": query,
                        "storage_checks": storage,
                        "checks": checks,
                        "source_retrievable": provenance,
                        "selection_audit": audit,
                        "actual_messages": call["transmitted_messages"],
                        "request_budget": call["request_budget"],
                    }
                )

        # The handout is genuinely assigned through the HO endpoint in Replay;
        # private_handouts applies the reader check before prompt projection.
        for reader, actor, keeper, public in (
            ("owner", game["player"], False, False),
            ("keeper", game["player"], True, False),
            ("teammate", game["agent"], False, False),
            ("public_narrator", game["room"]["host_member_id"], False, True),
        ):
            handouts = [] if public else private_handouts(room, actor, keeper=keeper)
            schema = KeeperNarration if public else KeeperPlan if keeper else TeammateDecision
            context = {"private_handouts": handouts,
                       "role": "keeper" if keeper else "investigator", "events": []}
            if public:
                context = {"response_brief": {"historical_memory": []}}
            prompt = generation_prompt(context, schema)
            adapter.responses.append(
                {"public_narration": "验收占位。"}
                if public
                else {
                    "plan_id": "matrix-ho-plan",
                    "cycle_id": "matrix-ho-cycle",
                    "current_scene_id": "square",
                    "parsed_intent": {
                        "type": "observe",
                        "actor_member_id": actor,
                        "actor_character_slot_id": "matrix-slot",
                        "evidence_quote": "核对私人资料",
                        "confidence": 1,
                    },
                }
                if keeper
                else {
                    "mode": "speak",
                    "speech_text": "验收占位。",
                    "related_player_action_seq": max(originals) + 1,
                    "confidence": 1,
                }
            )
            await gateway.generate(
                [
                    {"role": "system", "content": NARRATION_INSTRUCTION if public
                     else PLAN_INSTRUCTION if keeper else TEAMMATE_INSTRUCTION},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                response_schema=schema,
                max_attempts=1,
            )
            call = gateway.calls[-1]
            expected = reader in {"owner", "keeper"}
            assigned = next(
                h
                for h in room.session_state["handout_assignments"]
                if h["member_id"] == game["player"]
            )
            rows.append(
                {
                    "case": "private_ho",
                    "reader": reader,
                    "storage_checks": [field_check("assigned_text", PRIVATE, assigned["text"])],
                    "checks": {
                        "recall": [
                            field_check(
                                "private_text_visible",
                                expected,
                                PRIVATE in json.dumps(handouts, ensure_ascii=False),
                            )
                        ],
                        "actual_messages": [
                            field_check(
                                "private_text_visible",
                                expected,
                                PRIVATE
                                in json.dumps(call["transmitted_messages"], ensure_ascii=False),
                            )
                        ],
                    },
                    "source_retrievable": {
                        stage: [assigned["assigned_event_seq"] in originals]
                        for stage in ("recall", "actual_messages")
                    },
                    "actual_messages": call["transmitted_messages"],
                    "request_budget": call["request_budget"],
                }
            )
    totals = {}
    for stage in ("storage", "recall", "actual_messages"):
        checks = [
            c
            for row in rows
            for c in (row["storage_checks"] if stage == "storage" else row["checks"][stage])
        ]
        totals[stage] = {
            "passed": sum(c["passed"] for c in checks),
            "total": len(checks),
            "retention_rate": sum(c["passed"] for c in checks) / len(checks),
        }
    provenance = [
        v for row in rows for values in row["source_retrievable"].values() for v in values
    ]
    return {
        "mode": "synthetic_fixed_query_matrix_with_substitute_model",
        "extra_acceptance_calls": len(gateway.calls),
        "real_model_calls": 0,
        "coverage": totals,
        "source_retrievable_rate": sum(provenance) / len(provenance),
        "status": "passed"
        if all(t["retention_rate"] == 1 for t in totals.values()) and all(provenance)
        else "failed",
        "rows": rows,
    }
