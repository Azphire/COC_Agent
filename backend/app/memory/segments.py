"""Recoverable scene segments over permission-filtered, immutable source events.

The exact records below are a code projection, not a claim that model prose is
lossless. Large events are processed in contiguous source JSON chunks; completion
means every chunk and its required fields remain recoverable from original data.
"""

import hashlib
import json
from copy import deepcopy

from app.memory.events import epistemic_event, story_events
from app.memory.facts import fact_records


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_digest(event):
    return hashlib.sha256(canonical(event).encode("utf-8")).hexdigest()


def branch_revision(visible_events):
    return max((e["seq"] for e in visible_events if e["type"] == "snapshot.loaded"), default=0)


def source_metadata(event, scene=None):
    payload = event["payload"]
    return {
        "source_event_seq": event["seq"],
        "source_event_id": event.get("id"),
        "visibility": event.get("visibility", "public"),
        "actor_member_id": event.get("actor_member_id"),
        "speaker": payload.get("actor_name") or payload.get("entity_id"),
        "scene_id": payload.get("scene_id") or payload.get("scene_title") or scene,
        "cycle_id": payload.get("cycle_id"),
        "occurred_at": event.get("occurred_at") or event.get("created_at"),
        "epistemic_status": epistemic_event(event)["epistemic_status"],
    }


def source_metadata_map(events):
    scene, result = None, {}
    for event in events:
        payload = event["payload"]
        scene = payload.get("scene_id") or scene
        if event["type"] == "scene.updated":
            scene = payload.get("scene_id") or payload.get("scene_title") or scene
        if event["type"] == "entity.revealed" and payload.get("type") == "scene":
            scene = payload.get("id") or scene
        result[event["seq"]] = source_metadata(event, scene)
    return result


def required_records(events, completed_seqs):
    """Freeze all source fields, including numbers, ownership and uncertainty.

    Keeping the payload prevents a reference-only check passing when a model
    omits a password or changes success into completion. Derived facts supplement
    the raw record and never replace it. No text is sliced in this ledger.
    """
    completed = set(completed_seqs)
    metadata = source_metadata_map(events)
    selected = [e for e in events if e["seq"] in completed]
    records = [
        {
            "id": f"source:{e['seq']}",
            "kind": "exact_source_record",
            **metadata[e["seq"]],
            "event_type": e["type"],
            "payload": deepcopy(e["payload"]),
            "source_digest": source_digest(e),
        }
        for e in selected
    ]
    by_seq = {e["seq"]: e for e in selected}
    for record in fact_records(events):
        event = by_seq.get(record.get("source_event_seq"))
        if event:
            records.append({**deepcopy(record), **metadata[event["seq"]]})
    return records


def task_source_records(events, tasks):
    """A segment can mention tasks whose source lies outside its event chunks."""
    by_seq = {event["seq"]: event for event in events}
    records = {}
    for task in tasks:
        source = task.get("source", {})
        seq = source.get("seq")
        if seq is None:
            # BehaviorState goals have no original speech event. They must
            # remain private and are never grounds for a public segment.
            if source.get("visibility") not in {"agent_private", "keeper_only"}:
                raise ValueError("Task has no visible original source")
            continue
        event = by_seq.get(seq)
        if not event or source.get("visibility") != event.get("visibility", "public"):
            raise ValueError("Task source changed or is no longer visible")
        records[seq] = {"seq": seq, "digest": source_digest(event),
                        "visibility": event.get("visibility", "public")}
    return list(records.values())


def select_chunks(
    events, base_context, fits, *, partial_seq=None, partial_offset=0, all_events=None
):
    """Fill one bounded segment without ever skipping an oversized first event.

    ``fits`` measures the actual summary request envelope. A partial event uses
    an explicitly labelled JSON fragment and advances only after atomic commit.
    """
    context = {**base_context, "events": []}
    chunks = []
    metadata = source_metadata_map(all_events if all_events is not None else events)
    first_scene = None
    for event in events:
        scene = metadata[event["seq"]]["scene_id"]
        if chunks and scene and first_scene and scene != first_scene:
            break
        first_scene = first_scene or scene
        raw = canonical(event)
        start = partial_offset if event["seq"] == partial_seq else 0
        if start >= len(raw):
            raise ValueError("Invalid partial event progress")
        full = epistemic_event(event)
        proposed = {**context, "events": [*context["events"], full]}
        end = len(raw)
        if start or not fits(proposed):
            if chunks:
                break

            def fragment(stop):
                return {
                    "seq": event["seq"],
                    "type": event["type"],
                    **metadata[event["seq"]],
                    "source_json_fragment": raw[start:stop],
                    "source_chunk": {"start": start, "end": stop, "total": len(raw)},
                    "fragment_only": True,
                }

            low, high, end = start + 1, len(raw), start
            while low <= high:
                middle = (low + high) // 2
                if fits({**context, "events": [fragment(middle)]}):
                    end, low = middle, middle + 1
                else:
                    high = middle - 1
            if end == start:
                return context, []
            proposed = {**context, "events": [fragment(end)]}
        context = proposed
        chunks.append(
            {
                "seq": event["seq"],
                "start": start,
                "end": end,
                "total": len(raw),
                "digest": source_digest(event),
                **metadata[event["seq"]],
            }
        )
        if end < len(raw) or event["type"] == "scene.updated":
            break
    return context, chunks


