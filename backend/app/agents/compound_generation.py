"""Generation-only alternatives for the additive public CheckRequest contract."""

from functools import reduce
from operator import or_
from typing import Literal

from pydantic import Field, create_model

from app.rules.compound import CombinedCheck, OpposedCheck, noncombat_name


def explicit(default=None):
    return Field(default=default, json_schema_extra={"x-explicit-output": True})


def proposal_contract(base, context):
    actor_id = context["action_identifiers"]["actor_member_id"]
    actor = next((c for c in context.get("characters", []) if c.get("member_id") == actor_id), {})
    kinds = {
        "attribute": list(actor.get("effective_attributes", {})),
        "skill": [n for n in actor.get("skill_values", {}) if noncombat_name(n)],
    }
    if not any(kinds.values()):
        return base  # Old minimal model contexts remain readable.
    candidates = context.get("compound_candidates", {})
    opponents = []
    members = candidates.get("members", [])
    npcs = candidates.get("npcs", [])
    for kind, names in kinds.items():
        if members and names:
            opponents.append(
                create_model(
                    "MemberOpponent" + kind.title(),
                    __base__=OpposedCheck,
                    opponent_member_id=(Literal[tuple(members)], ...),
                    opponent_npc_id=(None, explicit()),
                    kind=(Literal[kind], ...),
                    name=(Literal[tuple(names)], ...),
                )
            )
        for npc in npcs:
            allowed = [
                n
                for n in npc.get("attributes" if kind == "attribute" else "skills", [])
                if noncombat_name(n)
            ]
            if allowed:
                opponents.append(
                    create_model(
                        "NPCOpponent" + kind.title() + str(len(opponents)),
                        __base__=OpposedCheck,
                        opponent_member_id=(None, explicit()),
                        opponent_npc_id=(Literal[npc["npc_id"]], ...),
                        kind=(Literal[kind], ...),
                        name=(Literal[tuple(allowed)], ...),
                    )
                )
    alternatives = [
        create_model(
            "OrdinaryCheckProposal",
            __base__=base,
            opposed=(None, explicit()),
            combined=(None, explicit()),
        )
    ]
    if opponents:
        opponent = reduce(or_, opponents)
        for kind, names in kinds.items():
            if names:
                alternatives.append(
                    create_model(
                        "Opposed" + kind.title() + "Proposal",
                        __base__=base,
                        opposed=(opponent, ...),
                        combined=(None, explicit()),
                        kind=(Literal[kind], ...),
                        name=(Literal[tuple(names)], ...),
                        difficulty=(Literal["regular"], explicit("regular")),
                        combat=(Literal[False], explicit(False)),
                    )
                )
    if len(kinds["skill"]) >= 2:
        second = create_model(
            "CombinedSkill", __base__=CombinedCheck, name=(Literal[tuple(kinds["skill"])], ...)
        )
        alternatives.append(
            create_model(
                "CombinedCheckProposal",
                __base__=base,
                opposed=(None, explicit()),
                combined=(second, ...),
                kind=(Literal["skill"], ...),
                name=(Literal[tuple(kinds["skill"])], ...),
                combat=(Literal[False], explicit(False)),
            )
        )
    return reduce(or_, alternatives)
