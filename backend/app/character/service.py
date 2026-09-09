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
            raise CharacterError(422, "规则集版本不匹配，不能使用当前配置修改或导入此角色")
        return ruleset

    async def get(self, character_id: UUID) -> Character:
        character = await self.repository.get(character_id)
        if character is None:
            raise CharacterError(404, "角色不存在")
        return character

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
        skills = {item.key for item in ruleset.skills}
        for field in ("occupation_skills", "interest_skills"):
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
            raise CharacterError(422, "第七版创建前须选择 15–89 岁年龄；生成后年龄锁定")
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
        changes = request.model_dump(exclude_unset=True, exclude={"version", "derived_values"})
        if ruleset.age_rules and "age" in changes and changes["age"] != character.age:
            raise CharacterError(422, "年龄已锁定，不能通过修改年龄重新获得幸运或教育检定")
        if "attributes" in changes and character.creation_mode == "random":
            raise CharacterError(422, "随机模式的属性不能修改或重掷")
        candidate = CharacterDraft.model_validate({**character.model_dump(), **changes})
        self.check_known(candidate, ruleset)
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
        await self.repository.save(candidate, events, expected_version=request.version)
        return candidate

    async def finalize(self, character_id: UUID, version: int) -> CharacterSheet:
        character = await self.get(character_id)
        self.check_editable(character, version)
        ruleset = self.ruleset(character.ruleset_id, character.ruleset_version)
        self.check_known(character, ruleset)
        recalculate(character, ruleset)
        if not character.validation.valid:
            raise CharacterError(422, "角色校验未通过，草稿已保留", character.validation.issues)
        sheet = CharacterSheet.model_validate(
            {
                **character.model_dump(),
                "status": "finalized",
                "version": version + 1,
                "updated_at": utc_now(),
            }
        )
        await self.repository.save(sheet, [("finalized", {})], expected_version=version)
        return sheet

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
        original = document.character
        if (document.ruleset.id, document.ruleset.version) != (
            original.ruleset_id,
            original.ruleset_version,
        ):
            raise CharacterError(422, "导入文件的规则集元数据不一致")
        ruleset = self.ruleset(document.ruleset.id, document.ruleset.version)
        character = CharacterDraft.model_validate(
            {
                **original.model_dump(),
                "id": uuid4(),
                "original_id": original.id,
                "status": "draft",
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
        await self.repository.save(character, [("imported", {"original_id": str(original.id)})])
        return character
