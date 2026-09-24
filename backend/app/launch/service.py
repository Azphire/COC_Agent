"""Durable launch steps, reusing the room/card/HO/Agent services."""

import asyncio
import json
import secrets
from functools import wraps
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select

from app.agents.schemas import AgentProfile, BindingInput
from app.auth import digest
from app.domain.character import utc_now
from app.knowledge.schemas import KnowledgeBinding
from app.launch.preflight import agent_start_issues, issue
from app.persistence.agent_models import ProfileRecord
from app.persistence.launch_models import LaunchDefault, LaunchDraft
from app.persistence.preparation_models import ModulePreparation
from app.persistence.room_models import GameRoom, RoomEvent
from app.rooms.schemas import AssignCharacter, AssignHandout, CreateRoom, ReadyRequest
from app.rooms.service import Identity, RoomError, require


def stable_model_configuration(operation):
    """Keep the checked model version stable through each bounded launch step."""

    @wraps(operation)
    async def guarded(self, *args, **kwargs):
        with self.models.operation("开团前置检查与采用"):
            return await operation(self, *args, **kwargs)

    return guarded


class LaunchService:
    def __init__(self, agents, characters, party, model_settings):
        self.agents, self.rooms, self.characters = agents, agents.rooms, characters
        self.party, self.models = party, model_settings
        self.locks = {}
        self.model_check_revision = None
        self.model_check_lock = asyncio.Lock()

    async def check_model(self):
        """One availability check per configuration version; never infer/download/switch."""
        async with self.model_check_lock:
            await self._check_model()

    async def _check_model(self):
        if not self.models.ready() or self.models.verification == "failed":
            return
        if self.model_check_revision == self.models.revision:
            return
        from app.agents.model import FakeModelAdapter

        if isinstance(self.agents.model.adapter, FakeModelAdapter):
            self.model_check_revision = self.models.revision
            return
        if self.models.current.provider == "ollama":
            from app.api.model import catalogue

            result = await catalogue()
            name = self.models.current.model
            expected = name if ":" in name else name + ":latest"
            if result["error"] or expected not in result["models"]:
                self.models.verification = "failed"
                self.models.error = result["error"] or "所选模型未安装，请在向导内选择已有模型"
                return
        elif self.models.verification != "available":
            self.models.error = "请在向导内对当前外部模型完成首次连接试运行"
            self.models.verification = "failed"
            return
        self.model_check_revision = self.models.revision

    def lock(self, draft_id):
        return self.locks.setdefault(str(draft_id), asyncio.Lock())

    async def row(self, session, draft_id):
        row = await session.get(LaunchDraft, str(draft_id))
        require(row, "开团草稿不存在", 404)
        return row

    async def view(self, session, row, issues=None):
        room = await session.get(GameRoom, row.room_id) if row.room_id else None
        return {
            "id": row.id,
            "version": row.version,
            "status": row.status,
            "room_id": row.room_id,
            "room_revision": room.revision if room else None,
            "document": row.document,
            "steps": row.steps,
            "invite_code": row.steps.get("room", {}).get("invite_code"),
            "local_member_id": row.steps.get("local_member", {}).get("id"),
            "issues": issues if issues is not None else row.steps.get("issues", []),
            "can_start": not issues if issues is not None else False,
        }

    async def rules(self, session):
        refs = [
            {"source_id": s.source_id, "source_hash": s.source_hash, "title": s.title}
            for s in self.agents.knowledge.repository.sources()
            if s.kind != "module"
            and s.visibility == "public_rules"
            and s.edition == "coc7"
            and s.chunk_count
            and self.agents.knowledge.repository.available(s.source_id, s.source_hash)
        ]
        saved = await session.get(LaunchDefault, "rules")
        available = {(s["source_id"], s["source_hash"]) for s in refs}
        default = saved.document["rules"] if saved else []
        if default and all((r["source_id"], r["source_hash"]) in available for r in default):
            return refs, default
        # Existing room selections are the user's prior configuration, not a guess.
        from app.persistence.knowledge_models import RoomKnowledgeBinding

        previous = await session.scalars(
            select(RoomKnowledgeBinding)
            .join(
                GameRoom,
                GameRoom.id == RoomKnowledgeBinding.room_id,
            )
            .order_by(GameRoom.updated_at.desc())
        )
        for row in previous:
            chosen = row.document.get("rules", [])
            if chosen and all((r["source_id"], r["source_hash"]) in available for r in chosen):
                return refs, chosen
        if len(refs) == 1:
            return refs, [{k: refs[0][k] for k in ("source_id", "source_hash")}]
        return refs, []

    async def options(self):
        from app.party.requirements import resolved_requirements

        async with self.rooms.database.sessions() as session:
            preparations = []
            rows = await session.scalars(
                select(ModulePreparation)
                .where(
                    ModulePreparation.status == "approved",
                )
                .order_by(ModulePreparation.updated_at.desc())
            )
            for prep in rows:
                entities = await self.agents.preparation.entities(session, prep.id)
                scene = next(
                    (e for e in entities if e.id == prep.document.get("initial_scene_entity_id")),
                    None,
                )
                preparations.append(
                    {
                        "id": prep.id,
                        "title": prep.document.get("display_title", prep.source_id),
                        "version": prep.version,
                        "source_hash": prep.source_hash,
                        "public_introduction": scene.document.get("public_summary", "")
                        if scene and scene.status == "approved"
                        else "",
                        "requirements": await resolved_requirements(session, prep),
                        "handouts": [
                            {
                                "id": h["id"],
                                "title": h["title"],
                                "adjustments": {
                                    k: v
                                    for k, v in (h.get("adjustments") or {}).items()
                                    if k not in {"requirements_note", "order_note"}
                                },
                            }
                            for h in prep.document.get("handouts", [])
                        ],
                    }
                )
            refs, defaults = await self.rules(session)
            drafts = [
                await self.view(session, r)
                for r in await session.scalars(
                    select(LaunchDraft)
                    .where(LaunchDraft.status != "started")
                    .order_by(LaunchDraft.updated_at.desc())
                )
            ]
        cards = await self.characters.repository.list_all()
        return {
            "preparations": preparations,
            "rules": refs,
            "default_rules": defaults,
            "characters": [
                {
                    "id": str(c.id),
                    "name": c.name
                    if not c.module_handout
                    else f"HO 角色 · {c.module_handout.handout_id}",
                    "age": c.age,
                    "occupation": c.occupation,
                    "era": c.era,
                    "status": c.status,
                    "module_handout": {
                        "preparation_id": c.module_handout.preparation_id,
                        "handout_id": c.module_handout.handout_id,
                    }
                    if c.module_handout
                    else None,
                    "remaining_points": c.remaining_points.model_dump(),
                }
                for c in cards
                if c.status == "finalized"
            ],
            "model": {
                **self.models.public(),
                "state": self.models.verification
                or ("unverified" if self.models.ready() else "unconfigured"),
                "error": self.models.error,
            },
            "drafts": drafts,
        }

    async def configure_requirements(self, preparation_id, body):
        from app.party.requirements import preparation_requirements

        raw = body.model_dump(exclude_none=True)
        require(
            body.minimum_players is None
            or body.maximum_players is None
            or body.minimum_players <= body.maximum_players,
            "人数范围不合法",
            422,
        )
        async with self.rooms.transaction() as session:
            prep = await self.agents.preparation.get(session, preparation_id)
            require(prep.status == "approved", "请选择已批准准备版本", 422)
            key = f"preparation:{prep.id}:{prep.version}:{prep.source_hash}"
            row = await session.get(LaunchDefault, key)
            document = {"launch_requirements": raw}
            if row:
                row.document = document
            else:
                session.add(LaunchDefault(key=key, document=document))
            return preparation_requirements(document)

    async def create(self, body):
        document = body.model_dump(mode="json", exclude={"client_request_id"})
        request_hash = digest(json.dumps(document, sort_keys=True, ensure_ascii=False))
        async with self.rooms.transaction() as session:
            previous = await session.scalar(
                select(LaunchDraft).where(LaunchDraft.request_id == str(body.client_request_id))
            )
            if previous:
                require(previous.request_hash == request_hash, "请求 ID 已用于其他开团选择", 409)
                return await self.view(session, previous)
            prep = await self.agents.preparation.get(session, body.preparation_id)
            require(prep.status == "approved", "请选择已批准的准备版本", 422)
            if document["rules"] is None:
                _, document["rules"] = await self.rules(session)
            document.update(preparation_version=prep.version, source_hash=prep.source_hash)
            from app.party.requirements import resolved_requirements

            requirements = await resolved_requirements(session, prep)
            if document.get("ai_count") is None:
                default = max(0, requirements["minimum_players"] - 1
                              - document.get("reserved_humans", 0))
                document["ai_count"] = min(6, max(default, min(
                    2, requirements["maximum_players"] - 1
                    - document.get("reserved_humans", 0),
                )))
            row = LaunchDraft(
                id=str(uuid4()),
                request_id=str(body.client_request_id),
                request_hash=request_hash,
                document=document,
                steps={},
            )
            session.add(row)
            await session.flush()
            return await self.view(session, row)

    async def get(self, draft_id=None):
        async with self.rooms.database.sessions() as session:
            if draft_id:
                return await self.view(session, await self.row(session, draft_id))
            return [
                await self.view(session, r)
                for r in await session.scalars(
                    select(LaunchDraft).order_by(LaunchDraft.updated_at.desc())
                )
            ]

    async def patch(self, draft_id, body):
        async with self.lock(draft_id), self.rooms.transaction() as session:
            row = await self.row(session, draft_id)
            require(row.version == body.version, "草稿已更新，请刷新后继续", 409)
            changes = body.model_dump(mode="json", exclude={"version"}, exclude_unset=True)
            if row.room_id:
                require(
                    not (set(changes) - {"rules", "handout_acknowledged", "acknowledge_unspent"}),
                    "队伍已采用，不能直接重抽替换角色",
                    409,
                )
                room = await self.rooms.room(session, row.room_id)
                require(room.status == "lobby", "游戏已开始，不能修改开团选择", 409)
                if "rules" in changes:
                    prep = await self.agents.entities.binding(session, room.id)
                    await self.agents.knowledge.bind(
                        session,
                        room,
                        KnowledgeBinding(
                            rules=changes["rules"] or [],
                            module={"source_id": prep.source_id, "source_hash": prep.source_hash},
                        ),
                        room.host_member_id,
                    )
            row.document = {**row.document, **changes}
            row.version += 1
            row.updated_at = utc_now()
            row.steps = {**row.steps, "issues": []}
            return await self.view(session, row)

    async def repair(self, draft_id):
        """Restore this draft's accepted identities, never generate replacement people."""
        async with self.lock(draft_id), self.rooms.transaction() as session:
            row = await self.row(session, draft_id)
            require(row.room_id, "请先采用队伍", 422)
            room = await self.rooms.room(session, row.room_id)
            require(room.status == "lobby", "游戏已开始，不能重建开团绑定", 409)
            identity = Identity(room.host_member_id, True)
            members = await self.rooms.members(session, room)
            slots = await self.rooms.slots(session, room)
            ids = row.steps["seats"]["member_ids"]
            cards = [
                row.document["character_id"],
                *row.steps.get("party", {}).get("character_ids", []),
            ]
            profiles = [
                row.steps["keeper"]["profile_id"],
                *row.steps.get("party", {}).get("profile_ids", []),
            ]
            for member_id, card_id in zip(ids, cards, strict=True):
                member = next((m for m in members if m.id == member_id and m.active), None)
                require(member, "原采用成员已离开，请在成员管理中核对；不会创建替身", 422)
                assigned = next((s for s in slots if s.member_id == member_id), None)
                if assigned:
                    require(
                        assigned.source_character_id == card_id,
                        "此席位已改配其他人物，请明确核对当前卡",
                        422,
                    )
                    continue
                slot = next((s for s in slots if s.source_character_id == card_id), None)
                if slot is None:
                    card = await self.characters.get(UUID(card_id))
                    slot = self.rooms.publish_character(session, room, room.host_member_id, card)
                    await session.flush()
                await self.rooms.apply(
                    session,
                    room,
                    identity,
                    "assign",
                    AssignCharacter(slot_id=slot.id, member_id=member_id),
                    None,
                )
                await session.flush()
            bindings = await self.agents.bindings(session, room.id)
            for member_id, profile_id in zip(
                [room.host_member_id, *ids[1:]], profiles, strict=True
            ):
                if any(b.member_id == member_id for b in bindings):
                    continue
                require(
                    await session.get(ProfileRecord, profile_id),
                    "原采用档案已缺失，请恢复档案后重试",
                    422,
                )
                await self.agents.apply(
                    session,
                    room,
                    identity,
                    "agent.binding",
                    BindingInput(member_id=member_id, profile_id=profile_id),
                    None,
                )
                await session.flush()
            row.steps = {**row.steps, "issues": []}
            row.version += 1
            await session.flush()
            return await self.view(session, row)

    async def draft_issues(self, session, row):
        from app.party.requirements import resolved_requirements

        doc, issues = row.document, []
        prep = await self.agents.preparation.get(session, doc["preparation_id"])
        if (
            prep.status != "approved"
            or prep.version != doc["preparation_version"]
            or (prep.source_hash != doc["source_hash"])
        ):
            issues.append(issue("preparation", "准备版本已变化，请重新选择当前批准版本", "module"))
        if not self.agents.knowledge.repository.available(prep.source_id, prep.source_hash):
            issues.append(issue("source", "模组来源不可用，请恢复对应版本索引", "module"))
        if prep.document.get("package_sha256"):
            from app.preparation.packages import validate_package_source

            try:
                structure = await self.agents.structure.view(session, prep.id)
                require(
                    structure["approved"] and structure["preparation_version"] == prep.version,
                    "准备包结构未批准或已变化",
                    422,
                )
                validate_package_source(
                    self.agents.knowledge.repository.source(prep.source_id, prep.source_hash),
                    self.agents.settings.data_dir,
                )
            except (RoomError, ValueError, OSError):
                issues.append(issue("package", "准备包结构或原稿不可用，请核对批准来源", "module"))
        if not self.models.ready() or self.models.verification == "failed":
            issues.append(issue("model", self.models.error or "当前模型未配置或不可用", "model"))
        valid, _ = await self.rules(session)
        available = {(r["source_id"], r["source_hash"]) for r in valid}
        if not doc.get("rules") or any(
            (r["source_id"], r["source_hash"]) not in available for r in doc["rules"]
        ):
            issues.append(issue("rules", "请选择有效的第七版规则来源（只需选择一次）", "rules"))
        cards = []
        if doc.get("character_id"):
            try:
                card = await self.characters.get(UUID(doc["character_id"]))
                if card.status != "finalized":
                    issues.append(issue("character", "自己的角色尚未最终确认", "character"))
                if any(
                    (getattr(card.remaining_points, key) or 0) > 0
                    for key in ("occupation", "interest", "experience")
                ) and not (doc.get("acknowledge_unspent")):
                    issues.append(
                        issue(
                            "unspent",
                            "自己的角色仍有未分配技能点，请编辑或明确确认放弃",
                            "character",
                        )
                    )
                cards.append(card.model_dump(mode="json"))
            except Exception as error:
                from app.character.service import CharacterError

                if not isinstance(error, CharacterError):
                    raise
                issues.append(issue("character", error.message, "character"))
        else:
            issues.append(issue("character", "请选择自己的角色或快速生成", "character"))
        if doc.get("party_batch_id"):
            batch = await self.party.get(doc["party_batch_id"])
            if batch.get("preparation_id") != prep.id or (
                batch.get("preparation_version") != prep.version
            ):
                issues.append(issue("party_version", "队伍与所选准备版本不一致", "team"))
            members = batch.get("members", [])
            if (not members and batch.get("count", 0) != 0) or any(
                m.get("status") not in {"complete", "completed", "ready", "adopted"}
                for m in members
            ):
                issues.append(issue("party", "队友尚未全部生成，请继续生成未完成成员", "team"))
            cards.extend(m["character"] for m in members if m.get("character"))
        requirements = await resolved_requirements(session, prep)
        if not row.room_id:
            names = [card.get("name", "").strip() for card in cards]
            if any(a and b and (a in b or b in a)
                   for index, a in enumerate(names) for b in names[index + 1:]):
                issues.append(issue(
                    "name_conflict", "队伍公开姓名相同或互相包含，"
                    "请明确编辑新人物姓名后采用", "team",
                ))
        count = len(cards) + (doc.get("reserved_humans") or 0)
        if row.room_id:
            room = await self.rooms.room(session, row.room_id)
            count = len(
                [
                    m
                    for m in await self.rooms.members(session, room)
                    if m.active and m.role == "player"
                ]
            )
        if requirements.get("players_verified") and not (
            requirements["minimum_players"] <= count <= requirements["maximum_players"]
        ):
            issues.append(issue("count", "调查员人数不符合模组已核准人数要求", "team"))
        handout_ids = []
        for card in cards:
            if requirements.get("era_verified") and card.get("era") != requirements["era"]:
                issues.append(issue("era", "角色年代不符合模组已核准要求", "character"))
            handout = card.get("module_handout")
            if handout:
                handout_ids.append(handout["handout_id"])
                if handout["preparation_id"] != prep.id:
                    issues.append(issue("handout_version", "角色 HO 属于其他准备版本", "team"))
        if len(handout_ids) != len(set(handout_ids)):
            issues.append(issue("handout_duplicate", "不同调查员不能采用同一私人 HO", "team"))
        if handout_ids and not doc.get("handout_acknowledged"):
            issues.append(issue("handout", "请在采用队伍时确认所选 HO 及对应人物规则", "team"))
        return issues

    @stable_model_configuration
    async def preflight(self, draft_id):
        await self.check_model()
        await self._review_party(draft_id)
        async with self.lock(draft_id), self.rooms.transaction() as session:
            row = await self.row(session, draft_id)
            issues = await self.draft_issues(session, row)
            if row.room_id:
                room = await self.rooms.room(session, row.room_id)
                issues += await agent_start_issues(self.agents, session, room, check_ready=False)
                for member in await self.rooms.members(session, room):
                    if (
                        member.active
                        and member.role == "player"
                        and (member.access_type == "remote" and not member.ready)
                    ):
                        issues.append(
                            issue("ready", f"{member.display_name} 需要本人确认准备", "invite")
                        )
            row.steps = {**row.steps, "issues": issues}
            return await self.view(session, row, issues)

    async def _review_party(self, draft_id):
        # May persist a rejected persona; run outside the launch write transaction.
        async with self.rooms.database.sessions() as session:
            row = await self.row(session, draft_id)
            batch_id = row.document.get("party_batch_id")
        if batch_id:
            await self.party.review(batch_id)

    async def _adopt(self, draft_id):
        async with self.rooms.database.sessions() as session:
            row = await self.row(session, draft_id)
            doc = dict(row.document)
        if not doc.get("party_batch_id"):
            return {"character_ids": [], "profile_ids": []}
        # Party adoption has its own idempotent transaction. No model call is made here.
        result = await self.party.adopt(
            doc["party_batch_id"],
            str(uuid5(NAMESPACE_URL, f"launch:{draft_id}:adopt")),
            approve_handouts=doc.get("handout_acknowledged", False),
        )
        result = {key: result[key] for key in ("character_ids", "profile_ids")}
        async with self.rooms.transaction() as session:
            row = await self.row(session, draft_id)
            row.steps = {**row.steps, "party": result}
        return result

    @stable_model_configuration
    async def assemble(self, draft_id, expected_version=None):
        await self.check_model()
        await self._review_party(draft_id)
        async with self.lock(draft_id):
            async with self.rooms.transaction() as session:
                row = await self.row(session, draft_id)
                if row.room_id:
                    return await self.view(session, row)
                require(
                    expected_version is None or row.version == expected_version,
                    "草稿已更新，请刷新后继续",
                    409,
                )
                issues = await self.draft_issues(session, row)
                if issues:
                    row.steps = {**row.steps, "issues": issues}
                    return await self.view(session, row, issues)
                adopted_version = row.version
            adopted = await self._adopt(draft_id)
            async with self.rooms.transaction() as session:
                row = await self.row(session, draft_id)
                if row.room_id:
                    return await self.view(session, row)
                require(row.version == adopted_version, "采用期间草稿已变化，请刷新后继续", 409)
                issues = await self.draft_issues(session, row)
                if issues:
                    row.steps = {**row.steps, "issues": issues}
                    return await self.view(session, row, issues)
                # Room, seats, frozen snapshots, binding, and their receipts commit together.
                # A crash rolls this bounded transaction back; an earlier adoption survives.
                doc = row.document
                invite = secrets.token_urlsafe(24)
                room = await self.rooms.create_in_session(
                    session, CreateRoom(name=doc["name"]), invite
                )
                identity = Identity(room.host_member_id, True)
                await self.agents.entities.bind(session, room, doc["preparation_id"])
                prep = await self.agents.entities.binding(session, room.id)
                await self.agents.knowledge.bind(
                    session,
                    room,
                    KnowledgeBinding(
                        rules=doc["rules"],
                        module={"source_id": prep.source_id, "source_hash": prep.source_hash},
                    ),
                    identity.member_id,
                )
                saved = await session.get(LaunchDefault, "rules")
                if saved:
                    saved.document = {"rules": doc["rules"]}
                else:
                    session.add(LaunchDefault(key="rules", document={"rules": doc["rules"]}))
                character_ids = [doc["character_id"], *adopted["character_ids"]]
                member_ids = []
                for index, character_id in enumerate(character_ids):
                    card = await self.characters.get(UUID(character_id))
                    require(card.status == "finalized", "采用角色必须通过最终确认", 422)
                    member = await self.rooms.new_member(
                        session,
                        room,
                        card.name if index == 0 else f"{card.name}（队友{index}）",
                        controller="human" if index == 0 else "agent",
                    )
                    self.rooms.append(
                        session,
                        room,
                        "member.joined",
                        identity.member_id,
                        {
                            "member_id": member.id,
                            "display_name": member.display_name,
                            "controller_type": member.controller_type,
                        },
                    )
                    slot = self.rooms.publish_character(session, room, identity.member_id, card)
                    await session.flush()
                    await self.rooms.apply(
                        session,
                        room,
                        identity,
                        "assign",
                        AssignCharacter(slot_id=slot.id, member_id=member.id),
                        None,
                    )
                    if card.module_handout:
                        await self.rooms.apply(
                            session,
                            room,
                            identity,
                            "handout.assign",
                            AssignHandout(
                                handout_id=card.module_handout.handout_id,
                                slot_id=slot.id,
                                member_id=member.id,
                                client_request_id=uuid5(NAMESPACE_URL, f"{draft_id}:ho:{index}"),
                            ),
                            None,
                        )
                    if index:
                        await self.agents.apply(
                            session,
                            room,
                            identity,
                            "agent.binding",
                            BindingInput(
                                member_id=member.id, profile_id=adopted["profile_ids"][index - 1]
                            ),
                            None,
                        )
                    member_ids.append(member.id)
                profile = AgentProfile(
                    id=uuid5(NAMESPACE_URL, f"launch:{draft_id}:keeper"),
                    role="keeper",
                    name="守秘人",
                    background="依据已批准模组主持调查。",
                    personality="公正、克制，尊重调查员选择。",
                    goals="依据规则、公开事实与实际检定推进调查。",
                    speaking_style="清楚自然，只叙述玩家可知的内容。",
                    action_tendency="先核对来源与实际结果；需要检定时等待服务端结果。",
                )
                session.add(
                    ProfileRecord(
                        id=str(profile.id), role="keeper", document=profile.model_dump(mode="json")
                    )
                )
                await session.flush()
                await self.agents.apply(
                    session,
                    room,
                    identity,
                    "agent.binding",
                    BindingInput(member_id=room.host_member_id, profile_id=profile.id),
                    None,
                )
                row.room_id, row.status = room.id, "assembled"
                row.steps = {
                    **row.steps,
                    "issues": [],
                    "room": {"id": room.id, "invite_code": invite},
                    "local_member": {"id": member_ids[0]},
                    "seats": {"member_ids": member_ids},
                    "binding": {
                        "preparation_id": prep.preparation_id,
                        "preparation_version": prep.preparation_version,
                    },
                    "keeper": {"profile_id": str(profile.id)},
                }
                row.version += 1
                row.updated_at = utc_now()
                await session.flush()
                return await self.view(session, row, [])

    @stable_model_configuration
    async def start(self, draft_id, body):
        result = await self.assemble(draft_id, body.expected_version)
        if result["issues"] and not result["room_id"]:
            return result
        room_id = result["room_id"]
        async with self.lock(draft_id), self.rooms.lock(room_id):
            async with self.rooms.transaction() as session:
                row = await self.row(session, draft_id)
                room = await self.rooms.room(session, room_id)
                if row.status == "started":
                    return await self.view(session, row, [])
                require(
                    body.expected_room_revision is None
                    or room.revision == body.expected_room_revision,
                    "房间已变化，请重新检查准备状态",
                    409,
                )
                before = room.revision
                identity = Identity(room.host_member_id, True)
                issues = await self.draft_issues(session, row)
                issues += await agent_start_issues(self.agents, session, room, check_ready=False)
                for member in await self.rooms.members(session, room):
                    if (
                        member.active
                        and member.role == "player"
                        and (member.access_type == "remote" and not member.ready)
                    ):
                        issues.append(
                            issue("ready", f"{member.display_name} 需要本人确认准备", "invite")
                        )
                if issues:
                    row.steps = {**row.steps, "issues": issues}
                    return await self.view(session, row, issues)
                # Managed players become ready in the same final start transaction.
                for member in await self.rooms.members(session, room):
                    if (
                        member.active
                        and member.role == "player"
                        and (member.access_type == "host_managed")
                    ):
                        await self.rooms.apply(
                            session,
                            room,
                            identity,
                            "ready",
                            ReadyRequest(member_id=member.id, ready=True),
                            None,
                        )
                issues += await agent_start_issues(self.agents, session, room)
                if issues:
                    row.steps = {**row.steps, "issues": issues}
                    return await self.view(session, row, issues)
                await self.rooms.apply(session, room, identity, "start", None, None)
                module = await self.agents.module(session, room.id)
                opening = module.document.get("public_introduction", "")
                require(opening.strip(), "批准准备版本缺少公开导入", 422)
                event = self.rooms.append(
                    session,
                    room,
                    "keeper.narration",
                    room.host_member_id,
                    {
                        "text": opening,
                        "opening": True,
                        "preparation_id": row.document["preparation_id"],
                        "preparation_version": row.document["preparation_version"],
                    },
                    request_id=str(uuid5(NAMESPACE_URL, f"launch:{draft_id}:opening")),
                )
                row.status = "started"
                row.version += 1
                row.updated_at = utc_now()
                row.steps = {
                    **row.steps,
                    "issues": [],
                    "start": {"status": "complete"},
                    "opening": {"event_seq": event.seq},
                }
                await session.flush()
                events = list(
                    await session.scalars(
                        select(RoomEvent)
                        .where(RoomEvent.room_id == room.id, RoomEvent.seq > before)
                        .order_by(RoomEvent.seq)
                    )
                )
                response = await self.view(session, row, [])
            await self.rooms.broadcast(room_id, events)
            return response
