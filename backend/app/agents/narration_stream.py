"""Ephemeral, sentence-gated public prose; never a persisted draft or game action."""

import re
import time
from copy import deepcopy
from types import SimpleNamespace

from app.agents.adjudication_schemas import KeeperNarration
from app.agents.generation_contracts import narration_body_field
from app.agents.modules import Module
from app.persistence.agent_models import AgentCycle, AgentRun
from app.preparation.inventory import inventory_context
from app.rooms.combat_service import load_state
from app.rooms.service import RoomError, require


def compact(text):
    return re.sub(r"[\W_]", "", text).lower()


def restored_recall_private_text(context, text, fact_ids=()):
    """Exempt only the exact server rendering of this turn's public evidence.

    All ordinary HO, identifier and result checks still apply to the actual
    output. This narrow projection is only for the legacy module-secret gate,
    which otherwise treats every previous scene description as undisclosed.
    """
    from app.memory.recall import public_historical_quotes

    # The source was selected from this reader's active public branch. Strip
    # only complete verbatim quotes from the legacy scene-secret comparison;
    # HO, result, attribution and publication checks still see the full output.
    original = text
    if re.search(r"之前|此前|当时|早先|原文|原话|曾经|记录", text):
        for quote in public_historical_quotes(context):
            if len(quote) >= 4:
                text = text.replace(quote, "")
    if not context.get("readonly_recall") or not context.get("fact_evidence"):
        return text
    from app.memory.facts import render_facts

    known = {record["id"]: record for record in context["fact_evidence"]}
    ids = list(dict.fromkeys([key for key in fact_ids if key in known] + list(known)))
    restored = render_facts([known[key] for key in ids])
    return "" if original.strip() == restored.strip() else text


def internal_identifiers(value):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "id" or key.endswith(("_id", "_reference")):
                if isinstance(item, str) and len(item) >= 3:
                    result.add(item)
            elif key.endswith("_ids") and isinstance(item, list):
                result.update(v for v in item if isinstance(v, str) and len(v) >= 3)
            result.update(internal_identifiers(item))
    elif isinstance(value, list):
        for item in value:
            result.update(internal_identifiers(item))
    return result


def private_fragments(values, public_text):
    """Do not let punctuation or chunk boundaries hide literal private material."""
    public = compact(public_text)
    fragments = set()
    for value in values:
        for phrase in re.split(r"[，。；：、\n]", value):
            phrase = compact(phrase)
            if len(phrase) < 2:
                continue
            if len(phrase) < 6:
                if phrase not in public:
                    fragments.add(phrase)
                continue
            for i in range(len(phrase) - 5):
                fragment = phrase[i:i + 6]
                if fragment not in public:
                    fragments.add(fragment)
    return fragments


