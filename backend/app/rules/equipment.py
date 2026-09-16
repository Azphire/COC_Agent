"""Shared, sourced equipment catalogue. See docs/equipment-coverage.md."""

from app.rooms.combat_schemas import Weapon, unarmed

MELEE_SOURCE = "规则书1907 PDF372／印刷371 表XVII；手册1.20 PDF154／印刷250–251"
GUN_SOURCE = (
    "规则书1907 PDF373／印刷372 表XVII；射程单位见PDF376／印刷375；"
    "交叉核对手册PDF154–155／印刷250–253"
)


def melee(name, skill, damage, *, impale=True, uses_db=True, source=MELEE_SOURCE):
    return dict(
        name=name,
        skill=skill,
        kind="melee",
        damage=damage,
        impale=impale,
        uses_db=uses_db,
        base_range="近",
        source=source,
    )


def gun(name, skill, damage, capacity, malfunction, yards):
    return dict(
        name=name,
        skill=skill,
        kind="firearm",
        damage=damage,
        impale=True,
        uses_db=False,
        capacity=capacity,
        malfunction=malfunction,
        base_range=f"{yards}码",
        source=GUN_SOURCE,
    )


# Existing four IDs and combat values are unchanged.
WEAPONS = {
    "club": melee("小棍棒", "brawl", "1d6", impale=False),
    "knife": melee("小刀", "brawl", "1d4"),
    "revolver": gun(".38 左轮", "handgun", "1d10", 6, 100, 15),
    "pistol": gun(".38 自动手枪", "handgun", "1d10", 8, 99, 15),
    "sword_light": melee("轻型剑（花剑、剑杖）", "fighting_sword", "1d6"),
    "sword_medium": melee("中型剑（佩剑、重剑）", "fighting_sword", "1d6+1"),
    "sword_large": melee(
        "大型剑（马刀）",
        "fighting_sword",
        "1d8+1",
        source=MELEE_SOURCE + "；贯穿按1907表√，手册表否存在差异",
    ),
    "hatchet": melee("手斧／手镰", "fighting_axe", "1d6+1"),
    "wood_axe": melee("伐木斧", "fighting_axe", "1d8+2"),
    # Both local tables explicitly omit DB. Do not infer +DB.
    "spear": melee("矛、骑士长枪", "fighting_spear", "1d8+1", uses_db=False),
    "rifle_22": gun(".22 栓式枪机步枪", "firearms_rifle_shotgun", "1d6+1", 6, 99, 30),
    "rifle_30": gun(".30 杠杆式枪机步枪", "firearms_rifle_shotgun", "2d6", 6, 98, 50),
    "rifle_303": gun(".303 李-恩菲尔德步枪", "firearms_rifle_shotgun", "2d6+4", 10, 100, 110),
    "rifle_3006": gun(".30-06 手动枪机步枪", "firearms_rifle_shotgun", "2d6+4", 5, 100, 110),
}

SKILL_NAMES = {
    "brawl": "格斗（斗殴）",
    "handgun": "射击（手枪）",
    "fighting_sword": "格斗（剑）",
    "fighting_axe": "格斗（斧）",
    "fighting_spear": "格斗（矛）",
    "firearms_rifle_shotgun": "射击（步枪／霰弹枪）",
}


def equipment_weapon(catalog_id, instance_id, *, ammo=0, reserve=0, ready=False):
    if catalog_id == "unarmed":
        if ammo or reserve:
            raise ValueError("徒手不能配置弹药")
        return unarmed()
    if catalog_id not in WEAPONS:
        return None
    template = WEAPONS[catalog_id]
    if template["kind"] != "firearm" and (ammo or reserve):
        raise ValueError("只有枪械能配置弹药")
    return Weapon(
        id=instance_id, template_id=catalog_id, **template, ammo=ammo, reserve=reserve, ready=ready
    )


def weapon_definition(key):
    w = equipment_weapon(key, key)
    return dict(
        id=key,
        name=w.name,
        eras=["1920s", "modern"],
        weapon=True,
        category="枪械" if w.kind == "firearm" else "近战武器",
        description="每次行动一发；散装弹装填沿用每行动最多两发。"
        if w.kind == "firearm"
        else "普通近战攻击；用途以实际场景为准。",
        source=w.source,
        skill_name=SKILL_NAMES[w.skill],
        weapon_template=w.model_dump(
            exclude={"id", "ammo", "reserve", "ready", "jammed", "quantity"}
        ),
    )


