"""Narrow prose checks against actual skills; never change or reroll a card."""

import hashlib
import re

LOW_SKILL_LIMIT = 30
_ABILITY = re.compile(r"擅长|精通|熟练|拿手|专精|高手|专家|大师|经验丰富")
_UNIVERSITY_TEACHING = re.compile(
    r"(?:大学|学院|高校)(?:里|内|中)?(?:担任过|出任过|担任|出任|曾经|曾|任)?"
    r"(?:教授|任教|讲授|授课)"
)
_NEGATIVE = re.compile(
    r"(?:不|没|无|非|未|没有|不是|未曾|不曾|从未|谈不上|算不上|不算|并非)"
    r"(?:很|太|特别|真正|在|担任)?$"
)
_ASPIRATION = re.compile(
    r"希望|想要|想在|渴望|梦想|力求|努力|学习|练习|尝试|争取|计划|打算|有朝一日"
)
_OTHER_PERSON = re.compile(r"父亲|母亲|导师|老师|队友|同伴|朋友|妻子|丈夫|兄弟|姐妹|同事")
_BREAK = re.compile(r"但是|不过|而|却|虽然|只是|弱项|不足|生疏|平平|一般|不熟|不善|请教|借助")
_SURNAMES = "林陈许沈程顾周苏陆叶江季方白徐宋杨温谢唐郑梁秦萧韩何杜罗"
_GIVEN_NAMES = (
    "青禾", "文安", "知远", "明川", "嘉宁", "亦舟", "清和", "书白",
    "怀瑾", "子墨", "云舒", "修齐", "若宁", "星言", "景初", "雨桐",
    "思齐", "安澜", "秋平", "闻溪", "言川", "令仪", "霁明", "时安",
)


def _other_names(member, document):
    return {m["character"]["name"] for m in document["members"]
            if m["index"] != member["index"] and m["character"].get("name")}


def _name_conflicts(name, used):
    # Existing teammate addressing uses substrings. Appending an occupation to
    # a duplicate name would still address both people in ordinary dialogue.
    return any(name in other or other in name for other in used)


def _seeded_name(member, document, namespace):
    """Choose another whole name from this member's stream, never reroll a card."""
    digest = hashlib.sha256(f"{namespace}:{member['stream_seed']}".encode()).digest()
    start = (int.from_bytes(digest[:8], "big") % len(_SURNAMES)) * len(_GIVEN_NAMES)
    start += int.from_bytes(digest[8:16], "big") % len(_GIVEN_NAMES)
    used = _other_names(member, document)
    size = len(_SURNAMES) * len(_GIVEN_NAMES)
    # 257 is coprime to 672: visit every candidate once with a bounded search.
    for offset in range(size):
        index = (start + offset * 257) % size
        name = _SURNAMES[index // len(_GIVEN_NAMES)] + _GIVEN_NAMES[index % len(_GIVEN_NAMES)]
        if not _name_conflicts(name, used):
            return name
    raise ValueError("现有姓名无法与随机姓名区分，请编辑当前人物姓名")


def ability_issues(profile, character, ruleset):
    """Catch affirmative low-skill expertise, including coordinated skill lists.

    This is an output consistency threshold, not a new creation or check rule.
    Ordinary interest, intentions, acknowledged weaknesses and another person's
    expertise remain valid. Ambiguous prose is not converted into a hard error.
    """
    issues = []
    low_skills = []
    for definition in ruleset.skills:
        value = character.skill_values.get(definition.key)
        if value is None or value > LOW_SKILL_LIMIT or not definition.allocatable:
            continue
        aliases = {definition.display_name}
        specialized = re.search(r"[（(]([^）)]+)[）)]", definition.display_name)
        if specialized:
            aliases.add(specialized[1])
        low_skills.append((definition.key, definition.display_name, value, aliases))
    for field in ("background", "personality", "goals", "speaking_style", "action_tendency"):
        text = profile.get(field, "")
        for clause in re.split(r"[，,。；;！？!?\n]", text):
            claims = sorted(
                [*_ABILITY.finditer(clause), *_UNIVERSITY_TEACHING.finditer(clause)],
                key=lambda match: match.start(),
            )
            if not claims:
                continue
            for key, label, value, aliases in low_skills:
                hits = [match for alias in aliases if len(alias) >= 2
                        for match in re.finditer(re.escape(alias), clause)]
                for hit in hits:
                    if _NEGATIVE.search(clause[max(0, hit.start() - 8):hit.start()]):
                        continue
                    if _ASPIRATION.search(clause[hit.end():]) or "是他的目标" in clause[hit.end():]:
                        continue
                    before = [claim for claim in claims if claim.end() <= hit.start()]
                    after = [claim for claim in claims if claim.start() >= hit.end()]
                    candidates = ([before[-1]] if before else []) + (after[:1] if after else [])
                    for claim in candidates:
                        prefix = clause[max(0, claim.start() - 16):claim.start()]
                        if _NEGATIVE.search(prefix) or _ASPIRATION.search(prefix):
                            continue
                        if _OTHER_PERSON.search(prefix[-8:]):
                            continue
                        if claim.end() <= hit.start():
                            bridge = clause[claim.end():hit.start()]
                            if len(bridge) > 35:
                                continue
                        else:
                            bridge = clause[hit.end():claim.start()]
                            if len(bridge) > 16 or re.search(r"不|没|弱|缺|未|学习|尝试", bridge):
                                continue
                        if _BREAK.search(bridge):
                            continue
                        issues.append({
                            "field": field, "skill": key, "display_name": label, "value": value,
                            "claim": clause.strip(),
                            "message": f"{label}当前为 {value}，人物文字不能肯定自称{claim[0]}",
                        })
                        break
                    else:
                        continue
                    break
    # The same phrase may match the full label and its specialty alias.
    return list({(i["field"], i["skill"], i["claim"]): i for i in issues}.values())


def unique_name(profile, member, document):
    """Disambiguate only this newly generated member; other members stay byte-identical."""
    original = profile.name
    member["original_name"] = original
    if not _name_conflicts(original, _other_names(member, document)):
        return
    profile.name = _seeded_name(member, document, "party-name-v2")


def enforce_public_identity(member, document):
    """A model that reads a private HO cannot choose a public room identity."""
    card = member["character"]
    if not card.get("module_handout"):
        return False
    changed = False
    name = member.get("public_name")
    if not name:
        name = _seeded_name(member, document, "public-name-v1")
        member["public_name"] = name
        changed = True
    if card["name"] != name:
        card["name"] = name
        changed = True
    profile = member.get("profile")
    if profile and profile["name"] != name:
        member.setdefault("original_name", profile["name"])
        profile["name"] = name
        changed = True
    return changed
