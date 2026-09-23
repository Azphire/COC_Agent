"""Isolated Batch 44 replay and fixed local-model probe.

Run from backend: python scripts/validate_batch44.py --name replay-01 [--real-model]
Existing logs are read only. Synthetic controls, model substitutes, exact-field
checks, and real-answer semantic checks are reported separately. No credentials
are written to the retained artifacts.
"""

# Direct script execution needs the repository package root before app imports.
# ruff: noqa: E402

import argparse
import hashlib
import json
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select

from app.agents.model import FakeModelAdapter
from app.memory.events import story_events
from app.persistence.adjudication_models import AgentBehaviorRecord, SummaryRecoveryRecord
from app.persistence.agent_models import AgentCycle, AgentMemory, AgentRun, ProfileRecord

SOURCE = ROOT / "data/prepared/zhuishuren/batch-40/run-20260921/public-events.json"
OUTPUT = ROOT / "data/prepared/batch-44"
PASSWORD = "青铜门口令：零七·玖二 / 07-92"
PRIVATE = "B44_PRIVATE_HO_SENTINEL_4176"
DRAFT = "B44_STREAM_DRAFT_SENTINEL"
ABANDONED = "B44_ABANDONED_BRANCH_SENTINEL"
TASK = "抵达门边后核对青铜门密码，尚未核对完成"
FIXED_ACTION = "我查看青铜门边的密码盘；队友，请根据早先的青铜门口令和未完成的核对任务提出下一步。"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")


