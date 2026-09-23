"""Disposable task operands and receipt-based progress; no model calls."""

import re
from copy import deepcopy

QUANTITY = r"([0-9]+|[零〇一二两三四五六七八九十百千]+)(?:个|把|本|张|份|支|枚|件|瓶)"
ITEM_OPERATIONS = {"give", "take", "pickup", "drop", "place", "throw", "use", "consume"}


def restore_task_operands(request, frozen):
    """Fill absent operands without overwriting newer values, zero or empty lists."""
    restored = deepcopy(request)
    for key, value in frozen.items():
        if key not in restored or restored[key] is None:
            restored[key] = deepcopy(value)
        elif isinstance(restored[key], dict) and isinstance(value, dict):
            restored[key] = restore_task_operands(restored[key], value)
    return restored


def quantity_value(value):
    if value.isdigit():
        return int(value)
    digits = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
    total, current = 0, 0
    for word in value:
        if word in digits:
            current = digits[word]
        else:
            total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[word]
            current = 0
    return total + current


def requested_quantity(text):
    found = re.search(QUANTITY, text)
    return quantity_value(found[1]) if found else 1


async def current_task_targets(session, room_id, public):
    """Add only approved aliases of identities already public in this room/scene."""
    from sqlalchemy import select

    from app.persistence.preparation_models import RoomEntityState

    targets = {t["id"]: deepcopy(t) for t in public if t.get("fact_scope") == "current_scene"}
    for target in targets.values():
        target["aliases"] = [a for a in (target.get("aliases") or []) if isinstance(a, str)]
    if targets:
        for entity in await session.scalars(select(RoomEntityState).where(
            RoomEntityState.room_id == room_id,
            RoomEntityState.source_entity_id.in_(targets),
            RoomEntityState.state.in_(["revealed", "corrected"]),
        )):
            target = targets[entity.source_entity_id]
            target["aliases"] = list(dict.fromkeys([
                *target["aliases"],
                *[a for a in entity.snapshot.get("aliases", []) if isinstance(a, str)],
            ]))
    return list(targets.values())


def bind_task_operands(request, inventory, actor, requester=None, *, targets=None):
    """Freeze only uniquely supported IDs before the proposed action executes."""
    from app.preparation.action_authority import operative_fragments, requested_action_kinds

    if targets is not None:
        targets = [t for t in targets if t.get("fact_scope", "current_scene") == "current_scene"]
        targets += [{"id": p["id"], "title": p.get("name", ""), "aliases": p.get("names", [])}
                    for p in inventory.get("other_actors", [])
                    if p.get("fact_scope") == "current_scene"]
        targets += [{"id": m["id"], "title": m.get("name", "")}
                    for m in inventory.get("members", []) if m["id"] != actor]
    operations = request.get("operations", [])
    if len(operations) > 1:
        bound = deepcopy(request)
        bound["executor_member_id"] = actor
        mapped = deepcopy(request.get("operation_operands", {}))
        aliases = {"drop": "place", "pickup": "take"}
        fragments = operative_fragments(request.get("text", ""))
        for operation in operations:
            if operation in mapped:
                continue
            parts = [f["text"] for f in fragments if aliases.get(operation, operation)
                     in requested_action_kinds(f["text"])]
            # A shared fragment with several actions does not establish which
            # item/quantity belongs to each. Keep that obligation pending.
            if len(parts) != 1 or len(set(requested_action_kinds(parts[0]))
                                       & set(operations)) > 1:
                mapped[operation] = {"unresolved_operands": True}
                continue
            single = {"text": parts[0], "operations": [operation],
                      **{k: request[k] for k in ("key", "source_event_seq", "source_start",
                                                "source_end", "scene_id") if k in request}}
            scoped = _bind_single(single, inventory, actor, requester, targets=targets)
            mapped[operation] = {
                "target_id": None, "recipient_member_id": None,
                "item_ids": [], "item_instance_ids": [], "required_items": {},
                **scoped,
            }
        bound["operation_operands"] = mapped
        return bound
    return _bind_single(request, inventory, actor, requester, targets=targets)


