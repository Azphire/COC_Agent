"""One generated prose source for mixed current-result KP answers.

The wire parts are private generation data. Every consumer uses this projection;
persisted narration and public APIs retain their existing schema.
"""

import json
import re
from collections import Counter
from typing import Annotated, Literal, Union

from pydantic import Field, create_model

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.narration_coverage import coverage_audit
from app.domain.character import DomainModel
from app.models.base import ModelFormatError

CONTRACT_VERSION = "kp-answer-parts-v1"
MIXED_CONTRACT_VERSION = "kp-answer-parts-v2-mixed"
SEPARATOR = "\n\n"


def server_parts(context):
    return [{"requirement_id": p["requirement_id"], "text": p["text"]}
            for p in context.get("response_brief", {}).get("server_parts", [])]


def contract_version(context):
    return MIXED_CONTRACT_VERSION if server_parts(context) else CONTRACT_VERSION


def part_origins(context, parts):
    model = {p["requirement_id"]: p for p in [
        *context.get("_answer_parts_retained", []), *_parts(parts),
    ]}
    server = {p["requirement_id"]: p for p in server_parts(context)}
    return [{"requirement_id": r["id"], "origin": "server" if r["id"] in server else "model",
             "text": (server.get(r["id"]) or model[r["id"]])["text"],
             "evidence_assessment": r.get("evidence_assessment")}
            for r in context["response_brief"]["answer_requirements"]
            if r["id"] in server or r["id"] in model]


def decode_answer_parts_document(text):
    """Use the streaming grammar at final decode too; never last-key-wins."""
    from app.agents.stream_json import IncrementalJSONObjectArray

    decoder = IncrementalJSONObjectArray("answer_parts")
    decoder.feed(text)
    if not decoder.finish():
        _fail(["answer_parts JSON不完整、重复键或结构无效"])
    return json.loads(text)


def uses_answer_parts(context):
    brief = context.get("response_brief", {})
    requirements = brief.get("answer_requirements", [])
    return bool(
        not context.get("readonly_recall")
        and context.get("intent_type") not in {"recall", "out_of_character"}
        and brief.get("responder", {}).get("kind") == "keeper"
        and brief.get("current_action_results")
        and any(r.get("kind") == "result" and r.get("source_ids") for r in requirements)
        and any(not r.get("source_ids") for r in requirements)
    )


def parts_field(context):
    retained = {p["requirement_id"] for p in context.get("_answer_parts_retained", [])}
    retained |= {p["requirement_id"] for p in server_parts(context)}
    variants = []
    for index, requirement in enumerate(context["response_brief"]["answer_requirements"]):
        if requirement["id"] in retained:
            continue
        allowed = tuple(requirement.get("source_ids", []))
        fields = {
            "requirement_id": (Literal[requirement["id"]], Field(
                description=requirement["text"],
            )),
            "text": (str, Field(
                min_length=1, max_length=2000,
                description="实际公开答复：" + requirement["text"] + (
                    "。仅据本项来源报告具体已知内容，保留原文、数量、条件和归属。"
                    if allowed else "。具体说明这项什么仍不清楚，不作无依据的肯定或否定。"
                ),
            )),
        }
        if len(allowed) > 1:
            fields["source_id"] = (Literal[allowed], ...)
        variants.append(create_model(
            f"AnswerPart{index + 1}", __base__=DomainModel, **fields,
        ))
    item = (variants[0] if len(variants) == 1 else Annotated[
        Union[tuple(variants)], Field(discriminator="requirement_id"),
    ]) if variants else DomainModel
    return list[item], Field(
        ..., max_length=len(variants),
        description="按冻结需求顺序每项恰好写一段实际正文。只写尚未由服务端保留的项；"
        "text直接呈现给玩家，不写ID或映射。未知须具体说明，状态不能代替回答。",
        json_schema_extra={"x-explicit-output": True},
    )


