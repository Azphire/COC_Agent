"""Small publication guards for attributed knowledge and unexecuted proposals."""

import re

from app.agents.narration_coverage import (
    TESTIMONY,
    _condition_errors,
    _factual_clauses,
    _topic_terms,
)
from app.memory.recall import terms


def public_wording_sources(context, accounts=()):
    """Use the same selected public evidence as the decision, never private HO."""
    sources = [
        {"text": e.get("public_summary", ""), "kind": "source_text"}
        for e in context.get("public_entities", [])
    ]
    sources += [r for r in context.get("memory_evidence", [])
                if r.get("kind") in {"source_text", "npc_statement"}
                and r.get("source", {}).get("visibility") == "public"]
    sources += [{**r, "kind": "npc_statement" if r["kind"] == "testimony" else "result"}
                for r in accounts if r.get("status") == "current"
                and r.get("kind") in {"testimony", "result"}]
    return sources


def source_claim_error(text, sources):
    """Keep a referenced condition with its consequence, not every source detail.

    This is deliberately bounded to recognizable assertions of the same public
    predicate. Questions and new attempts do not assert a source's consequence.
    """
    for sentence in re.findall(r"[^。；;！？!?\n]+[。；;！？!?\n]*", text):
        assertions = "".join(c for c in _factual_clauses(sentence) if not re.search(
            r"是否|能否|会不会|有没有|我(?:去|试着|尝试)", c,
        ))
        if not assertions:
            continue
        for source in sources:
            support = source.get("text", "")
            if len(_topic_terms(support) & terms(sentence)) < 2:
                continue
            for clause in re.split(r"[，,。；;！？!?\n]", support):
                # The existing source guard handles explicit if/only premises.
                # Keep it scoped to the consequence actually repeated here.
                match = re.search(r"(?:就能|才能|才可以|就可以)(.{2,16})", clause)
                if not match or not (_topic_terms(match[1]) & terms(assertions)):
                    continue
                consequence_claims = [c for c in _factual_clauses(assertions)
                                      if _topic_terms(match[1]) & terms(c)]
                if consequence_claims and all(re.search(
                    r"不能|不一定|不代表|不等于|无法|未必|没法|不是说|尚未|没有确认", c,
                ) for c in consequence_claims):
                    continue  # Caution does not assert the weakened consequence.
                if _condition_errors(sentence, support):
                    return "source_condition_changed"
                effort = re.search(r"(?:足够|足夠|充分|使劲|用足)(?:地)?用?力|足够用力", clause)
                if effort and (
                    not re.search(r"足够用力|充分用力|用足力气|使劲|用力足够|足夠用力", sentence)
                    or re.search(r"轻松|轻易|毫不费力|不用力|无需用力", sentence)
                ):
                    return "source_condition_changed"
            if source.get("kind") != "npc_statement":
                continue
            if re.search(r"我(?:自己)?(?:还|也|仍|目前)?(?:不知|不清楚|不了解|没把握)", assertions):
                continue  # A character can describe their own uncertainty naturally.
            # Only attribute a recognizable claim from testimony. An overlapping
            # object name or a suggestion is not enough to demand an attribution.
            related = [c for c in _factual_clauses(support)
                       if len(_topic_terms(c) & terms(assertions)) >= 3]
            if not related:
                continue
            speaker = source.get("speaker", "")
            names = [n for n in re.split(r"[·・\s]", speaker) if len(n) >= 2]
            if names and not (
                any(n in sentence for n in names) and re.search(TESTIMONY, sentence)
            ):
                # A separately revealed fact need not be presented as hearsay.
                confirmed = any(
                    s.get("kind") in {"source_text", "result"}
                    and any(c.strip("，,。；;") in s.get("text", "") for c in related)
                    for s in sources
                )
                if not confirmed:
                    return "source_attribution_missing"
    return None


def proposal_completion_error(action, speech, *, explicit_action_request=False):
    """A proposal is submitted before its child cycle; old receipts cannot run it."""
    for clause in re.split(r"[，,。；;！？!?\n]", action):
        if re.search(r"没有|没能|尚未|未曾|不曾|如果|是否|能否|试着|尝试", clause):
            continue
        if re.search(r"刚才|此前|先前|早先|之前|上次|过去那次", clause):
            continue  # The ordinary actor/target result check still applies.
        if re.search(
            r"我(?:们)?(?:已经|已|刚刚|刚|成功)?(?:查看|检查|观察|搜索|核对|打开|确认)了|"
            r"我(?:们)?(?:已经|已|成功)(?:查看|检查|观察|搜索|核对|打开|确认)|"
            r"^(?:并|也|而且)?(?:查明了|发现了|确认了)|"
            r"^(?:并|也|而且)?确认.{0,15}(?:确实|已经|就是)",
            clause.strip(),
        ):
            return "premature_action_completion"
    if explicit_action_request and re.search(
        r"我(?:这次|本次|现在)?(?:已经|已|成功)(?:查看|检查|观察|搜索|核对|打开|确认)|"
        r"(?:这次|本次)(?:已经|已)?(?:确认|查明|完成)", speech,
    ) and not re.search(r"刚才|此前|先前|早先|之前|上次|过去那次", speech):
        return "premature_action_completion"
    return None
