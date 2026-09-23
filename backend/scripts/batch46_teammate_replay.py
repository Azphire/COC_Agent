"""Read-only replay of the retained real-07 target/cooldown failure; no model calls."""

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.adjudication_schemas import BehaviorState, TeammateDecision  # noqa: E402
from app.agents.behavior import TeammateBehaviorPolicy, public_fingerprint  # noqa: E402
from app.agents.generation_contracts import generation_contract  # noqa: E402
from app.agents.task_receipts import normalize_teammate_target  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    database = args.database.resolve()
    inputs = [database, Path(str(database) + "-wal")]
    before = {str(p): digest(p) for p in inputs}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        runs = list(connection.execute(
            "SELECT id,cycle_id,context,structured_output FROM agent_runs "
            "WHERE graph_node IN ('decide_teammates','repair_teammate_decision') "
            "ORDER BY created_at"
        ))
        checks = [json.loads(row[0]) for row in connection.execute(
            "SELECT document FROM pending_checks WHERE status='resolved'"
        )]
    cases = []
    for run_id, cycle_id, raw_context, raw_output in runs:
        context, output = json.loads(raw_context), json.loads(raw_output or "null")
        requests = context.get("addressed_requests", [])
        if not output or not any(r.get("kind") == "delegate" for r in requests):
            continue
        actor = context["self_identity"]["member_id"]
        original = TeammateDecision.model_validate(output)
        candidate = original.model_copy(deep=True)
        state = BehaviorState.model_validate(context["behavior_state"])
        targets = context["public_entities"]
        fingerprint_context = {
            "public_state": {"scene": context["public_state"]["scene_id"]},
            "public_entities": targets, "checks": checks,
        }
        bound = normalize_teammate_target(
            candidate, requests, context["inventory_state"], actor,
            context["triggering_action"].get("actor_member_id"), targets=targets,
        )

        def validate(value):
            return TeammateBehaviorPolicy().validate(
                value, state=state, recent_outputs=context["recent_outputs"],
                other_outputs=context.get("other_teammate_outputs", []),
                player_text="\n".join(r["text"] for r in requests), player_intent="observe",
                public_ids={t["id"] for t in targets},
                action_seq=context["triggering_action"]["seq"],
                fingerprint=public_fingerprint(fingerprint_context, value.target_id, actor),
                fingerprint_context=fingerprint_context,
                fact_scopes={t["id"]: t.get("fact_scope") for t in targets},
                explicit_action_request=True, requested_operations=context["requested_operations"],
                requested_targets=[r["target_id"] for r in requests if r.get("target_id")],
                inventory_state=context["inventory_state"], actor_id=actor,
                actor_name=context["self_identity"]["name"], active_requests=bound,
                public_accounts=context.get("public_accounts", []),
                result_facts=context.get("result_facts", []),
            ).model_dump(mode="json")

        try:
            generation_contract(TeammateDecision, context).model_validate(output)
            instance_schema_errors = []
        except ValueError as error:
            instance_schema_errors = [{"field": e["loc"], "type": e["type"]}
                                      for e in error.errors()]
        cases.append({
            "run_id": run_id, "cycle_id": cycle_id, "original_output": output,
            "request_keys": [r["key"] for r in requests],
            "cooldowns_before": [c.model_dump() for c in state.cooldowns],
            "original_fingerprint": public_fingerprint(fingerprint_context, original.target_id,
                                                       actor),
            "normalized_fingerprint": public_fingerprint(fingerprint_context, candidate.target_id,
                                                         actor),
            "original_validation": validate(original),
            "normalized_validation": validate(candidate),
            "normalized_decision": candidate.model_dump(mode="json"),
            "original_output_schema_errors": instance_schema_errors,
            "bound_requests": bound,
        })
    after = {str(p): digest(p) for p in inputs}
    report = {"database": str(database), "readonly": True,
              "evidence_kind": "offline_policy_replay_not_actual_execution",
              "input_hashes_before": before, "input_hashes_after": after,
              "source_unchanged": before == after, "cases": cases}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "source_unchanged": before == after,
                      "cases": [{"run_id": c["run_id"],
                                 "original_validation": c["original_validation"],
                                 "normalized_validation": c["normalized_validation"]}
                                for c in cases]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