def normalize_teammate_target(decision, requests, inventory, actor, requester=None, *, targets=()):
    """Bind an already chosen attempt to its uniquely authorized current operand.

    Request binding is also returned for generation, queueing and receipt settlement.
    Discussion, refusal and explicit alternative choices remain the agent's choice.
    """
    from app.preparation.action_authority import action_kinds

    current = [t for t in targets if t.get("fact_scope") == "current_scene"]
    current += [{"id": p["id"], "title": p.get("name", ""),
                 "aliases": p.get("names", [])}
                for p in inventory.get("other_actors", [])
                if p.get("fact_scope") == "current_scene"]
    current += [{"id": m["id"], "title": m.get("name", "")}
                for m in inventory.get("members", []) if m["id"] != actor]
    allowed = {t["id"] for t in current}
    bound = [bind_task_operands(r, inventory, actor, requester, targets=current) for r in requests]
    if decision.mode not in {"act", "assist"} or decision.target_id:
        return bound
    operations = set(action_kinds(decision.action_text or "")) - {"converse", "pass"}
    compatible = operations | ({"observe"} if "search" in operations else set())
    candidates = set()
    for request in bound:
        if request.get("kind") != "delegate" or not request.get("available", True):
            continue
        for operation in compatible & set(request.get("operations", [])):
            operands = {**request, **request.get("operation_operands", {}).get(operation, {})}
            if operands.get("unresolved_operands"):
                continue
            target = operands.get("target_id")
            if target in allowed:
                candidates.add(target)
            candidates.update(set(operands.get("target_candidates", [])) & allowed)
    actual = bind_task_operands(
        {"text": decision.action_text or "", "operations": sorted(operations)},
        inventory, actor, requester, targets=current,
    )
    # If the agent names an alternative object, keep the normal clarification
    # or authority path. Never reinterpret that action as the requested object.
    named = {actual["target_id"]} if actual.get("target_id") else set(
        actual.get("target_candidates", [])
    )
    if named:
        candidates &= named
    if len(candidates) == 1:
        decision.target_id = next(iter(candidates))
        decision.related_public_entity_ids = list(dict.fromkeys([
            *decision.related_public_entity_ids, decision.target_id,
        ]))[:8]
    return bound


def teammate_target_options(context):
    """Separate actionable public entity IDs from available physical instances."""
    inventory = context.get("inventory_state") or {}
    actor = context.get("self_identity", {}).get("member_id")
    actor = actor or context.get("action_identifiers", {}).get("actor_member_id")
    entities = context.get("public_entities", context.get("current_targets", []))
    current = [e["id"] for e in entities if e.get("fact_scope", "current_scene") == "current_scene"]
    current += [p["id"] for p in inventory.get("other_actors", [])
                if p.get("fact_scope") == "current_scene"]
    current += [m["id"] for m in inventory.get("members", []) if m["id"] != actor]
    current += [h["item_id"] for h in inventory.get("holders", []) if h.get("item_id")]
    instances = [h["instance_id"] for h in inventory.get("holders", [])
                 if actor and h.get("holder_id") == actor]
    scene = context.get("action_identifiers", {}).get("current_scene_id")
    scene = scene or context.get("public_state", {}).get("scene_id")
    scene = scene or context.get("public_state", {}).get("scene")
    instances += [item["instance_id"] for item in inventory.get("dropped_items", [])
                  if scene and item.get("scene_node_id") == scene]
    return list(dict.fromkeys(current)), list(dict.fromkeys(instances))


def target_reference_spans(text, targets):
    """Explicit testimony attribution identifies a source, not an action object.

    Keep every ordinary object mention, including a later mention of the same
    speaker. Only a closed source phrase and its directly attached quote are
    excluded; arbitrary surrounding text cannot consume an operation clause.
    """
    quote = r'(?:“[^”]*”|‘[^’]*’|「[^」]*」|"[^"]*")'
    modifier = r"(?:早先|先前|此前|之前|刚才|原先|曾经|所)*"
    testimony = r"(?:说法|证词|陈述|描述|报告)"
    saying = r"(?:说过|说|表示|提到过|提到)"
    spans = set()
    for target in targets:
        for name in [target.get("title", ""), *target.get("aliases", [])]:
            if not name:
                continue
            source = re.escape(name) + modifier
            patterns = [
                r"(?:根据|依据|按照|依照|据)\s*" + source + r"的?" + testimony
                + r"(?:\s*[:：]?\s*" + quote + r")?",
                r"(?:^|(?<=[，。；！？,;.!?\n]))\s*" + source + saying
                + r"\s*[:：]?\s*" + quote,
            ]
            for pattern in patterns:
                spans.update(m.span() for m in re.finditer(pattern, text))
    spans = sorted(span for span in spans if not any(
        other != span and other[0] <= span[0] and span[1] <= other[1] for other in spans
    ))
    return [{"start": start, "end": end, "text": text[start:end]} for start, end in spans]


