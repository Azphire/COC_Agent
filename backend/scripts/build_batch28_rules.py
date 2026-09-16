# ruff: noqa: E501
"""Reproducible transcription of local IH1.20 occupational numerical blocks.

Read the immutable 1.1.0 archive. Never read/write source PDFs, saves or .env.
The coverage table is a derived review aid, not a replacement for SOURCES.md.
"""

from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS = ROOT / "backend/app/rules/definitions"
SOCIAL = "charm fast_talk intimidate persuade"


def printed_range(page):
    # Checked page-header/boxed page markers in the local compressed layout.
    ranges = {
        40: (70, 71),
        41: (71, 72),
        42: (72, 73),
        43: (73, 74),
        44: (74, 75),
        45: (75, 76),
        46: (77, 78),
        47: (78, 79),
        48: (79, 80),
        49: (80, 81),
        50: (81, 82),
        51: (82, 83),
        52: (83, 84),
        53: (84, 85),
        54: (86, 87),
        55: (87, 88),
        56: (88, 89),
        57: (89, 90),
        58: (90, 91),
        59: (91, 93),
    }
    pages = [int(n) for n in str(page).split("–")]
    return f"{ranges[pages[0]][0]}–{ranges[pages[-1]][1]}"


def group(key, skills="", families="", count=1, distinct=True, label=None):
    return dict(
        key=key,
        display_name=label or key,
        skills=skills.split(),
        specialization_groups=families.split(),
        count=count,
        distinct_directions=distinct,
    )


def social(n=1):
    return group("social", SOCIAL, count=n, label="社交技能")


def spec(family, n=1):
    labels = {
        "language": "外语",
        "art_craft": "艺术与手艺",
        "science": "科学",
        "fighting": "格斗",
        "firearms": "射击",
        "survival": "生存",
        "pilot": "驾驶",
    }
    return group(family, families=family, count=n, distinct=False, label=labels[family])


def personal(n=1):
    return dict(key="personal", display_name="个人或时代特长", count=n, any_skill=True)


def either(key, skills="", families="", label=None):
    return group(key, skills, families, label=label or "选择一项相关技能")