def _fail(errors, verified=()):
    raise ModelFormatError("KP逐段正文未通过", [{
        "field": "answer_parts",
        "code": "仅补齐或修正错误片段。已独立验证的片段由服务端保留；"
        "每项text就是实际回答，具体未知可自然表达；不生成覆盖映射或另一份正文。"
        + json.dumps({"errors": errors, "verified_parts": list(verified)},
                     ensure_ascii=False, separators=(",", ":")),
    }])


def _parts(parts):
    if not isinstance(parts, list):
        _fail(["answer_parts必须为列表"])
    return [p.model_dump(mode="json") if hasattr(p, "model_dump") else p for p in parts]


def bind_part(part, context, *, server=False):
    """Bind one source, original event and epistemic state without writing prose."""
    brief = context["response_brief"]
    requirements = {r["id"]: r for r in brief["answer_requirements"]}
    sources = {s["id"]: s for s in brief["answer_sources"]}
    if not isinstance(part, dict) or not isinstance(part.get("requirement_id"), str):
        _fail(["片段必须绑定本轮需求ID"])
    rid = part["requirement_id"]
    if rid not in requirements:
        _fail(["片段不是本轮冻结需求：" + rid])
    if not server and rid in {p["requirement_id"] for p in server_parts(context)}:
        _fail([{"id": rid, "reason": "服务端说明只读，模型不能生成或覆盖"}])
    allowed = requirements[rid].get("source_ids", [])
    assessment = requirements[rid].get("evidence_assessment", {})
    if not allowed and assessment.get("state") == "unresolved":
        _fail([{"id": rid, "reason": assessment["reason"]}])
    if set(part) - {"requirement_id", "text", "source_id"}:
        _fail([{"id": rid, "reason": "片段含非契约字段"}])
    text = part.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        _fail([{"id": rid, "reason": "缺少实际正文或正文超限"}])
    source_id = allowed[0] if len(allowed) == 1 else None
    if len(allowed) > 1:
        source_id = part.get("source_id")
        if source_id not in allowed:
            _fail([{"id": rid, "reason": "须选择本项合法来源，不能自动选第一条"}])
    elif "source_id" in part and part["source_id"] != source_id:
        _fail([{"id": rid, "reason": "不能改写服务端绑定来源"}])
    if source_id and source_id not in sources:
        _fail([{"id": rid, "reason": "绑定来源不在当前可见来源中"}])
    quote = sources[source_id]["text"] if source_id else ""
    if len(quote) > 1200:
        from app.agents.narration_coverage import _source_errors, _topic_terms
        from app.memory.recall import terms

        # Existing storage limits still apply. Choose a verified contiguous
        # excerpt of this very source; full-source conditions/values remain
        # checked by _source_errors and the full source remains in the audit.
        spans = list(re.finditer(r"[^。！？!?；;\n]+(?:[。！？!?；;\n]+|$)", quote))
        candidates = []
        for start in range(len(spans)):
            if not requirements[rid].get("generic_scope") and not (
                _topic_terms(requirements[rid]["text"]) & terms(spans[start][0])
            ):
                continue
            for end in range(start, len(spans)):
                excerpt = quote[spans[start].start():spans[end].end()]
                if len(excerpt) > 1200:
                    break
                if not _source_errors(requirements[rid], text, {"source_quote": excerpt},
                                      sources[source_id]):
                    candidates.append((len(excerpt), spans[start].start(), excerpt))
                    break
        if not candidates:
            _fail([{"id": rid, "reason": "来源无法形成保留必要事实的合法引用片段"}])
        quote = min(candidates)[2]
    # Source text is metadata only; it never supplies model prose.
    return {"requirement_id": rid, "body_quote": text, "source_id": source_id,
            "source_quote": quote,
            "status": "answered" if source_id else "unknown"}


def _unknown_assertions(body, brief):
    from app.agents.narration_coverage import unknown_assertion_errors

    return [{"id": r["id"], "reason": error} for r in brief["answer_requirements"]
            for error in unknown_assertion_errors(body, r)]


