"""Seeded construction uses the existing dice and final rule engine, never an LLM."""

import hashlib
import random
from uuid import UUID, uuid5

from app.character.service import CharacterError, CharacterService
from app.dice.service import DiceService
from app.domain.character import CharacterDraft, CharacteristicValue, SkillAllocation
from app.domain.character_details import EquipmentEntry
from app.rules.age import age_band
from app.rules.character_options import group_options
from app.rules.engine import recalculate
from app.rules.equipment import CATALOG


def member_seed(seed, index, reroll_count):
    return hashlib.sha256(f"party-v1:{seed}:{index}:{reroll_count}".encode()).hexdigest()


def _eligible(skill, ruleset, era):
    policy = ruleset.specialization_policies.get(skill.specialization_group)
    return (
        skill.allocatable and era in skill.eras
        and not (policy and policy.requires_keeper_approval and not policy.custom_only)
    )


def _groups(rng, occupation, ruleset, era, definitions):
    """Backtrack choices, never attributes: overlapping groups must remain distinct."""
    groups = sorted(occupation.skill_groups, key=lambda g: len(
        group_options(g, ruleset, era, definitions.values())
    ))

    def choose(position, used, result):
        if position == len(groups):
            return result
        group = groups[position]
        options = sorted(group_options(group, ruleset, era, definitions.values()) - used)
        rng.shuffle(options)

        def within(start, selected, directions):
            if len(selected) == group.count:
                return choose(position + 1, used | set(selected), {
                    **result, group.key: selected,
                })
            for offset in range(start, len(options)):
                key = options[offset]
                direction = definitions[key].specialization_group
                distinct = group.distinct_directions and not group.any_skill
                if distinct and key not in group.skills and direction in directions:
                    continue
                next_directions = directions | ({direction} if key not in group.skills else set())
                found = within(offset + 1, selected + [key], next_directions)
                if found is not None:
                    return found
            return None

        return within(0, [], set())

    result = choose(0, set(occupation.fixed_skills), {})
    if result is None:
        raise CharacterError(422, "当前职业分组没有不需额外许可的合法组合，请调整职业")
    return result


def _spend(rng, pool, keys, capacities):
    allocated = {}
    available = [key for key in sorted(keys) if capacities.get(key, 0) > 0]
    if sum(capacities[key] for key in available) < pool:
        raise CharacterError(422, "当前技能选择不足以分配全部点数；数值骰未重掷")
    weights = {key: rng.randint(1, 10) for key in available}
    while pool:
        key = rng.choices(available, weights=[weights[k] for k in available])[0]
        amount = min(pool, capacities[key], rng.randint(1, 15))
        allocated[key] = allocated.get(key, 0) + amount
        capacities[key] -= amount
        pool -= amount
        if capacities[key] == 0:
            available.remove(key)
    return {key: SkillAllocation(points=value) for key, value in allocated.items()}


