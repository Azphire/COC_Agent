"""Derive independent batch17 packages from the immutable locally audited source package."""

from copy import deepcopy

from module_package import ROOT, read, write

DEST = ROOT / "data/prepared/changan/batch-17"


def build():
    DEST.mkdir(parents=True, exist_ok=True)
    p = read(ROOT / "data/prepared/changan/batch-16/package.json")
    entities = {e["key"]: e for e in p["entities"]}
    fields = {key: e["fields"] for key, e in entities.items()}
    blocks = {b["source_order"]: b["block_id"] for b in p["ir"]["blocks"]}
    p["title"] = "常暗之厢 · 第十七批原稿数值版"
    fields["staff"]["keeper_summary"] = (
        "原稿姓名：京山人吉；公开职业称呼：乘务员。" + fields["staff"]["keeper_summary"]
    )
    fields["staff"]["title"] = "乘务员"
    p["reviewer"] = "Codex 第十七批本地核对（非用户批准数值）"
    p["required_npc_operations"] = {"staff": ["treatment"], "clicker": ["combat"]}
    p["batch17"] = {
        "numeric_variant": "original",
        "user_approved": False,
        "source_package": "batch-16/package.json",
    }
    for f in fields.values():
        f["reviewed_by"] = p["reviewer"]
        for effect in f.get("sanity_effects", []):
            if effect.get("automation") == "host_review":
                effect["kp_enabled"] = True
        for rule in f.get("interactions", []):
            rule["kp_enabled"] = True
            rule["situation"] = rule["instruction"]

    def add(key, rid, instruction, orders, **kwargs):
        source = [blocks[i] for i in orders]
        fields[key]["source_block_ids"] = list(
            dict.fromkeys([*fields[key].get("source_block_ids", []), *source])
        )
        rule = dict(
            id=rid,
            instruction=instruction,
            situation=instruction,
            source_block_ids=source,
            kp_enabled=True,
            public_result=kwargs.pop("public_result", instruction),
            **kwargs,
        )
        fields[key].setdefault("interactions", []).append(rule)
        return rule

    def rule(key, rid):
        return next(r for r in fields[key]["interactions"] if r["id"] == rid)

    for key in ("keys", "phone", "torch"):
        for op, label in (
            ("give", "实际将本人持有的物品交给指定队友"),
            ("drop", "实际把本人持有物品放在当前场景"),
            ("pickup", "拾取实际放在当前场景的物品"),
        ):
            add(
                key,
                op,
                label,
                [22, 92, 102, 106],
                inventory_operation=op,
                item_id=key,
                public_result={
                    "give": "物品已交给指定队友。",
                    "drop": "物品已放在这里。",
                    "pickup": "你拾起了物品。",
                }[op],
            )

    add(
        "note_front",
        "turn_over",
        "实际揭下便签并翻看背面，无需检定",
        [
            b["source_order"]
            for b in p["ir"]["blocks"]
            if b["block_id"] in fields["note_back"]["source_block_ids"]
        ],
        reveal_entity_ids=["note_back"],
        public_result=fields["note_back"]["public_summary"],
    )

    for key in ("phone", "torch"):
        keep = rule(key, "keep_" + key)
        keep.update(
            check_name="luck",
            inventory_operation="recover",
            item_id=key,
            acquire_item_ids=[],
            scene_node_ids=entities["s3"]["node_ids"],
            once_per_actor=True,
            instruction="在3号车厢寻找并拿回原先丢失的" + fields[key]["title"],
            situation="本次实际寻找自己原先携带的物品；仅在3号车厢，须原稿幸运减暗骰1d20检定。",
        )
        keep["kp_enabled"] = False
        add(
            "s3",
            "recover_" + key,
            "在3号车厢实际寻找并拿回起始幸运失败丢失的" + fields[key]["title"],
            [
                b["source_order"]
                for b in p["ir"]["blocks"]
                if b["block_id"] in keep["source_block_ids"]
            ],
            check_name="luck",
            inventory_operation="recover",
            item_id=key,
            reveal_entity_ids=[key],
            once_per_actor=True,
            public_result="你找回了起初遗失的" + fields[key]["title"] + "。",
        )
        # Belonging choices are explicit actions against the revealed initial scene.
        add(
            "s6",
            "initial_" + key,
            "醒来后实际检查原先携带的" + fields[key]["title"],
            [22, 23, 24, 37],
            check_name="luck",
            inventory_operation="initial",
            item_id=key,
            once_per_actor=True,
            failure_interaction_id="lost_" + key,
            reveal_entity_ids=[key],
            public_result="随身物已按本次幸运结果登记；手机没有信号。",
        )
        add(
            "s6",
            "lost_" + key,
            "本次起始幸运失败，物品未保留",
            [22, 37],
            check_name="luck",
            check_passed=False,
            inventory_operation="initial",
            item_id=key,
            public_result="你没有找到原先携带的物品；结果已记录。",
        )

    recovery = next(
        a for a in fields["phone"]["check_adjustments"] if a.get("subtract_die") == "1d20"
    )
    fields["s3"].setdefault("check_adjustments", []).append(deepcopy(recovery))
    fields["s3"]["source_block_ids"] = list(
        dict.fromkeys([*fields["s3"]["source_block_ids"], *recovery["source_block_ids"]])
    )

    add(
        "phone",
        "ring",
        "实际开启本人手机铃声作为持续声源",
        [24, 126, 129, 135],
        required_item_ids=["phone"],
        encounter_operation="sound_start",
        sound_item_id="phone",
        set_flags={"continuous_sound": True},
        public_result="手机持续响起铃声。",
    )
    add(
        "phone",
        "silence",
        "实际关闭正在响的手机铃声",
        [135],
        required_item_ids=["phone"],
        encounter_operation="sound_stop",
        sound_item_id="phone",
        set_flags={"continuous_sound": False},
        public_result="手机铃声停止。",
    )

    # Preserve the existing scenario's source choices; make their effects durable.
    staff = rule("staff", "follow")
    staff.update(
        check_name="str",
        carry_attribute="str",
        encounter_operation="carry",
        npc_id="staff",
        situation="乘务员腿伤无法自行走动；本次实际背起。行动者STR低于70须力量检定，达到70免检定。",
    )
    add(
        "staff",
        "put_down",
        "实际把本人背着的乘务员放在当前场景",
        [92, 94],
        encounter_operation="put_down",
        npc_id="staff",
        set_flags={"staff_following": False},
        public_result="你放下了乘务员。",
    )
    add(
        "staff",
        "carry_con",
        "继续背负乘务员经过一节车厢或五分钟时承受体力负担",
        [94],
        check_name="con",
        carry_attribute="con",
        encounter_operation="carry_check",
        npc_id="staff",
        public_result="背负伤员的体力结算已完成。",
    )
    clicker = fields["clicker"]["combat_template"]
    clicker["traits"].append("blind_sound_tracking")
    for rid in ("sound_lure", "distant_sound_lure", "near_sound_lure_dex", "near_sound_lure_throw"):
        rule("clicker", rid)["encounter_operation"] = "sound_once"
    rule("clicker", "sound_lure")["allow_worn_sound_item"] = True
    rule("clicker", "sound_lure")["situation"] = (
        "本次实际将穿着的鞋或已持有物砸向远处墙壁制造碰撞声；首次应急声响诱导自动成功。光本身不会吸引失明的循声者。"
    )
    rule("clicker", "distant_sound_lure")["situation"] = (
        "已有场景事实明确声源与循声者距离超过半节车厢，实际扔物制造撞击声；未知距离先询问。"
    )
    rule("clicker", "evade")["failure_interaction_id"] = "caught"
    add(
        "clicker",
        "caught",
        "实际敏捷对抗失败，被本次对手抓住",
        [136, 138, 139, 217],
        check_name="dex",
        check_passed=False,
        opposed_npc_id="clicker",
        encounter_operation="grab",
        npc_id="clicker",
        public_result="本次追上的循声者抓住了你。",
    )
    rule("clicker", "break_grab").update(encounter_operation="release", max_npc_count=1)
    # Sound methods remain usable when only the passage/breathing is perceived.
    for r in fields["clicker"]["interactions"]:
        if "sound_lure" in r["id"]:
            fields["quiet_passage"]["interactions"].append(deepcopy(r))
            fields["breathing"].setdefault("interactions", []).append(deepcopy(r))
            fields["breathing"]["source_block_ids"] = list(
                dict.fromkeys([*fields["breathing"]["source_block_ids"], *r["source_block_ids"]])
            )
            scene_rule = deepcopy(r)
            scene_rule["scene_node_ids"] = entities["s2"]["node_ids"]
            fields["s2"].setdefault("interactions", []).append(scene_rule)
            fields["s2"]["source_block_ids"] = list(
                dict.fromkeys([*fields["s2"]["source_block_ids"], *r["source_block_ids"]])
            )
            fields["quiet_passage"]["source_block_ids"] = list(
                dict.fromkeys(
                    [*fields["quiet_passage"]["source_block_ids"], *r["source_block_ids"]]
                )
            )
    add(
        "quiet_passage",
        "continuous_lure",
        "利用已在响的持续声源引开循声者，实际通过它们让开的通道",
        [126, 129, 135],
        encounter_operation="continuous_lure",
        required_flags={"continuous_sound": True},
        set_flags={"safe_passage": True, "clickers_lured": True},
        public_result="持续的声响引开了循声者，你获得通过的机会。",
    )
    for light in ("phone", "torch"):
        add(
            "s2",
            "look_with_" + light,
            "实际借助已开启的照明观察喘息来源；KP须核实实际距离和角度足以看清非人形态，仅听到声音或灯亮不算目睹。",
            [
                b["source_order"]
                for b in p["ir"]["blocks"]
                if b["block_id"] in fields["clicker"]["source_block_ids"]
            ][:20],
            scene_node_ids=entities["s2"]["node_ids"],
            required_flags={light + "_light": True},
            reveal_entity_ids=["clicker"],
            public_result="在照明下，你看清了发出喘息的非人形态。",
        )
    add(
        "s1",
        "close_car_door",
        "实际在循声者赶到之前关上车头与2号车厢之间的门",
        [118, 126, 129, 232],
        encounter_operation="close_door",
        door_id="quiet_passage",
        set_flags={"clickers_blocked": True},
        public_result="你关上了门，将循声者隔在外面。",
    )
    rule("cab_door", "unlock_door").update(encounter_operation="open_door", door_id="cab_door")
    fields["ending_b"]["sanity_effects"][0]["audience"] = "party"
    rule("ending_c", "crazy_end")["requires_party_loss"] = True
    # Environmental correction is frozen for attacks and defenses, not just generic checks.
    node2 = entities["s2"]["node_ids"][0]
    for light, addend, flags in (
        ("torch", 20, {"torch_light": True}),
        ("phone", 10, {"torch_light": False, "phone_light": True}),
        ("dark", 0, {"torch_light": False, "phone_light": False}),
    ):
        fields["s2"].setdefault("check_adjustments", []).append(
            dict(
                id="combat_" + light,
                kind="skill",
                name="*",
                source_block_ids=[blocks[145]],
                denominator=2,
                add=addend,
                required_flags=flags,
                combat_only=True,
                scene_node_ids=[node2],
                basis="原稿p11建议：调查员战斗技能减半；手电+20／手机+10。未采用可选-80。",
            )
        )
    fields["s2"]["source_block_ids"] = list(
        dict.fromkeys([*fields["s2"]["source_block_ids"], blocks[145]])
    )
    rule("controls", "accelerate")["situation"] = (
        "本次实际下推右侧油门并持续加速，面板已解锁。若乘务员跟随且未被说服或安置在够不到控制台处，先处理他的干预。"
    )
    rule("controls", "accelerate")["san_rewards"][-1]["surviving_npc_id"] = "staff"
    # Do not remove unresolved coverage merely because code has been written.
    for entry in p["coverage"]:
        if entry["id"] in {"initial_belongings", "sourced_modifiers", "sound_and_grab"}:
            entry["gaps"] = ["第十七批实现后待冻结版本验收；模型裁定和真实结算不能由夹具替代。"]
    write(DEST / "package-original.json", p)

    proposal = {
        "id": "batch17-npc-values-v1",
        "status": "pending_user_confirmation",
        "user_approved": False,
        "basis": "本地原稿全文及NPC页未提供缺项。以下仅为独立主机设计方案；测试不构成批准。",
        "staff": {
            "attributes": {"con": 50, "siz": 65},
            "hp_max": 11,
            "hp": 3,
            "armor": 0,
            "injury": {
                "major_wound": True,
                "prone": True,
                "unconscious": False,
                "dying": False,
                "last_damage_minute": 0,
            },
            "provenance": {
                "attributes.con": "纯设计50",
                "attributes.siz": "纯设计65，用于HP公式",
                "hp_max": "依设计CON50+SIZ65按规则PDF27取整11",
                "hp": "纯设计当前3HP；原稿仅明确清醒重伤",
                "armor": "纯设计0",
                "injury": "清醒重伤采用原稿p5/p15允许分支；伤发生在当前计时起点为隔离方案",
            },
        },
        "clicker": {
            "attributes": {"con": 70, "siz": 75},
            "skills": {"brawl": 60, "dodge": 20},
            "hp_max": 14,
            "hp": 14,
            "armor": 0,
            "damage_bonus": "1d6",
            "weapons": [
                {
                    "id": "bite",
                    "name": "撕咬",
                    "skill": "brawl",
                    "damage": "1d4",
                    "source": "batch17-npc-values-v1：纯设计基础伤害1d4，另加派生DB；未获用户批准",
                }
            ],
            "provenance": {
                "attributes.con": "纯设计70",
                "attributes.siz": "纯设计75",
                "skills.brawl": "纯设计60",
                "skills.dodge": "采用DEX40/2的基础闪避20，此基础值用于怪物是方案选择",
                "hp_max": "设计CON70+SIZ75按规则PDF27取整14",
                "hp": "设计为满血14",
                "armor": "纯设计0",
                "damage_bonus": "原STR100+设计SIZ75=175，规则PDF27表得1d6",
                "weapons": "纯设计咬击1d4，非原稿数据",
            },
        },
    }
    write(DEST / "npc-supplement-proposal.json", proposal)
    supplemented = deepcopy(p)
    supplemented["title"] = "常暗之厢 · 第十七批补充数值隔离测试（未批准）"
    supplemented["batch17"]["numeric_variant"] = "supplement_test"
    supplemented["numeric_supplement"] = proposal
    for e in supplemented["entities"]:
        if e["key"] in {"staff", "clicker"}:
            template = e["fields"]["combat_template"]
            extra = proposal[e["key"]]
            for k, v in extra.items():
                if k == "provenance":
                    continue
                if k in {"attributes", "skills", "injury"}:
                    template[k] = {**template.get(k, {}), **v}
                else:
                    template[k] = v
            template["source"] += "; pending-user:batch17-npc-values-v1; ISOLATED TEST ONLY"
            template["limitations"] = [
                "原稿缺项保留于原稿包；补充值未获用户批准。乘务员仅治疗和伤害，不配置无关攻击。"
            ]
    write(DEST / "package-supplement-test.json", supplemented)
    return p, supplemented


if __name__ == "__main__":
    build()
    print(DEST)
