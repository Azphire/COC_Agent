"""Safe fragments still need the shared privacy gate after composition."""

import re

from test_action_adjudication import modern_response
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_rooms import lobby, ok  # noqa: F401

from app.agents.generation_contracts import narration_body_field
from app.agents.model import FakeModelAdapter


def test_fallback_does_not_publish_a_private_phrase_assembled_from_safe_fragments(
    client,
    game,  # noqa: F811
    monkeypatch,
):
    from app.agents import narration_coverage

    first, second = "灯是青色的。", "门是石制的。"
    secret = "灯是青色的门是石制的"
    requirements = [
        {"id": "r-color", "kind": "question", "text": "灯是什么颜色？", "source_ids": ["color"]},
        {
            "id": "r-material",
            "kind": "question",
            "text": "门是什么材质？",
            "source_ids": ["material"],
        },
    ]
    sources = [
        {"id": source, "text": text, "kind": "observation"}
        for source, text in [("color", first), ("material", second)]
    ]
    monkeypatch.setattr(
        narration_coverage,
        "prepare_response_contract",
        lambda context: {
            **context["response_brief"],
            "answer_requirements": requirements,
            "answer_sources": sources,
        },
    )
    service = client.app.state.agent_service

    async def seed_private_catalog():
        async def operation(session, room):
            room.session_state = {
                **room.session_state,
                "handout_catalog": {
                    "handouts": [{"text": secret}],
                },
            }

        await service.mutate(game["room"]["id"], operation)

    client.portal.call(seed_private_catalog)
    attempts = []

    def respond(messages, kwargs):
        schema = kwargs["response_schema"]
        if schema.__name__ != "KeeperNarration":
            return modern_response(messages, kwargs)
        index = len(attempts)
        attempts.append(messages)
        text, requirement, source = (
            (first, requirements[0], "color")
            if index == 0
            else (
                second,
                requirements[1],
                "material",
            )
        )
        return {
            narration_body_field(schema): text,
            "answer_coverage": [
                {
                    "requirement_id": requirement["id"],
                    "body_quote": text,
                    "source_id": source,
                    "source_quote": text,
                    "status": "answered",
                }
            ],
        }

    service.model.adapter = FakeModelAdapter(responder=respond)
    validate = service.runtime.validate_narration_output
    safe_partials = []

    async def capture(*args, **kwargs):
        audit = await validate(*args, **kwargs)
        if kwargs.get("partial"):
            safe_partials.append(args[-1].public_narration)
        return audit

    monkeypatch.setattr(service.runtime, "validate_narration_output", capture)
    ok(submit(client, game, "我看看四周"))
    cycle = wait_cycle(client, game)
    assert cycle["status"] == "completed" and len(attempts) == 2
    assert first in safe_partials and second in safe_partials
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    final = [
        e["payload"]
        for e in events
        if e["type"] == "keeper.narration" and e["payload"].get("cycle_id") == cycle["id"]
    ][-1]
    assert secret not in re.sub(r"[\W_]", "", final["text"])
    assert first in final["text"]
    assert final["safe_fallback"] and final["answer_origin"] == "server_fallback"
    audit = ok(client.get(game["prefix"] + f"/cycles/{cycle['id']}/validation"))[
        "narration_validation"
    ]
    assert audit["answer_complete"] is False