def _bind_single(request, inventory, actor, requester, *, targets):
    from app.preparation.action_authority import mentions_alias

    bound = deepcopy(request)
    bound["executor_member_id"] = actor
    text = request.get("text", "")
    item_ids = [item["id"] for item in inventory.get("known_items", [])
                if any(mentions_alias(text, name) for name in item.get("names", []))]
    if item_ids and not bound.get("item_ids"):
        bound["item_ids"] = item_ids
    if set(request.get("operations", [])) & ITEM_OPERATIONS and item_ids:
        required_items = {}
        named_spans = []
        for item in inventory.get("known_items", []):
            if item["id"] not in item_ids:
                continue
            spans = {(m.start(), m.end()) for name in item.get("names", []) if name
                     for m in re.finditer(re.escape(name), text)}
            # A title and its shorter alias can name the same occurrence.
            spans = {span for span in spans if not any(
                outer != span and outer[0] <= span[0] and span[1] <= outer[1] for outer in spans
            )}
            if len(spans) != 1:
                bound["unresolved_operands"] = True
                continue
            start, end = next(iter(spans))
            if any(start < previous_end and previous_start < end
                   for previous_start, previous_end in named_spans):
                bound["unresolved_operands"] = True
            named_spans.append((start, end))
            prefix = text[:start]
            quantity = re.search(QUANTITY + r"\s*$", prefix)
            count = quantity_value(quantity[1]) if quantity else 1
            if re.search(r"所有|全部|若干|几|一些|一半|半", prefix[-8:]) or count < 1:
                bound["unresolved_operands"] = True
            required_items[item["id"]] = count
        bound["required_items"] = required_items
        bound["quantity"] = sum(required_items.values())
    if targets is not None and "give" not in request.get("operations", []):
        visible = [t for t in targets
                   if t.get("fact_scope", "current_scene") == "current_scene"]
        references = target_reference_spans(text, visible)
        target_text = list(text)
        for span in references:
            # A non-whitespace separator prevents alias matches across an
            # excluded reference when mentions_alias compacts ordinary spaces.
            target_text[span["start"]:span["end"]] = "\ufffc" * (span["end"] - span["start"])
        target_text = "".join(target_text)
        named = {t["id"] for t in visible if any(
            mentions_alias(target_text, name)
            for name in [t.get("title", ""), *t.get("aliases", [])] if name
        )}
        if len(named) == 1:
            bound["target_id"] = next(iter(named))
            bound.pop("target_candidates", None)
            bound.pop("target_binding_rejection", None)
            target = next(t for t in visible if t["id"] == bound["target_id"])
            bound["target_source"] = {
                "request_key": request.get("key"),
                "request_source_event_seq": request.get("source_event_seq"),
                "target_id": target["id"], "title": target.get("title", ""),
                "fact_scope": target.get("fact_scope", "current_scene"),
                "excluded_reference_spans": references,
                **{k: target[k] for k in ("source_event_seq", "revealed_event_seq", "source",
                                         "ref", "scene_id")
                   if k in target},
            }
        elif named:
            bound["target_id"] = None
            bound["target_candidates"] = sorted(named)[:8]
            bound.pop("target_source", None)
        elif bound.get("target_id"):
            source = bound.get("target_source") or {}
            verified = (
                bound["target_id"] in {t["id"] for t in visible}
                and source.get("target_id") == bound["target_id"]
                and source.get("request_key") is not None
                and source["request_key"] == request.get("key")
                and source.get("request_source_event_seq") == request.get("source_event_seq")
            )
            if not verified:
                bound["target_binding_rejection"] = {
                    "reason": "unverified_target_id", "proposed_target_id": bound["target_id"],
                }
                bound["target_id"] = None
                bound.pop("target_source", None)
    if "give" in request.get("operations", []):
        recipients = {member["id"] for member in inventory.get("members", [])
                      if member["id"] != actor and member.get("name")
                      and re.search(r"(?:给|到)" + re.escape(member["name"]), text)}
        if requester and re.search(r"(?:给|到)我", text):
            recipients.add(requester)
        if len(recipients) == 1:
            bound["recipient_member_id"] = next(iter(recipients))
        bound.setdefault("quantity", requested_quantity(text))
        held = [h["instance_id"] for h in inventory.get("holders", [])
                if h["holder_id"] == actor and h["item_id"] in item_ids]
        # Multiple interchangeable instances with a smaller requested quantity
        # remain item-type bound; never pick an arbitrary physical instance.
        if len(held) == bound["quantity"] and not bound.get("item_instance_ids"):
            bound["item_instance_ids"] = held
    return bound