def request(client, method, path, body=None, headers=None):
    response = client.request(method, path, json=body, headers=headers)
    if not response.is_success:
        raise AssertionError(f"{method} {path}: HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def create_game(client):
    """Ordinary HTTP setup; the replay itself is an explicitly synthetic import."""
    created = request(client, "POST", "/api/rooms", {"name": "第44批隔离回放"})
    prefix = "/api/rooms/" + created["room"]["id"]
    remote = request(
        client,
        "POST",
        "/api/rooms/join",
        {
            "invite_code": created["invite_code"],
            "display_name": "回放玩家",
        },
    )
    room = request(
        client,
        "POST",
        prefix + "/members",
        {
            "display_name": "队友",
            "controller_type": "agent",
        },
    )["room"]
    agent = next(m["id"] for m in room["members"] if m["controller_type"] == "agent")
    player = remote["room"]["self_member_id"]
    for actor, name in ((player, "回放玩家"), (agent, "队友")):
        card = request(
            client,
            "POST",
            "/api/characters/point-buy",
            {
                "ruleset_id": "coc7-character-creation",
                "name": name,
                "age": 25,
                "attributes": {
                    k: {"value": v}
                    for k, v in dict(
                        str=60,
                        con=60,
                        siz=60,
                        dex=60,
                        app=50,
                        int=60,
                        pow=50,
                        edu=60,
                    ).items()
                },
            },
        )
        path = "/api/characters/" + card["id"]
        card = request(
            client,
            "PATCH",
            path,
            {
                "version": card["version"],
                "occupation": "professor",
                "selected_occupation_skills": [
                    "accounting",
                    "anthropology",
                    "archaeology",
                    "history",
                ],
                "occupation_skills": {"credit_rating": {"points": 20}},
            },
        )
        card = request(client, "POST", path + "/finalize", {"version": card["version"]})
        room = request(
            client,
            "POST",
            prefix + "/character-slots",
            {
                "character_id": card["id"],
            },
        )["room"]
        slot = next(s for s in room["character_slots"] if s["source_character_id"] == card["id"])
        request(
            client,
            "POST",
            prefix + "/character-assignments",
            {
                "slot_id": slot["id"],
                "member_id": actor,
            },
        )
    request(
        client,
        "POST",
        prefix + "/ready",
        {"ready": True},
        {
            "Authorization": "Bearer " + remote["member_token"],
        },
    )
    request(client, "POST", prefix + "/ready", {"ready": True, "member_id": agent})
    request(client, "POST", prefix + "/start")
    request(client, "POST", prefix + "/pause")
    request(client, "POST", prefix + "/module", {"module_id": "stopped-clock"})
    profiles = []
    for role, actor in (("keeper", room["host_member_id"]), ("investigator", agent)):
        profile = request(client, "POST", "/api/agent-profiles", {"role": role, "name": role})
        profiles.append(profile)
        request(
            client,
            "POST",
            prefix + "/agent-bindings",
            {
                "member_id": actor,
                "profile_id": profile["id"],
            },
        )
    request(client, "POST", prefix + "/resume")
    return dict(
        prefix=prefix, room=room, remote=remote, player=player, agent=agent, profiles=profiles
    )


def control_response(messages, kwargs):
    """Deliberately omits mandatory facts: coverage must repair from originals."""
    schema = kwargs["response_schema"].__name__
    if schema == "SummaryOutput":
        return {"content": "调查员经过现场；未核实的判断仍须核实。"}
    if schema == "KeeperPlan":
        return {"parsed_intent": {"type": "observe"}, "focus": {"action_clause_ids": ["u1"]}}
    if schema == "KeeperNarration":
        return {"public_narration": "你查看了眼前的情况。"}
    if schema == "TeammateDecision":
        return {
            "mode": "speak",
            "speech_text": "此前的密码仍需核对，任务还没有完成。",
            "reason_summary": "核对待办",
            "confidence": 1,
        }
    raise AssertionError("Unexpected deterministic model phase: " + schema)


def source_replay():
    raw = SOURCE.read_bytes()
    events, _ = story_events(json.loads(raw), include_initial_reveals=True)
    records = [
        e
        for e in events
        if e["type"]
        in {
            "entity.revealed",
            "npc.spoke",
            "module.interaction",
            "check.resolved",
        }
    ][:12]
    return records, {
        "path": str(SOURCE.relative_to(ROOT)),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source_seqs": [e["seq"] for e in records],
        "mode": "existing_public_log_replay",
    }


class Replay:
    def __init__(self, client, game):
        self.client, self.game = client, game
        self.svc = client.app.state.agent_service
        self.room_id = game["room"]["id"]
        self.keeper = game["profiles"][0]["id"]
        self.adapter = FakeModelAdapter(responder=control_response)
        self.svc.model.adapter = self.adapter
        self.svc.settings.agent_context_chars = 14000
        self.svc.settings.summary_context_threshold = 9000
        self.svc.settings.agent_event_window = 6
        self.svc.settings.summary_event_threshold = 6
        self.svc.settings.model_context_limit = max(32768, self.svc.settings.model_context_limit)
        self.source_map, self.seq, self.timings = [], {}, []

    def call(self, function, *args):
        return self.client.portal.call(function, *args)

    async def seed(self, rows, label):
        async def operation(session, room):
            for name, kind, payload, visibility in rows:
                event = self.svc.rooms.append(
                    session,
                    room,
                    kind,
                    room.host_member_id,
                    payload,
                    visibility,
                )
                self.seq[name] = event.seq
                self.source_map.append(
                    {
                        "name": name,
                        "seq": event.seq,
                        "origin": label,
                        "type": kind,
                        "payload_sha256": hashlib.sha256(encoded(payload).encode()).hexdigest(),
                    }
                )
            return room.revision

        return await self.svc.mutate(self.room_id, operation)

    async def cycle(self):
        from app.agents.conversation import initial_state

        cid = str(uuid4())

        async def operation(session, room):
            state = initial_state(room.id, cid, self.game["player"], room.revision, [])
            session.add(AgentCycle(id=cid, room_id=room.id, status="completed", state=state))

        await self.svc.mutate(self.room_id, operation)
        return cid

    async def inspect(self):
        async with self.svc.rooms.database.sessions() as session:
            rows = list(
                await session.scalars(
                    select(AgentMemory).where(
                        AgentMemory.room_id == self.room_id,
                        AgentMemory.kind == "summary_segment",
                        AgentMemory.active.is_(True),
                    )
                )
            )
            recovery = await session.get(SummaryRecoveryRecord, (self.room_id, self.keeper))
            recoveries = list(
                await session.scalars(
                    select(SummaryRecoveryRecord).where(
                        SummaryRecoveryRecord.room_id == self.room_id,
                    )
                )
            )
            from app.memory.segments import validate_segment
            from app.rooms.service import Identity

            room = await self.svc.rooms.room(session, self.room_id)
            bindings = {b.profile_id: b for b in await self.svc.bindings(session, room.id)}
            traceable = {}
            for memory in rows:
                profile = await session.get(ProfileRecord, memory.profile_id)
                visible = await self.svc.rooms.events(
                    session,
                    room,
                    Identity(
                        bindings[memory.profile_id].member_id,
                        profile.role == "keeper",
                    ),
                )
                active, _ = story_events(visible, include_initial_reveals=True)
                try:
                    validate_segment(json.loads(memory.content), active)
                    traceable[memory.id] = True
                except ValueError:
                    traceable[memory.id] = False
            return {
                "segments": [
                    {
                        "id": r.id,
                        "scope": r.scope,
                        "profile_id": r.profile_id,
                        "source_event_ids": r.source_event_ids,
                        "document": json.loads(r.content),
                    }
                    for r in rows
                ],
                "recovery": recovery.document if recovery else {},
                "recoveries": {r.profile_id: r.document for r in recoveries},
                "source_retrievable": traceable,
            }

    def compress(self, *, broken=False):
        self.adapter.responder = (
            (lambda messages, kwargs: {"content": "不存在的事件 seq:999999 entity_forged"})
            if broken
            else control_response
        )
        cid, started, before = self.call(self.cycle), time.monotonic(), len(self.adapter.prompts)
        self.call(self.svc.summary_recovery.update, self.room_id, cid, True)
        state = self.call(self.inspect)
        self.timings.append(
            {
                "cycle_id": cid,
                "seconds": time.monotonic() - started,
                "calls": len(self.adapter.prompts) - before,
                "fault_injected": broken,
                "recovery": state["recovery"],
            }
        )
        return state

    async def context(self, phase, actor_profile=None):
        from app.memory.service import build_context

        async with self.svc.rooms.database.sessions() as session:
            room = await self.svc.rooms.room(session, self.room_id)
            bindings = await self.svc.bindings(session, room.id)
            binding = next(b for b in bindings if b.profile_id == (actor_profile or self.keeper))
            profile = await session.get(ProfileRecord, binding.profile_id)
            cycle = await self.svc.cycle(session, room.id)
            return (
                await build_context(self.svc, session, room, binding, profile, cycle, phase=phase)
            )[0]


def run_replay(client, game):
    replay = Replay(client, game)

    # Approved text-only catalog is a labelled synthetic control. Assignment,
    # reader filtering and save/load still use the ordinary application paths.
    async def ho_catalog():
        async def operation(session, room):
            room.session_state = {
                **room.session_state,
                "handout_catalog": {
                    "preparation_id": "b44-synthetic-ho",
                    "source_id": "b44-synthetic-source",
                    "source_hash": "4" * 64,
                    "preparation_version": 1,
                    "handouts": [
                        {
                            "id": "b44-ho",
                            "title": "合成私人HO",
                            "text": PRIVATE,
                            "source_hash": "4" * 64,
                            "source_pages": [1],
                            "source_block_ids": ["b44-private-block"],
                            "adjustments": None,
                        }
                    ],
                },
            }

        await replay.svc.mutate(replay.room_id, operation)

    request(client, "POST", game["prefix"] + "/pause")
    replay.call(ho_catalog)
    view = request(client, "GET", game["prefix"])
    own_slot = next(s for s in view["character_slots"] if s["member_id"] == game["player"])
    request(
        client,
        "POST",
        game["prefix"] + "/handout-assignments",
        {
            "handout_id": "b44-ho",
            "member_id": game["player"],
            "slot_id": own_slot["id"],
            "client_request_id": str(uuid4()),
        },
    )
    request(client, "POST", game["prefix"] + "/resume")
    records, source = source_replay()
    replay.call(
        replay.seed,
        [(f"log-{e['seq']}", e["type"], e["payload"], "public") for e in records],
        "existing_public_log_replay",
    )
    replay.compress()
    synthetic = [
        ("scene", "scene.updated", {"scene_id": "gate", "scene_title": "青铜门"}, "public"),
        (
            "password",
            "clue.revealed",
            {
                "id": "bronze-password",
                "title": "青铜门口令",
                "type": "clue",
                "content": PASSWORD,
                "scene_id": "gate",
            },
            "public",
        ),
        (
            "testimony",
            "npc.spoke",
            {
                "text": "青铜钥匙从未离开我手里。",
                "actor_name": "看门人",
                "entity_id": "gatekeeper",
                "scene_id": "gate",
            },
            "public",
        ),
        (
            "transfer",
            "module.interaction",
            {
                "text": "青铜钥匙已从玩家转交给队友。",
                "status": "executed",
                "operation": "transfer",
                "item_id": "bronze-key",
                "from_member_id": game["player"],
                "to_member_id": game["agent"],
                "quantity": 1,
                "scene_id": "gate",
            },
            "public",
        ),
        (
            "correction",
            "npc.spoke",
            {
                "text": "我先前说错了，青铜钥匙已经交给队友。",
                "actor_name": "看门人",
                "entity_id": "gatekeeper",
                "scene_id": "gate",
            },
            "public",
        ),
        (
            "old-success",
            "check.resolved",
            {
                "id": "historic-check",
                "cycle_id": "historic-cycle",
                "name": "spot_hidden",
                "reason": "确认密码盘",
                "text": "此前侦查成功。",
                "result": {"passed": True, "total": 17, "threshold": 55},
                "scene_id": "gate",
            },
            "public",
        ),
        (
            "promise",
            "agent.spoke",
            {"text": "抵达门边后我会核对青铜门密码。", "actor_name": "队友", "scene_id": "gate"},
            "public",
        ),
        (
            "resource",
            "sanity.state_changed",
            {
                "summary": "魔法值资源从10降为7。",
                "before": {"mp": 10},
                "after": {"mp": 7},
                "resource_changes": {"mp": -3},
                "target_member_id": game["agent"],
                "scene_id": "gate",
            },
            "public",
        ),
        ("private-ho", "chat.message", {"text": PRIVATE}, "host_only"),
        ("draft", "keeper.stream.delta", {"text": DRAFT}, "public"),
    ]
    replay.call(replay.seed, synthetic, "synthetic_control")
    replay.compress()
    replay.call(
        replay.seed,
        [
            (f"gap-{i}", "chat.message", {"text": f"走廊里第{i}次脚步声。"}, "public")
            for i in range(30)
        ],
        "synthetic_window_gap",
    )
    replay.compress()
    # Failure must retain both previous segment versions and completion cursor.
    before = replay.call(replay.inspect)
    replay.call(
        replay.seed,
        [
            (
                "failure-target",
                "clue.revealed",
                {
                    "content": "失败恢复必须保留原文 314159",
                    "type": "clue",
                    "title": "失败恢复",
                },
                "public",
            )
        ],
        "synthetic_fault_control",
    )
    replay.call(
        replay.seed,
        [
            (
                f"failure-tail-{i}",
                "chat.message",
                {
                    "text": f"保留最近完整对话：失败控制之后{i}。",
                },
                "public",
            )
            for i in range(6)
        ],
        "synthetic_recent_window",
    )
    failed = replay.compress(broken=True)
    failure_atomic = (
        [s["id"] for s in before["segments"]] == [s["id"] for s in failed["segments"]]
        and before["recovery"].get("last_successful_summary_seq")
        == failed["recovery"].get("last_successful_summary_seq")
        and any(r.get("stale") for r in failed["recoveries"].values())
    )
    replay.compress()
    # A single event is larger than the prompt: all source subchunks must recover.
    long_text = "超长档案开头。" + "原始档案段落与印章。" * 1700 + "末尾原文：B44-LONG-END-8152"
    replay.call(
        replay.seed,
        [
            (
                "long-event",
                "clue.revealed",
                {
                    "content": long_text,
                    "type": "clue",
                    "title": "超长原始档案",
                },
                "public",
            )
        ],
        "synthetic_long_event",
    )
    replay.call(
        replay.seed,
        [
            (
                f"long-tail-{i}",
                "chat.message",
                {
                    "text": f"保留最近完整对话：超长事件之后{i}。",
                },
                "public",
            )
            for i in range(6)
        ],
        "synthetic_recent_window",
    )
    for _ in range(30):
        state = replay.compress()
        if state["recovery"].get("last_successful_summary_seq", 0) >= replay.seq["long-event"]:
            break
    prefix = game["prefix"]
    saved = request(client, "POST", prefix + "/snapshots", {"name": "第44批分段压缩后"})["snapshot"]
    saved_state = replay.call(replay.inspect)
    replay.call(
        replay.seed,
        [
            (
                "abandoned",
                "clue.revealed",
                {
                    "content": ABANDONED,
                    "type": "clue",
                    "title": "废弃分支",
                },
                "public",
            )
        ],
        "synthetic_abandoned_branch",
    )
    replay.compress()
    restores = []
    for _ in range(2):
        view = request(client, "GET", prefix)
        if view["status"] != "paused":
            request(client, "POST", prefix + "/pause")
        request(client, "POST", prefix + f"/snapshots/{saved['id']}/load", {})
        restored = replay.call(replay.inspect)
        restores.append(restored == saved_state)
    request(client, "POST", prefix + "/resume")
    replay.call(
        replay.seed,
        [
            (
                "new-failure",
                "check.resolved",
                {
                    "id": "current-check",
                    "cycle_id": "current-cycle",
                    "name": "spot_hidden",
                    "reason": "本次确认密码盘",
                    "text": "本次侦查失败。",
                    "scene_id": "gate",
                    "result": {"passed": False, "total": 83, "threshold": 55},
                },
                "public",
            )
        ],
        "synthetic_current_failure",
    )

    async def task():
        async def operation(session, room):
            from app.agents.adjudication_schemas import BehaviorState

            row = await session.get(AgentBehaviorRecord, (room.id, game["agent"]))
            behavior = BehaviorState(
                current_short_term_goal=TASK,
                task_status="pending",
                task_scene_id="gate",
                pending_requests=[
                    {
                        "text": TASK,
                        "source_event_seq": replay.seq["promise"],
                        "status": "pending",
                        "key": "b44-task",
                        "kind": "delegate",
                        "operations": ["observe"],
                        "requester_id": game["player"],
                        "request_id": "b44-task",
                        "sender_member_id": game["player"],
                    }
                ],
            )
            if row:
                row.document = behavior.model_dump(mode="json")
            else:
                session.add(
                    AgentBehaviorRecord(
                        room_id=room.id,
                        member_id=game["agent"],
                        document=behavior.model_dump(mode="json"),
                    )
                )

        await replay.svc.mutate(replay.room_id, operation)

    replay.call(task)
    replay.adapter.responder = control_response
    start = len(replay.adapter.prompts)
    final_cycle = submit_and_wait(client, game)
    messages = replay.adapter.prompts[start:]
    state = replay.call(replay.inspect)
    contexts = {
        phase: replay.call(replay.context, phase, actor)
        for phase, actor in (
            ("plan_keeper_action", None),
            ("generate_keeper_narration", None),
            ("decide_teammates", game["profiles"][1]["id"]),
        )
    }
    result = {
        "source": source,
        "synthetic_controls": [r[0] for r in synthetic],
        "cycle_status": final_cycle["status"],
        "failure_atomic": failure_atomic,
        "multiple_restores_exact": restores,
        "storage": state,
        "retrieval": contexts,
        "actual_messages": messages,
        "source_map": replay.source_map,
        "summary_timings": replay.timings,
        "semantic_accuracy": {
            "mode": "deterministic_substitute",
            "assessed": False,
            "reason": "Exact fields and transmitted prompts are separate from answer semantics.",
        },
    }
    result["checks"] = evaluate_replay(result, replay.seq, synthetic, long_text)
    result["input_comparison"] = replay.call(compare_actual_inputs, replay)
    from scripts.batch44_matrix import run_matrix

    result["fixed_query_matrix"] = replay.call(run_matrix, replay)
    return replay, result


async def compare_actual_inputs(replay):
    """Paired gateway requests for one saved turn; no execution or new facts.

    Disabling only the derived summaries measures their added prompt cost;
    the existing event window and fact retrieval stay enabled in both variants.
    This is not the size of the whole original history before compression.
    Both requests pass through the production
    projection, dynamic schema, and gateway with an explicit model substitute.
    """
    from app.agents.action_runtime import PLAN_INSTRUCTION, generation_prompt
    from app.agents.adjudication_schemas import KeeperPlan
    from app.agents.generation_contracts import generation_contract

    svc = replay.svc
    async with svc.rooms.database.sessions() as session:
        run = await session.scalar(
            select(AgentRun)
            .where(
                AgentRun.room_id == replay.room_id,
                AgentRun.graph_node == "plan_keeper_action",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        extras = dict(run.context)
    states = {}

    async def active(value):
        async def operation(session, room):
            rows = list(
                await session.scalars(
                    select(AgentMemory).where(
                        AgentMemory.room_id == room.id,
                        AgentMemory.kind.in_(["summary", "summary_segment"]),
                    )
                )
            )
            for row in rows:
                if value is False:
                    states[row.id], row.active = row.active, False
                elif row.id in states:
                    row.active = states[row.id]

        await svc.mutate(replay.room_id, operation)

    measured = {}
    call_start = len(svc.model.calls)
    try:
        for label, compressed in (("before", False), ("after", True)):
            await active(compressed)
            context = {**extras, **await replay.context("plan_keeper_action")}
            # build_context returns only the original turn fields; the final
            # runtime additions retain the already frozen action grammar.
            schema = generation_contract(KeeperPlan, context)
            prompt = generation_prompt(context, KeeperPlan)
            messages = [
                {"role": "system", "content": PLAN_INSTRUCTION},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
            await svc.model.generate(messages, response_schema=schema, max_attempts=1)
            call = svc.model.calls[-1]
            measured[label] = {
                "request_budget": call["request_budget"],
                "messages": call["transmitted_messages"],
            }
    finally:
        await active(True)
    return {
        "mode": "same_turn_model_substitute_pair",
        "basis": "same original branch/action; derived summaries disabled then restored",
        "extra_acceptance_calls": len(svc.model.calls) - call_start,
        **measured,
    }


def evaluate_replay(result, seqs, synthetic, long_text):
    """Compare source values, never just presence of a citation identifier."""
    segments = result["storage"]["segments"]
    exact = [
        r
        for s in segments
        for r in s["document"]["required_facts"]
        if r.get("kind") == "exact_source_record"
    ]
    expected = {name: payload for name, _, payload, _ in synthetic if name != "draft"}
    fields = {
        name: any(r["source_event_seq"] == seqs[name] and r["payload"] == payload for r in exact)
        for name, payload in expected.items()
    }
    long_rows = [r for r in exact if r["source_event_seq"] == seqs["long-event"]]
    fields["long-event-entire-original"] = any(
        r["payload"].get("content") == long_text for r in long_rows
    )
    chunks = [
        c
        for s in segments
        for c in s["document"]["source_chunks"]
        if c["seq"] == seqs["long-event"]
    ]
    intervals = sorted({(c["start"], c["end"]) for c in chunks})
    cursor = 0
    for start, end in intervals:
        if start > cursor:
            break
        cursor = max(cursor, end)
    recoverable = bool(chunks) and cursor == chunks[0]["total"]
    prompts = [json.loads(p[-1]["content"]) for p in result["actual_messages"]]
    kp = [p for p in prompts if p.get("response_brief")]
    teammate = [p for p in prompts if p.get("role") == "investigator" and p.get("self_identity")]
    public = [
        *kp,
        *teammate,
        result["retrieval"]["generate_keeper_narration"],
        result["retrieval"]["decide_teammates"],
    ]
    keeper_memories = [
        record
        for prompt in kp
        for record in prompt.get("response_brief", {}).get("historical_memory", [])
    ]
    outcome_fields = [r.get("fields", {}).get("result") for r in keeper_memories]
    npc_correction = any(
        r.get("text") == expected["correction"]["text"]
        and r.get("epistemic") == "attributed_testimony"
        and r.get("source", {}).get("seq") == seqs["correction"]
        and r.get("historical_only") is True
        for r in keeper_memories
    )
    all_projection = encoded([result["retrieval"], prompts, segments])
    return {
        "designated_fields": fields,
        "designated_field_retention": sum(fields.values()) / len(fields),
        "source_retrievable_rate": (
            sum(result["storage"]["source_retrievable"].values())
            / max(1, len(result["storage"]["source_retrievable"]))
        ),
        "long_event_contiguous_recoverable": recoverable,
        "long_event_chunk_count": len(intervals),
        "completed_compressions": sum(
            t["calls"] == 1 and not t["fault_injected"] for t in result["summary_timings"]
        ),
        "failure_atomic": result["failure_atomic"],
        "multiple_restores_exact": all(result["multiple_restores_exact"]),
        "public_private_pollution": int(PRIVATE in encoded(public)),
        "abandoned_branch_pollution": int(ABANDONED in all_projection),
        "draft_memory_pollution": int(DRAFT in all_projection),
        "retrieval_early_password": PASSWORD in encoded(result["retrieval"]),
        "actual_keeper_prompt_early_password": PASSWORD in encoded(kp),
        "actual_teammate_prompt_early_password": PASSWORD in encoded(teammate),
        "actual_teammate_prompt_pending_task": TASK in encoded(teammate),
        "actual_keeper_prompt_distinct_check_outcomes": all(
            result in outcome_fields
            for result in (
                {"passed": True, "total": 17, "threshold": 55},
                {"passed": False, "total": 83, "threshold": 55},
            )
        ),
        "actual_keeper_prompt_corrected_testimony": npc_correction,
        "ordinary_cycle_completed": result["cycle_status"] == "completed",
    }


def real_probe(client, game, replay, configured):
    """One unchanged action, real configured provider, observed WebSocket receipt."""
    endpoint = urlsplit(configured.model_base_url)
    if configured.model_provider != "ollama" or endpoint.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("The private local-model probe requires existing loopback Ollama")
    svc = replay.svc
    for key in ("agent_context_chars", "model_context_limit", "model_output_limit"):
        setattr(svc.settings, key, getattr(configured, key))
    svc.model.adapter = None
    before_calls = len(svc.model.calls)
    before_seq = request(client, "GET", game["prefix"] + "/events")["latest_seq"]
    first_visible, frames = [], []
    path = "/ws/rooms/" + replay.room_id
    with client.websocket_connect(path) as socket:
        socket.send_json(
            {
                "type": "auth",
                "credential_type": "member",
                "token": game["remote"]["member_token"],
                "after_seq": 0,
            }
        )
        while socket.receive_json()["type"] != "room.synced":
            pass
        submitted_at = time.time()

        def receive():
            while True:
                frame, at = socket.receive_json(), time.time()
                if frame["type"] == "pong":
                    return
                data = frame.get("data") or {}
                kind = frame["type"]
                visible = (
                    (
                        kind in {"keeper.stream.delta", "keeper.stream.replace"}
                        and bool(data.get("text"))
                    )
                    or kind == "room.event"
                    and data.get("type")
                    in {
                        "keeper.narration",
                        "npc.spoke",
                        "agent.spoke",
                    }
                )
                if visible:
                    frames.append({"at": at, "type": kind, "data": data})
                    if not first_visible:
                        first_visible.append(at)

        with ThreadPoolExecutor(max_workers=1) as pool:
            reader = pool.submit(receive)
            cycle = submit_and_wait(client, game)
            socket.send_json({"type": "ping"})
            reader.result(timeout=10)
    calls = svc.model.calls[before_calls:]
    events = request(client, "GET", game["prefix"] + "/events")["events"]
    after = [
        e
        for e in events
        if e["seq"] > before_seq and e["type"] in {"keeper.narration", "agent.spoke"}
    ]
    semantic = {}
    for role, event_type in (("keeper", "keeper.narration"), ("teammate", "agent.spoke")):
        prose = "\n".join(e["payload"].get("text", "") for e in after if e["type"] == event_type)
        semantic[role] = {
            "text": prose,
            "password_literal_used": "07-92" in prose or "零七" in prose,
            "pending_task_acknowledged": "核对" in prose
            and any(w in prose for w in ("未", "还", "会", "先")),
        }
    summary_start = len(svc.model.calls)
    summary_at = time.monotonic()
    summary_cycle = replay.call(replay.cycle)
    replay.call(svc.summary_recovery.update, replay.room_id, summary_cycle, True)
    summary_calls = svc.model.calls[summary_start:]
    return {
        "mode": "real_local_ollama_one_fixed_probe",
        "question": FIXED_ACTION,
        "provider": configured.model_provider,
        "model": configured.model_name,
        "context_limit": svc.settings.model_context_limit,
        "output_limit": svc.settings.model_output_limit,
        "agent_context_chars": svc.settings.agent_context_chars,
        "cycle_status": cycle["status"],
        "actual_calls": calls,
        "extra_summary_calls": sum(c["schema"] == "SummaryOutput" for c in calls),
        "additional_summary_validation": {
            "mode": "one_bounded_real_summary_after_visible_response",
            "seconds": time.monotonic() - summary_at,
            "calls": summary_calls,
        },
        "first_visible_seconds": first_visible[0] - submitted_at if first_visible else None,
        "first_visible_measurement": "authenticated TestClient WebSocket receive; not browser DOM",
        "frames": frames,
        "final_public_events": after,
        "semantic_accuracy": {
            **semantic,
            "notes": "Role-specific literal/status checks; source-use needs separate review.",
        },
    }


def submit_and_wait(client, game):
    started = time.monotonic()
    request(
        client,
        "POST",
        game["prefix"] + "/actions",
        {
            "text": FIXED_ACTION,
            "client_request_id": str(uuid4()),
        },
        {"Authorization": "Bearer " + game["remote"]["member_token"]},
    )
    cycle = None
    while time.monotonic() - started < 360:
        cycle = request(client, "GET", game["prefix"] + "/agent-cycle")
        if cycle and cycle["status"] not in {"running", "queued", "queued_action"}:
            if not client.app.state.agent_service.runtime.tasks:
                return cycle
        time.sleep(0.1)
    raise AssertionError("Fixed acceptance turn did not settle: " + encoded(cycle))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--real-model", action="store_true")
    args = parser.parse_args()
    directory = (OUTPUT / args.name).resolve()
    if not directory.is_relative_to(OUTPUT.resolve()) or directory == OUTPUT.resolve():
        raise ValueError("Output must be a fresh child directory of batch-44")
    directory.mkdir(parents=True, exist_ok=False)
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.models.settings import ModelSettings

    configured = Settings()
    ModelSettings(configured)
    settings = Settings(
        **{
            **configured.model_dump(),
            "host_admin_token": secrets.token_urlsafe(32),
            "data_dir": directory,
            "database_url": "sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
            "checkpoint_db_path": directory / "checkpoint.db",
            "knowledge_db_path": directory / "knowledge.db",
            "model_settings_path": directory / "model-settings.json",
        }
    )
    with TestClient(
        create_app(settings),
        headers={
            "Authorization": "Bearer " + settings.host_admin_token.get_secret_value(),
        },
    ) as client:
        game = create_game(client)
        replay, result = run_replay(client, game)
        write(directory / "replay.json", result)
        write(directory / "matrix.json", result["fixed_query_matrix"])
        if args.real_model:
            result["real_model"] = real_probe(client, game, replay, configured)
        write(directory / "acceptance.json", result)
    print(str(directory / "acceptance.json"))


if __name__ == "__main__":
    main()
