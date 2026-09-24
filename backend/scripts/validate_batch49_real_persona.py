"""One explicit real prose repair in an isolated copy; never writes the source store."""

import argparse
import asyncio
import copy
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi import FastAPI

from app.agents.model import AgentModelClient
from app.api.party import router
from app.character.repository import CharacterRepository
from app.character.service import CharacterService
from app.config import Settings
from app.models.settings import ModelSettings
from app.party.service import PartyService
from app.persistence.database import Database
from app.persistence.party_models import PartyBatch
from app.rules.loader import load_rulesets

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def numeric(card):
    return {key: value for key, value in card.items() if key not in {"name", "background"}}


async def run(args):
    source = args.source.resolve()
    snapshot = args.snapshot.resolve()
    output = args.output.resolve()
    output.relative_to(ROOT / "data" / "prepared" / "batch-49")
    if not args.run_model:
        print(json.dumps({"source": str(source), "snapshot": str(snapshot),
                          "output": str(output), "calls": 0,
                          "next": "Pass --run-model to make exactly one explicit repair call."}))
        return
    if output.exists():
        raise ValueError("Output already exists; select a new isolated evidence directory")
    output.mkdir(parents=True)
    source_hashes = {str(path): digest(path) for path in (source, snapshot)}
    with sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True) as original:
        with sqlite3.connect(output / "game.db") as cloned:
            original.backup(cloned)
    frozen = json.loads(snapshot.read_text(encoding="utf-8"))
    if "party_generation_batches" in frozen:
        frozen = frozen["party_generation_batches"][0]
    else:
        frozen = {"id": frozen["id"], "document": frozen}
    document = copy.deepcopy(frozen["document"])
    document.update(id=str(uuid4()), _revision=0,
                    _requests={}, _active_owner=None, _lease_until=0)
    document.pop("launch", None)
    document["character_ids"], document["profile_ids"] = [], []
    settings = Settings()
    # Load current selected model before pointing writes at the isolated data directory.
    configuration = ModelSettings(settings)
    settings.data_dir = output
    settings.database_url = f"sqlite+aiosqlite:///{(output / 'game.db').as_posix()}"
    database = Database(settings.database_url)
    await database.initialize()
    try:
        async with database.sessions.begin() as session:
            session.add(PartyBatch(id=document["id"], request_id=str(uuid4()),
                                   document=document))
        model = AgentModelClient(settings)
        model.configuration = configuration
        svc = PartyService(SimpleNamespace(
            rooms=SimpleNamespace(database=database), settings=settings, model=model,
        ), CharacterService(CharacterRepository(database), load_rulesets()))
        application = FastAPI()
        application.state.settings = settings
        application.state.party_service = svc
        application.include_router(router)
        before = await svc.get(document["id"])
        write(output / "before.json", before)
        write(output / "origin.json", {"source_hashes": source_hashes,
                                      "source_batch_id": frozen["id"],
                                      "member_index": args.member_index,
                                      "provider": settings.model_provider,
                                      "model": settings.model_name,
                                      "context_limit": settings.model_context_limit})
        started = time.monotonic()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://isolated") as client:
            response = await client.post(
                f"/api/party-batches/{document['id']}/members/{args.member_index}/repair-persona",
                headers={"Authorization": "Bearer " + settings.host_admin_token.get_secret_value()},
                json={"request_id": str(uuid4())},
            )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        after = await svc.get(document["id"])
        write(output / "after.json", after)
        write(output / "response.json", {"status": response.status_code, "body": response.json()})
        a, b = before["members"][args.member_index], after["members"][args.member_index]
        calls = b.get("_model_calls", [])[len(a.get("_model_calls", [])):]
        checks = {
            "source_unchanged": all(digest(Path(path)) == value
                                    for path, value in source_hashes.items()),
            "one_call": b["model_calls"] - a["model_calls"] == 1,
            "numeric_card_unchanged": numeric(a["character"]) == numeric(b["character"]),
            "name_unchanged": a["character"]["name"] == b["character"]["name"],
            "seed_unchanged": before["seed"] == after["seed"]
            and a["stream_seed"] == b["stream_seed"],
            "other_members_unchanged": all(old == new for index, (old, new)
                                           in enumerate(zip(before["members"], after["members"]))
                                           if index != args.member_index),
            "prior_attempts_preserved": b["attempts"][:len(a["attempts"])] == a["attempts"],
            "prior_rejections_preserved": b.get("_persona_rejections", [])[
                :len(a.get("_persona_rejections", []))] == a.get("_persona_rejections", []),
        }
        result = {"status": b["status"], "elapsed_ms": elapsed_ms, "checks": checks,
                  "model_calls": len(calls), "cumulative_model_calls": b["model_calls"],
                  "usage": [call.get("token_usage") for call in calls],
                  "error": b.get("error"), "batch_id": document["id"]}
        write(output / "calls.json", calls)
        write(output / "result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        if not all(checks.values()):
            raise AssertionError("Isolation or repair preservation check failed")
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=ROOT / "data/prepared/batch-48/real-01/game.db")
    parser.add_argument("--snapshot", type=Path,
                        default=ROOT / "data/prepared/batch-48/real-01/before-final-persona.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data/prepared/batch-49/real-persona-01")
    parser.add_argument("--member-index", type=int, default=0)
    parser.add_argument("--run-model", action="store_true")
    asyncio.run(run(parser.parse_args()))
