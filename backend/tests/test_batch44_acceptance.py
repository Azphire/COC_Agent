"""Existing local public log plus clearly labelled synthetic end-to-end controls."""

from scripts.validate_batch44 import create_game, run_replay


def test_isolated_replay_preserves_exact_fields_and_actual_model_messages(client):
    _, result = run_replay(client, create_game(client))
    checks = result["checks"]
    assert result["source"]["mode"] == "existing_public_log_replay"
    assert len(result["source"]["sha256"]) == 64
    assert checks["designated_field_retention"] == 1, checks
    assert checks["source_retrievable_rate"] == 1, checks
    assert checks["long_event_contiguous_recoverable"], checks
    assert checks["long_event_chunk_count"] >= 2
    assert checks["completed_compressions"] >= 3
    assert checks["failure_atomic"]
    assert checks["multiple_restores_exact"]
    assert checks["public_private_pollution"] == 0
    assert checks["abandoned_branch_pollution"] == 0
    assert checks["draft_memory_pollution"] == 0
    assert checks["retrieval_early_password"]
    assert checks["actual_keeper_prompt_early_password"]
    assert checks["actual_teammate_prompt_early_password"]
    assert checks["actual_teammate_prompt_pending_task"]
    assert checks["actual_keeper_prompt_distinct_check_outcomes"]
    assert checks["actual_keeper_prompt_corrected_testimony"]
    assert checks["ordinary_cycle_completed"]
    assert result["semantic_accuracy"]["assessed"] is False
    assert result["input_comparison"]["extra_acceptance_calls"] == 2
    matrix = result["fixed_query_matrix"]
    assert matrix["status"] == "passed", matrix
    assert matrix["real_model_calls"] == 0
    assert matrix["extra_acceptance_calls"] == 25
    assert matrix["source_retrievable_rate"] == 1
    assert all(c["retention_rate"] == 1 for c in matrix["coverage"].values())
