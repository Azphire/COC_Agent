"""Count actual public outcomes and preserve review links, without grading prose automatically."""

import json
from collections import Counter
from pathlib import Path

from scripts.play_batch36 import write


def audit_quality(directory: Path):
    def load(name):
        return json.loads(
            (directory / "private-audit" / (name + ".json")).read_text(encoding="utf-8")
        )

    events, cycles, calls, plans = [
        load(name) for name in ("host-events", "cycles", "model-calls", "cycle-audits")
    ]
    public = [e for e in events if e["visibility"] == "public"]
    by_id = {p["cycle_id"]: p["document"] for p in plans}
    reviews = []
    for cycle in cycles:
        state = cycle["state"]
        trigger = next((e for e in events if e["seq"] == state.get("triggering_event_seq")), {})
        related = [e for e in public if e["payload"].get("cycle_id") == cycle["id"]]
        plan = by_id.get(cycle["id"], {}).get("plan", {})
        reviews.append(
            {
                "trigger_seq": trigger.get("seq"),
                "origin": state.get("origin"),
                "input": trigger.get("payload", {}).get("text"),
                "cycle_id": cycle["id"],
                "status": cycle["status"],
                "clarification": state.get("requires_clarification", False),
                "attempt": (plan.get("focus") or {}).get("action"),
                "feedback": [
                    {
                        "seq": e["seq"],
                        "type": e["type"],
                        "text": e["payload"].get("text")
                        or e["payload"].get("display_text")
                        or e["payload"].get("public_summary")
                        or e["payload"].get("scene_title"),
                    }
                    for e in related
                    if e["type"]
                    in {
                        "keeper.narration",
                        "npc.spoke",
                        "agent.spoke",
                        "agent.action_proposed",
                        "scene.updated",
                        "check.resolved",
                        "combat.resolved",
                        "module.interaction",
                    }
                ],
            }
        )
    counts = Counter(e["type"] for e in public)
    rejections = Counter(
        r.get("reason")
        for e in events
        if e["type"] == "agent.teammate_decision"
        for r in e["payload"].get("rejections", [])
    )
    write(
        directory / "interaction-metrics.json",
        {
            "definition": (
                "Counts describe actual events; content quality needs the linked review."
            ),
            "public_event_counts": dict(counts),
            "human_inputs": sum(
                e["type"] == "action.submitted" and not e["payload"].get("continuation_of")
                for e in public
            ),
            "question_continuations": sum(
                e["type"] == "action.submitted" and bool(e["payload"].get("continuation_of"))
                for e in public
            ),
            "teammate_attempts": counts["agent.action_proposed"],
            "clarifications": counts["action.clarification_requested"],
            "model_calls": len(calls),
            "model_call_failures": sum(bool(c["document"].get("error_category")) for c in calls),
            "teammate_generation_failures": sum(
                e["type"] == "agent.teammate_generation_failed" for e in events
            ),
            "failed_cycles": sum(c["status"] == "failed" for c in cycles),
            "narration_fallbacks": sum(bool(e["payload"].get("safe_fallback")) for e in public),
            "cycles_with_narration_fallback": sum(
                bool(p["document"].get("narration_validation", {}).get("fallback_reason"))
                for p in plans
            ),
            "npc_partial_fallback_cycles": [
                p["cycle_id"] for p in plans
                if p["document"].get("narration_validation", {}).get("fallback_reason")
                and any(e["type"] == "npc.spoke"
                        and e["payload"].get("cycle_id") == p["cycle_id"] for e in public)
            ],
            "teammate_rejections": dict(rejections),
            "ending_events": [e["seq"] for e in public if e["type"] == "module.completed"],
            "cycle_review": reviews,
        },
    )