def make_segment(content, events, chunks, *, tasks=(), member_id=None, prior_chunks=()):
    completed = [chunk["seq"] for chunk in chunks if chunk["end"] == chunk["total"]]
    required = required_records(events, completed)
    selected = {chunk["seq"] for chunk in chunks}
    metadata = source_metadata_map(events)
    source = [e for e in events if e["seq"] in selected]
    document = {
        "version": 1,
        "reader_member_id": member_id,
        "summary": {
            "content": content,
            "positions": [r for r in required if r.get("kind") in {"npc_statement", "judgment"}],
            "unfinished": deepcopy(list(tasks)),
            "epistemic_status": "derived_summary_not_fact_authority",
        },
        "required_facts": required,
        "task_source_records": task_source_records(events, tasks),
        "source_chunks": deepcopy(chunks),
        "prior_chunks": [
            deepcopy(previous)
            for previous in prior_chunks
            if any(
                c["start"]
                and c["seq"] == previous["seq"]
                and c["digest"] == previous["digest"]
                and previous["end"] <= c["start"]
                for c in chunks
            )
        ],
        "index": {
            "scenes": list(
                dict.fromkeys(
                    metadata[e["seq"]]["scene_id"] for e in source if metadata[e["seq"]]["scene_id"]
                )
            ),
            "actors": list(
                dict.fromkeys(e.get("actor_member_id") for e in source if e.get("actor_member_id"))
            ),
            "source_start": min(selected),
            "source_end": max(selected),
            "visibility": sorted({e.get("visibility", "public") for e in source}),
        },
        "coverage": {
            "basis": "exact designated records and recoverable source chunks; not prose semantics",
            "required_count": len(required),
            "retained_count": len(required),
            "fully_covered_seqs": completed,
            "validated": True,
        },
    }
    validate_segment(document, events)
    return document


def validate_segment(document, events):
    """Reject modified values even when all citation IDs remain present."""
    by_seq = {e["seq"]: e for e in events}
    metadata = source_metadata_map(events)
    chunks = document["source_chunks"]
    if not chunks:
        raise ValueError("Empty segment")
    for chunk in [*document.get("prior_chunks", []), *chunks]:
        event = by_seq.get(chunk["seq"])
        if not event or chunk["digest"] != source_digest(event):
            raise ValueError("Segment source changed or is no longer visible")
        if not 0 <= chunk["start"] < chunk["end"] <= chunk["total"] == len(canonical(event)):
            raise ValueError("Invalid source chunk coverage")
        if any(chunk.get(key) != value for key, value in metadata[event["seq"]].items()):
            raise ValueError("Source permissions or attribution changed")
    for chunk in chunks:
        if chunk["start"]:
            previous = sorted(
                (c for c in document.get("prior_chunks", []) if c["seq"] == chunk["seq"]),
                key=lambda c: c["start"],
            )
            covered = 0
            for prior in previous:
                if prior["start"] != covered:
                    raise ValueError("Source chunk coverage contains a gap")
                covered = prior["end"]
            if covered != chunk["start"]:
                raise ValueError("Source chunk prefix is not recoverably covered")
    completed = [c["seq"] for c in chunks if c["end"] == c["total"]]
    required = required_records(events, completed)
    if canonical(document["required_facts"]) != canonical(required):
        raise ValueError("Required source fields changed or omitted")
    if document["coverage"]["fully_covered_seqs"] != completed:
        raise ValueError("Completion does not match processed chunks")
    if document["coverage"]["retained_count"] != len(required):
        raise ValueError("Coverage count does not match retained records")
    tasks = document.get("summary", {}).get("unfinished", [])
    if canonical(document.get("task_source_records", [])) != canonical(
        task_source_records(events, tasks)
    ):
        raise ValueError("Task source changed or is no longer visible")
    return True


def segment_document(memory):
    if memory.kind != "summary_segment":
        return None
    try:
        document = json.loads(memory.content)
    except (TypeError, ValueError):
        return None
    return {**document, "segment_id": memory.id} if document.get("version") == 1 else None


def active_segments(memories, visible_events):
    """Recheck reader visibility and the active branch before using any prose."""
    events, _ = story_events(visible_events, include_initial_reveals=True)
    by_seq = {e["seq"]: e for e in events}
    selected = []
    for memory in memories:
        document = segment_document(memory)
        if not memory.active or not document:
            continue
        tasks = document.get("summary", {}).get("unfinished", [])
        if getattr(memory, "scope", None) == "public" and any(
            task.get("source", {}).get("visibility") != "public" for task in tasks
        ):
            continue
        try:
            if canonical(document.get("task_source_records", [])) != canonical(
                task_source_records(events, tasks)
            ):
                continue
        except ValueError:
            continue
        if all(
            c["seq"] in by_seq and c["digest"] == source_digest(by_seq[c["seq"]])
            for c in document["source_chunks"]
        ):
            selected.append(document)
    return selected


def segment_prompt_view(document):
    """A short selected reference; full provenance remains in AgentMemory."""
    return {
        "ref": "segment:" + document["segment_id"],
        "source_range": [document["index"]["source_start"], document["index"]["source_end"]],
        "scenes": document["index"]["scenes"],
        "content": document["summary"]["content"],
        "epistemic_status": "historical_summary_not_current_execution_receipt",
    }