def receipt_progress(request, facts, *, actor, cycle_id, consumed):
    """Return remaining request and completion flag; each receipt unit counts once."""
    current = deepcopy(request)
    completed_seqs = list(current.get("completion_event_seqs", []))
    previous_seqs = set(completed_seqs)
    progress = deepcopy(current.get("operation_progress", {}))
    remaining_ops = []
    for operation in request.get("operations", []):
        operands = {**request, **request.get("operation_operands", {}).get(operation, {})}
        if operands.get("unresolved_operands"):
            remaining_ops.append(operation)
            continue
        previous = progress.get(operation, {})
        if not previous and len(request.get("operations", [])) == 1:
            previous = {k: request[k] for k in ("remaining_quantity", "remaining_item_instance_ids")
                        if k in request}
        item_operation = operation in ITEM_OPERATIONS
        instances = list(previous.get(
            "remaining_item_instance_ids", operands.get("item_instance_ids", [])
        )) if item_operation else []
        quantity = previous.get("remaining_quantity", operands.get(
            "quantity", requested_quantity(operands.get("text", ""))
        )) if item_operation else 1
        instance_bound = bool(instances)
        counted_instances = set(previous.get("counted_item_instances", []))
        remaining_items = deepcopy(previous.get(
            "remaining_items", operands.get("required_items", {})
        ))
        if remaining_items:
            quantity = sum(remaining_items.values())
        if instances:
            quantity = len(instances)
        item_ids = set(operands.get("item_ids", [])) if item_operation else set()
        target = (operands.get("recipient_member_id") if operation == "give" else None)
        target = target or operands.get("target_id")
        progressed = False
        for fact in facts:
            if (fact.get("status") != "success" or fact.get("operation") != operation
                    or fact.get("actor_id") != actor or fact.get("cycle_id") != cycle_id
                    or current.get("executor_member_id",
                                   current.get("addressee_id", actor)) != actor
                    or fact.get("source_event_seq") in previous_seqs):
                continue
            actual_target = (fact.get("recipient_member_id") if operation == "give" else None)
            actual_target = actual_target or fact.get("action_target_id") or fact.get("target_id")
            if target and actual_target != target:
                continue
            items = fact.get("operated_items", [])
            if not item_operation and not target:
                continue  # An observation of an unrelated object is not completion.
            if item_operation and not (instances or item_ids):
                # A legacy request without frozen operands cannot use just the
                # subset named by a receipt: another requested item may be absent.
                # Keep it pending until the ordinary enqueue path binds it.
                continue
            if operation == "give" and not target:
                continue  # Ambiguous recipient remains pending.
            units = items if item_operation or instances or item_ids else [{}]
            for item in units:
                instance = item.get("instance_id") or item.get("id")
                if operation in {"give", "pickup", "take", "drop", "place", "throw"} and (
                    instance in counted_instances
                ):
                    continue
                if instances and instance not in instances:
                    continue
                if item_ids and item.get("id") not in item_ids:
                    continue
                if remaining_items and remaining_items.get(item.get("id"), 0) <= 0:
                    continue
                key = (fact.get("source_event_seq"), operation, instance)
                if key in consumed:
                    continue
                consumed.add(key)
                if instances:
                    instances.remove(instance)
                quantity -= 1 if instance_bound or instance else max(1, item.get("quantity", 1))
                if instance:
                    counted_instances.add(instance)
                if remaining_items:
                    remaining_items[item["id"]] -= 1
                progressed = True
                if fact.get("source_event_seq") not in completed_seqs:
                    completed_seqs.append(fact["source_event_seq"])
                if quantity <= 0:
                    break
            if quantity <= 0:
                break
        if quantity > 0:
            remaining_ops.append(operation)
            if progressed or previous:
                progress[operation] = {"remaining_quantity": quantity}
                if counted_instances:
                    progress[operation]["counted_item_instances"] = sorted(counted_instances)
                if remaining_items:
                    progress[operation]["remaining_items"] = remaining_items
                if operands.get("item_instance_ids"):
                    progress[operation]["remaining_item_instance_ids"] = instances
        else:
            progress.pop(operation, None)
    current["operations"] = remaining_ops
    current.pop("remaining_quantity", None)
    current.pop("remaining_item_instance_ids", None)
    if progress:
        current["operation_progress"] = progress
        if len(remaining_ops) == 1:
            current.update(progress.get(remaining_ops[0], {}))
    if completed_seqs:
        current["completion_event_seqs"] = completed_seqs
    return current, bool(request.get("operations")) and not remaining_ops
