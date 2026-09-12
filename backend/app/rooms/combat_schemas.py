"""Small, sourced combat state. Model decisions never include numbers or dice."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.domain.character import DomainModel

Number = Annotated[StrictInt, Field(ge=0, le=100_000)]
Stat = Annotated[StrictInt, Field(ge=0, le=999)]


class Weapon(DomainModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    skill: str = Field(min_length=1, max_length=80)
    kind: Literal["melee", "firearm"] = "melee"
    damage: str = "1d3"
    impale: StrictBool = False
    quantity: Number = 1
    ammo: Number = 0
    capacity: Number = 0
    reserve: Number = 0
    malfunction: Annotated[StrictInt, Field(ge=1, le=100)] = 100
    jammed: StrictBool = False
    ready: StrictBool = False
    source: str = Field(min_length=1, max_length=500)

    @field_validator("damage")
    @classmethod
    def formula(cls, value):
        from app.rules.combat import damage_bounds

        low, high = damage_bounds(value)
        if low < 0 or high > 1000:
            raise ValueError("武器伤害范围不合法")
        return value

    @model_validator(mode="after")
    def loaded(self):
        if self.ammo > self.capacity:
            raise ValueError("弹药超过容量")
        return self


def unarmed():
    return Weapon(id="unarmed", name="徒手", skill="brawl", source="1907规则书PDF88／印刷87")


class Injury(DomainModel):
    major_wound: StrictBool = False
    prone: StrictBool = False
    unconscious: StrictBool = False
    dying: StrictBool = False
    dead: StrictBool = False
    stabilized: StrictBool = False
    con_pending: Literal["wound", "dying", "hourly"] | None = None
    dying_since_round: int | None = None
    check_due_minute: int | None = None
    last_damage_minute: int | None = None
    first_aid_attempted: StrictBool = False
    medicine_attempted: StrictBool = False
    damage_day: int = 0
    wounds: list[dict] = Field(default_factory=list)
    receipts: dict[str, dict] = Field(default_factory=dict)


class Combatant(DomainModel):
    id: str
    label: str
    member_id: str | None = None
    slot_id: str | None = None
    npc_id: str | None = None
    team: str = "investigators"
    scene_id: str
    public: StrictBool = True
    stats_public: StrictBool = False
    attributes: dict[str, Stat]
    skills: dict[str, Stat]
    hp: Number
    hp_max: Annotated[StrictInt, Field(ge=1, le=100_000)]
    armor: Number = 0
    damage_bonus: str = "0"
    weapons: list[Weapon] = Field(default_factory=lambda: [unarmed()], min_length=1, max_length=20)
    injury: Injury = Field(default_factory=Injury)
    source: str = Field(min_length=1, max_length=500)

    @field_validator("damage_bonus")
    @classmethod
    def bonus(cls, value):
        from app.rules.combat import damage_bounds

        low, high = damage_bounds(value)
        if low < -2 or high > 1000:
            raise ValueError("伤害加值范围不合法")
        return value

    @model_validator(mode="after")
    def stats(self):
        if not {"dex", "con"} <= self.attributes.keys():
            raise ValueError("战斗必须有DEX和CON")
        if self.hp > self.hp_max or len({w.id for w in self.weapons}) != len(self.weapons):
            raise ValueError("HP或武器ID不合法")
        if any(w.skill not in self.skills for w in self.weapons):
            raise ValueError("武器技能必须来自角色数值")
        return self


class CombatState(DomainModel):
    id: str | None = None
    active: StrictBool = False
    round: int = 0
    order: list[str] = Field(default_factory=list)
    index: int = 0
    participants: dict[str, Combatant] = Field(default_factory=dict)
    defenses: dict[str, int] = Field(default_factory=dict)
    firearm_priority: list[str] = Field(default_factory=list)
    skip_turn: list[str] = Field(default_factory=list)
    pending_id: str | None = None
    actions: dict[str, dict] = Field(default_factory=dict)
    turn_key: int = 0
    reason: str = ""


class CombatSetup(DomainModel):
    expected_revision: int
    member_id: UUID | None = None
    npc: Combatant | None = None
    armor: Number = 0
    weapons: list[Weapon] = Field(default_factory=lambda: [unarmed()], min_length=1, max_length=20)
    team: str = Field(default="investigators", min_length=1, max_length=80)
    stats_public: StrictBool = False
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def target(self):
        if bool(self.member_id) == bool(self.npc):
            raise ValueError("指定成员或主机创建NPC之一")
        if self.npc and (self.npc.member_id or self.npc.slot_id):
            raise ValueError("NPC不能伪装成员")
        return self


class CombatDecision(DomainModel):
    operation: Literal[
        "attack", "first_aid", "medicine", "reload", "pass", "end", "talk", "investigate"
    ]
    target_id: str | None = Field(default=None, json_schema_extra={"x-explicit-output": True})
    weapon_id: str | None = Field(default=None, json_schema_extra={"x-explicit-output": True})
    reason: str = Field(min_length=1, max_length=300)
    # Basic single shots at a declared range band. Exceptional modifiers need host ruling.
    range_band: Literal["base", "long", "extreme", "point_blank"] = "base"


class CombatCommand(CombatDecision):
    actor_id: str
    client_request_id: UUID
    turn_key: int


class CombatStep(DomainModel):
    action_id: str
    stage: str
    operation: Literal["dodge", "fight_back", "cover", "take", "roll", "accept", "luck"]
    weapon_id: str | None = None
    spend: Annotated[StrictInt, Field(ge=1, le=99)] | None = None


class DamageCommand(DomainModel):
    target_id: str
    client_request_id: UUID
    amount: Number
    armor_applies: StrictBool = True
    reason: str = Field(min_length=1, max_length=500)


class CombatNarration(DomainModel):
    text: str = Field(min_length=1, max_length=800)


class CombatControl(DomainModel):
    operation: Literal["end", "advance"]
    expected_revision: int
    minutes: Annotated[StrictInt, Field(ge=0, le=60)] = 0
    reason: str = Field(min_length=1, max_length=500)
