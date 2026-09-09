"""Port historical scripted scenarios to the explicit batch-7 model contracts.

This is fixture data construction only. Runtime adapters never accept legacy plans.
Malformed historical responses are intentionally left malformed for schema-error tests.
"""

import json

from app.agents.action_policy import explicit_movement
from app.agents.model import FakeModelAdapter


class ScenarioAdapter(FakeModelAdapter):
    async def generate(self, messages, **kwargs):
        response = await super().generate(messages, **kwargs)
        schema = kwargs.get("response_schema")
        if not schema or not messages:
            return response
        if schema.__name__ not in {"KeeperPlan", "KeeperNarration", "TeammateDecision"}:
            return response
        value = response.structured
        if not isinstance(value, dict):
            return response
        context = json.loads(messages[-1]["content"])
        if schema.__name__ == "KeeperPlan" and "tools" in value:
            if len(value["tools"]) > 4 or set(value) - {"tools", "claims", "needs_host_ruling"}:
                return response
            ids = context["action_identifiers"]
            text = context["triggering_action"]["payload"]["text"]
            moves = [t for t in value["tools"] if t["name"] in {"update_scene", "transition_scene"}]
            transition = None
            if moves:
                target = moves[0]["arguments"].get("target_scene_node_id") or moves[0][
                    "arguments"
                ].get("scene_id")
                transition = next(
                    (
                        t
                        for t in context["approved_exits"]
                        if target in {t["target_scene_node_id"], t.get("target_entity_id")}
                    ),
                    None,
                )
            intent = {
                "type": "move" if moves and explicit_movement(text) else "investigate",
                "actor_member_id": ids["actor_member_id"],
                "actor_character_slot_id": ids["actor_character_slot_id"],
                "evidence_quote": text,
                "confidence": 1,
            }
            if intent["type"] == "move" and transition:
                intent["target_id"] = transition["target_scene_node_id"]
            tools = [t for t in value["tools"] if t["name"] != "send_narration"]
            if context.get("structure_navigation") and transition:
                tools = [
                    {
                        "name": "transition_scene",
                        "arguments": {
                            "target_scene_node_id": transition["target_scene_node_id"],
                            "expected_revision": ids["expected_navigation_revision"],
                            "request_id": ids["plan_id"] + ":move",
                        },
                    }
                    if t["name"] == "transition_scene" and set(t["arguments"]) == {"scene_id"}
                    else t
                    for t in tools
                ]
            for claim in value.get("claims", []):
                if claim.get("category") == "module_fact" and claim.get("evidence_ids"):
                    tools.append(
                        {
                            "name": "propose_module_fact",
                            "arguments": {
                                "proposed_title": claim["statement"][:80],
                                "proposed_public_summary": claim["statement"],
                                "evidence_ids": claim["evidence_ids"],
                            },
                        }
                    )
            response.structured = {
                "plan_id": ids["plan_id"],
                "cycle_id": ids["cycle_id"],
                "current_scene_id": ids["current_scene_id"],
                "expected_navigation_revision": ids["expected_navigation_revision"],
                "parsed_intent": intent,
                "proposed_tool_calls": tools,
                "proposed_check": None,
                "proposed_transition_id": transition["transition_id"] if transition else None,
            }
        elif schema.__name__ == "KeeperNarration" and (
            "content" in value or "claims" in value or "needs_host_ruling" in value
        ):
            claims = value.get("claims")
            if claims is None:
                claims = context["PUBLIC_CLAIM_OPTIONS"][-1:]
            response.structured = {
                "public_narration": "\n".join(c["statement"] for c in claims),
                "grounded_claims": claims,
                "needs_host_ruling": value.get("needs_host_ruling", False),
            }
        elif schema.__name__ == "TeammateDecision" and "tools" in value:
            speech = next(
                (t for t in value["tools"] if t["name"] in {"speak", "propose_action"}), None
            )
            response.structured = {
                "mode": "speak"
                if speech and speech["name"] == "speak"
                else "act"
                if speech
                else "pass",
                "related_player_action_seq": context["triggering_action"]["seq"],
                "confidence": 1,
            }
            if speech:
                response.structured[
                    "speech_text" if speech["name"] == "speak" else "action_text"
                ] = speech["arguments"]["text"]
        return response
