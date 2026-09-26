"""Evidence-scoped, server-authored uncertainty; never a world fact or source."""

import re
from copy import deepcopy
from hashlib import sha256


def _question_binding(subject, question, target, entity, brief, cycle_id, action_seq):
    """Use public names or the original server-bound same-request reference."""
    subject = re.sub(r"^(?:这|那|这个|那个|该)", "", subject).strip()
    if any(name and len(name) >= 2 and (name in subject or subject in name)
           for name in [entity.get("title", ""), *entity.get("aliases", [])]):
        return {"kind": "public_object_name", "target_id": target}
    for request in brief.get("delegated_requests", []):
        if (request.get("target_id") != target or question not in request.get("text", "")
                or request.get("cycle_id") != cycle_id
                or request.get("source_action_seq") != action_seq):
            continue
        for operand in request.get("operation_operands", {}).values():
            source = operand.get("target_source", {})
            if (operand.get("target_id") == target and source.get("target_id") == target
                    and source.get("request_key") == request.get("key")
                    and request.get("key")
                    and source.get("request_source_event_seq") == request.get("source_event_seq")
                    and source.get("binding_kind") == "same_request_reference"
                    and subject in source.get("reference_text", "")):
                return {"kind": "same_request_reference", "target_id": target,
                        "request_key": request["key"],
                        "source_event_seq": request["source_event_seq"]}
    return None


def capture_evidence_scope(context, public_entities):
    """Called before prompt trimming, with only the public entity projection."""
    public_entities = deepcopy(public_entities)
    scene = context.get("module", {}).get("scene", {})
    if scene.get("id") and not any(e["id"] == scene["id"] for e in public_entities):
        public_entities.append({"id": scene["id"], "type": "scene",
                                "title": scene.get("title", ""),
                                "public_summary": scene.get("public_description", ""),
                                "fact_scope": "current_scene"})
    return {
        "public_entities": public_entities,
        "complete_public_projection": True,
        "visibility": "public",
    }


def prepare_server_parts(context, brief, sources):
    from app.agents.narration_coverage import _topic_terms
    from app.memory.recall import terms

    scope = context.get("answer_evidence_scope")
    if not scope or brief.get("responder", {}).get("kind") != "keeper":
        return brief
    requirements = deepcopy(brief.get("answer_requirements", []))
    if not (brief.get("current_action_results")
            and any(r.get("kind") == "result" and r.get("source_ids") for r in requirements)
            and any(not r.get("source_ids") for r in requirements)):
        return brief
    target = context.get("fact_target") or (brief.get("observation_subject") or {}).get("id")
    public = scope.get("public_entities", [])
    entity = next((e for e in public if e.get("id") == target), None)
    receipts = context.get("public_tool_results", {})
    current = receipts.get("current_result_facts", [])
    cycle_id = receipts.get("cycle_id")
    actor = brief.get("speaker_id") or context.get("triggering_action", {}).get("actor_member_id")
    action_seq = brief.get("trigger_seq") or context.get("triggering_action", {}).get("seq")
    bound = [r for r in current if r.get("status") == "success"
             and r.get("operation") in {"search", "reveal", "observe"}
             and r.get("cycle_id") == cycle_id and r.get("source_event_seq")
             and actor and r.get("actor_id") == actor
             and action_seq and r.get("source_action_seq") == action_seq
             and (r.get("action_target_id") or r.get("target_id")) == target]
    selection = context.get("memory_selection_audit", {})
    omissions = deepcopy(selection.get("omitted", []))
    omissions += deepcopy(selection.get("required_omitted", []))
    omissions += deepcopy(context.get("prompt_budget_audit", {}).get("omitted_memory", []))
    if context.get("memory_omission", {}).get("count"):
        omissions.append(deepcopy(context["memory_omission"]))
    # Inspect full public texts before the lexical answer-source selector, plus
    # selected original history and current receipts. No hidden snapshot fields.
    checked = [{"id": "entity:" + e["id"], "text": e.get("public_summary", ""),
                "kind": "untrimmed_public_entity"} for e in public
               if e.get("fact_scope", "current_scene") == "current_scene"]
    checked += [{"id": "e" + str(r["source_event_seq"]), "text": r.get("effect", ""),
                 "kind": "current_receipt"} for r in current]
    checked += [s for s in sources if s.get("historical")]
    audit_scope = [{"id": s["id"], "kind": s["kind"], "chars": len(s["text"]),
                    "sha256": sha256(s["text"].encode()).hexdigest()} for s in checked]
    parts = []
    for requirement in requirements:
        question = requirement["text"]
        probe = re.fullmatch(r"(.+?)(有没有|有无|是否)(.+)", question)
        binding = (_question_binding(
            probe[1], question, target, entity, brief, cycle_id, action_seq,
        ) if probe and entity else None)
        evidence = {"object_id": target, "object_name": (entity or {}).get("title"),
                    "question": question, "checked_scope": audit_scope,
                    "target_binding": binding,
                    "omissions": omissions, "receipt_seqs": [r["source_event_seq"] for r in bound]}
        if requirement.get("source_ids"):
            evidence.update(state="sourced", reason="selected_visible_sources")
        else:
            reason = None
            if not target or not entity or not probe or not binding:
                reason = "target_or_question_unclear"
            elif not scope.get("complete_public_projection") or scope.get("visibility") != "public":
                reason = "incomplete_public_projection"
            elif omissions or selection.get("required_complete") is False:
                reason = "budget_omission"
            elif selection.get("unloaded_event_seqs") or context.get("memory_retrieval_failed"):
                reason = "recall_failure"
            elif (not bound or receipts.get("blocked_discovery")
                  or brief.get("unconfirmed_target") or context.get("rejected_actions")):
                reason = "execution_blocked_or_unbound"
            elif any(_topic_terms(probe[3]) & terms(s["text"]) for s in checked):
                # A potentially relevant full source was missed by selection.
                # Conservative: repair retrieval; do not assert an absence.
                reason = "recall_gap"
            if reason:
                evidence.update(state="unresolved", reason=reason)
            else:
                evidence.update(state="not_confirmed", reason="checked_visible_scope")
                parts.append({"requirement_id": requirement["id"],
                              "text": question + "，目前还不能确定。",
                              "origin": "server", "epistemic_status": "unknown"})
        requirement["evidence_assessment"] = evidence
    server_ids = {p["requirement_id"] for p in parts}
    requirements.sort(key=lambda r: (2 if r["id"] in server_ids else
                                    0 if r.get("kind") == "result" else 1))
    return {**brief, "answer_requirements": requirements, "server_parts": parts}