class NarrationStream:
    def __init__(self, runtime, state, run_id, contract, snapshot):
        from app.agents.stream_json import IncrementalJSONObjectArray, IncrementalJSONObjectString

        self.runtime, self.hub = runtime, runtime.rooms.hub
        self.state, self.run_id, self.snapshot = state, run_id, snapshot
        parts_field = contract.model_fields.get("answer_parts")
        self.parts_mode = bool(parts_field) and not (
            parts_field.json_schema_extra or {}
        ).get("x-server-bound")
        self.field = "answer_parts" if self.parts_mode else narration_body_field(contract)
        self.parser_class = IncrementalJSONObjectArray if self.parts_mode else (
            IncrementalJSONObjectString
        )
        self.parser = None
        self.stream_id, self.attempt = None, 0
        self.accepted = ""
        self.first_display_at = None
        self.buffered_reason = None
        self.valid = True
        context = snapshot["run"].context
        self.part_order = tuple(
            row["id"] for row in context.get("response_brief", {}).get("answer_requirements", [])
        ) if self.parts_mode else ()
        self.parts_context = None
        self.parts_received = {}
        self.parts_seen = set()
        self.parts_published = []
        # These branches depend on server restoration, later source fields or
        # another speaker's complete answer. Keep their existing publication.
        self.eligible = bool(self.field) and not (
            context.get("readonly_recall")
            or context.get("intent_type") in {"recall", "out_of_character"}
            or context.get("response_brief", {}).get("responder", {}).get("kind")
            in {"npc", "teammate"}
            or context.get("RULE_EVIDENCE")
        )
        self.initial_eligible = self.eligible

    @classmethod
    async def create(cls, runtime, state, run_id, contract):
        async with runtime.rooms.lock(state["room_id"]):
            async with runtime.rooms.database.sessions() as session:
                room = await runtime.rooms.room(session, state["room_id"])
                cycle = await session.get(AgentCycle, state["cycle_id"])
                run = await session.get(AgentRun, run_id)
                rows = await runtime.service.entities.rows(session, room.id)
                public = await runtime.service.entities.public(session, room.id)
                module = await runtime.service.module(session, room.id)
                nav = await runtime.service.navigation.state(session, room.id)
                snapshot = {
                    "room": SimpleNamespace(id=room.id),
                    "cycle": SimpleNamespace(id=cycle.id, state=deepcopy(cycle.state)),
                    "run": SimpleNamespace(id=run.id, context=deepcopy(run.context)),
                    "public": {e["id"]: deepcopy(e) for e in public},
                    "rows": [SimpleNamespace(
                        snapshot=deepcopy(r.snapshot), state=r.state,
                        source_entity_id=r.source_entity_id, entity_type=r.entity_type,
                    ) for r in rows],
                    "module": SimpleNamespace(
                        document=deepcopy(module.document), state=deepcopy(module.state)
                    ),
                    "nav": SimpleNamespace(current_scene_node_id=nav.current_scene_node_id)
                    if nav else None,
                    "runtime": load_state(room).module_runtime.model_copy(deep=True),
                    "inventory": await inventory_context(runtime.service, session, room),
                    "results": deepcopy(await runtime.public_results(session, room, cycle)),
                }
                context = snapshot["run"].context
                scene = context.get("module", {}).get("scene", {})
                sources = [scene.get("public_description", "")]
                sources += [e.get("title", "") + "\n" + e.get("public_summary", "") for e in public
                            if e.get("fact_scope", "current_scene") == "current_scene"]
                sources += [f.get("text", "") for f in
                            context.get("response_brief", {}).get("allowed_facts", [])]
                sources += [f.get("effect", "") for f in snapshot["results"].get(
                    "current_result_facts", [])]
                from app.memory.recall import public_historical_quotes

                sources += public_historical_quotes(context)
                if context.get("readonly_recall") and context.get("fact_evidence"):
                    from app.memory.facts import render_facts

                    # Narrator evidence was selected from public events for
                    # this question. Do not authorize other historical scenes.
                    sources.append(render_facts(context["fact_evidence"]))
                snapshot["sources"] = "\n".join(sources)
                private = []
                for row in rows:
                    private.append(row.snapshot.get("keeper_summary", ""))
                    if row.state == "hidden":
                        private += [row.snapshot.get("public_summary", ""),
                                    row.snapshot.get("title", ""),
                                    *row.snapshot.get("aliases", [])]
                spec = Module.model_validate(module.document)
                private += [spec.keeper_brief, *[n.keeper_notes for n in spec.npcs],
                            *[s.keeper_notes for s in spec.scenes]]
                private += [c.content for c in spec.clues if c.visibility == "keeper_only"
                            or c.id not in module.state["revealed_clues"]]
                private += [s.public_description for s in spec.scenes
                            if s.id != module.state["scene_id"]]
                handouts = [h.get("text", "") for h in room.session_state.get(
                    "handout_assignments", [])]
                catalog = room.session_state.get("handout_catalog") or {}
                handouts += [h.get("text", "") for h in catalog.get("handouts", [])]
                private += handouts
                snapshot["handout_private"] = private_fragments(handouts, snapshot["sources"])
                snapshot["private"] = private_fragments(private, snapshot["sources"])
                snapshot["internal_ids"] = (
                    {r.source_entity_id for r in rows if len(r.source_entity_id) >= 6}
                    | {state["cycle_id"], run_id} | internal_identifiers(context)
                    | internal_identifiers(snapshot["results"])
                )
        return cls(runtime, state, run_id, contract, snapshot)

    def check_private(self, text, *, prefix=False):
        normalized = compact(text)
        require(not any(p in normalized for p in self.snapshot["private"]),
                "句段包含私密内容", 403)
        # A secret split by a sentence boundary remains buffered, including its
        # first fragment: no later retraction is used as a privacy mechanism.
        if prefix:
            public = compact(self.snapshot["sources"])
            last_sentence = re.split(r"[。！？!?；;\n]", text.rstrip("。！？!?；;\n”’\""))[-1]
            require(not any(normalized.endswith(p[:n]) for p in self.snapshot["private"]
                            for n in range(1 if len(p) < 6 else 2, len(p))
                            if p[:n] not in public
                            and (n > 1 or len(compact(last_sentence)) == 1)),
                    "句段可能是私密内容的前缀", 403)
        require(not re.search(
            r"<\s*/?\s*think|thinking|reasoning|chain.of.thought|"
            r"[{}]|\bHO\s*\d|系统提示|隐藏身份|秘密资料|内部规划|"
            r"(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
            r"(?![0-9a-f])", text, re.I
        ), "句段包含内部内容", 403)
        require(not any(eid in text for eid in self.snapshot["internal_ids"]),
                "句段包含内部标识", 403)

    async def validate(self, text, *, output=None):
        self.check_private(text, prefix=True)
        if self.field == "observed_detail":
            require(not re.match(r"(?:你|我)(?:们)?(?:正|试图|尝试|仔细|蹲|开始)", text),
                    "观察只有动作，没有内容", 422)
        sources = compact(self.snapshot["sources"])
        # A literal or core factual assertion needs its already-public source,
        # not a citation that might appear later in the JSON object.
        if not self.parts_mode:
            for clause in re.split(r"[。！？；\n]", text):
                if re.search(r"写着|写道|写有|内容是|密码|暗号|真相|凶手|身份|意味着|证明|"
                             r"因为|所以|导致|尸体|死亡原因|仪式|祭祀|神祇|通往|隐藏|藏着", clause):
                    require(compact(clause) in sources, "关键事实仍需完整来源校验", 422)
        # These references are server-bound during restore_output, not chosen
        # by a later model field. Bind the identical last-receipt references now
        # so a mixed success/failure turn cannot pass an ambiguous prefix and
        # only contradict the selected check at final validation.
        check_reference = transition_reference = None
        for event in self.snapshot.get("results", {}).get("events", []):
            if event["type"] == "check.resolved":
                check_reference = event["payload"].get("id", event["payload"].get("check_id"))
            elif event["type"] == "scene.updated":
                transition_reference = str(event["seq"])
        output = output or KeeperNarration(public_narration=text)
        output.check_result_reference = check_reference
        output.transition_result_reference = transition_reference
        await self.runtime.validate_narration_output(
            None, self.snapshot["room"], self.snapshot["cycle"], self.snapshot["run"],
            output, prefix_snapshot=self.snapshot,
        )

    async def receive_parts(self, additions):
        """Release only complete, bound parts in the frozen public order."""
        from app.agents.answer_parts import project_answer_parts
        from app.models.base import ModelFormatError

        try:
            # Inspect the entire decoded batch before publishing any of it. A
            # repeated ID is an invalid contract, including a repair changing
            # a server-retained part.
            seen = set(self.parts_seen)
            for part in additions:
                rid = part.get("requirement_id")
                require(rid in self.part_order and rid not in seen,
                        "应答片段需求缺失、重复或不属于本轮", 422)
                seen.add(rid)
            for part in additions:
                output = project_answer_parts([part], self.parts_context, partial=True)
                self.check_private(output.public_narration, prefix=True)
                self.parts_received[part["requirement_id"]] = part
            self.parts_seen = seen
            while len(self.parts_published) < len(self.part_order):
                rid = self.part_order[len(self.parts_published)]
                if rid not in self.parts_received:
                    break
                parts = [*self.parts_published, self.parts_received[rid]]
                output = project_answer_parts(parts, self.parts_context, partial=True)
                candidate = output.public_narration
                require(len(candidate) <= 2000, "公开正文超过长度上限", 422)
                require(candidate.startswith(self.accepted), "片段不得改写已发布顺序", 422)
                await self.validate(candidate, output=output)
                if not await self.hub.stream_delta(
                    self.state["room_id"], self.state["cycle_id"], self.stream_id,
                    candidate[len(self.accepted):], attempt=self.attempt,
                ):
                    return
                self.parts_published = parts
                self.accepted = candidate
                self.first_display_at = self.first_display_at or time.time()
        except (RoomError, ModelFormatError, ValueError, TypeError):
            # A later part cannot make an unbound assertion safe. Keep genuine
            # verified output, but never send the failing part before interrupt.
            self.eligible = False
            self.buffered_reason = "invalid_answer_part"

    async def __call__(self, event):
        if not self.valid:
            return
        kind = event["type"]
        if kind == "start":
            self.attempt = event["attempt"]
            self.accepted = ""
            self.eligible = self.initial_eligible
            self.first_display_at = None
            self.buffered_reason = None
            self.parser = self.parser_class(self.field) if self.field else None
            self.parts_received, self.parts_seen, self.parts_published = {}, set(), []
            self.stream_id = await self.hub.stream_start(
                self.state["room_id"], self.state["cycle_id"], attempt=self.attempt,
            )
            if self.parts_mode:
                self.parts_context = deepcopy(self.snapshot["run"].context)
                retained = self.parts_context.pop("_answer_parts_retained", [])
                if retained and self.eligible:
                    await self.receive_parts(retained)
            return
        if kind == "error" and self.stream_id:
            await self.hub.stream_interrupt(
                self.state["room_id"], cycle_id=self.state["cycle_id"],
                stream_id=self.stream_id, attempt=self.attempt,
                reason="retrying" if event.get("retrying") else "model_failed",
            )
            return
        if kind != "delta" or not self.eligible or event["attempt"] != self.attempt:
            return
        additions = self.parser.feed(event["text"])
        if self.parser.invalid:
            self.eligible = False
            self.buffered_reason = "invalid_json"
            return
        if self.parts_mode:
            if additions:
                await self.receive_parts(additions)
            return
        value = self.parser.value
        if len(value) > 2000:
            self.eligible = False
            self.buffered_reason = "body_limit"
            return
        # Full sentences only; unterminated tails and quoted fragments stay
        # buffered. Re-check accepted prefix plus all new complete sentences.
        ends = list(re.finditer(r"[。！？!?；;\n]+[”’\"]*", value))
        if not ends:
            return
        for end in ends:
            candidate = value[:end.end()]
            if len(candidate) <= len(self.accepted):
                continue
            try:
                await self.validate(candidate)
            except RoomError:
                self.buffered_reason = "awaiting_full_validation"
                return
            if await self.hub.stream_delta(
                self.state["room_id"], self.state["cycle_id"], self.stream_id,
                candidate[len(self.accepted):], attempt=self.attempt,
            ):
                self.accepted = candidate
                self.first_display_at = self.first_display_at or time.time()

    def metadata(self):
        return {"stream_id": self.stream_id, "stream_attempt": self.attempt}
