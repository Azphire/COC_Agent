from uuid import UUID, uuid4

from app.character.repository import CharacterRepository
from app.character.schemas import CreateCharacterRequest, PatchCharacterRequest, PointBuyRequest
from app.dice.service import DiceService
from app.domain.character import (
    Character,
    CharacterDraft,
    CharacterExport,
    CharacteristicValue,
    CharacterSheet,
    ExportRuleset,
    ValidationIssue,
    utc_now,
)
from app.rules.age import age_band, expected_rolls
from app.rules.engine import recalculate
from app.rules.schemas import RuleSet


class CharacterError(Exception):
    def __init__(self, status: int, message: str, issues: list[ValidationIssue] | None = None):
        self.status = status
        self.message = message
        self.issues = issues or []


class CharacterService:
    def __init__(
        self,
        repository: CharacterRepository,
        rulesets: dict[str, RuleSet],
        dice: DiceService | None = None,
    ):
        self.repository = repository
        self.rulesets = rulesets
        self.dice = dice if dice is not None else DiceService()

    def ruleset(
        self, ruleset_id: str, version: str | None = None, require_enabled: bool = True
    ) -> RuleSet:
        ruleset = self.rulesets.get(ruleset_id)
        if ruleset is None:
            raise CharacterError(422, "规则集不存在")
        if require_enabled and not ruleset.enabled:
            raise CharacterError(422, f"规则集未启用：{ruleset.notice}")
        if version is not None and ruleset.version != version:
            from app.rules.loader import archived_ruleset

            ruleset = archived_ruleset(ruleset_id, version)
            if ruleset is None:
                raise CharacterError(422, "规则集版本不匹配，不能使用当前配置修改或导入此角色")
        return ruleset

    async def get(self, character_id: UUID) -> Character:
        character = await self.repository.get(character_id)
        if character is None:
            raise CharacterError(404, "角色不存在")
        return character

    @staticmethod
    def validate_handout_source(character: Character) -> None:
        """Imports cannot turn a claimed source/hash or old approval into authority."""
        if character.module_handout is None:
            return
        from app.preparation.handouts import validate_handouts

        definition = character.module_handout.definition
        try:
            validate_handouts([definition.model_dump(mode="json")], definition.source_hash)
        except ValueError as error:
            raise CharacterError(422, str(error)) from error

    async def resolve_handout(self, selection):
        from app.domain.handouts import CharacterHandout
        from app.persistence.preparation_models import ModulePreparation
        from app.preparation.handouts import validate_handouts

        async with self.repository.database.sessions() as session:
            prep = await session.get(ModulePreparation, selection.preparation_id)
            if prep is None or prep.status != "approved":
                raise CharacterError(422, "HO 必须来自本机已核准的准备包")
            try:
                handouts = validate_handouts(prep.document.get("handouts", []), prep.source_hash)
            except ValueError as error:
                raise CharacterError(422, str(error)) from error
            definition = next((item for item in handouts if item.id == selection.handout_id), None)
            if definition is None:
                raise CharacterError(422, "准备包中不存在该已核准 HO")
            return CharacterHandout(**selection.model_dump(), definition=definition)

    @staticmethod
    def check_known(character: Character, ruleset: RuleSet) -> None:
        issues = []

        def unknown(field: str):
            issues.append(
                ValidationIssue(field=field, code="unknown", message="规则中不存在此选项")
            )

        if character.occupation is not None:
            if character.occupation not in {item.key for item in ruleset.occupations}:
                unknown("occupation")
        for key in character.attributes.keys() - {item.key for item in ruleset.attributes}:
            unknown(f"attributes.{key}")
        from app.rules.specializations import character_skills

        skills = set(character_skills(character, ruleset))
        for field in ("occupation_skills", "interest_skills", "experience_skills"):
            for key in getattr(character, field).keys() - skills:
                unknown(f"{field}.{key}")
        for key in set(character.selected_occupation_skills) - skills:
            unknown("selected_occupation_skills")
        if issues:
            raise CharacterError(422, "包含规则中不存在的选项", issues)

    async def create_random(self, request: CreateCharacterRequest) -> CharacterDraft:
        ruleset = self.ruleset(request.ruleset_id)
        character = CharacterDraft(
            **request.model_dump(exclude={"derived_values"}),
            ruleset_version=ruleset.version,
            creation_mode="random",
        )
        self.prepare_rolls(character, ruleset)
        character.attributes = {
            record.attribute: CharacteristicValue(value=record.total * record.multiplier)
            for record in character.roll_records
            if record.purpose == "attribute"
        }
        recalculate(character, ruleset)
        await self.repository.save(
            character,
            [
                ("created", {"mode": "random"}),
                (
                    "attributes_rolled",
                    {"roll_ids": [str(record.id) for record in character.roll_records]},
                ),
            ],
        )
        return character

    async def create_point_buy(self, request: PointBuyRequest) -> CharacterDraft:
        ruleset = self.ruleset(request.ruleset_id)
        attributes = (
            request.attributes
            if request.attributes is not None
            else {
                item.key: CharacteristicValue(
                    value=(
                        item.point_buy_minimum
                        if item.point_buy_minimum is not None
                        else item.minimum
                    )
                )
                for item in ruleset.attributes
            }
        )
        character = CharacterDraft(
            **request.model_dump(exclude={"attributes", "derived_values"}),
            ruleset_version=ruleset.version,
            creation_mode="point_buy",
            attributes=attributes,
        )
        self.check_known(character, ruleset)
        self.prepare_rolls(character, ruleset)
        recalculate(character, ruleset)
        await self.repository.save(
            character,
            [
                ("created", {"mode": "point_buy"}),
                ("allocation_updated", {"field": "attributes"}),
            ],
        )
        return character

    def prepare_rolls(self, character: CharacterDraft, ruleset: RuleSet) -> None:
        if ruleset.age_rules and age_band(character, ruleset) is None:
            bands = ruleset.age_rules.bands
            raise CharacterError(
                422,
                f"须选择 {bands[0].minimum}–{bands[-1].maximum} 岁；"
                "本地条款未覆盖90岁及以上调整，生成后年龄锁定",
            )
        for key, purpose, formula, multiplier in expected_rolls(character, ruleset):
            record = self.dice.roll(formula, key)
            record.purpose = purpose
            record.multiplier = multiplier
            character.roll_records.append(record)

    @staticmethod
    def check_editable(character: Character, version: int) -> None:
        if character.version != version:
            raise CharacterError(409, "角色已被更新，请重新加载后编辑")
        if character.status == "finalized":
            raise CharacterError(409, "角色已最终确认，不能修改")

    async def patch(self, character_id: UUID, request: PatchCharacterRequest) -> Character:
        character = await self.get(character_id)
        self.check_editable(character, request.version)
        ruleset = self.ruleset(character.ruleset_id, character.ruleset_version)
        changes = request.model_dump(
            exclude_unset=True,
            exclude={
                "version",
                "derived_values",
                "approve_specializations",
                "approve_occupation_exceptions",
                "approve_experience",
                "approve_module_handout",
                "module_handout",
            },
        )
        if "module_handout" in request.model_fields_set:
            changes["module_handout"] = (
                (await self.resolve_handout(request.module_handout)).model_dump()
                if request.module_handout is not None else None
            )
        if ruleset.age_rules and "age" in changes and changes["age"] != character.age:
            raise CharacterError(422, "年龄已锁定，不能通过修改年龄重新获得幸运或教育检定")
        if "attributes" in changes and character.creation_mode == "random":
            raise CharacterError(422, "随机模式的属性不能修改或重掷")
        candidate = CharacterDraft.model_validate({**character.model_dump(), **changes})
        self.check_known(candidate, ruleset)
        from app.rules.experiences import approve_experience, prepare_roll

        if not candidate.original_id or (
            candidate.experience
            and candidate.experience.package != "mythos"
            and (
                not character.experience
                or character.experience.package != candidate.experience.package
            )
        ):
            prepare_roll(candidate, ruleset, self.dice)
        # Normalize existing occupation choices before binding a new local consent.
        recalculate(candidate, ruleset)
        if request.approve_experience is not None:
            try:
                approve_experience(candidate, ruleset, request.approve_experience)
            except ValueError as error:
                raise CharacterError(422, str(error)) from error
        if request.approve_specializations is not None:
            from app.rules.specializations import approve_specializations

            try:
                approve_specializations(candidate, ruleset, request.approve_specializations)
            except ValueError as error:
                raise CharacterError(422, str(error)) from error
        if request.approve_occupation_exceptions is not None:
            from app.rules.occupation_exceptions import approve_occupation_exceptions

            try:
                approve_occupation_exceptions(
                    candidate, ruleset, request.approve_occupation_exceptions
                )
            except ValueError as error:
                raise CharacterError(422, str(error)) from error
        if request.approve_module_handout:
            from app.rules.handouts import approve_module_handout

            self.validate_handout_source(candidate)
            try:
                approve_module_handout(candidate, ruleset)
            except ValueError as error:
                raise CharacterError(422, str(error)) from error
        recalculate(candidate, ruleset)
        candidate.version += 1
        candidate.updated_at = utc_now()
        events = (
            [
                (
                    "basic_information_updated",
                    {"fields": sorted(changes.keys() & {"name", "age", "player_name"})},
                )
            ]
            if changes.keys() & {"name", "age", "player_name"}
            else []
        )
        if changes.keys() & {"attributes", "age_deductions"}:
            events.append(
                (
                    "allocation_updated",
                    {"fields": sorted(changes.keys() & {"attributes", "age_deductions"})},
                )
            )
        if changes.keys() & {"occupation", "selected_occupation_skills"}:
            events.append(("occupation_selected", {"occupation": candidate.occupation}))
        if changes.keys() & {"occupation_skills", "interest_skills"}:
            events.append(
                (
                    "skills_updated",
                    {"fields": sorted(changes.keys() & {"occupation_skills", "interest_skills"})},
                )
            )
        details = changes.keys() & {
            "era",
            "background",
            "asset_details",
            "equipment",
            "occupation_attribute",
            "occupation_group_choices",
            "selected_specializations",
            "custom_specializations",
            "occupation_skill_replacement",
            "initial_mythos_proposal",
        }
        if details:
            events.append(("character_details_updated", {"fields": sorted(details)}))
        if changes.keys() & {"experience", "experience_skills"}:
            events.append(("experience_updated", {
                "selection": candidate.experience.model_dump(mode="json")
                if candidate.experience else None,
                "effects": candidate.experience_effects,
                "roll_ids": [str(r.id) for r in candidate.experience_rolls.values()],
            }))
        if request.approve_experience:
            events.append(("experience_approved", {"effects": candidate.experience_effects}))
        if "module_handout" in changes or request.approve_module_handout:
            events.append(("module_handout_updated", {
                "handout_id": candidate.module_handout.handout_id
                if candidate.module_handout else None,
                "approved": candidate.module_handout_approval is not None,
                "effects": [effect.model_dump() for effect in candidate.module_handout_effects],
            }))
        if request.approve_specializations:
            events.append(("specializations_approved", {"skills": request.approve_specializations}))
        if request.approve_occupation_exceptions:
            events.append(
                (
                    "occupation_exceptions_approved",
                    {
                        "options": request.approve_occupation_exceptions,
                        "replacement": candidate.occupation_skill_replacement.model_dump()
                        if candidate.occupation_skill_replacement
                        else None,
                        "initial_mythos": candidate.initial_mythos,
                        "san": candidate.derived_values.get("san"),
                    },
                )
            )
        await self.repository.save(candidate, events, expected_version=request.version)
        return candidate

    async def finalize(self, character_id: UUID, version: int) -> CharacterSheet:
        character = await self.get(character_id)
        sheet = self.finalize_candidate(character, version)
        await self.repository.save(sheet, [("finalized", {})], expected_version=version)
        return sheet

    def finalize_candidate(self, character: Character, version: int) -> CharacterSheet:
        """Validate and freeze without committing, also used by room acceptance."""
        self.check_editable(character, version)
        ruleset = self.ruleset(character.ruleset_id, character.ruleset_version)
        self.check_known(character, ruleset)
        self.validate_handout_source(character)
        recalculate(character, ruleset)
        if not character.validation.valid:
            raise CharacterError(422, "角色校验未通过，草稿已保留", character.validation.issues)
        return CharacterSheet.model_validate(
            {
                **character.model_dump(),
                "status": "finalized",
                "version": version + 1,
                "updated_at": utc_now(),
            }
        )

    async def export(self, character_id: UUID) -> CharacterExport:
        character = await self.get(character_id)
        ruleset = self.ruleset(
            character.ruleset_id, character.ruleset_version, require_enabled=False
        )
        return CharacterExport(
            character=character,
            ruleset=ExportRuleset(
                id=ruleset.id,
                version=ruleset.version,
                verification_status=ruleset.verification_status,
            ),
        )

    async def import_character(self, document: CharacterExport) -> CharacterDraft:
        character = self.prepare_import(document)
        await self.repository.save(
            character, [("imported", {"original_id": str(document.character.id)})]
        )
        return character

    def prepare_import(self, document: CharacterExport) -> CharacterDraft:
        """Recalculate an imported draft without storing it or generating any dice."""
        original = document.character
        if original.experience and original.experience.package == "mythos":
            ids = [r.id for r in original.roll_records] + [
                r.id for r in original.experience_rolls.values()
            ]
            if len(ids) != len(set(ids)):
                raise CharacterError(422, "导入原骰ID重复；不能在重建本地属性记录ID时掩盖冲突")
        if (document.ruleset.id, document.ruleset.version) != (
            original.ruleset_id,
            original.ruleset_version,
        ):
            raise CharacterError(422, "导入文件的规则集元数据不一致")
        ruleset = self.ruleset(document.ruleset.id, document.ruleset.version)
        if document.ruleset.verification_status != ruleset.verification_status:
            raise CharacterError(422, "导入文件的规则集核对状态不匹配")
        self.validate_handout_source(original)
        character = CharacterDraft.model_validate(
            {
                **original.model_dump(),
                "id": uuid4(),
                "original_id": original.id,
                "status": "draft",
                "specialization_approvals": {},
                "occupation_exception_approvals": {},
                "experience_approvals": {},
                "module_handout_approval": None,
                "experience_rolls": {
                    key: {**record.model_dump(), "source": "imported"}
                    for key, record in original.experience_rolls.items()
                },
                "version": 1,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "roll_records": [
                    {**record.model_dump(), "id": uuid4(), "source": "imported"}
                    for record in original.roll_records
                ],
            }
        )
        self.check_known(character, ruleset)
        recalculate(character, ruleset)
        return character
