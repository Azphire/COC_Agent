import json

from app.knowledge.text import normalize, tokens
from app.module_ir.schemas import NodeArgs
from app.persistence.agent_models import AgentCycle
from app.rooms.service import require


def public_interaction_results(runtime, public_ids):
    """Keep completed public results when the optional event history is trimmed.

    These are historical receipts, not new facts inferred from player prose.
    Only the latest result per visible entity is needed; private rulings and
    inventory snapshots must not enter a teammate's context.
    """
    latest = {}
    for receipt in sorted(
        runtime.get("receipts", {}).values(), key=lambda r: r.get("source_event_seq", 0)
    ):
        if receipt.get("entity_id") in public_ids and receipt.get("text"):
            latest[receipt["entity_id"]] = {
                k: receipt.get(k)
                for k in ("entity_id", "source_event_seq", "actor_member_id", "text")
            }
    return sorted(latest.values(), key=lambda r: r["source_event_seq"])[-8:]


def scene_nodes(snapshot, current_id):
    nodes = {n.node_id: n for n in snapshot.nodes}
    selected = []

    def visit(node):
        if not node.included or node.node_id != current_id and node.approved_type == "scene":
            return
        selected.append(node.node_id)
        for child in node.child_ids:
            visit(nodes[child])

    visit(nodes[current_id])
    return selected