def build():
    rules = yaml.safe_load(
        (DEFINITIONS / "legacy/coc7_character_creation_1_1_0.yaml").read_text(encoding="utf-8")
    )
    rules["version"] = "1.2.0"
    rules["custom_specialization_templates"].update(
        pilot="pilot_aircraft", survival="survival_desert", lore="lore_folklore"
    )
    rules["specialization_policies"] = {
        "pilot": dict(
            requires_keeper_approval=True,
            custom_only=True,
            note="驾驶基础1；具体水上或空中载具须由KP核对年代与背景后允许。1920年代飞行器限热气球、飞艇和民用螺旋桨飞机。",
            modern_name_fragments=["喷气", "直升", "客机", "jet", "helicopter", "airliner"],
        ),
        "survival": dict(
            note="生存基础10；名称须是具体环境，各专业独立分配，不自动获得跨专业加值。"
        ),
        "lore": dict(
            requires_keeper_approval=True,
            note="学问基础1；仅在KP决定将该特殊知识领域引入游戏后购点。须为具体、非常规的知识，不能代替克苏鲁神话或授予法术。",
            forbidden_names=["克苏鲁神话", "克苏鲁神话知识", "Cthulhu Mythos", "cthulhu_mythos"],
        ),
    }
    # Named ordinary specialties explicitly called for by IH occupational blocks.
    for key, name, template in [
        ("survival_mountains", "生存（高山）", "survival_arctic"),
        ("art_typing", "艺术与手艺（打字）", "art_carpentry"),
        ("art_shorthand", "艺术与手艺（速记）", "art_carpentry"),
    ]:
        s = deepcopy(next(s for s in rules["skills"] if s["key"] == template))
        s.update(key=key, display_name=name)
        rules["skills"].append(s)
    occupations = rules["occupations"]
    original = {o["key"] for o in occupations}
    academic = deepcopy(
        next(
            g
            for o in occupations
            if o["key"] == "professor"
            for g in o["skill_groups"]
            if g["key"] == "academic"
        )
    )
    academic.update(
        specialization_groups=["science", "language", "art_craft", "lore"],
        distinct_directions=False,
    )
    for o in occupations:
        for i, g in enumerate(o["skill_groups"]):
            if g["key"] == "academic":
                o["skill_groups"][i] = {**deepcopy(academic), "count": g["count"]}

    def academics(n):
        return {**deepcopy(academic), "count": n}

    coverage = {}

    def occ(key, name, page, credit, fixed, groups=(), formula="edu", era=None):
        printed = printed_range(page)
        if formula == "edu":
            points = dict(fixed={"edu": 4}, display="EDU×4")
        elif "/" in formula:
            attrs = formula.split("/")
            points = dict(
                fixed={"edu": 2},
                choice_attributes=attrs,
                choice_multiplier=2,
                display="EDU×2 + (" + "/".join(a.upper() for a in attrs) + ")×2",
            )
        else:
            points = dict(fixed={"edu": 2, formula: 2}, display=f"EDU×2 + {formula.upper()}×2")
        entry = dict(
            key=key,
            display_name=name,
            fixed_skills=fixed.split(),
            skill_groups=list(groups),
            credit_rating_minimum=credit[0],
            credit_rating_maximum=credit[1],
            point_formula=points,
            eras=[era] if era else ["1920s", "modern"],
            source=f"调查员手册1.20 PDF {page}（印刷 {printed}）",
        )
        old = next((o for o in occupations if o["key"] == key), None)
        if old:
            old.update(entry)
        else:
            occupations.append(entry)
        coverage[key] = (str(page), printed_range(page))

    phy = "dex/str"

    def repair():
        return either("repair", "electrical_repair mechanical_repair")

    def research():
        return either("research", "computer_use library_use")

    def combat():
        return either("combat", families="fighting firearms")

    def language():
        return spec("language")

    def art():
        return spec("art_craft")

    def fight():
        return spec("fighting")

    def gun():
        return spec("firearms")

    def survive():
        return spec("survival")

    occ(
        "acrobat",
        "杂技演员",
        40,
        (9, 20),
        "climb dodge jump throw spot_hidden swim",
        [personal(2)],
        "dex",
    )
    occ(
        "stage_actor",
        "戏剧演员",
        40,
        (9, 40),
        "art_acting disguise history psychology",
        [fight(), social(2), personal()],
        "app",
    )
    occ(
        "film_actor",
        "电影演员",
        40,
        (20, 90),
        "art_acting disguise drive_auto psychology",
        [social(2), personal(2)],
        "app",
    )
    occ(
        "agency_detective",
        "事务所侦探",
        40,
        (20, 45),
        "brawl law library_use psychology stealth track",
        [social(), gun()],
        phy,
    )
    occ(
        "alienist",
        "精神病医生（古典）",
        40,
        (10, 60),
        "law listen medicine psychoanalysis psychology biology chemistry",
        [language()],
        era="1920s",
    )
    occ(
        "animal_trainer",
        "动物训练师",
        41,
        (10, 40),
        "jump listen natural_world animal_handling science_zoology stealth track",
        [personal()],
        "app/pow",
    )
    occ(
        "antique_dealer",
        "古董商",
        41,
        (30, 50),
        "accounting appraise drive_auto history library_use navigate",
        [social(2)],
    )
    occ(
        "archaeologist",
        "考古学家",
        41,
        (10, 40),
        "appraise archaeology history library_use spot_hidden mechanical_repair",
        [language(), either("field", "navigate", "science")],
    )
    occ(
        "architect",
        "建筑师",
        41,
        (30, 70),
        "accounting art_technical_drawing law own_language persuade psychology science_mathematics",
        [research()],
    )
    occ(
        "asylum_attendant",
        "精神病院看护",
        42,
        (8, 20),
        "dodge brawl first_aid listen psychology stealth",
        [social(2)],
        phy,
    )
    occ(
        "bartender",
        "酒保",
        42,
        (8, 25),
        "accounting brawl listen psychology spot_hidden",
        [social(2), personal()],
        "app",
    )
    occ(
        "big_game_hunter",
        "猎人",
        42,
        (20, 50),
        "natural_world navigate stealth track",
        [
            gun(),
            either("awareness", "listen spot_hidden"),
            either("environment", families="language survival"),
            either("science", "biology science_botany"),
        ],
        phy,
    )
    occ(
        "book_dealer",
        "书商",
        "42–43",
        (20, 40),
        "accounting appraise drive_auto history library_use own_language",
        [language(), social()],
    )
    occ(
        "bounty_hunter",
        "赏金猎人",
        43,
        (9, 30),
        "drive_auto law psychology track stealth",
        [either("electrical", "electronics electrical_repair"), combat(), social()],
        phy,
    )
    occ(
        "boxer_wrestler",
        "拳击手／摔跤手",
        43,
        (9, 60),
        "dodge brawl intimidate jump psychology spot_hidden",
        [personal(2)],
        "str",
    )
    occ(
        "butler",
        "管家／男仆／女仆",
        43,
        (9, 40),
        "first_aid listen psychology spot_hidden",
        [either("accounts", "accounting appraise"), art(), personal(2)],
    )
    occ(
        "computer_programmer",
        "计算机程序员／工程师",
        44,
        (10, 70),
        "computer_use electrical_repair electronics library_use science_mathematics spot_hidden",
        [personal(2)],
        era="modern",
    )
    occ(
        "cowboy",
        "牛仔",
        44,
        (9, 20),
        "dodge jump ride throw track",
        [combat(), either("care", "first_aid natural_world"), survive()],
        phy,
    )
    occ(
        "craftsperson",
        "工匠",
        44,
        (10, 40),
        "accounting mechanical_repair natural_world spot_hidden",
        [spec("art_craft", 2), personal(2)],
        "dex",
    )
    occ(
        "assassin",
        "刺客",
        "44–45",
        (30, 60),
        "disguise electrical_repair locksmith mechanical_repair stealth psychology",
        [fight(), gun()],
        phy,
    )
    occ(
        "bank_robber",
        "银行劫匪",
        45,
        (5, 75),
        "drive_auto intimidate locksmith operate_heavy_machinery",
        [repair(), fight(), gun(), personal()],
        phy,
    )
    occ(
        "thug",
        "打手／暴徒",
        45,
        (5, 30),
        "drive_auto psychology stealth spot_hidden",
        [fight(), gun(), social(2)],
        "str",
    )
    occ(
        "burglar",
        "窃贼",
        45,
        (5, 40),
        "appraise climb listen locksmith sleight_of_hand stealth spot_hidden",
        [repair()],
        "dex",
    )
    occ(
        "conman",
        "欺诈师",
        45,
        (10, 65),
        "appraise art_acting listen psychology sleight_of_hand",
        [either("knowledge", "law", "language"), social(2)],
        "app",
    )
    occ(
        "freelance_criminal",
        "独行罪犯",
        45,
        (5, 65),
        "appraise stealth psychology spot_hidden",
        [
            either("cover", "art_acting disguise"),
            social(),
            combat(),
            either("entry", "locksmith mechanical_repair"),
        ],
        "dex/app",
    )
    occ(
        "gun_moll",
        "女飞贼（古典）",
        45,
        (10, 80),
        "drive_auto listen stealth",
        [art(), social(2), either("combat", "brawl handgun"), personal()],
        "app",
        "1920s",
    )
    occ(
        "fence",
        "赃物贩子",
        45,
        (20, 40),
        "accounting appraise art_forgery history library_use spot_hidden",
        [social(), personal()],
        "app",
    )
    occ(
        "forger",
        "赝造者／伪造者",
        45,
        (20, 60),
        "accounting appraise art_forgery history library_use spot_hidden sleight_of_hand",
        [personal()],
    )
    occ(
        "smuggler",
        "走私者",
        45,
        (20, 60),
        "listen navigate psychology sleight_of_hand spot_hidden",
        [gun(), social(), either("transport", "drive_auto pilot_aircraft pilot_boat")],
        "app/dex",
    )
    occ(
        "street_punk",
        "混混",
        45,
        (3, 10),
        "climb jump sleight_of_hand stealth throw",
        [social(), fight(), gun()],
        phy,
    )
    occ(
        "cult_leader",
        "教团首领",
        "45–46",
        (30, 60),
        "accounting occult psychology spot_hidden",
        [social(2), personal(2)],
    )
    occ(
        "deprogrammer",
        "除魅师（现代）",
        46,
        (20, 50),
        "drive_auto history occult psychology stealth",
        [social(2), either("combat", "brawl", "firearms")],
        era="modern",
    )
    occ(
        "designer",
        "设计师",
        46,
        (20, 60),
        "accounting photography mechanical_repair psychology spot_hidden",
        [art(), research(), personal()],
    )
    occ(
        "diver",
        "潜水员",
        46,
        (9, 30),
        "diving first_aid mechanical_repair pilot_boat biology spot_hidden swim",
        [personal()],
        "dex",
    )
    occ(
        "chauffeur",
        "私人司机",
        47,
        (10, 40),
        "drive_auto listen mechanical_repair navigate spot_hidden",
        [social(2), personal()],
        "dex",
    )
    occ(
        "driver",
        "司机",
        47,
        (9, 20),
        "accounting drive_auto listen mechanical_repair navigate psychology",
        [social(), personal()],
        phy,
    )
    occ(
        "taxi_driver",
        "出租车司机",
        47,
        (9, 30),
        "accounting drive_auto electrical_repair fast_talk mechanical_repair navigate spot_hidden",
        [personal()],
        "dex",
    )
    occ(
        "editor",
        "编辑",
        47,
        (10, 30),
        "accounting history own_language psychology spot_hidden",
        [social(2), personal()],
    )
    occ(
        "elected_official",
        "政府官员",
        "47–48",
        (50, 90),
        "charm history intimidate fast_talk listen own_language persuade psychology",
        formula="app",
    )
    occ(
        "explorer",
        "探险家（古典）",
        48,
        (55, 80),
        "history jump natural_world navigate",
        [either("physical", "climb swim"), gun(), language(), survive()],
        "app/dex/str",
        "1920s",
    )
    occ(
        "federal_agent",
        "联邦探员",
        "48–49",
        (20, 40),
        "drive_auto brawl law persuade stealth spot_hidden",
        [gun(), personal()],
    )
    occ(
        "foreign_correspondent",
        "驻外记者",
        49,
        (10, 40),
        "history own_language listen psychology",
        [language(), social(2), personal()],
    )
    occ(
        "forensic_surgeon",
        "法医",
        49,
        (40, 60),
        "language_latin library_use medicine persuade biology science_forensics science_pharmacy spot_hidden",
    )
    occ(
        "gambler",
        "赌徒",
        49,
        (8, 50),
        "accounting art_acting listen psychology sleight_of_hand spot_hidden",
        [social(2)],
        "app/dex",
    )
    occ(
        "gangster_boss",
        "黑帮老大",
        "49–50",
        (60, 95),
        "law listen psychology spot_hidden",
        [fight(), gun(), social(2)],
        "app",
    )
    occ(
        "gangster_underling",
        "黑帮马仔",
        50,
        (9, 20),
        "drive_auto psychology",
        [fight(), gun(), social(2), personal(2)],
        phy,
    )
    occ(
        "gentleman",
        "绅士／淑女",
        50,
        (40, 90),
        "firearms_rifle_shotgun history navigate ride",
        [art(), social(2), language()],
        "app",
    )
    occ(
        "hobo",
        "游民",
        50,
        (0, 5),
        "climb jump listen navigate stealth",
        [art(), either("hands", "locksmith sleight_of_hand"), personal()],
        "app/dex",
    )
    occ(
        "hospital_orderly",
        "勤杂护工",
        50,
        (6, 15),
        "electrical_repair brawl first_aid listen mechanical_repair psychology stealth",
        [social()],
        "str",
    )
    # Same occupation identity, explicit 1.2.0 handbook variant; 1.1.0 remains archived.
    occ(
        "journalist",
        "调查记者（记者）",
        50,
        (9, 30),
        "history library_use own_language psychology",
        [either("art", "art_fine_art photography"), social(), personal(2)],
    )
    occ(
        "reporter",
        "通讯记者",
        "50–51",
        (9, 30),
        "art_acting history listen own_language psychology stealth spot_hidden",
        [social()],
    )
    occ(
        "judge",
        "法官",
        51,
        (50, 80),
        "history intimidate law library_use listen own_language persuade psychology",
    )
    occ(
        "laboratory_assistant",
        "实验室助理",
        51,
        (10, 30),
        "electrical_repair chemistry spot_hidden",
        [research(), language(), spec("science", 2), personal()],
    )
    occ(
        "laborer",
        "非熟练工人",
        51,
        (9, 30),
        "drive_auto electrical_repair first_aid mechanical_repair operate_heavy_machinery throw",
        [fight(), personal()],
        phy,
    )
    occ(
        "lumberjack",
        "伐木工",
        51,
        (9, 30),
        "climb dodge fighting_chainsaw first_aid jump mechanical_repair throw",
        [either("nature", "natural_world biology science_botany")],
        phy,
    )
    occ(
        "miner",
        "矿工",
        51,
        (9, 30),
        "climb science_geology jump mechanical_repair operate_heavy_machinery stealth spot_hidden",
        [personal()],
        phy,
    )
    occ(
        "mechanic",
        "技师",
        52,
        (9, 40),
        "climb drive_auto electrical_repair mechanical_repair operate_heavy_machinery",
        [art(), personal(2)],
    )
    occ(
        "military_officer",
        "军官",
        52,
        (20, 70),
        "accounting navigate first_aid psychology",
        [gun(), social(2), personal()],
        phy,
    )
    occ(
        "missionary",
        "传教士",
        52,
        (0, 30),
        "first_aid mechanical_repair medicine natural_world",
        [art(), social(), personal(2)],
        "app",
    )
    occ(
        "mountain_climber",
        "登山家",
        52,
        (30, 60),
        "climb first_aid jump listen navigate survival_mountains track",
        [language()],
        phy,
    )
    occ(
        "museum_curator",
        "博物馆管理员",
        52,
        (10, 30),
        "accounting appraise archaeology history library_use occult spot_hidden",
        [language()],
    )
    occ(
        "occultist",
        "神秘学家",
        53,
        (9, 65),
        "anthropology history library_use occult science_astronomy",
        [social(), language(), personal()],
    )
    occ(
        "outdoorsman",
        "旅行家",
        53,
        (5, 20),
        "first_aid listen natural_world navigate spot_hidden track",
        [gun(), survive()],
        phy,
    )
    occ(
        "pharmacist",
        "药剂师",
        54,
        (35, 75),
        "accounting first_aid language_latin library_use psychology science_pharmacy chemistry",
        [social()],
    )
    occ(
        "photographer",
        "摄影师",
        54,
        (9, 30),
        "photography psychology chemistry stealth spot_hidden",
        [social(), personal(2)],
    )
    occ(
        "photojournalist",
        "摄影记者",
        54,
        (10, 30),
        "photography climb psychology chemistry",
        [social(), language(), personal(2)],
    )
    occ(
        "aviator",
        "特技飞行员（古典）",
        54,
        (30, 60),
        "accounting electrical_repair listen mechanical_repair navigate pilot_aircraft spot_hidden",
        [personal()],
        era="1920s",
    )
    occ(
        "prospector",
        "淘金客",
        55,
        (0, 10),
        "climb first_aid history mechanical_repair navigate science_geology spot_hidden",
        [personal()],
        phy,
    )
    occ(
        "prostitute",
        "性工作者",
        55,
        (5, 50),
        "dodge psychology sleight_of_hand stealth",
        [art(), social(2), personal()],
        "app",
    )
    occ(
        "psychiatrist",
        "精神病学家",
        56,
        (30, 80),
        "listen medicine persuade psychoanalysis psychology biology chemistry",
        [language()],
    )
    occ(
        "psychologist",
        "心理学家／心理分析学家",
        56,
        (10, 40),
        "accounting library_use listen persuade psychoanalysis psychology",
        [academics(2)],
    )
    occ(
        "researcher",
        "研究员",
        56,
        (9, 30),
        "history library_use spot_hidden",
        [social(), language(), academics(3)],
    )
    occ(
        "naval_sailor",
        "军舰海员",
        56,
        (9, 30),
        "first_aid navigate pilot_boat survival_sea swim",
        [repair(), fight(), gun()],
        phy,
    )
    occ(
        "commercial_sailor",
        "民用船海员",
        56,
        (20, 40),
        "first_aid mechanical_repair natural_world navigate pilot_boat spot_hidden swim",
        [social()],
        phy,
    )
    occ(
        "salesperson",
        "推销员",
        57,
        (9, 40),
        "accounting drive_auto listen psychology",
        [social(2), either("hands", "stealth sleight_of_hand"), personal()],
        "app",
    )
    occ(
        "scientist",
        "科学家",
        57,
        (9, 50),
        "own_language spot_hidden",
        [spec("science", 3), research(), language(), social()],
    )
    occ(
        "secretary",
        "秘书",
        57,
        (9, 30),
        "accounting own_language psychology",
        [either("office", "art_typing art_shorthand"), social(2), research(), personal()],
        "dex/app",
    )
    occ(
        "shopkeeper",
        "店老板",
        57,
        (20, 40),
        "accounting electrical_repair listen mechanical_repair psychology spot_hidden",
        [social(2)],
        "app/dex",
    )
    occ(
        "spy",
        "间谍",
        58,
        (20, 60),
        "listen psychology sleight_of_hand stealth",
        [either("cover", "art_acting disguise"), gun(), language(), social()],
        "app/dex",
    )
    occ(
        "student",
        "学生／实习生",
        58,
        (5, 10),
        "library_use listen",
        [either("language", "own_language", "language"), academics(3), personal(2)],
    )
    occ(
        "stuntman",
        "替身演员",
        58,
        (10, 50),
        "climb dodge first_aid jump swim",
        [repair(), fight(), either("stunt", "diving drive_auto ride", "pilot")],
        phy,
    )
    occ(
        "undertaker",
        "殡葬师",
        58,
        (20, 40),
        "accounting drive_auto history occult psychology biology chemistry",
        [social()],
    )
    occ(
        "union_activist",
        "工会活动家",
        "58–59",
        (5, 50),
        "accounting brawl law listen operate_heavy_machinery psychology",
        [social(2)],
    )
    occ(
        "waiter",
        "服务生",
        59,
        (9, 20),
        "accounting dodge listen psychology",
        [art(), social(2), personal()],
        "app/dex",
    )
    occ(
        "clerk",
        "职员／主管",
        59,
        (9, 20),
        "accounting law listen",
        [either("language", "own_language", "language"), research(), social(), personal(2)],
    )
    occ(
        "manager",
        "中层／高层管理人员",
        59,
        (20, 80),
        "accounting law psychology",
        [language(), social(2), personal(2)],
    )
    occ(
        "zookeeper",
        "饲养员",
        59,
        (9, 40),
        "animal_handling accounting dodge first_aid natural_world medicine science_pharmacy science_zoology",
    )

    # Existing ordinary occupations cross-checked against the complete handbook index.
    for key, page, printed in [
        ("accountant", 40, 70),
        ("antiquarian", 41, 71),
        ("artist", 41, 72),
        ("athlete", 42, 72),
        ("author", 42, "72–73"),
        ("clergy", 43, 74),
        ("hacker", 44, 74),
        ("dilettante", 46, 78),
        ("doctor", 47, 78),
        ("drifter", 47, 78),
        ("engineer", 48, 79),
        ("entertainer", 48, "79–80"),
        ("farmer", 48, 80),
        ("firefighter", 49, 81),
        ("lawyer", 51, 83),
        ("librarian", 51, 83),
        ("musician", 53, 84),
        ("nurse", 53, 84),
        ("parapsychologist", 53, 85),
        ("pilot", 54, 86),
        ("police_detective", 55, 87),
        ("police_officer", 55, 87),
        ("private_investigator", 55, "87–88"),
        ("professor", 55, 88),
        ("soldier", "57–58", "90"),
        ("tribal_member", 58, 91),
        ("zealot", 59, 92),
    ]:
        coverage[key] = (str(page), printed_range(page))
    rules["notice"] = (
        f"本地规则核对：{len(occupations)}职业、{len(rules['skills'])}目录技能、6类自定义专业。"
        "驾驶须核对载具年代，学问须KP引入且不代替神话；手册普通职业及特例边界见覆盖表。"
        "15–89岁，购点460；未分配技能点确认时放弃。"
    )
    rules["source_reference"].append(
        dict(
            filename="克苏鲁的呼唤第七版调查员手册1.20.pdf",
            section="PDF38–59（职业目录及全部职业数值块）",
            notes="第二十八批逐项覆盖见docs/occupation-coverage.md；同名职业更新沿用ID，特殊可选条款单列。",
        )
    )
    rules["source_reference"].append(
        dict(
            filename="克苏鲁的呼唤第七版规则书1907.pdf",
            section="PDF29–31、48、59–60、63、68",
            notes="学术组允许不同专业；驾驶1、生存10、学问1；学问须KP引入，驾驶载具受年代限制。",
        )
    )
    (DEFINITIONS / "coc7_character_creation.yaml").write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    write_coverage(rules, coverage, original)
    print(
        f"1.2.0: {len(occupations)} occupations; {len(rules['skills'])} catalogue skills; 6 custom groups"
    )


