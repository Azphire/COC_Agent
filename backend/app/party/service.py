"""Persist every completed step; model calls never run in a database transaction."""

import asyncio
import copy
import hashlib
import json
import secrets
import time
from contextlib import suppress
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.agents.schemas import AgentProfile, ProfileInput
from app.agents.security import scrub
from app.character.schemas import PatchCharacterRequest
from app.character.service import CharacterError
from app.domain.character import CharacterDraft, utc_now
from app.domain.character_details import Background
from app.domain.handouts import CharacterHandout
from app.models.base import ModelError, ModelFormatError
from app.party.generator import generate_card, member_seed
from app.party.persona import ability_issues, enforce_public_identity, unique_name
from app.party.requirements import preparation_requirements, resolved_requirements
from app.party.schemas import Persona
from app.persistence.agent_models import ProfileRecord
from app.persistence.party_models import PartyBatch
from app.persistence.preparation_models import ModuleEntity, ModulePreparation
from app.preparation.handouts import validate_handouts
from app.rooms.service import RoomError, require
from app.rules.engine import recalculate
from app.rules.handouts import approve_module_handout

MAX_PERSONA_CALLS = 4
GENERATION_LEASE_SECONDS = 30


class PartyWriteConflict(RoomError):
    def __init__(self):
        super().__init__("队伍状态已更新，请刷新后继续", 409)