class ModuleContextResolver:
    def __init__(self, agents):
        self.agents = agents

    async def resolve(
        self,
        session,
        room,
        role,
        *,
        recent_action="",
        budget=3000,
        cycle=None,
        persist_selection=True,
    ):
        nav = self.agents.navigation
        state = await nav.state(session, room.id)
        if not state:
            return None
        public = await nav.public_scene(session, room)
        public_entities = await self.agents.entities.public(session, room.id)
        from app.module_ir.facts import relevant_public_facts

        public_entities = relevant_public_facts(public_entities, recent_action)
        shell = await self.agents.module(session, room.id)
        from app.preparation.inventory import public_inventory

        holdings = await public_inventory(self.agents, session, room)
        if role != "keeper":
            return {
                "module": {
                    "id": shell.document["id"],
                    "title": shell.document["title"],
                    "scene": shell.document["scenes"][0],
                },
                "public_entities": public_entities,
                "public_state": {
                    "scene_id": shell.state["scene_id"],
                    "completed_interactions": public_interaction_results(
                        room.session_state.get("module_runtime", {}),
                        {e["id"] for e in public_entities},
                    ),
                },
                "structure_navigation": True,
                "item_holders": holdings,
            }
        snapshot, ir = await nav.snapshot(session, state)
        await nav.refresh(session, room, state, snapshot)
        nodes = {n.node_id: n for n in snapshot.nodes}
        current = nodes[state.current_scene_node_id]
        selected_nodes = scene_nodes(snapshot, current.node_id)
        candidate_blocks = [b for b in ir.blocks if b.node_id in selected_nodes]
        from app.preparation.runtime import current_entity_ids

        entity_ids = current_entity_ids(
            snapshot, selected_nodes, room.session_state.get("module_runtime", {})
        )
        entities = await self.agents.entities.host(session, room.id)
        relevant_ids = {e["id"] for e in public_entities}
        entities = [e for e in entities if e["id"] in entity_ids or e["id"] in relevant_ids]
        entity_types = {"scene": 0, "npc": 1, "location": 2, "clue": 3, "item": 4}
        entities.sort(key=lambda e: (entity_types[e["type"]], e["id"]))
        outgoing = [
            t
            for t in snapshot.transitions
            if t.source_scene_node_id == current.node_id and t.approved
        ]
        module = {
            "id": ir.module_id,
            "title": ir.display_title,
            "public_introduction": shell.document["public_introduction"],
            "current_scene": {
                "node_id": current.node_id,
                "title": current.title,
                "heading_path": current.heading_path,
                "summary": current.keeper_summary or public["summary"],
            },
            "blocks": [],
            "approved_entities": [],
            "outgoing_transitions": [],
            "ancestors": [],
            "recent_scenes": [],
        }
        audit = {
            "current_scene_node_id": current.node_id,
            "heading_path": current.heading_path,
            "structure_snapshot_id": snapshot.snapshot_id,
            "module_source_hash": snapshot.source_hash,
            "navigation_revision": state.navigation_revision,
            "selected_node_ids": [current.node_id],
            "selected_block_ids": [],
            "omitted_block_count": 0,
            "budget_used": 0,
            "budget": budget,
            "context_mode": "structure",
            "fallback_reason": None,
        }

        def fits():
            return len(json.dumps(module, ensure_ascii=False)) <= budget

        def append(key, item):
            module[key].append(item)
            if not fits():
                module[key].pop()
                return False
            return True

        compact_entities = [
            {
                k: e[k]
                for k in (
                    "id",
                    "type",
                    "title",
                    "keeper_summary",
                    "public_summary",
                    "state",
                    "reveal_conditions",
                    "suggested_checks",
                )
                if e.get(k)
            }
            for e in entities
        ]
        runtime = room.session_state.get("module_runtime", {})
        if runtime.get("flags") or runtime.get("inventory"):
            module["interaction_state"] = {
                "flags": runtime.get("flags", {}),
                "held_items": holdings,
                "doors": runtime.get("doors", {}),
            }
        if not fits():
            # Include mandatory current state before allocating the scene prose.
            # The complete summary remains in the frozen snapshot and blocks.
            summary = module["current_scene"]["summary"]
            remaining = budget - len(json.dumps(module, ensure_ascii=False)) + len(summary) - 4
            module["current_scene"]["summary"] = summary[: max(0, remaining)]
            audit["scene_summary_truncated"] = True
        raw_size = sum(len(b.text) + 130 for b in candidate_blocks)
        long_scene = (
            len(json.dumps(module, ensure_ascii=False))
            + raw_size
            + len(json.dumps(compact_entities, ensure_ascii=False))
            + len(json.dumps([t.model_dump() for t in outgoing], ensure_ascii=False))
        ) > budget
        query_tokens = set(tokens(recent_action))
        matched_titles = [
            e["title"]
            for e in entities
            if any(
                alias and normalize(alias) in normalize(recent_action)
                for alias in (e["title"], *e.get("aliases", []))
            )
        ]
        important = {b for n in snapshot.nodes for b in n.important_block_ids}
        if long_scene:
            audit["context_mode"] = "local_fallback"
            audit["fallback_reason"] = "current_scene_budget"
            compact_entities.sort(
                key=lambda e: (
                    -int(e["title"] in matched_titles),
                    -len(query_tokens & set(tokens(e["title"]))),
                    {"npc": 0, "clue": 1, "location": 2, "item": 3, "scene": 4}[e["type"]],
                )
            )
            compact_entities = [
                {
                    k: v[:240] if k in {"keeper_summary", "public_summary"} else v
                    for k, v in e.items()
                }
                for e in compact_entities
            ]
            candidate_blocks.sort(
                key=lambda b: (
                    -int(any(title in b.text for title in matched_titles)),
                    -int(b.block_type == "read_aloud" or b.block_id in important),
                    -int(b.block_id in state.selected_block_ids),
                    -len(query_tokens & set(tokens(b.text))),
                    b.source_order,
                )
            )
        # Exact current-action entities must survive a small context budget.
        # Full exit provenance remains in the frozen navigation snapshot/audit.
        for entity in compact_entities:
            if entity["title"] in matched_titles:
                append("approved_entities", entity)
        for transition in outgoing:
            append(
                "outgoing_transitions",
                {
                    "transition_id": transition.transition_id,
                    "target_scene_node_id": transition.target_scene_node_id,
                    "target_public_title": nodes[transition.target_scene_node_id].public_title,
                    "condition_summary": transition.condition_summary[:160],
                    "available": transition.transition_id in state.available_transition_ids,
                },
            )
        # Keep room for entity and transition metadata in long scenes.
        reserve = min(900, max(0, budget // 3)) if long_scene else 0
        for block in candidate_blocks:
            item = {
                "block_id": block.block_id,
                "node_id": block.node_id,
                "type": block.block_type,
                "text": block.text,
                "physical_page": block.page_reference,
            }
            if block.table_structure is not None:
                item["table"] = block.table_structure
            capacity = budget - reserve - len(json.dumps(module, ensure_ascii=False))
            if len(json.dumps(item, ensure_ascii=False)) > capacity:
                # Keep a bounded observed prefix when one source paragraph exceeds the
                # remaining scene budget; the original IR block stays intact.
                overhead = len(
                    json.dumps({**item, "text": "", "truncated": True}, ensure_ascii=False)
                )
                if capacity - overhead < 80 or block.table_structure is not None:
                    continue
                item["text"] = block.text[: capacity - overhead - 4]
                item["truncated"] = True
            if append("blocks", item):
                audit["selected_block_ids"].append(block.block_id)
                if block.node_id not in audit["selected_node_ids"]:
                    audit["selected_node_ids"].append(block.node_id)
        # Selection priority does not change the source's block order in the prompt.
        orders = {b.block_id: b.source_order for b in ir.blocks}
        module["blocks"].sort(key=lambda b: orders[b["block_id"]])
        for entity in compact_entities:
            if entity not in module["approved_entities"]:
                append("approved_entities", entity)
        parent = nodes.get(current.parent_node_id)
        while parent:
            append(
                "ancestors",
                {
                    "node_id": parent.node_id,
                    "title": parent.title,
                    "summary": parent.keeper_summary[:160],
                },
            )
            parent = nodes.get(parent.parent_node_id)
        for node_id in state.visited_scene_node_ids[-3:]:
            if node_id != current.node_id:
                n = nodes[node_id]
                append(
                    "recent_scenes",
                    {
                        "node_id": node_id,
                        "title": n.public_title,
                        "summary": n.public_summary[:160],
                    },
                )
        audit["omitted_block_count"] = len(candidate_blocks) - len(module["blocks"])
        audit["truncated_block_count"] = sum(bool(b.get("truncated")) for b in module["blocks"])
        audit["omitted_entity_count"] = len(entities) - len(module["approved_entities"])
        audit["budget_used"] = len(json.dumps(module, ensure_ascii=False))
        require(fits(), "当前场景摘要超过结构上下文预算", 422)
        if persist_selection:
            state.selected_node_ids = audit["selected_node_ids"]
            state.selected_block_ids = audit["selected_block_ids"]
            await nav.persist(session, state)
        if cycle:
            cycle.state = {**cycle.state, **audit, "module_fallback_mode": audit["context_mode"]}
        return {
            "module": module,
            "public_entities": [
                {k: e[k] for k in ("id", "state", "fact_scope")} for e in public_entities
            ],
            "public_state": {"scene_id": shell.state["scene_id"]},
            "structure_navigation": True,
            "structure_incomplete": snapshot.incomplete,
            "module_context_audit": audit,
        }

    async def allowed(self, session, room):
        state = await self.agents.navigation.require_available(session, room.id)
        require(state, "module_structure_missing", 422)
        snapshot, ir = await self.agents.navigation.snapshot(session, state)
        nodes = {n.node_id: n for n in snapshot.nodes}
        current = nodes[state.current_scene_node_id]
        local = scene_nodes(snapshot, current.node_id)
        linked = [n for n in current.linked_node_ids if nodes[n].included]
        ancestors = []
        parent = nodes.get(current.parent_node_id)
        while parent:
            ancestors.append(parent.node_id)
            parent = nodes.get(parent.parent_node_id)
        return state, snapshot, ir, local, linked, ancestors

    async def open_node(self, session, room, run, args):
        state, snapshot, ir, local, linked, ancestors = await self.allowed(session, room)
        require(args.node_id in {*local, *linked, *ancestors}, "node_access_denied", 403)
        node = next(n for n in snapshot.nodes if n.node_id == args.node_id)
        result = {
            "node_id": node.node_id,
            "title": node.title,
            "heading_path": node.heading_path,
            "summary": node.keeper_summary[:160],
            "blocks": [],
        }
        # Ancestors expose metadata only, never their complete subtree or hidden siblings.
        if node.node_id not in ancestors:
            left = 1600
            for block in ir.blocks:
                if block.node_id == node.node_id and left > 0:
                    text = block.text[: min(600, left)]
                    left -= len(text)
                    result["blocks"].append({"block_id": block.block_id, "text": text})
        if run:
            audit = dict(run.context.get("module_context_audit", {}))
            audit["selected_node_ids"] = list(
                dict.fromkeys([*audit.get("selected_node_ids", []), node.node_id])
            )
            run.context = {**run.context, "module_context_audit": audit}
        return result

    async def lookup(self, session, room, query, public=False):
        entities = (
            await self.agents.entities.public(session, room.id)
            if public
            else await self.agents.entities.host(session, room.id)
        )
        matches = [
            e for e in entities if e["id"] == query or normalize(e["title"]) == normalize(query)
        ]
        if public:
            return {"candidates": matches}
        _, snapshot, _, local, linked, _ = await self.allowed(session, room)
        allowed_ids = {
            b.entity_id for b in snapshot.entity_bindings if b.node_id in {*local, *linked}
        }
        nodes = {n.node_id: n for n in snapshot.nodes}
        return {
            "candidates": [
                {
                    **e,
                    "node_paths": [
                        nodes[b.node_id].heading_path
                        for b in snapshot.entity_bindings
                        if b.entity_id == e["id"] and b.node_id in {*local, *linked}
                    ],
                }
                for e in matches
                if e["id"] in allowed_ids or e["state"] != "hidden"
            ]
        }

    async def search(self, session, room, run, args, *, host=False):
        state, snapshot, ir, local, linked, _ = await self.allowed(session, room)
        if args.scope == "global":
            require(host or snapshot.incomplete, "global_module_search_requires_host_approval", 403)
            source = {"source_id": snapshot.source_id, "source_hash": snapshot.source_hash}
            if run:
                from app.persistence.agent_models import ProfileRecord

                profile = await session.get(ProfileRecord, run.profile_id)
                evidence, record = await self.agents.knowledge.search(
                    session,
                    room,
                    run_id=run.id,
                    profile=profile,
                    actor_id=run.actor_member_id,
                    query=args.query,
                    kind="module",
                    top_k=args.top_k,
                )
                record.source_filters = {
                    **record.source_filters,
                    "scope": "global",
                    "reason": "structure_incomplete",
                }
                record.injected_ids = [e["evidence_id"] for e in evidence]
                cycle = await session.get(AgentCycle, run.cycle_id)
                cycle.state = {**cycle.state, "module_fallback_mode": "global_fallback"}
                run.context = {
                    **run.context,
                    "module_context_audit": {
                        **run.context.get("module_context_audit", {}),
                        "context_mode": "global_fallback",
                        "fallback_reason": "structure_incomplete",
                    },
                }
            else:
                evidence = self.agents.knowledge.retriever.search(
                    args.query,
                    refs=[source],
                    run_id="host-structure-search",
                    kind="module",
                    keeper=True,
                    top_k=args.top_k,
                )
            self.agents.rooms.append(
                session,
                room,
                "module.global_search",
                room.host_member_id,
                {
                    "scope": "global",
                    "result_count": len(evidence),
                    "reason": "host_search" if host else "structure_incomplete",
                },
                "host_only",
            )
            return {"context_mode": "global_fallback", "evidence": evidence}
        allowed_ids = local if args.scope == "current_scene" else linked
        query_tokens = set(tokens(args.query))
        ranked = sorted(
            (b for b in ir.blocks if b.node_id in allowed_ids),
            key=lambda b: (-len(query_tokens & set(tokens(b.text))), b.source_order),
        )
        selected = [b for b in ranked if query_tokens & set(tokens(b.text))][: args.top_k]
        if run:
            for node_id in dict.fromkeys(b.node_id for b in selected):
                await self.open_node(session, room, run, NodeArgs(node_id=node_id))
            cycle = await session.get(AgentCycle, run.cycle_id)
            cycle.state = {**cycle.state, "module_fallback_mode": "local_fallback"}
        return {
            "context_mode": "local_fallback",
            "scope": args.scope,
            "blocks": [
                {"node_id": b.node_id, "block_id": b.block_id, "text": b.text[:420]}
                for b in selected
            ],
            "fallback_reason": None if selected else "current_scene_content_missing",
        }

    async def validate_node_claim(self, session, room, run, claim, public_only):
        require(
            claim.visibility == "keeper_only" and not public_only, "原始 node 内容不能直接公开", 403
        )
        state, snapshot, ir, local, linked, ancestors = await self.allowed(session, room)
        cycle = await session.get(AgentCycle, run.cycle_id)
        await self.agents.navigation.check_cycle(session, room, cycle)
        observed = set(run.context.get("module_context_audit", {}).get("selected_node_ids", []))
        require(
            set(claim.node_ids) <= observed & {*local, *linked, *ancestors},
            "node_grounding_not_observed_or_authorized",
            422,
        )
        texts = [
            b["text"]
            for b in run.context.get("module", {}).get("blocks", [])
            if b["node_id"] in claim.node_ids
        ]
        for result in run.tool_results:
            data = result.get("data", {})
            if isinstance(data, dict):
                texts += [
                    b["text"]
                    for b in data.get("blocks", [])
                    if data.get("node_id", b.get("node_id")) in claim.node_ids
                ]
        require(
            any(normalize(claim.statement) in normalize(text) for text in texts),
            "node_claim_exceeds_observed_text",
            422,
        )
        return {**claim.model_dump(), "sources": [], "basis_type": "module_node"}