def generate_card(ruleset, stream_seed, identity, *, era="1920s", handout=None,
                  age=None, occupation_key=None, on_card=None):
    rng = random.Random(stream_seed)
    policy = handout.definition.adjustments if handout else None
    era = policy.required_era if policy and policy.required_era else era
    age = policy.required_age if policy and policy.required_age is not None else age
    occupation_key = (
        policy.required_occupation if policy and policy.required_occupation else occupation_key
    )
    if age is None:
        # Ordinary adult defaults avoid age bands with potentially impossible flat
        # deductions. Explicit HO ages still use all published age rules unchanged.
        minimum, maximum = (18, 59)
        if ruleset.age_rules:
            minimum = max(minimum, ruleset.age_rules.bands[0].minimum)
            maximum = min(maximum, ruleset.age_rules.bands[-1].maximum)
        age = rng.randint(minimum, maximum)
    definitions = {s.key: s for s in ruleset.skills if _eligible(s, ruleset, era)}
    occupations = [
        o for o in ruleset.occupations if era in o.eras
        and set(o.fixed_skills) <= definitions.keys()
        and (not occupation_key or o.key == occupation_key)
    ]
    if not occupations:
        raise CharacterError(422, "模组年龄、年代或职业要求不支持当前规则下的普通随机建卡")
    occupation = rng.choice(occupations)
    character = CharacterDraft(
        id=identity, ruleset_id=ruleset.id, ruleset_version=ruleset.version,
        creation_mode="random", name="待生成姓名", age=age, era=era,
        occupation=occupation.key, module_handout=handout,
    )
    roller = CharacterService(None, {ruleset.id: ruleset}, DiceService(rng))
    roller.prepare_rolls(character, ruleset)
    character.attributes = {
        r.attribute: CharacteristicValue(value=r.total * r.multiplier)
        for r in character.roll_records if r.purpose == "attribute"
    }
    if on_card:
        # Keep the exact raw rolls even when a later HO/age constraint is impossible.
        on_card(character)
    band = age_band(character, ruleset)
    if band and band.deduction_pool:
        capacity = {
            key: max(0, character.attributes[key].value + band.flat_adjustments.get(key, 0) - 1)
            for key in band.deduction_attributes
        }
        character.age_deductions = {
            key: amount.points for key, amount in _spend(
                rng, band.deduction_pool, band.deduction_attributes, capacity,
            ).items()
        }
    recalculate(character, ruleset)
    if policy and policy.attribute_points:
        caps = {
            key: max(0, policy.attribute_maxima.get(
                key, policy.attribute_maximum or 999,
            ) - character.effective_attributes.get(key, 0))
            for key in policy.attribute_choices
        }
        character.module_handout.attribute_allocations = {
            key: amount.points for key, amount in _spend(
                rng, policy.attribute_points, policy.attribute_choices, caps,
            ).items()
        }
    if occupation.point_formula:
        choices = occupation.point_formula.choice_attributes
        character.occupation_attribute = rng.choice(choices) if choices else None
        character.occupation_group_choices = _groups(rng, occupation, ruleset, era, definitions)
    else:
        character.selected_occupation_skills = rng.sample(
            sorted(set(occupation.selectable_skills) & definitions.keys()),
            occupation.required_selection_count,
        )
    recalculate(character, ruleset)
    allowed = set(character.effective_occupation_skills)
    bonuses = policy.skill_bonuses if policy else {}
    capacity = {
        key: max(0, min(s.maximum, policy.skill_maximum if policy and key in bonuses else s.maximum)
                 - character.skill_base_values[key] - bonuses.get(key, 0))
        for key, s in definitions.items()
    }
    pool = character.remaining_points.occupation
    credit_min = occupation.credit_rating_minimum
    if credit_min is not None:
        credit_max = min(occupation.credit_rating_maximum, pool)
        if policy and policy.credit_maximum is not None:
            credit_max = min(credit_max, policy.credit_maximum - bonuses.get("credit_rating", 0))
        if credit_max < credit_min:
            raise CharacterError(422, "职业信用最低要求与本次点池或 HO 上限冲突；不会重掷属性")
        credit = rng.randint(credit_min, credit_max)
        character.occupation_skills["credit_rating"] = SkillAllocation(points=credit)
        pool -= credit
        capacity["credit_rating"] = 0
    else:
        # Development rules have no occupation credit bracket.
        allowed.discard("credit_rating")
        capacity["credit_rating"] = 0
    character.occupation_skills.update(_spend(rng, pool, allowed, capacity))
    interests = sorted(definitions.keys() - {"credit_rating"})
    rng.shuffle(interests)
    interest_pool = character.remaining_points.interest
    count = min(len(interests), rng.randint(3, 7))
    while sum(capacity[k] for k in interests[:count]) < interest_pool and count < len(interests):
        count += 1
    character.interest_skills = _spend(rng, interest_pool, interests[:count], capacity)
    character.selected_specializations = sorted({
        key for key in set(character.interest_skills) | allowed
        if key in definitions and definitions[key].specialization_group
    })
    equipment = [item for item in CATALOG if not item["weapon"] and era in item["eras"]]
    chosen = rng.sample(equipment, rng.randint(3, min(6, len(equipment))))
    character.equipment = [EquipmentEntry(
        id=str(uuid5(UUID(str(identity)), item["id"])),
        catalog_id=item["id"], name=item["name"],
    ) for item in chosen]
    recalculate(character, ruleset)
    blocking = [i for i in character.validation.issues if i.code != "keeper_approval"]
    if blocking:
        raise CharacterError(422, "随机卡未通过原规则校验；不重掷属性或放宽规则", blocking)
    options = {
        "temperament": rng.choice([
            "克制寡言", "热情健谈", "直率急躁", "谨慎多疑", "温和固执", "爱开玩笑",
        ]),
        "motivation": rng.choice([
            "保护身边的人", "偿还人情", "职业责任", "证明自己的判断", "寻找失落联系",
            "好奇但顾惜安全",
        ]),
        "flaw": rng.choice([
            "不愿向人求助", "容易先入为主", "害怕冲突", "欠缺耐心", "太容易相信熟人",
            "在陌生人前拘谨",
        ]),
        "voice": rng.choice(["短句直接", "先问再说", "善用生活比喻", "措辞客气", "轻松但不轻佻"]),
    }
    return character, options
