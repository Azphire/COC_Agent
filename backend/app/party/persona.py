"""Narrow prose checks against actual skills; never change or reroll a card."""

import hashlib
import re

LOW_SKILL_LIMIT = 30
_ABILITY = re.compile(r"擅长|精通|熟练|拿手|专精|高手|专家|大师|经验丰富|深厚造诣")
_EXPERT = re.compile(r"精通|专精|高手|专家|大师|深厚造诣|大学|学院|高校")
_CURRENT_CONDITION = re.compile(
    r"失明|失聪|瘫痪|截肢|断臂|断腿|无法行走|不能行走|无法看见|丧失视力|失去视力|双目失明"
)
_RECOVERED = re.compile(r"(?:已经|现已|已|如今|现在).{0,6}(?:康复|恢复|痊愈)|已无后遗症")
_PAST = re.compile(r"曾经|曾|过去|幼时|小时候|当年|一度")
_POSSESSION = re.compile(
    r"(?:随身携带|随身带着|随身带有|配备|配有|装备着|持有|带着|携带|拥有)"
    r"(?:了|着)?(?:一|两|三|几)?(?:把|支|套|副|个|张|本)?"
    r"(?P<item>[^，,。；;！？!?\n]{1,200})"
)
_GEAR_ALIASES = (
    {"手电筒", "手电"}, {"照相机", "相机"}, {"绳索", "绳子"},
    {"双筒望远镜", "望远镜"}, {"纱布绷带", "绷带"},
)
_GEAR = re.compile(
    r"枪|刀|剑|匕首|手杖|手电|放大镜|相机|照相机|绳|工具|药|急救|"
    r"护甲|防弹|子弹|弹药|汽车|轿车|摩托|证件|徽章|探测仪|望远镜|撬棍"
)
_CREDENTIAL = re.compile(
    r"(?:驾驶|医师|律师执业|律师|行医|飞行|持枪|教师)?(?:执照|资格证)|"
    r"(?:医学|法学|历史学|人类学)?博士学位|持证(?:医师|医生|律师)"
)
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
    return {*document.get("reserved_names", []),
            *(m["character"]["name"] for m in document["members"]
              if m["index"] != member["index"] and m["character"].get("name"))}


def _name_conflicts(name, used):
    # Creation avoids confusing public identities. Runtime still supports old
    # overlapping names; this check never migrates adopted investigators.
    normalized = re.sub(r"\s+", "", name).casefold()
    return any(normalized in re.sub(r"\s+", "", other).casefold()
               or re.sub(r"\s+", "", other).casefold() in normalized
               for other in used if other.strip())


def name_issues(name, member, document):
    if not _name_conflicts(name, _other_names(member, document)):
        return []
    return [{"field": "name", "claim": name,
             "message": "此姓名与本人或队友姓名重叠，请为当前新人物选择不同的完整姓名"}]


def _affirmative(clause, start, end):
    prefix, suffix = clause[:start], clause[end:]
    return not (_NEGATIVE.search(prefix[-16:]) or _ASPIRATION.search(prefix)
                or prefix.endswith("想")
                or _OTHER_PERSON.search(prefix[-8:]) or "是他的梦想" in suffix
                or "是她的梦想" in suffix or "是他的目标" in suffix
                or re.search(r"(?:没有|不具备|并无|未持有|未获得|未取得).{0,8}$", prefix)
                or re.match(r"的?(?:父亲|母亲|朋友|同伴|队友|导师)", suffix))


def _approved_text(character):
    sources = []
    handout = getattr(character, "module_handout", None)
    if handout:
        sources.append(handout.definition.text)
    experience = getattr(character, "experience", None)
    if experience and getattr(character, "experience_approvals", None):
        sources.extend([experience.history, experience.background_detail])
    return "。".join(sources)


