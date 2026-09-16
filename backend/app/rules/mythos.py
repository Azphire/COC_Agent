"""Mythos package creation only; known spells never authorize arbitrary effects."""

import unicodedata

from app.domain.mythos import InitialMythosSource, KnownSpell


def evaluate_package(character, policy, issue):
    selection = character.experience
    proposal = selection.mythos
    approved = bool(character.experience_approvals)
    if not proposal:
        issue("experience.mythos", "required", "请填写神话来源、相信者状态及两项背景")
        return 0, set()
    if not selection.history.strip():
        issue("experience.history", "background", "须与KP说明研究或实际经历如何取得神话知识")
    if (
        selection.variant
        or selection.choices
        or selection.background_detail
        or any(
            v is not None
            for v in (selection.war_year, selection.scenario_year, selection.age_at_war)
        )
    ):
        issue("experience", "selection", "神话包使用独立两项背景，不使用四包身份、技能组或年份")
    if len(proposal.backgrounds) != 2 or any(not b.detail.strip() for b in proposal.backgrounds):
        issue("experience.mythos.backgrounds", "background", "须填写两项与神话经历有关的背景")
    elif len({b.detail.strip().casefold() for b in proposal.backgrounds}) != 2:
        issue("experience.mythos.backgrounds", "duplicate", "两项背景不能重复")
    if proposal.knowledge == "direct" and proposal.belief == "unbeliever":
        issue(
            "experience.mythos.belief",
            "selection",
            "包内不信者选择适用于读书来源；实际经历须选相信者",
        )
    value = proposal.value
    if proposal.method == "suggested_roll":
        record = character.experience_rolls.get("mythos")
        if value is not None:
            issue("experience.mythos.value", "selection", "建议骰方案须使用原骰，不能另填数值")
        if not record:
            issue("experience_rolls", "incomplete", "缺少初始神话原骰；导入不会补掷")
        value = record.total if record and 6 <= record.total <= 15 else 0
    elif value is None:
        issue(
            "experience.mythos.value", "required", "手定方案须填写KP决定的神话值（1D10+5仅为建议）"
        )
        value = 0
    if character.initial_mythos_proposal:
        issue(
            "experience.mythos",
            "unsupported_combination",
            "软件暂不支持神秘学家初始神话与神话包组合：原文未明确叠加方式；不是官方禁止规则",
        )
        value = 0
    spells = proposal.spells
    if spells and proposal.belief != "believer":
        issue(
            "experience.mythos.spells",
            "selection",
            "本包初始法术许可条款限相信者；可选择无初始法术",
        )
    names = [unicodedata.normalize("NFKC", s.name).strip().casefold() for s in spells]
    if len({s.id for s in spells}) != len(spells) or len(set(names)) != len(names):
        issue("experience.mythos.spells", "duplicate", "已知法术的稳定标识与名称不能重复")
    if any(not s.name.strip() or not s.source.strip() for s in spells):
        issue("experience.mythos.spells", "required", "法术须填写名称及可供KP核对的来源")
    if not approved:
        issue(
            "experience_approvals.mythos",
            "keeper_approval",
            f"待KP核准知识来源、数值{value}、相信者状态、两项背景及全部初始法术：{policy.source}",
        )
    character.initial_belief = proposal.belief
    if approved and proposal.belief == "believer":
        character.known_spells = [
            KnownSpell(**s.model_dump(), permission=character.experience_approvals["mythos"])
            for s in spells
        ]
    character.experience_effects = {
        "package": "mythos",
        "name": policy.display_name,
        "source": policy.source,
        "pool": 0,
        "allowed_skills": [],
        "mythos": value,
        "san_loss": value if proposal.belief == "believer" else 0,
        "immunity": [],
        "immunity_reasons": [],
        "approved": approved,
        "belief": proposal.belief,
        "backgrounds": [b.model_dump() for b in proposal.backgrounds],
        "casting_status": "unsupported",
    }
    return 0, set()


def aggregate_initial(character):
    """Occupation is freshly derived first; package source is added exactly once."""
    sources = []
    if character.initial_mythos:
        sources.append(
            InitialMythosSource(
                source="occupation:occultist",
                value=character.initial_mythos,
                approved="initial_mythos" in character.occupation_exception_approvals,
            )
        )
    effect = character.experience_effects
    if effect.get("package") == "mythos":
        sources.append(
            InitialMythosSource(
                source="experience:mythos",
                value=effect["mythos"],
                approved=effect["approved"],
                san_cost=effect["san_loss"],
            )
        )
    character.initial_mythos_sources = sources
    character.initial_mythos = sum(s.value for s in sources)


def initial_belief(snapshot):
    """Only locally approved 1.5 package cards opt into belief semantics."""
    from app.domain.character import CharacterSheet
    from app.rules.experiences import policy_for, review_requirements
    from app.rules.loader import runtime_ruleset

    if not snapshot.get("experience", {}) or snapshot["experience"].get("package") != "mythos":
        return None
    card = CharacterSheet.model_validate(snapshot)
    rules = runtime_ruleset(card.ruleset_id, card.ruleset_version)
    policy = policy_for(card, rules) if rules else None
    if not policy or not policy.initial_mythos_roll or not card.experience.mythos:
        return None
    if card.initial_mythos_proposal or card.experience_approvals != review_requirements(
        card, rules
    ):
        return None
    return card.experience.mythos.belief