def write_coverage(rules, coverage, original):
    skills = {s["key"]: s["display_name"] for s in rules["skills"]}
    rows = [
        "# 调查员手册职业覆盖表（规则 1.2.0）",
        "",
        "本地《调查员手册1.20》PDF38–39 表4-1／4-2为索引，PDF40–59为正文。PDF页从1起；印刷页为所引扫描页覆盖范围（非条目精确起止），按页内标记记录。只记录数值事实和实现映射，不复制职业描述。",
        "",
        f"共 **{len(rules['occupations'])}** 个职业 ID：原31项＋本批{len(rules['occupations']) - 31}项。索引的总类和别名不单独计数。有独立信用／技能／公式的子职业分别计数。",
        "",
        "`*类别×N` 表示该类别不同专业选N，允许自定义（格斗、射击仅目录）；`任意×N`仍受神话禁购、年代、KP引入及不重复名额限制。未标年代限制者两年代适用。固定技能不含额外的信用评级。",
        "",
        "| 名称／ID | 本地手册PDF／印刷页 | 状态 | 信用 | 固定技能 | 分组与数量／专业类别 | 属性公式／选择 | 年代 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for o in rules["occupations"]:
        key = o["key"]
        page, printed = coverage.get(key, ("—", "—"))
        choices = []
        for g in o["skill_groups"]:
            opts = "、".join(skills[k] for k in g.get("skills", []))
            families = "、".join("*" + k for k in g.get("specialization_groups", []))
            if g.get("any_skill"):
                opts = "任意"
            opts = "、".join(x for x in [opts, families] if x)
            if g["key"] == "academic":
                opts = "学术/个人专业范围（详见下文）"
            choices.append(
                f"{g['key']}: [{opts}]选{g.get('count', 1)}"
                + (
                    "（不同方向）"
                    if g.get("distinct_directions", True)
                    and not g.get("any_skill")
                    and g.get("count", 1) > 1
                    else ""
                )
            )
        status = "已有" if key in original else "新增"
        if key in {"journalist", "military_officer", "missionary"}:
            status = "同ID更新手册条目"
        if key == "criminal":
            status = "保留规则书通用模板；手册分项见下"
        rows.append(
            f"| {o['display_name']} `{key}` | {page}／{printed} | {status} | "
            f"{o['credit_rating_minimum']}–{o['credit_rating_maximum']} | "
            + "、".join(skills[k] for k in o["fixed_skills"])
            + " | "
            + "；".join(choices)
            + f" | {o['point_formula']['display']} | "
            + "／".join(o.get("eras", ["1920s", "modern"]))
            + " |"
        )
    rows += [
        "",
        "## 目录别名与合并",
        "",
        "- Actor → 戏剧演员／电影演员；Criminal → 刺客、银行劫匪、打手／暴徒、窃贼、欺诈师、独行罪犯、女飞贼、赃物贩子、赝造者／伪造者、走私者、混混。规则书通用罪犯模板另保留。",
        "- Driver → 私人司机／司机／出租车司机；Gangster → 老大／马仔；Journalist → 调查记者／通讯记者；Photographer → 摄影师／摄影记者；Pilot → 飞行员／特技飞行员。",
        "- Laborer → 非熟练工人／伐木工／矿工；技师另有数值块；Sailor → 军舰／民用船海员；White-collar Worker → 职员／主管、中层／高层管理人员。",
        "- 管家／男仆／女仆、拳击手／摔跤手、绅士／淑女、学生／实习生、心理学家／心理分析学家、士兵／海军陆战队、计算机程序员／工程师为同一数值模板。Hacker索引重复归到既有黑客。Bootlegger索引翻译有歧义，按手册译注参考走私者，不新增一个猜测模板。",
        "",
        "## 来源差异、范围和具体剩余项",
        "",
        "普通职业数值块均已建立映射。以下**特殊条款／背景范围仍有限制**，因此不声称完整手册所有机制已实现：",
        "",
        "| 条目 | 本次行为 | 剩余条款／依赖 |",
        "| --- | --- | --- |",
        "| 除魅师 PDF46／77 | 普通8技能与现代限制 | 可经KP许可用催眠替换其中一项；需逐卡职业技能替换机制，未启用 |",
        "| 神秘学家 PDF53／85 | 普通8技能；神话不可购点 | 可经KP许可初始获得神话、建议不超过10%；需独立神话获取、SAN及许可规则，不以学问替代 |",
        "| 动物训练师／饲养员 PDF41／71、59／93 | 数值模板可用 | 对动物使用心理学／医学的特殊裁定与完整动物治疗不是新增职业数值所自动实现 |",
        "| 学术／个人、学生学习专业、研究员学术领域 | 沿用受限学术白名单，补science/language/art_craft/lore类别；同类不同专业可选 | 角色背景相关性由KP判断；新职业并未解锁任意职业或任意替换固定技能 |",
        "| 艺人／技师／工匠 | 艺术或工艺专业按相关背景选择 | 技师须选具体工艺、艺人须选表演方向；名称语义由KP核对，不声称自动理解任意名称 |",
        "| 登山家 PDF52／84 | 新目录生存（高山），用于阿尔卑斯或类似环境 | 自定义其它类似环境的职业替换未开放；可作为兴趣专业 |",
        "| 秘书 PDF57／89–90 | 新目录打字／速记，固定二选一 | 非这两项的自定义名称不自动取得此名额 |",
        "| 军官／传教士／记者 | 1.2.0同ID采用手册：军官急救；传教士EDU×2+APP×2且增加急救；调查记者母语及美术/摄影选一 | 规则书版本仍在1.1.0原样归档，旧草稿/快照不迁移 |",
        "| 精神病学家 PDF56／88 | 两年代；表头无现代限定，正文还说明早期发展 | 古典精神病医生有单独模板；未凭首句的现代描述把全条目强行标现代 |",
        "| 经历包 PDF35／61–62 | 未启用 | 战场、警务、罪犯、医务、神话包的年龄、SAN、额外点数、免疫与法术 |",
        "",
        "职业数据不会授予武器、法术、关系人或装备；格斗／射击仅已有目录、武器特例仍按原边界。90+、跨专业可选加值及完整装备另批处理。",
        "",
    ]
    (ROOT / "docs/occupation-coverage.md").write_text("\n".join(rows), encoding="utf-8")


if __name__ == "__main__":
    build()
