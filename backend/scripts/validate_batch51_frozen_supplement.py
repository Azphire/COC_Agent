"""Recover the actual batch50 failed report on read-only database backups.

No provider requests are permitted. The original verified model part plus the
new evidence-scoped server explanation must complete the old report via HTTP.
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.agents.model import FakeModelAdapter
from app.config import Settings
from app.main import create_app
from scripts.check_character_creation import TEST_HOST

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/prepared/batch-50/real-01"
DESTINATION = ROOT / "data/prepared/batch-51/frozen-report-recovery"
PROTECTED_TABLES = (
    "character_drafts", "character_roll_records", "character_events", "agent_profiles",
    "module_preparations", "module_entities", "module_snapshots", "room_entity_states",
    "room_character_slots", "pending_checks", "agent_tool_receipts",
    "room_module_navigation_states",
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(connection, table, where="", params=()):
    return [dict(r) for r in connection.execute("SELECT * FROM " + table + where, params)]


def state(room_id):
    with sqlite3.connect(DESTINATION / "game.db") as connection:
        connection.row_factory = sqlite3.Row
        return {
            "protected": {table: rows(connection, table) for table in PROTECTED_TABLES},
            "room": rows(connection, "game_rooms", " WHERE id=?", (room_id,))[0],
            "events": rows(connection, "room_events", " WHERE room_id=? ORDER BY seq", (room_id,)),
            "runs": rows(connection, "agent_runs", " WHERE room_id=?", (room_id,)),
            "plans": rows(connection, "agent_action_plans", " WHERE room_id=?", (room_id,)),
            "behavior": rows(connection, "agent_behavior_states", " WHERE room_id=?", (room_id,)),
            "calls": rows(connection, "agent_model_calls"),
        }


async def completed(service):
    await asyncio.gather(*list(service.report_supplements.tasks.values()))


def reject_network(*_args, **_kwargs):
    raise AssertionError("Offline recovery must never call a provider or external HTTP")


def main():
    global DESTINATION
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DESTINATION)
    DESTINATION = parser.parse_args().directory.resolve()
    assert DESTINATION.is_relative_to((ROOT / "data/prepared/batch-51").resolve())
    DESTINATION.mkdir(parents=True, exist_ok=False)
    protected = [SOURCE / name for name in ("game.db", "knowledge.db", "checkpoint.db")]
    fixture = ROOT / "backend/tests/fixtures/batch50/real-01-current-failure.json"
    manifest = read(fixture.with_name("real-01-current-manifest.json"))
    assert digest(fixture) == manifest["sha256"]
    frozen = read(fixture)
    hashes = {str(p): digest(p) for p in [*protected, fixture]}
    for path in protected:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(DESTINATION / path.name) as backup:
                original.backup(backup)
    setup = read(SOURCE / "setup-state.json")
    room_id = setup["room_id"]
    before = state(room_id)
    old_run = next(r for r in before["runs"] if r["id"] == frozen["run"]["id"])
    assert json.loads(old_run["context"]) == frozen["run"]["context"]
    original_report = next(e for e in before["events"]
                           if e["type"] == "keeper.narration"
                           and json.loads(e["payload"]).get("cycle_id") == old_run["cycle_id"])
    assert json.loads(original_report["payload"])["safe_fallback"]
    token = TEST_HOST
    settings = Settings(
        _env_file=None, host_admin_token=token, data_dir=DESTINATION,
        database_url=f"sqlite+aiosqlite:///{(DESTINATION / 'game.db').as_posix()}",
        checkpoint_db_path=DESTINATION / "checkpoint.db",
        knowledge_db_path=DESTINATION / "knowledge.db",
        model_settings_path=DESTINATION / "model-settings.json",
    )
    adapter = FakeModelAdapter([])
    app = create_app(settings)
    app.state.agent_model_adapter = adapter
    with patch.object(httpx.HTTPTransport, "handle_request", reject_network), patch.object(
        httpx.AsyncHTTPTransport, "handle_async_request", reject_network,
    ), TestClient(app, headers={"Authorization": "Bearer " + setup["player_token"]}) as client:
        prefix = "/api/rooms/" + room_id
        view = client.get(prefix)
        assert view.status_code == 200, view.text
        recovery = next(r for r in view.json()["game"]["report_recoveries"]
                        if r["report_seq"] == original_report["seq"])
        assert recovery["available"], recovery
        key = str(uuid4())
        endpoint = prefix + f"/reports/{original_report['seq']}/supplement"
        response = client.post(endpoint, json={"client_request_id": key})
        assert response.status_code == 200, response.text
        client.portal.call(completed, app.state.agent_service)
        replay = client.post(endpoint, json={"client_request_id": key})
        assert replay.status_code == 200 and replay.json()["supplement"]["id"] == key
        client.portal.call(completed, app.state.agent_service)
        assert not app.state.agent_service.model.calls
        after = state(room_id)
        supplement = next(r for r in after["runs"] if r["id"] == key)
        context = json.loads(supplement["context"])
        assert supplement["status"] == "completed", supplement["safe_error"]
        assert context["model_call_count"] == 0
        assert before["protected"] == after["protected"]
        assert before["plans"] == after["plans"]
        assert before["room"]["session_state"] == after["room"]["session_state"]
        assert before["calls"] == after["calls"]
        assert all(r in after["runs"] for r in before["runs"])
        assert all(e in after["events"] for e in before["events"])
        additions = [e for e in after["events"] if e["seq"] > original_report["seq"]
                     and json.loads(e["payload"]).get("supplement_of") == original_report["seq"]]
        formal = [e for e in additions if e["type"] == "keeper.narration"]
        assert len(formal) == 1
        payload = json.loads(formal[0]["payload"])
        assert not payload["safe_fallback"] and payload["answer_origin"] == "mixed"
        assert frozen["run"]["context"]["retained_answer_parts"][0]["text"] in payload["text"]
        actor = frozen["run"]["context"]["response_brief"]["speaker_id"]
        behavior = json.loads(next(
            b["document"] for b in after["behavior"] if b["member_id"] == actor
        ))
        assert not any(r["key"] == "25:7" for r in behavior["pending_requests"])
        settled = next(r for r in behavior["request_history"] if r["key"] == "25:7")
        assert settled["status"] == "completed"
        assert settled["result_cycle_id"] == old_run["cycle_id"]
        assert behavior["last_result"]["unknown_properties"][0]["status"] == "unknown"
        from app.memory.facts import fact_records

        public = client.get(prefix + "/events").json()["events"]
        facts = fact_records(public)
        assert not any(f["source_event_seq"] == formal[0]["seq"] for f in facts)
        assert not any("没有夹层" in f.get("text", "") for f in facts)
        report = {"original_report_seq": original_report["seq"], "supplement_seq": formal[0]["seq"],
                  "original_cycle_id": old_run["cycle_id"], "original_run_id": old_run["id"],
                  "supplement_id": key, "actual_model_calls": 0, "actual_provider_calls": 0,
                  "old_event_and_run_unchanged": True, "old_action_plan_unchanged": True,
                  "cards_dice_resources_and_receipts_unchanged": True,
                  "formal_text": payload["text"], "part_origins": context["part_origins"],
                  "settled_request": settled,
                  "unknown_properties": behavior["last_result"]["unknown_properties"],
                  "canonical_facts_exclude_supplement": True,
                  "source_hashes": deepcopy(hashes)}
    assert all(digest(Path(path)) == sha for path, sha in hashes.items())
    report["all_original_source_hashes_match"] = True
    (DESTINATION / "proof.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    print(json.dumps({"completed": True, "original_report_seq": original_report["seq"],
                      "supplement_seq": formal[0]["seq"], "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
