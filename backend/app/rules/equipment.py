"""Small equipment catalogue; weapon numbers reuse the verified combat panel."""

from app.rooms.combat_schemas import Weapon

WEAPONS = {
    "club": dict(name="小棍棒", skill="brawl", damage="1d6"),
    "knife": dict(name="小刀", skill="brawl", damage="1d4", impale=True),
    "revolver": dict(
        name=".38 左轮", skill="handgun", kind="firearm", damage="1d10", impale=True, capacity=6
    ),
    "pistol": dict(
        name=".38 自动手枪",
        skill="handgun",
        kind="firearm",
        damage="1d10",
        impale=True,
        capacity=8,
        malfunction=99,
    ),
}
CATALOG = [
    dict(id=key, name=name, eras=eras, weapon=False)
    for key, name, eras in [
        ("notebook", "笔记本", ["1920s", "modern"]),
        ("pen", "钢笔", ["1920s", "modern"]),
        ("camera", "照相机", ["1920s", "modern"]),
        ("torch", "手电筒", ["1920s", "modern"]),
        ("rope", "绳索", ["1920s", "modern"]),
        ("first_aid_kit", "急救包", ["1920s", "modern"]),
        ("toolkit", "工具箱", ["1920s", "modern"]),
        ("watch", "手表", ["1920s", "modern"]),
        ("phone", "手机", ["modern"]),
    ]
] + [
    dict(id=key, name=w["name"], eras=["1920s", "modern"], weapon=True)
    for key, w in WEAPONS.items()
]


def validate_equipment(character, issue):
    if len({e.id for e in character.equipment}) != len(character.equipment):
        issue("equipment", "duplicate", "装备条目ID不能重复")
    catalog = {e["id"]: e for e in CATALOG}
    count = 0
    for e in character.equipment:
        if e.catalog_id and e.catalog_id not in catalog:
            issue("equipment", "unknown", "装备目录条目不存在")
        elif e.catalog_id:
            if character.era not in catalog[e.catalog_id]["eras"]:
                issue("equipment", "era", "装备不适用于所选年代")
            if e.name != catalog[e.catalog_id]["name"]:
                issue("equipment", "name", "目录装备名称须与模板一致；其他物品请用自定义装备")
            if e.catalog_id in WEAPONS:
                count += e.quantity
    if count > 19:
        issue("equipment", "limit", "基础战斗最多携带19件武器（另保留徒手）")
    if sum(e.quantity for e in character.equipment) > 100:
        issue("equipment", "limit", "装备总数量最多100件")


def equipment_weapon(catalog_id, instance_id):
    if catalog_id not in WEAPONS:
        return None
    return Weapon(
        id=instance_id, **WEAPONS[catalog_id], source="1907规则书PDF372–373；沿用已核对基础战斗模板"
    )