class PartyService:
    def __init__(self, agents, characters):
        self.agents, self.characters = agents, characters
        self.database = agents.rooms.database
        self.locks = {}
        self.create_lock = asyncio.Lock()

    def lock(self, batch_id):
        return self.locks.setdefault(str(batch_id), asyncio.Lock())

    async def get(self, batch_id):
        async with self.database.sessions() as session:
            row = await session.get(PartyBatch, str(batch_id))
            require(row is not None, "队伍草稿不存在", 404)
            return copy.deepcopy(row.document)

    @staticmethod
    def view(document):
        """Normal wizard projection never returns an AI's private HO or derived prose."""
        result = copy.deepcopy(document)
        generations = result["members"] + result.get("history", [])
        result["metrics"] = {
            "model_calls": sum(m["model_calls"] for m in generations),
            "elapsed_ms": sum(m["latency_ms"] for m in generations),
            "rerolls": sum(m["reroll_count"] for m in result["members"]),
        }
        result.pop("_requests", None)
        result.pop("public_introduction", None)
        result.pop("history", None)
        result.pop("_revision", None)
        result.pop("_active_owner", None)
        result.pop("_lease_until", None)
        for member in result["members"]:
            member.pop("persona_options", None)
            member.pop("_model_calls", None)
            member.pop("_persona_rejections", None)
            member.pop("original_name", None)
            for attempt in member.get("attempts", []):
                attempt.pop("raw_error", None)
            card = member["character"]
            handout = card.get("module_handout")
            member["private_profile"] = bool(handout)
            if handout:
                card["name"] = member.get("public_name") or f"调查员 {member['index'] + 1}"
                definition = handout["definition"]
                card["module_handout"] = {
                    "preparation_id": handout["preparation_id"],
                    "handout_id": handout["handout_id"],
                    "title": definition["title"],
                    "attribute_allocations": handout["attribute_allocations"],
                    "adjustments": {
                        key: value for key, value in (definition.get("adjustments") or {}).items()
                        if key not in {"requirements_note", "order_note"}
                    } or None,
                }
                card["background"] = Background().model_dump()
                member["profile"] = None
                if member["error"] and member.get("generation_stage") != "numeric":
                    member["error"] = "人物文字生成失败；数值卡已保存，可重试未完成部分"
        if any(m.get("private_profile") and m.get("error")
               and m.get("generation_stage") != "numeric" for m in result["members"]):
            result["error"] = "人物文字生成失败；数值卡已保存，可重试未完成部分"
        return result

    @staticmethod
    def _live(document):
        return document.get("_active_owner") and document.get("_lease_until", 0) > time.time()

    async def _write(self, session, document):
        """CAS the durable step before side effects, including across service instances."""
        revision = document.get("_revision", 0)
        stored = copy.deepcopy(document)
        stored["_revision"] = revision + 1
        changed = await session.execute(update(PartyBatch).where(
            PartyBatch.id == document["id"],
            func.coalesce(PartyBatch.document["_revision"].as_integer(), 0) == revision,
        ).values(document=stored, updated_at=utc_now()))
        if changed.rowcount != 1:
            raise PartyWriteConflict()

    async def _save(self, document):
        document["updated_at"] = utc_now().isoformat()
        if document.get("_active_owner"):
            document["_lease_until"] = time.time() + GENERATION_LEASE_SECONDS
        async with self.database.sessions.begin() as session:
            await self._write(session, document)
        document["_revision"] = document.get("_revision", 0) + 1

    async def _heartbeat(self, batch_id, owner):
        while True:
            await asyncio.sleep(GENERATION_LEASE_SECONDS / 3)
            async with self.database.sessions.begin() as session:
                result = await session.execute(update(PartyBatch).where(
                    PartyBatch.id == batch_id,
                    PartyBatch.document["_active_owner"].as_string() == owner,
                ).values(document=func.json_set(
                    PartyBatch.document, "$._lease_until", time.time() + GENERATION_LEASE_SECONDS,
                )))
                if result.rowcount != 1:
                    return

    async def _preparation(self, preparation_id):
        if not preparation_id:
            return None, "", []
        async with self.database.sessions() as session:
            prep = await session.get(ModulePreparation, preparation_id)
            require(prep and prep.status == "approved", "请选择已核准的模组准备版本", 422)
            scene = await session.get(
                ModuleEntity, prep.document.get("initial_scene_entity_id", ""),
            )
            require(scene and scene.preparation_id == prep.id and scene.status == "approved",
                    "准备版本缺少已批准公开开场", 422)
            handouts = validate_handouts(prep.document.get("handouts", []), prep.source_hash)
            return prep, scene.document.get("public_summary", ""), handouts

    async def _version(self, document):
        ruleset = self.characters.ruleset(document["ruleset_id"], document["ruleset_version"])
        if document["preparation_id"]:
            async with self.database.sessions() as session:
                prep = await session.get(ModulePreparation, document["preparation_id"])
                require(prep and prep.status == "approved"
                        and prep.version == document["preparation_version"]
                        and prep.source_hash == document["preparation_source_hash"],
                        "准备版本已变化；原队伍已保留，请重新选择已核准版本", 409)
        return ruleset

    @staticmethod
    def _operation(document, request_id, kind, payload=None):
        key = str(request_id)
        signature = hashlib.sha256(json.dumps(
            [kind, payload], sort_keys=True, ensure_ascii=False, default=str,
        ).encode()).hexdigest()
        existing = document["_requests"].get(key)
        if existing:
            require(existing["signature"] == signature, "同一请求 ID 不能用于不同队伍操作", 409)
            return existing, True
        operation = {"kind": kind, "signature": signature, "status": "started"}
        document["_requests"][key] = operation
        return operation, False

    @staticmethod
    def _status(document):
        document["completed"] = sum(m["status"] == "ready" for m in document["members"])
        if document["status"] == "adopted":
            return
        states = {m["status"] for m in document["members"]}
        document["status"] = (
            "ready" if states == {"ready"} else "generating" if "generating" in states
            else "failed" if "failed" in states else "preview"
        )

    @staticmethod
    def _member(document, index, reroll_count, ruleset, handout=None):
        stream = member_seed(document["seed"], index, reroll_count)
        identity = uuid5(UUID(document["id"]), f"member:{index}:{reroll_count}")
        if handout:
            handout = handout.model_copy(deep=True, update={"attribute_allocations": {}})
        partial = []
        error = None
        try:
            card, options = generate_card(
                ruleset, stream, identity, era=document["era"], handout=handout,
                on_card=partial.append,
            )
        except CharacterError as failure:
            card = partial[0] if partial else CharacterDraft(
                id=identity, ruleset_id=ruleset.id, ruleset_version=ruleset.version,
                creation_mode="random", name="待完成规则建卡", era=document["era"],
                module_handout=handout,
            )
            recalculate(card, ruleset)
            options, error = {}, failure.message
        member = {
            "index": index, "reroll_count": reroll_count, "stream_seed": stream,
            "status": "failed" if error else "card_ready",
            "generation_stage": "numeric" if error else "persona",
            "character": card.model_dump(mode="json"),
            "profile": None, "profile_id": str(uuid5(identity, "profile")),
            "persona_options": options, "error": error, "model_calls": 0, "latency_ms": 0,
            "attempts": [],
            "_model_calls": [],
            "_persona_rejections": [],
        }
        enforce_public_identity(member, document)
        return member

    async def create(self, body):
        async with self.create_lock:
            async with self.database.sessions() as session:
                existing = await session.scalar(select(PartyBatch).where(
                    PartyBatch.request_id == str(body.request_id),
                ))
                if existing:
                    require(existing.document["input"] == body.model_dump(mode="json"),
                            "同一请求 ID 不能创建不同队伍", 409)
                    return copy.deepcopy(existing.document)
            ruleset = self.characters.ruleset(body.ruleset_id)
            prep, introduction, handouts = await self._preparation(body.preparation_id)
            async with self.database.sessions() as session:
                requirements = (
                    await resolved_requirements(session, prep) if prep
                    else preparation_requirements({})
                )
            if requirements["era_verified"]:
                require(body.era == requirements["era"], "所选年代不符合已核准模组来源", 422)
            doc = {
                "id": str(uuid4()), "status": "preview", "count": body.count, "completed": 0,
                "seed": body.seed if body.seed is not None else secrets.token_hex(16),
                "generator_version": "party-v1",
                "ruleset_id": ruleset.id, "ruleset_version": ruleset.version,
                "preparation_id": prep.id if prep else None,
                "preparation_version": prep.version if prep else None,
                "preparation_source_hash": prep.source_hash if prep else None,
                "era": body.era, "requirements": requirements,
                "public_introduction": introduction, "input": body.model_dump(mode="json"),
                "members": [], "history": [], "character_ids": [], "profile_ids": [],
                "error": None, "_requests": {}, "_revision": 0,
                "created_at": utc_now().isoformat(),
                "updated_at": utc_now().isoformat(),
            }
            catalog = {item.id: item for item in handouts}
            selected = body.handout_ids or [None] * body.count
            require(all(not key or key in catalog for key in selected),
                    "所选 HO 不在已核准准备版本中", 422)
            for index, handout_id in enumerate(selected):
                require(not handout_id or handout_id in catalog,
                        "所选 HO 不在已核准准备版本中", 422)
                handout = CharacterHandout(
                    preparation_id=prep.id, handout_id=handout_id,
                    definition=catalog[handout_id],
                ) if handout_id else None
                doc["members"].append(self._member(doc, index, 0, ruleset, handout))
            self._status(doc)
            doc["error"] = next((m["error"] for m in doc["members"] if m["error"]), None)
            try:
                async with self.database.sessions.begin() as session:
                    session.add(PartyBatch(
                        id=doc["id"], request_id=str(body.request_id), document=copy.deepcopy(doc),
                    ))
            except IntegrityError:
                async with self.database.sessions() as session:
                    existing = await session.scalar(select(PartyBatch).where(
                        PartyBatch.request_id == str(body.request_id),
                    ))
                    require(existing and existing.document["input"] == body.model_dump(mode="json"),
                            "同一请求 ID 不能创建不同队伍", 409)
                    return copy.deepcopy(existing.document)
            return doc

    def _messages(self, document, member, ruleset):
        character = CharacterDraft.model_validate(member["character"])
        occupation = next(o for o in ruleset.occupations if o.key == character.occupation)
        skill_names = {s.key: s.display_name for s in ruleset.skills}
        context = {
            "public_introduction": document["public_introduction"],
            "own_handout": character.module_handout.definition.text
            if character.module_handout else None,
            "age": character.age, "era": character.era, "occupation": occupation.display_name,
            "attributes": character.effective_attributes,
            "skills": {skill_names.get(k, k): value for k, value in character.skill_values.items()},
            "actual_strengths": {
                skill_names.get(key, key): value
                for key, value in sorted(character.skill_values.items(), key=lambda item: -item[1])
                if value >= 50
            },
            "equipment": [item.name for item in character.equipment],
            "finances": character.finances.model_dump(),
            "personality_options": member["persona_options"],
            "public_name_hint": member.get("public_name"),
            "previous_output_issues": [
                issue["message"] for rejection in member.get("_persona_rejections", [])[-1:]
                for issue in rejection["issues"]
            ],
        }
        return [
            {"role": "system", "content": (
                "为已由规则代码完整建卡的调查员写简短中文人物档案。只能填写 schema 的文字字段。"
                "姓名匹配公开导入时代地域，背景、性格、动机、说话风格及行动倾向须各有具体差异。"
                "若 public_name_hint 非空，姓名必须原样使用这一公开身份；不能从 HO 提取姓名。"
                "严格尊重给定年龄、职业、属性和技能；承认弱项和缺点，不把低技能写成专家。"
                "技能值不超过30时，不能肯定自称擅长、精通、熟练或专家；可写兴趣、"
                "愿意尝试或具体弱项。职业名称不代表其所有相关技能都熟练。"
                "过去经历也必须与实际能力一致：不能给低技能添加大学教授该专业、"
                "专业任教等资历。允许换职业，但背景应围绕当前职业和actual_strengths，"
                "不要无依据编造学术教职。背景只写2至3句，其它项各用一句简短自然的话。"
                "说话风格与行动倾向各不超过500字符；保持原schema全部字段长度上限。"
                "人物是调查员队友，不是 KP 或模组 NPC。只能知道公开导入及本人的 HO，"
                "不得编造模组隐藏线索、其他 HO、结局或已完成的调查。"
                "背景不授予额外物品、法术、技能点或属性。能力以卡上数值为准。"
                "使用给定人格选项形成自然人物，避免千篇一律的冷静观察者。不要输出推理。"
            )},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

    @staticmethod
    def _reject_persona(member, profile, issues):
        member.setdefault("_persona_rejections", []).append({
            "profile": copy.deepcopy(profile), "issues": issues,
            "reviewed_at": utc_now().isoformat(), "checker_version": "low-skill-claims-v1",
        })
        member["status"] = "failed"
        member["error"] = "本次人物文字输出未通过能力检查：" + "；".join(
            issue["message"] for issue in issues
        ) + "。原文字与数值卡已保留，请仅重试未完成文字。"

    def _review_document(self, document, ruleset):
        changed = False
        if document["status"] == "adopted":
            return False
        for member in document["members"]:
            changed = enforce_public_identity(member, document) or changed
            if member["status"] == "ready" and member.get("profile"):
                card = CharacterDraft.model_validate(member["character"])
                issues = ability_issues(member["profile"], card, ruleset)
                if issues:
                    self._reject_persona(member, member["profile"], issues)
                    document["error"] = member["error"]
                    changed = True
        if changed:
            self._status(document)
        return changed

    async def review(self, batch_id):
        """Call before opening a launch write transaction; preserve rejected old outputs."""
        async with self.lock(batch_id):
            doc = await self.get(batch_id)
            if self._live(doc):
                return doc
            ruleset = await self._version(doc)
            if self._review_document(doc, ruleset):
                try:
                    await self._save(doc)
                except PartyWriteConflict:
                    return await self.get(batch_id)
            return doc

    async def next(self, batch_id, request_id):
        async with self.lock(batch_id):
            doc = await self.get(batch_id)
            require(doc["status"] != "adopted", "队伍已采用，不能重新生成角色", 409)
            ruleset = await self._version(doc)
            if self._live(doc):
                # Another worker owns this step; only that worker calls the model.
                await asyncio.sleep(0.5)
                return await self.get(batch_id)
            if self._review_document(doc, ruleset):
                await self._save(doc)
            operation, duplicate = self._operation(doc, request_id, "next")
            if duplicate and operation["status"] != "started":
                return doc
            if duplicate and "member_index" in operation:
                member = doc["members"][operation["member_index"]]
            else:
                member = next((m for m in doc["members"] if m["status"] != "ready"), None)
            if member is None:
                operation["status"] = "complete"
                await self._save(doc)
                return doc
            operation["member_index"] = member["index"]
            require(member["model_calls"] < MAX_PERSONA_CALLS,
                    "此人物文字生成已达 4 次调用上限；可编辑文字或显式重抽此人", 422)
            require(member.get("generation_stage") != "numeric",
                    member["error"] or "规则建卡未完成；请在预览内编辑或重抽此人", 422)
            member["status"], member["error"], doc["error"] = "generating", None, None
            doc["_active_owner"] = str(uuid4())
            self._status(doc)
            try:
                await self._save(doc)
            except PartyWriteConflict:
                return await self.get(batch_id)
            started = time.monotonic()
            attempt = {"request_id": str(request_id), "started_at": utc_now().isoformat()}
            member.setdefault("attempts", []).append(attempt)
            heartbeat = asyncio.create_task(self._heartbeat(doc["id"], doc["_active_owner"]))

            async def on_call():
                member["model_calls"] += 1
                await self._save(doc)

            async def on_result(call):
                # Keep the original selected model/configuration, transmitted
                # request and response for host audits. Wizard projections never
                # return these traces (they may contain this investigator's HO).
                record = scrub(call, [
                    self.agents.settings.host_admin_token.get_secret_value(),
                    self.agents.settings.model_api_key.get_secret_value(),
                ])
                member.setdefault("_model_calls", []).append(record)
                await self._save(doc)

            try:
                response, latency = await self.agents.model.generate(
                    self._messages(doc, member, ruleset), response_schema=Persona,
                    max_attempts=1, on_call=on_call, on_result=on_result, output_limit=1800,
                )
                persona = Persona.model_validate(response.structured)
                safe = scrub(persona.model_dump(), [
                    self.agents.settings.host_admin_token.get_secret_value(),
                    self.agents.settings.model_api_key.get_secret_value(),
                ])
                profile = ProfileInput(role="investigator", **safe)
                card = CharacterDraft.model_validate(member["character"])
                issues = ability_issues(profile.model_dump(), card, ruleset)
                if issues:
                    self._reject_persona(member, profile.model_dump(), issues)
                    raise ValueError("；".join(issue["message"] for issue in issues))
                if card.module_handout:
                    member["original_name"] = profile.name
                    profile.name = member["public_name"]
                else:
                    unique_name(profile, member, doc)
                card.name = profile.name
                card.background = Background(
                    people=profile.background, beliefs=profile.goals, traits=profile.personality,
                )
                recalculate(card, ruleset)
                member.update(
                    status="ready", profile=profile.model_dump(mode="json"),
                    character=card.model_dump(mode="json"),
                )
                attempt["model_latency_ms"] = latency
                operation["status"] = "complete"
            except (ModelError, ValueError) as error:
                message = scrub(str(error), [
                    self.agents.settings.host_admin_token.get_secret_value(),
                    self.agents.settings.model_api_key.get_secret_value(),
                ])
                member["status"] = "failed"
                attempt["raw_error"] = message
                member["error"] = (
                    "本次人物文字输出未通过格式或能力检查；原输出、数值卡及已完成成员已保留，"
                    "可仅重试未完成文字。"
                )
                if isinstance(error, ModelError) and not isinstance(error, ModelFormatError):
                    member["error"] = (
                        f"人物文字生成暂不可用：{message}；数值卡和已完成成员已保存，"
                        "请检查当前模型配置后重试。"
                    )
                doc["error"] = member["error"]
                operation["status"] = "failed"
            except asyncio.CancelledError:
                member["status"] = "failed"
                member["error"] = "生成已中断；数值卡已保留，可继续未完成成员"
                operation["status"] = "failed"
                attempt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                attempt["status"] = "interrupted"
                member["latency_ms"] += attempt["elapsed_ms"]
                doc["_active_owner"], doc["_lease_until"] = None, 0
                self._status(doc)
                await asyncio.shield(self._save(doc))
                raise
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
            attempt["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            attempt["status"] = operation["status"]
            member["latency_ms"] += attempt["elapsed_ms"]
            doc["_active_owner"], doc["_lease_until"] = None, 0
            self._status(doc)
            await self._save(doc)
            return doc

    async def reroll(self, batch_id, body):
        async with self.lock(batch_id):
            doc = await self.get(batch_id)
            require(doc["status"] != "adopted", "队伍已采用，不能重抽替换角色", 409)
            require(not self._live(doc), "该人物仍在生成中；请等待当前步骤完成", 409)
            ruleset = await self._version(doc)
            operation, duplicate = self._operation(
                doc, body.request_id, "reroll", body.member_index,
            )
            if duplicate:
                return doc
            indices = (
                list(range(doc["count"])) if body.member_index is None else [body.member_index]
            )
            require(all(i < doc["count"] for i in indices), "队伍中不存在此成员", 404)
            for index in indices:
                old = doc["members"][index]
                handout = CharacterDraft.model_validate(old["character"]).module_handout
                doc["history"].append(copy.deepcopy(old))
                doc["members"][index] = self._member(
                    doc, index, old["reroll_count"] + 1, ruleset, handout,
                )
            operation["status"] = "complete"
            doc["error"] = None
            self._status(doc)
            await self._save(doc)
            return doc

    async def edit(self, batch_id, index, body):
        async with self.lock(batch_id):
            doc = await self.get(batch_id)
            require(doc["status"] != "adopted", "已采用队伍不能直接修改", 409)
            require(not self._live(doc), "该人物仍在生成中；请等待当前步骤完成", 409)
            require(0 <= index < doc["count"], "队伍中不存在此成员", 404)
            ruleset = await self._version(doc)
            operation, duplicate = self._operation(
                doc, body.request_id, "edit", [index, body.model_dump(mode="json")],
            )
            if duplicate:
                return doc
            member = doc["members"][index]
            card = CharacterDraft.model_validate(member["character"])
            if body.character:
                request = PatchCharacterRequest.model_validate({
                    **body.character, "version": card.version,
                })
                changes = request.model_dump(exclude_unset=True, exclude={"version"})
                protected = {"attributes", "age", "module_handout", "approve_module_handout",
                             "approve_specializations", "approve_occupation_exceptions",
                             "approve_experience", "derived_values"}
                require(not changes.keys() & protected,
                        "随机原始属性、年龄、HO 来源及审批不能在人物编辑中覆盖", 422)
                card = CharacterDraft.model_validate({**card.model_dump(), **changes})
                self.characters.check_known(card, ruleset)
                recalculate(card, ruleset)
            if body.profile:
                require(body.profile.role == "investigator", "随机队伍仅支持调查员", 422)
                member["profile"] = body.profile.model_dump(mode="json")
                card.name = body.profile.name
                card.background = Background(
                    people=body.profile.background, beliefs=body.profile.goals,
                    traits=body.profile.personality,
                )
            if member["profile"]:
                member["profile"]["name"] = card.name
                member["status"], member["error"] = "ready", None
            recalculate(card, ruleset)
            if not any(i.code != "keeper_approval" for i in card.validation.issues):
                member["generation_stage"] = "persona"
            member["character"] = card.model_dump(mode="json")
            if member.get("profile"):
                issues = ability_issues(member["profile"], card, ruleset)
                if issues:
                    self._reject_persona(member, member["profile"], issues)
                    doc["error"] = member["error"]
            enforce_public_identity(member, doc)
            operation["status"] = "complete"
            self._status(doc)
            await self._save(doc)
            return doc

    async def adopt(self, batch_id, request_id, approve_handouts=False):
        async with self.lock(batch_id):
            doc = await self.get(batch_id)
            operation, duplicate = self._operation(doc, request_id, "adopt", approve_handouts)
            if doc["status"] == "adopted":
                if not duplicate:
                    operation["status"] = "complete"
                    await self._save(doc)
                return doc
            ruleset = await self._version(doc)
            require(not self._live(doc), "人物仍在生成中；请等待当前步骤完成", 409)
            if self._review_document(doc, ruleset):
                await self._save(doc)
            require(all(m["status"] == "ready" for m in doc["members"]),
                    "请先完成所有人物文字；已完成成员已保存", 422)
            sheets, profiles = [], []
            for member in doc["members"]:
                card = CharacterDraft.model_validate(member["character"])
                if card.module_handout:
                    require(approve_handouts, "采用前请一并确认 HO 来源与角色调整方案", 422)
                    approve_module_handout(card, ruleset)
                require(card.remaining_points.occupation == 0
                        and card.remaining_points.interest == 0
                        and card.remaining_points.experience == 0,
                        "仍有未分配技能点，请在人物预览内完成分配", 422)
                sheets.append(self.characters.finalize_candidate(card, card.version))
                profiles.append(AgentProfile(id=member["profile_id"], **member["profile"]))
            doc["character_ids"] = [str(c.id) for c in sheets]
            doc["profile_ids"] = [str(p.id) for p in profiles]
            doc["status"] = "adopted"
            operation["status"] = "complete"
            # Final checks and both card/profile inserts commit atomically with adoption.
            try:
                async with self.database.sessions.begin() as session:
                    await self._write(session, doc)
                    for card, profile in zip(sheets, profiles, strict=True):
                        await self.characters.repository.save_in_session(session, card, [
                            ("party_generated", {"batch_id": doc["id"], "seed": doc["seed"]}),
                            ("finalized", {}),
                        ])
                        session.add(ProfileRecord(
                            id=str(profile.id), role="investigator",
                            document=profile.model_dump(mode="json"),
                        ))
                doc["_revision"] = doc.get("_revision", 0) + 1
            except PartyWriteConflict:
                current = await self.get(batch_id)
                if current["status"] == "adopted":
                    self._operation(current, request_id, "adopt", approve_handouts)
                    return current
                raise
            return doc
