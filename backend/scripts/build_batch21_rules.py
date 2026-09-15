"""Reproducible catalogue derived from local 1907 PDF 29–31,47 and handbook pages.

Never modifies the archived 1.0.0 rules. See definitions/SOURCES.md for scope.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "app/rules/definitions"


def build():
    rules = yaml.safe_load((ROOT / "legacy/coc7_character_creation-1.0.0.yaml").read_text("utf8"))
    rules["version"] = "1.1.0"
    rules["notice"] = (
        "按本地规则核对：基础技能及可选专业、28项基础职业和3项手册职业；"
        "1920年代/现代信用资产。15–89岁；90岁以上无完整本地调整条款。"
        "购点460，未分配技能点确认时放弃。"
    )
    skills = {s["key"]: s for s in rules["skills"]}

    def skill(key, name, base, category, family=None, modern=False):
        skills.setdefault(
            key, dict(key=key, display_name=name, base_value=base, maximum=99, category=category)
        )
        if family:
            skills[key]["specialization_group"] = family
        if modern:
            skills[key]["eras"] = ["modern"]

    for key, name, base, cat in [
        ("animal_handling", "动物驯养", 5, "实践"),
        ("appraise", "估价", 5, "知识"),
        ("artillery", "炮术", 1, "战斗"),
        ("climb", "攀爬", 20, "行动"),
        ("computer_use", "计算机使用", 5, "技术"),
        ("demolitions", "爆破", 1, "技术"),
        ("disguise", "乔装", 5, "社交"),
        ("diving", "潜水", 1, "行动"),
        ("drive_auto", "汽车驾驶", 20, "驾驶"),
        ("electrical_repair", "电气维修", 10, "技术"),
        ("electronics", "电子学", 1, "技术"),
        ("hypnosis", "催眠", 1, "实践"),
        ("jump", "跳跃", 20, "行动"),
        ("locksmith", "锁匠", 1, "技术"),
        ("mechanical_repair", "机械维修", 10, "技术"),
        ("navigate", "导航", 10, "探索"),
        ("operate_heavy_machinery", "操作重型机械", 1, "技术"),
        ("psychoanalysis", "精神分析", 1, "实践"),
        ("lip_reading", "读唇", 1, "实践"),
        ("ride", "骑术", 5, "行动"),
        ("sleight_of_hand", "妙手", 10, "行动"),
        ("swim", "游泳", 20, "行动"),
        ("track", "追踪", 10, "探索"),
    ]:
        skill(key, name, base, cat, modern=key in {"computer_use", "electronics"})
    families = {
        "language": (
            "其他语言",
            "语言",
            1,
            [
                ("language_latin", "拉丁语"),
                ("language_english", "英语"),
                ("language_french", "法语"),
                ("language_german", "德语"),
                ("language_chinese", "汉语"),
                ("language_japanese", "日语"),
                ("language_spanish", "西班牙语"),
                ("language_arabic", "阿拉伯语"),
                ("language_greek", "古希腊语"),
                ("language_russian", "俄语"),
            ],
        ),
        "art_craft": (
            "艺术与手艺",
            "艺术",
            5,
            [
                ("photography", "摄影"),
                ("art_acting", "表演"),
                ("art_fine_art", "美术"),
                ("art_forgery", "伪造"),
                ("art_literature", "文学"),
                ("art_technical_drawing", "设计图纸"),
                ("art_farming", "农事"),
                ("art_piano", "钢琴"),
                ("art_violin", "小提琴"),
                ("art_singing", "歌唱"),
                ("art_carpentry", "木工"),
                ("art_cooking", "烹饪"),
            ],
        ),
        "science": (
            "科学",
            "科学",
            1,
            [
                ("biology", "生物学"),
                ("chemistry", "化学"),
                ("science_astronomy", "天文学"),
                ("science_botany", "植物学"),
                ("science_cryptography", "密码学"),
                ("science_engineering", "工程学"),
                ("science_forensics", "司法科学"),
                ("science_geology", "地质学"),
                ("science_mathematics", "数学"),
                ("science_meteorology", "气象学"),
                ("science_pharmacy", "药学"),
                ("science_physics", "物理学"),
                ("science_zoology", "动物学"),
            ],
        ),
        "fighting": (
            "格斗",
            "战斗",
            1,
            [
                ("brawl", "斗殴"),
                ("fighting_axe", "斧"),
                ("fighting_chainsaw", "链锯"),
                ("fighting_flail", "连枷"),
                ("fighting_garrote", "绞索"),
                ("fighting_spear", "矛"),
                ("fighting_sword", "剑"),
                ("fighting_whip", "鞭"),
            ],
        ),
        "firearms": (
            "射击",
            "战斗",
            1,
            [
                ("handgun", "手枪"),
                ("firearms_bow", "弓"),
                ("firearms_flamethrower", "火焰喷射器"),
                ("firearms_heavy", "重武器"),
                ("firearms_machine_gun", "机枪"),
                ("firearms_rifle_shotgun", "步枪/霰弹枪"),
                ("firearms_smg", "冲锋枪"),
            ],
        ),
        "pilot": ("驾驶", "驾驶", 1, [("pilot_aircraft", "飞行器"), ("pilot_boat", "船")]),
        "survival": (
            "生存",
            "探索",
            10,
            [
                ("survival_desert", "沙漠"),
                ("survival_forest", "森林"),
                ("survival_arctic", "极地"),
                ("survival_sea", "海洋"),
            ],
        ),
        "lore": ("学问", "知识", 1, [("lore_folklore", "民俗")]),
    }
    bases = dict(
        science_mathematics=10,
        brawl=25,
        fighting_axe=15,
        fighting_chainsaw=10,
        fighting_flail=10,
        fighting_garrote=15,
        fighting_spear=20,
        fighting_sword=20,
        fighting_whip=5,
        handgun=20,
        firearms_bow=15,
        firearms_flamethrower=10,
        firearms_heavy=10,
        firearms_machine_gun=10,
        firearms_rifle_shotgun=25,
        firearms_smg=15,
    )
    for family, (label, cat, base, entries) in families.items():
        for key, name in entries:
            skill(key, f"{label}（{name}）", bases.get(key, base), cat, family)
    rules["skills"] = list(skills.values())
    occupations = []

    def group(key, count=1, ids=(), families=(), any_skill=False, label=None):
        return dict(
            key=key,
            display_name=label or key,
            count=count,
            skills=list(ids),
            specialization_groups=list(families),
            any_skill=any_skill,
        )

    def social(n=1):
        return group(
            "social", n, ["charm", "fast_talk", "intimidate", "persuade"], label="社交技能"
        )

    def lang():
        return group("language", families=["language"], label="其他语言专业")

    def art():
        return group("art", families=["art_craft"], label="艺术/手艺专业")

    def fight():
        return group("fighting", families=["fighting"], label="格斗专业")

    def gun():
        return group("firearms", families=["firearms"], label="射击专业")

    def survival():
        return group("survival", families=["survival"], label="生存环境")

    def anygroup(n):
        return group("personal", n, any_skill=True, label="个人或时代特长")

    def academic(n):
        return group(
            "academic",
            n,
            ids=[
                s["key"]
                for s in skills.values()
                if s["category"] in {"知识", "科学", "艺术", "实践", "社交", "语言"}
                and s.get("allocatable", True)
            ],
            label="学术/个人专业（按人物背景选择）",
        )

    def occ(key, name, fixed, groups, credit, choice=(), extra=None, page=29, handbook=False):
        formula = dict(fixed={"edu": 4}, display="EDU×4")
        if choice or extra:
            formula = dict(
                fixed={"edu": 2, **({extra: 2} if extra else {})},
                choice_attributes=list(choice),
                choice_multiplier=2,
                display="EDU×2 + "
                + (" / ".join(a.upper() for a in choice) if choice else extra.upper())
                + "×2",
            )
        occupations.append(
            dict(
                key=key,
                display_name=name,
                fixed_skills=fixed.split(),
                skill_groups=groups,
                credit_rating_minimum=credit[0],
                credit_rating_maximum=credit[1],
                point_formula=formula,
                source=f"{'调查员手册1.20' if handbook else '规则书1907'} PDF {page}",
            )
        )

    physical = ["dex", "str"]
    occ(
        "antiquarian",
        "古文物学家",
        "appraise history library_use spot_hidden",
        [art(), lang(), social(), anygroup(1)],
        (30, 70),
    )
    occ(
        "artist",
        "艺术家",
        "psychology spot_hidden",
        [
            art(),
            group("knowledge", ids=["history", "natural_world"], label="历史或博物学"),
            lang(),
            social(),
            anygroup(2),
        ],
        (9, 50),
        ["pow", "dex"],
    )
    occ(
        "athlete",
        "运动员",
        "climb jump brawl ride swim throw",
        [social(), anygroup(1)],
        (9, 70),
        physical,
    )
    occ(
        "author",
        "作家",
        "art_literature history library_use own_language psychology",
        [
            group("knowledge", ids=["natural_world", "occult"], label="博物学或神秘学"),
            lang(),
            anygroup(1),
        ],
        (9, 30),
    )
    occ(
        "clergy",
        "神职人员",
        "accounting history library_use listen psychology",
        [lang(), social(), anygroup(1)],
        (9, 60),
    )
    occ(
        "criminal",
        "罪犯",
        "psychology spot_hidden stealth",
        [
            social(),
            group(
                "criminal",
                4,
                ["appraise", "disguise", "locksmith", "mechanical_repair", "sleight_of_hand"],
                ["fighting", "firearms"],
                label="犯罪特长（选四个不同方向）",
            ),
        ],
        (5, 65),
        physical,
    )
    occ(
        "dilettante",
        "业余艺术爱好者",
        "ride",
        [art(), gun(), lang(), social(), anygroup(3)],
        (50, 99),
        extra="app",
    )
    occ(
        "doctor",
        "医生",
        "first_aid language_latin medicine psychology biology science_pharmacy",
        [academic(2)],
        (30, 80),
    )
    occ(
        "drifter",
        "流浪者",
        "climb jump listen navigate stealth",
        [social(), anygroup(2)],
        (0, 5),
        ["app", "dex", "str"],
    )
    occ(
        "engineer",
        "工程师",
        "art_technical_drawing electrical_repair library_use mechanical_repair "
        "operate_heavy_machinery science_engineering science_physics",
        [anygroup(1)],
        (30, 60),
    )
    occ(
        "entertainer",
        "艺人",
        "art_acting disguise listen psychology",
        [social(2), anygroup(2)],
        (9, 70),
        extra="app",
    )
    occ(
        "farmer",
        "农民",
        "art_farming drive_auto mechanical_repair natural_world operate_heavy_machinery track",
        [
            social(),
            anygroup(1),
        ],
        (9, 30),
        physical,
        page=30,
    )
    occ(
        "hacker",
        "黑客",
        "computer_use electrical_repair electronics library_use spot_hidden",
        [social(), anygroup(2)],
        (10, 70),
        page=30,
    )
    occupations[-1]["eras"] = ["modern"]
    occ(
        "journalist",
        "记者",
        "photography history library_use psychology",
        [lang(), social(), anygroup(2)],
        (9, 30),
        page=30,
    )
    occ(
        "lawyer",
        "律师",
        "accounting law library_use psychology",
        [social(2), anygroup(2)],
        (30, 80),
        page=30,
    )
    occ(
        "librarian",
        "图书馆管理员",
        "accounting library_use own_language",
        [lang(), academic(4)],
        (9, 35),
        page=30,
    )
    occ(
        "military_officer",
        "军官",
        "accounting navigate psychology",
        [gun(), social(2), survival(), anygroup(1)],
        (20, 70),
        physical,
        page=30,
    )
    occ(
        "missionary",
        "传教士",
        "mechanical_repair medicine natural_world",
        [art(), social(), anygroup(2)],
        (0, 30),
        page=30,
    )
    occ(
        "musician",
        "音乐家",
        "listen psychology",
        [
            group("instrument", ids=["art_piano", "art_violin"], label="乐器专业"),
            social(),
            anygroup(4),
        ],
        (9, 30),
        ["dex", "pow"],
        page=30,
    )
    occ(
        "parapsychologist",
        "超心理学家",
        "anthropology photography history library_use occult psychology",
        [lang(), anygroup(1)],
        (9, 30),
        page=30,
    )
    occ(
        "pilot",
        "飞行员",
        "electrical_repair mechanical_repair navigate operate_heavy_machinery "
        "pilot_aircraft science_astronomy",
        [anygroup(2)],
        (20, 70),
        extra="dex",
        page=30,
    )
    occ(
        "police_detective",
        "警探",
        "law listen psychology spot_hidden",
        [
            group("disguise", ids=["art_acting", "disguise"], label="表演或乔装"),
            gun(),
            social(),
            anygroup(1),
        ],
        (20, 50),
        physical,
        page=30,
    )
    occ(
        "police_officer",
        "警察",
        "brawl first_aid law psychology spot_hidden",
        [gun(), social(), group("transport", ids=["drive_auto", "ride"], label="汽车驾驶或骑术")],
        (9, 30),
        physical,
        page=30,
    )
    occ(
        "private_investigator",
        "私家侦探",
        "photography disguise law library_use psychology spot_hidden",
        [social(), anygroup(1)],
        (9, 30),
        physical,
        page=30,
    )
    occ(
        "professor",
        "教授",
        "library_use own_language psychology",
        [lang(), academic(4)],
        (20, 70),
        page=31,
    )
    occ(
        "soldier",
        "士兵",
        "dodge stealth",
        [
            group("physical", ids=["climb", "swim"], label="攀爬或游泳"),
            fight(),
            gun(),
            survival(),
            group(
                "training",
                2,
                ["first_aid", "mechanical_repair"],
                ["language"],
                label="急救/机械维修/其他语言中选二",
            ),
        ],
        (9, 30),
        physical,
        page=31,
    )
    occ(
        "tribal_member",
        "部落成员",
        "climb natural_world listen occult spot_hidden swim",
        [group("combat", ids=["throw"], families=["fighting"], label="格斗或投掷"), survival()],
        (0, 15),
        physical,
        page=31,
    )
    occ(
        "zealot",
        "狂热者",
        "history psychology stealth",
        [social(2), anygroup(3)],
        (0, 30),
        ["app", "pow"],
        page=31,
    )
    occ(
        "accountant",
        "会计师",
        "accounting law library_use listen persuade spot_hidden",
        [anygroup(2)],
        (30, 70),
        page=40,
        handbook=True,
    )
    occ(
        "firefighter",
        "消防员",
        "climb dodge drive_auto first_aid jump mechanical_repair operate_heavy_machinery throw",
        [],
        (9, 30),
        physical,
        page=49,
        handbook=True,
    )
    occ(
        "nurse",
        "护士",
        "first_aid listen medicine psychology biology chemistry spot_hidden",
        [social()],
        (9, 30),
        page=53,
        handbook=True,
    )
    for occupation in occupations:
        if occupation["key"] in {"professor", "librarian"}:
            occupation["legacy_selection_group"] = "academic"
            occupation["legacy_group_defaults"] = {"language": ["language_latin"]}
    rules["occupations"] = occupations
    rules["source_reference"] += [
        dict(
            filename="克苏鲁的呼唤第七版规则书1907.pdf",
            section="PDF29–31、33–37、47–69",
            notes="第二十一批基础职业、技能与专业、背景、两年代信用资产；专业只在选择后分配。",
        ),
        dict(
            filename="克苏鲁的呼唤第七版调查员手册1.20.pdf",
            section="PDF40、49、53",
            notes="会计师、消防员、护士；有限目录，不代表全手册。",
        ),
    ]
    (ROOT / "coc7_character_creation.yaml").write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf8"
    )
    print(f"{len(skills)} skills, {len(occupations)} occupations")


if __name__ == "__main__":
    build()