def project_answer_parts(parts, context, *, partial=False, include_server=False):
    """Validate and render in frozen order; partial still checks all assertions."""
    parts = [*_parts(context.get("_answer_parts_retained", [])), *_parts(parts)]
    brief = context["response_brief"]
    ids = [p.get("requirement_id") if isinstance(p, dict) else None for p in parts]
    if any(not isinstance(rid, str) for rid in ids):
        _fail(["片段缺少本轮需求ID"])
    duplicates = [rid for rid, count in Counter(ids).items() if count != 1]
    if duplicates:
        _fail([{"duplicate_ids": duplicates}])
    rows = {row["requirement_id"]: row for row in (bind_part(p, context) for p in parts)}
    if not partial or include_server:
        rows.update({p["requirement_id"]: bind_part(p, context, server=True)
                     for p in server_parts(context)})
    ordered = [rows[r["id"]] for r in brief["answer_requirements"] if r["id"] in rows]
    narration = KeeperNarration(
        public_narration=SEPARATOR.join(row["body_quote"] for row in ordered),
        answer_coverage=ordered, current_scene_reference=context.get("current_scene_reference"),
    )
    # Every item must independently answer its own requirement. Other text may
    # not supply a missing answer or introduce a contradictory assertion.
    errors = []
    for row in ordered:
        local = coverage_audit({"public_narration": row["body_quote"], "answer_coverage": [row]},
                               brief, partial=True)
        errors.extend(e for e in local["errors"] if e.get("id") == row["requirement_id"])
        requirement = next(r for r in brief["answer_requirements"]
                           if r["id"] == row["requirement_id"])
        if not requirement.get("source_ids"):
            from app.agents.narration_coverage import _unknown_for_requirement

            predicate = re.split(r"有没有|有无|是否", requirement["text"], maxsplit=1)[-1]
            for item in re.split(r"或|和|与|以及|、", predicate):
                if item.strip() and not _unknown_for_requirement(
                    row["body_quote"], {**requirement, "text": item},
                ):
                    errors.append({"id": requirement["id"], "reason": "须具体说明未知项：" + item})
    errors.extend(_unknown_assertions(narration.public_narration, brief))
    audit = coverage_audit(narration, brief, partial=partial)
    errors.extend(e for e in audit["errors"] if not partial or e.get("id") in rows)
    if errors or not partial and not audit["complete"]:
        _fail(errors or audit["errors"])
    for event in context.get("public_tool_results", {}).get("events", []):
        if event["type"] == "check.resolved":
            narration.check_result_reference = event["payload"].get(
                "id", event["payload"].get("check_id"),
            )
        elif event["type"] == "scene.updated":
            narration.transition_result_reference = str(event["seq"])
    return narration


def inspect_answer_parts(raw, context):
    """Call audit/failed-part retention; never promote a partial draft to success."""
    raw = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
    parts = raw.get("answer_parts", []) if isinstance(raw, dict) else []
    local = {k: v for k, v in context.items() if k != "_answer_parts_retained"}
    candidates, failures = [], []
    ids = ([p.get("requirement_id") for p in parts if isinstance(p, dict)]
           if isinstance(parts, list) else [])
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict) or ids.count(part.get("requirement_id")) != 1:
            failures.append({"reason": "重复或无效片段", "part": part})
            continue
        try:
            project_answer_parts([part], local, partial=True)
        except (ValueError, ModelFormatError) as error:
            failures.append({"id": part.get("requirement_id"),
                             "issues": getattr(error, "issues", [str(error)])})
        else:
            candidates.append(part)
    try:
        projected = project_answer_parts(parts, context)
    except (ValueError, ModelFormatError) as error:
        failures.append({"issues": getattr(error, "issues", [str(error)])})
        projected = None
    return {"contract_version": contract_version(context), "valid": projected is not None,
            "part_origins": part_origins(context, candidates),
            "verified_parts": candidates, "errors": failures,
            "projection": projected.model_dump(mode="json") if projected else None}