# Selection aids only: descriptions never grant bonuses, healing or special effects.
CATALOG = [
    dict(
        id=key,
        name=name,
        eras=eras,
        weapon=False,
        category=category,
        description=description,
        source=source,
        weapon_template=None,
        skill_name=None,
    )
    for key, name, category, description, source, eras in [
        (
            "notebook",
            "笔记本",
            "记录",
            "记录线索和访谈。",
            "原目录保留；规则书PDF33–37一般背景装备；非武器表数值",
            ["1920s", "modern"],
        ),
        (
            "pen",
            "钢笔",
            "记录",
            "书写笔记。",
            "手册PDF149／印刷243–244，自来水笔",
            ["1920s", "modern"],
        ),
        (
            "camera",
            "照相机",
            "记录",
            "拍摄现场与资料。",
            "手册PDF150／印刷245–246、PDF152／印刷247–249",
            ["1920s", "modern"],
        ),
        (
            "torch",
            "手电筒",
            "照明",
            "照亮近处环境。",
            "手册PDF148／印刷242–243",
            ["1920s", "modern"],
        ),
        (
            "rope",
            "绳索",
            "户外",
            "捆扎或辅助攀爬。",
            "手册PDF148、152／印刷242–243、248–249",
            ["1920s", "modern"],
        ),
        (
            "first_aid_kit",
            "急救包",
            "医疗",
            "携带急救材料；治疗仍走急救规则。",
            "手册PDF147医药箱、152完整急救箱／印刷240–241、248–249",
            ["1920s", "modern"],
        ),
        (
            "toolkit",
            "工具箱",
            "工具",
            "常用维修工具。",
            "手册PDF148、152／印刷242–243、248–249",
            ["1920s", "modern"],
        ),
        (
            "watch",
            "手表",
            "记录",
            "查看和记录时间。",
            "手册PDF149／印刷243–244，腕表",
            ["1920s", "modern"],
        ),
        ("phone", "手机", "通讯", "条件允许时通话和记录。", "手册PDF151／印刷246–247", ["modern"]),
        *[
            (
                key,
                name,
                category,
                description,
                "手册PDF148／印刷242–243，1920年代清单；现代沿用普通物品",
                ["1920s", "modern"],
            )
            for key, name, category, description in [
                ("binoculars", "双筒望远镜", "调查", "观察远处可见事物。"),
                ("compass", "指南针", "户外", "辨认方向。"),
                ("backpack", "帆布背包", "户外", "收纳随身物品；不增加自动负重额度。"),
                ("matches", "防水盒装火柴", "照明", "点燃合适的燃料。"),
                ("candles", "蜡烛", "照明", "提供小范围照明。"),
                ("canteen", "水壶", "户外", "携带饮水。"),
                ("crowbar", "撬棍", "工具", "尝试撬动物件；不附带武器数值。"),
                ("shovel", "工兵铲", "工具", "挖掘土壤；不附带武器数值。"),
                ("bandage", "纱布绷带", "医疗", "包扎材料；不自动恢复HP。"),
                ("handcuffs", "手铐", "调查", "拘束工具；不自动擒抱或制服目标。"),
            ]
        ],
    ]
] + [weapon_definition(key) for key in WEAPONS]


def validate_equipment(character, issue):
    if len({e.id for e in character.equipment}) != len(character.equipment):
        issue("equipment", "duplicate", "装备条目ID不能重复")
    catalog = {e["id"]: e for e in CATALOG}
    count = 0
    for e in character.equipment:
        weapon = WEAPONS.get(e.catalog_id)
        if (e.initial_ammo or e.initial_reserve) and (not weapon or weapon["kind"] != "firearm"):
            issue("equipment", "ammo_kind", "仅目录枪械允许初始已装弹／备弹")
        if weapon and e.initial_ammo > weapon.get("capacity", 0):
            issue("equipment", "ammo_capacity", "每件武器初始已装弹不能超过容量")
        if e.catalog_id and e.catalog_id not in catalog:
            issue("equipment", "unknown", "装备目录条目不存在")
        elif e.catalog_id:
            if character.era not in catalog[e.catalog_id]["eras"]:
                issue("equipment", "era", "装备不适用于所选年代")
            if e.name != catalog[e.catalog_id]["name"]:
                issue("equipment", "name", "目录装备名称须与模板一致；其他物品请用自定义装备")
            if weapon:
                count += e.quantity
                if weapon["skill"] not in character.skill_values:
                    issue(
                        "equipment",
                        "weapon_skill",
                        f"缺少武器对应技能：{SKILL_NAMES[weapon['skill']]}",
                    )
    if count > 19:
        issue("equipment", "limit", "基础战斗最多携带19件武器（另保留徒手）")
    if sum(e.quantity for e in character.equipment) > 100:
        issue("equipment", "limit", "装备总数量最多100件")