def _supported(claim, source):
    for sentence in re.split(r"[。；;！？!?\n]", source):
        if _CURRENT_CONDITION.fullmatch(claim) and _RECOVERED.search(sentence):
            continue
        for clause in re.split(r"[，,]", sentence):
            for match in re.finditer(re.escape(claim), clause):
                prefix = clause[:match.start()]
                if re.search(r"(?:不得|不能|禁止|未获准).{0,8}$", prefix):
                    continue
                if _affirmative(clause, match.start(), match.end()):
                    return True
    return False


def _unsupported_effects(profile, character):
    """Require source support for explicit present effects, never personality flaws."""
    source = _approved_text(character)
    equipment = {item.name for item in getattr(character, "equipment", [])}
    # Some existing catalogue names themselves enumerate equivalent names.
    for name in tuple(equipment):
        for variant in re.split(r"[、／/]", re.sub(r"[（）()]", "、", name)):
            if variant.strip():
                equipment.add(variant.strip())
    for aliases in _GEAR_ALIASES:
        if equipment & aliases:
            equipment.update(aliases)
    issues = []
    for field in ("background", "personality", "goals", "speaking_style", "action_tendency"):
        text = profile.get(field, "")
        for sentence in re.split(r"[。；;！？!?\n]", text):
            for clause in re.split(r"[，,]", sentence):
                for match in _CURRENT_CONDITION.finditer(clause):
                    if not _affirmative(clause, match.start(), match.end()):
                        continue
                    if _PAST.search(clause[:match.start()]) and _RECOVERED.search(sentence):
                        continue
                    if not _supported(match[0], source):
                        issues.append({"field": field, "claim": clause.strip(),
                                       "message": f"当前身体状态“{match[0]}”没有卡或批准背景支持"})
                for match in _POSSESSION.finditer(clause):
                    if not _affirmative(clause, match.start(), match.end()):
                        continue
                    carried = re.split(r"用来|用于|以便|进行|来(?=照|记|拍|检|攀)",
                                       match["item"])[0]
                    # Validate each conjunct, including a repeated possession verb.
                    # Exact aliases never let e.g. gun oil authorize a gun.
                    for item in re.split(r"(?:以及|并且|还有|[、和与及并])(?![^（）()]*[）)])",
                                         carried):
                        item = item.strip()
                        repeated = _POSSESSION.fullmatch(item)
                        if repeated:
                            item = repeated["item"]
                        item = re.sub(r"^(?:一|两|二|三|几|\d+)?(?:把|支|套|副|个|张|本|根|条|台)",
                                      "", item).strip()
                        # Wishes, memories and personality traits are not gear.
                        if not _GEAR.search(item) or re.match(r"(?:不|没|未|无)", item):
                            continue
                        if item not in equipment and not _supported(item, source):
                            issues.append({"field": field, "claim": clause.strip(),
                                           "message": f"额外装备“{item}”未列入本人的卡或批准背景"})
                for match in _CREDENTIAL.finditer(clause):
                    if _affirmative(clause, match.start(), match.end()) \
                            and not _supported(match[0], source):
                        issues.append({"field": field, "claim": clause.strip(),
                                       "message": "人物文字中的额外执照或资格没有批准背景支持"})
    return issues


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
    group_maxima = {}
    for definition in ruleset.skills:
        group = definition.specialization_group
        if group:
            group_maxima[group] = max(group_maxima.get(group, 0),
                                      character.skill_values.get(definition.key, 0))
    for definition in ruleset.skills:
        value = character.skill_values.get(definition.key)
        if value is None or value >= 50 or not definition.allocatable:
            continue
        aliases = {definition.display_name}
        specialized = re.search(r"[（(]([^）)]+)[）)]", definition.display_name)
        if specialized:
            aliases.add(specialized[1])
            if value == group_maxima.get(definition.specialization_group):
                # A general "survival" claim is grounded in the best actual
                # specialty, not silently accepted because no suffix was named.
                aliases.add(definition.display_name[:specialized.start()])
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
                        if value > LOW_SKILL_LIMIT and not _EXPERT.search(claim[0]):
                            continue
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
    issues.extend(_unsupported_effects(profile, character))
    return list({(i["field"], i.get("skill"), i["claim"]): i for i in issues}.values())


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
