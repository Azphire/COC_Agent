"Source-reviewed Paper Chase package; no model-generated numeric approvals."

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/prepared/zhuishuren/batch-40"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (OUT / "bootstrap.db").as_posix()
os.environ["KNOWLEDGE_DB_PATH"] = str(OUT / "bootstrap-knowledge.db")
os.environ["CHECKPOINT_DB_PATH"] = str(OUT / "bootstrap-checkpoint.db")

from app.knowledge.repository import KnowledgeRepository  # noqa: E402
from app.module_ir.parser import stable_id  # noqa: E402
from app.module_ir.schemas import ModuleDocumentIR, ModuleNode  # noqa: E402
from app.preparation.schemas import EntityFields  # noqa: E402
from scripts.module_package import audit, write  # noqa: E402

REVIEWER = "Codex source review, batch-40 (not human/user approval)"


def build():
    source = json.loads((OUT / "indexed-source.json").read_text(encoding="utf-8"))
    raw = ModuleDocumentIR.model_validate_json((OUT / "source-ir.json").read_text(encoding="utf-8"))
    ir = raw.model_copy(deep=True)
    # Keep every original block and page. The PDF's outline omits most scene
    # headings; this explicit Codex-reviewed outline is recorded independently
    # of the unchanged extractor output in source-ir.json.
    scenes = [
        (
            "home",
            "金博尔家",
            8,
            "玩家信息",
            (
                "1922年，密歇根州阿诺兹堡。托马斯·金博尔请你调查叔叔失踪和藏"
                "书失窃，承担费用并支付10美元。你已住进他家的一间空卧室，托马斯"
                "在家中等你商量从哪里查起。"
            ),
            "开局仅公开委托。叔叔外貌白发、秃顶、中等身高、圆框眼镜。托马斯未报警；可以询问他、查看书房或去镇上调查。",
        ),
        (
            "neighbors",
            "附近街区",
            9,
            "询问附近居民",
            "金博尔家的附近住宅不多，步行五分钟能走到几所房屋。一位老妇人恰好经过。",
            "莉拉·奥黛尔：APP或信用一次成功才愿意停留谈话，不能同时试两种。一般邻居所知甚少。",
        ),
        (
            "cemetery",
            "公墓",
            9,
            "查看墓地周边",
            "古老公墓植被茂盛，打理得尚好。高灌木和老树点缀在墓穴、墓碑之间，一名园艺工在另一头清理杂草。",
            "守墓人杰弗逊须魅惑/说服才愿详谈。私密墓地真相不能自动公开。",
        ),
        (
            "library",
            "本地图书馆",
            11,
            "图书馆中的调查",
            "本地图书馆收藏着旧报刊，可以向管理员询问或查找地方消息。",
            "图书馆使用成功找到十余年前墓地怪人报道。失败可换方法孤注：再失败仍获得信息但被锁到翌日八点。",
        ),
        (
            "police",
            "警局",
            11,
            "询问警方",
            "值班警官正在处理公务。你可以询问当地的失踪案、盗窃案或公墓。",
            "有专业往来的私家侦探/记者可直接获知资料；否则法律/魅惑/说服。未通过时警官忙碌，明日再来。",
        ),
        (
            "news",
            "阿诺兹堡广告报社",
            12,
            "阿诺兹堡广告报",
            "报社编辑在这里工作，过期旧刊收藏在资料室。",
            (
                "记者或作家免入室检定，其他职业魅惑/说服/话术。查资料图书馆使用"
                "普通3小时、困难1小时、极限几分钟；孤注失败仍获得线索但耗时受困"
                "。"
            ),
        ),
        (
            "study",
            "道格拉斯的书房",
            12,
            "金博尔家",
            "藏书塞满书房，大小和内容各异，但都受到仔细保养。架上几处空档十分显眼，书房有一扇通向屋外的窗户。",
            (
                "托马斯允许调查；不知道具体缺哪些书，估计六本。检查窗户可发现锁松"
                "。搜索全书房至少一天，侦查得日记；孤注失败也得日记但毁物，酬金减"
                "5美元。"
            ),
        ),
        (
            "tomb",
            "旧墓碑与陵墓",
            13,
            "检查道格拉斯",
            "一块饱经风雨的低矮旧墓碑表面很光滑，碑上墓主名字已无法辨认。附近有几座陵墓。",
            "墓碑周围侦查或追踪可见蹄状脚印，跟踪抵达陵墓门。力量开门；失败可取铲子换方法孤注。未屏息会昏迷到夜晚、醒来见道格拉斯。",
        ),
        (
            "tunnels",
            "地下隧道网",
            14,
            "探索隧道网",
            "徒手挖掘的泥土通道延伸到黑暗之中，岔道交错。",
            "导航无论成败都会流逝到夜晚；失败迷路筋疲力尽，返地面必遇道格拉斯，不能把失败写成找对路。",
        ),
        (
            "watch",
            "屋外与墓地的夜晚",
            15,
            "监视屋子或墓地",
            "夜色笼罩金博尔家的书房外墙和附近公墓。这里可以设岗监视书房的窗户及墓地的小路。",
            "每晚幸运成功才见人影进入书房，数分钟后满载书离开。封窗会撞破玻璃。失败可以次夜继续，不能同夜刷骰。",
        ),
        (
            "meeting",
            "墓碑旁的夜色",
            17,
            "如果调查员试图和人影交谈",
            "夜色中，一个抱着书本的人影坐在低矮墓碑上。你靠近时，他向你轻声说：你好。",
            (
                "靠近后辨认食尸鬼道格拉斯并0/1D4 SAN；礼貌问话可得真相。"
                "他请求保密，最后封闭通道。新解法允许。不得在实际观察前把他的身份"
                "公开。"
            ),
        ),
        (
            "ending",
            "金博尔家的次日",
            17,
            "尾声",
            "你回到了托马斯·金博尔家。托马斯等着听调查经过。",
            "只有隧道实际封闭、道格拉斯离去或对应暴力结局成立才能结案。玩家自行选择是否说出真相。付10美元，毁物扣5美元。成长按现有规则。",
        ),
    ]
    root = ir.nodes[0].model_copy(update={"child_ids": []})
    nodes, ids, anchors = [root], {}, {}
    for key, title, page, heading, public, private in scenes:
        candidates = [b for b in ir.blocks if b.page_reference == page]
        anchor = next((b for b in candidates if heading in b.text.replace("\n", "")), candidates[0])
        nid = stable_id("node_", source["source_hash"], "codex-reviewed-outline", key)
        ids[key], anchors[key] = nid, anchor
        nodes.append(
            ModuleNode(
                node_id=nid,
                parent_node_id=root.node_id,
                source_order=anchor.source_order,
                depth=1,
                title=title,
                normalized_title=title,
                heading_path=[root.title, title],
                detected_type="section",
                detection_source="host",
                confidence=1,
                source_position=anchor.source_position.model_copy(deep=True),
            )
        )
    nodes[1:] = sorted(nodes[1:], key=lambda n: n.source_order)
    root.child_ids = [n.node_id for n in nodes[1:]]
    for block in ir.blocks:
        owner = next((n for n in reversed(nodes[1:]) if n.source_order <= block.source_order), root)
        block.node_id = owner.node_id
    ir.nodes, ir.node_count = nodes, len(nodes)
    ir.extraction_method += "+codex_reviewed_outline"
    ir.warnings = [
        ("Outline reviewed by Codex; not user approval. Raw extractor IR retained separately.")
    ]
    ir = ModuleDocumentIR.model_validate(ir.model_dump())
    entities = []

    def blocks(pages):
        # Up to 60 exact source blocks per entity, favoring content over folios.
        found = [b for b in ir.blocks if b.page_reference in pages and len(b.text) > 5]
        return [
            b.block_id
            for b in sorted(
                sorted(found, key=lambda b: -len(b.text))[:60], key=lambda b: b.source_order
            )
        ]

    def entity(
        key,
        kind,
        title,
        public,
        pages,
        locations,
        *,
        private="",
        visible=False,
        check=None,
        aliases=(),
        access=None,
        **extra,
    ):
        fields = EntityFields(
            type=kind,
            title=title,
            public_summary=public,
            keeper_summary=private,
            initial_visibility="revealed" if visible else "hidden",
            aliases=list(aliases),
            source_pages=pages,
            source_block_ids=blocks(pages),
            reviewed_by=REVIEWER,
            review_basis="对照指定PDF物理页全文与渲染图校对；原文数值与下述运行缺项分列。",
            reveal_conditions={
                "access_policy": access or ("requires_check" if check else "automatic"),
                **({"successful_check": {"name": check}} if check else {}),
            },
            **extra,
        )
        entry = {
            "key": key,
            "fields": fields.model_dump(mode="json"),
            "node_ids": [ids[k] for k in locations],
        }
        entities.append(entry)
        return entry["fields"]

    by = {}
    for key, title, page, _, public, private in scenes:
        by[key] = entity(
            key, "scene", title, public, [page], [key], private=private, visible=key == "home"
        )

    def rule(owner, rid, instruction, kinds, result, **extra):
        # These manuscript circumstances use the existing bounded KP ruling.
        # required_facts is reserved for executable, registered scene facts.
        if conditions := extra.pop("required_facts", []):
            extra["situation"] = "；".join(conditions)
            extra["host_review"] = True
        value = {
            "id": rid,
            "instruction": instruction,
            "action_kinds": kinds,
            "kp_enabled": True,
            "public_result": result,
            "source_block_ids": by[owner]["source_block_ids"][:12],
            **extra,
        }
        by[owner]["interactions"].append(value)

    def clue(key, title, public, pages, locations, **kwargs):
        by[key] = entity(key, "clue", title, public, pages, locations, **kwargs)

    by["thomas"] = entity(
        "thomas",
        "npc",
        "托马斯·金博尔",
        "委托人托马斯·金博尔，现住在叔叔留下的房屋里。",
        [8, 12, 13, 17],
        ["home", "study", "ending"],
        visible=True,
        aliases=["托马斯", "金博尔先生"],
        private="配合调查，不知叔叔真相；可自然提议监视，但不能替调查员宣布结案。",
    )
    clue(
        "commission",
        "托马斯的委托",
        (
            "叔叔道格拉斯一年前失踪。近日他最喜爱的藏书被偷，估计少了六本；托"
            "马斯不知道具体书名。叔叔白发秃顶，戴圆框眼镜，中等身高。托马斯愿"
            "付10美元并承担开销，也愿提供客房。"
        ),
        [8, 12],
        ["home", "study"],
        visible=True,
    )
    by["thomas"]["dialogue_topics"] = [
        {
            "keywords": ["叔叔", "书", "失踪", "被盗", "报警", "报酬", "调查"],
            "reveal_entity_ids": ["commission"],
        }
    ]
    clue(
        "window",
        "松动的窗锁",
        "书房窗锁因年久而松动，从外面足够用力就能打开。",
        [12],
        ["study"],
        aliases=["窗户", "窗锁"],
    )
    clue(
        "diary",
        "道格拉斯的日记",
        (
            "日记最后一条写于失踪前一天：已经“做出决定”，要加入“我在地下的"
            "朋友们”。更早的条目暗示公墓地下有隧道网络，居住着他夜里见过的神"
            "秘生物。"
        ),
        [12, 13],
        ["study"],
        check="spot_hidden",
        aliases=["日记"],
    )
    rule(
        "study",
        "search_diary",
        "至少花一天全面搜索书房，侦查；失败后可换方法孤注，不能普通重复刷骰。",
        ["search", "observe"],
        "你经过一天的搜索，找到了道格拉斯的日记。",
        check_name="spot_hidden",
        elapsed_minutes=1440,
        reveal_entity_ids=["diary"],
        set_flags={"diary_found": True},
    )
    by["odell"] = entity(
        "odell",
        "npc",
        "莉拉·奥黛尔",
        "一名打量过路人衣着的老妇人。",
        [9],
        ["neighbors"],
        aliases=["老妇人", "奥黛尔女士"],
        private="外貌或信用只能选一项；成功说他常夹书去墓地，已多年未见，认为侄子继承房屋。",
    )
    clue(
        "neighbor_account",
        "奥黛尔的回忆",
        "道格拉斯以前大多数日子都夹着书走向墓地，总在读书。奥黛尔多年未见他，以为他已去世，侄子继承了房屋。",
        [9],
        ["neighbors"],
        check="app",
    )
    for skill in ("app", "credit_rating"):
        rule(
            "odell",
            "talk_" + skill,
            "与奥黛尔体面地交谈，外貌或信用任选其一，只能有一次机会。",
            ["converse"],
            "奥黛尔愿意停下，谈起道格拉斯以前经常夹着书去墓地。",
            check_name=skill,
            reveal_entity_ids=["neighbor_account"],
        )
    by["jefferson"] = entity(
        "jefferson",
        "npc",
        "梅洛迪亚斯·杰弗逊",
        "公墓守墓人，身高中等，穿着打补丁的衣服，正在用铁铲除草。",
        [10, 11, 18],
        ["cemetery"],
        aliases=["守墓人", "杰弗逊"],
        private="粗鲁好斗，58岁，工作二十多年。乐意谈旧友后会隐瞒近来夜间人影。瓶子不能未侦查就公开。",
    )
    clue(
        "keeper_account",
        "守墓人的旧友",
        "杰弗逊常与道格拉斯聊天，喜欢听他谈异国故事。他指明道格拉斯最喜欢坐着读书的旧墓碑。",
        [11],
        ["cemetery"],
        check="persuade",
    )
    for skill in ("persuade", "charm"):
        rule(
            "jefferson",
            "talk_" + skill,
            "争取守墓人愿意闲聊道格拉斯；失败可明确改换办法孤注。",
            ["converse"],
            "杰弗逊谈起旧友，并指明他最喜欢的墓碑。",
            check_name=skill,
            reveal_entity_ids=["keeper_account"],
            set_flags={"tomb_known": True},
        )
    clue(
        "bottle",
        "守墓人口袋里的瓶子",
        "外套口袋微微露出一截酒瓶。",
        [11],
        ["cemetery"],
        check="spot_hidden",
        aliases=["酒瓶", "瓶子"],
    )
    clue(
        "withholding",
        "守墓人的隐瞒",
        "问到深夜怪事时，杰弗逊急于结束话题。他有所隐瞒，知道的比说出的更多。",
        [11],
        ["cemetery"],
        check="psychology",
    )
    clue(
        "night_account",
        "守墓人的夜间目击",
        "杰弗逊承认最近深夜在旧墓碑旁见到人影；他以为是道格拉斯的鬼魂，害怕得不敢走近。",
        [11],
        ["cemetery"],
        check="intimidate",
    )
    for skill, difficulty in (("intimidate", "regular"), ("persuade", "hard")):
        rule(
            "jefferson",
            "pressure_" + skill,
            "发现酒瓶后用禁酒令要挟他透露夜间怪事。",
            ["converse"],
            "杰弗逊承认夜里看见过旧墓碑旁的人影。",
            required_entity_ids=["bottle"],
            check_name=skill,
            check_difficulty=difficulty,
            reveal_entity_ids=["night_account"],
        )
    by["liquor"] = entity(
        "liquor",
        "item",
        "一品脱烈酒",
        "一品脱烈酒。",
        [11],
        ["neighbors", "cemetery"],
        private="EDU成功后花2美元购得；购买失败须幸运，96—100必捕，关一夜。金额及逮捕特殊分支需现有主机裁决，不能模型声称已买到。",
        aliases=["酒", "烈酒"],
        access="host_review",
    )
    # Non-automated purchase remains a clearly marked source-supported gap;
    # it does not pre-seed a resource in either investigator's possession.
    clue(
        "old_report",
        "十多年前的旧报",
        "十多年前有人声称见过一群裸体怪人在金博尔家旁的公墓嬉戏。警方搜索未找到人，但留下形状怪异的脚印。",
        [11],
        ["library", "news"],
        check="library_use",
    )
    rule(
        "library",
        "research",
        "查找本地旧报道，图书馆使用。",
        ["search", "observe"],
        "你查到了十多年前公墓怪人和怪异足迹的报道。",
        check_name="library_use",
        reveal_entity_ids=["old_report"],
    )
    by["officer"] = entity(
        "officer",
        "npc",
        "值班警官",
        "一位忙碌的值班警官。",
        [11, 12],
        ["police"],
        private="先看职业和专业往来；记者和私家侦探有业务联系可免检定。其他人法律、魅惑或说服。只回答所问的信息。",
        aliases=["警官", "警察"],
    )
    clue(
        "police_account",
        "警方的记录",
        (
            "最近没有盗窃报案；一年前的飞贼杰克“六指”汤普森仍在服刑。六七年"
            "前公墓有人报怪声，巡逻数周没查到异常。道格拉斯失踪时的照片和寻人"
            "启事未带来结果。"
        ),
        [12],
        ["police"],
        check="law",
    )
    rule(
        "officer",
        "professional_enquiry",
        "原文允许有专业往来的记者、私家侦探免检定询问警方；必须先确认职业和往来。",
        ["converse"],
        "警官向你说明了对应的旧案记录。",
        required_facts=["调查员是有专业往来的记者或私家侦探"],
        reveal_entity_ids=["police_account"],
    )
    for skill in ("law", "charm", "persuade"):
        rule(
            "officer",
            "enquiry_" + skill,
            "无专业往来时争取忙碌警官答话。",
            ["converse"],
            "警官愿意答复你的调查。",
            check_name=skill,
            reveal_entity_ids=["police_account"],
        )
    by["editor"] = entity(
        "editor",
        "npc",
        "广告报编辑",
        "报社编辑，可以决定是否允许来访者查阅旧刊。",
        [12],
        ["news"],
        aliases=["编辑"],
        private="记者、作家免准入检定，其他人用魅惑/说服/话术。",
    )
    clue(
        "ward_notes",
        "未发表的采访笔记",
        (
            "现年64岁、失眠的希尔达·沃德曾声称二十多年间见到墓地的“魔鬼子"
            "嗣”：人形、犬类特征、蹄状足、沾腐泥。她已搬到底特律，其余邻居不"
            "承认见过。"
        ),
        [12],
        ["news"],
        check="library_use",
    )
    rule(
        "news",
        "archives",
        "记者或作家获编辑许可后查档；其他职业须先社交获准。图书馆使用普通3小时，困难1小时，极限数分钟。",
        ["search", "observe"],
        "你找到了沃德女士的未发表采访笔记。",
        required_facts=["已获编辑允许查阅旧刊"],
        check_name="library_use",
        reveal_entity_ids=["ward_notes"],
    )
    clue(
        "tracks",
        "墓碑周围的奇怪足迹",
        "足迹像成年人赤足留下，脚趾部位却呈偶蹄状。它们通向一座陵墓的门。",
        [13],
        ["tomb"],
        check="spot_hidden",
        aliases=["脚印", "足迹"],
    )
    for skill in ("spot_hidden", "track"):
        rule(
            "tomb",
            "tracks_" + skill,
            "查看旧墓碑四周的足迹。",
            ["search", "observe"],
            "你发现偶蹄状足迹，可以沿着它们到陵墓门前。",
            check_name=skill,
            reveal_entity_ids=["tracks"],
        )
    rule(
        "tomb",
        "open_tomb",
        "沿足迹找到墓门并用力量打开；须明确屏住呼吸，否则开门后昏厥。",
        ["open", "control"],
        "陵墓门被推开，恶臭涌出，里面有一条伸向黑暗的土隧道。",
        required_entity_ids=["tracks"],
        required_facts=["调查员在开门前明确屏住呼吸"],
        check_name="str",
        set_flags={"tunnel_open": True},
    )
    rule(
        "tunnels",
        "navigate",
        "探索隧道并尝试返回地面，导航成功也会到夜晚，遇到抱书的人影。",
        ["search", "observe", "pass"],
        "你辨明方向走向地面，夜晚已经降临，出口附近出现一个抱书的人影。",
        check_name="navigate",
        failure_interaction_id="lost",
        set_flags={"meeting_ready": True},
    )
    rule(
        "tunnels",
        "lost",
        "导航失败后在隧道中迷路到筋疲力尽，返回时遇见人影。",
        ["search", "observe", "pass"],
        "你在通道里兜转得精疲力尽，天色已晚；寻找出口时碰见一个抱书的人影。",
        check_name="navigate",
        check_passed=False,
        set_flags={"meeting_ready": True},
    )
    clue(
        "shadow",
        "夜间抱书的人影",
        "一条人影从公墓走向书房窗户，进入后数分钟又抱着书本走向公墓。",
        [15],
        ["watch"],
        check="luck",
        aliases=["人影", "窃贼"],
    )
    rule(
        "watch",
        "watch_night",
        "整晚监视书房或墓地，每个新夜晚一次幸运；失败可次夜继续。",
        ["observe", "rest", "search"],
        "你等到了人影：它从公墓来，经书房窗户取走书，正抱着书返回墓地。",
        check_name="luck",
        failure_interaction_id="watch_failed",
        required_flags={"watch_attempted": False},
        reveal_entity_ids=["shadow"],
        set_flags={"shadow_seen": True, "watch_attempted": True},
    )
    rule(
        "watch",
        "watch_failed",
        "本晚幸运失败，整晚没有等到可辨认的人影。",
        ["observe", "rest", "search"],
        "这一夜你没有发现取书的人影。可以改日再来守候。",
        check_name="luck",
        check_passed=False,
        set_flags={"watch_attempted": True},
    )
    rule(
        "watch",
        "next_night",
        "玩家明确等待至下一晚再守候；推进一个日夜，不重新投本晚的骰。",
        ["rest"],
        "一个日夜过去，你再次来到监视的位置。",
        required_flags={"watch_attempted": True},
        elapsed_minutes=1440,
        set_flags={"watch_attempted": False},
    )
    rule(
        "watch",
        "follow_shadow",
        "不攻击，跟随抱书人影到公墓，喊出道格拉斯的名字会使它停顿等待。",
        ["pass", "converse", "observe"],
        "人影走到低矮墓碑旁坐下，等待你靠近。",
        required_entity_ids=["shadow"],
        set_flags={"meeting_ready": True},
    )
    by["douglas"] = entity(
        "douglas",
        "npc",
        "抱书的人影",
        "一名坐在墓碑上、抱着书本的人影向你打招呼。",
        [8, 14, 15, 16, 17, 18],
        ["meeting"],
        aliases=["人影", "道格拉斯", "道格拉斯·金博尔"],
        private="实际为食尸鬼道格拉斯。靠近辨认触发SAN，随后礼貌交流；他会友善回答，不主动攻击。除非遭攻击，不启动战斗。",
    )
    clue(
        "identity",
        "失踪者的面容",
        "这个人的皮肤和足部已经异变，面部带有犬类特征，却仍能辨认出他正是道格拉斯·金博尔。",
        [17],
        ["meeting"],
    )
    clue(
        "truth",
        "道格拉斯的解释",
        (
            "道格拉斯厌倦人类琐事，自愿跟地下的食尸鬼朋友生活。他只想自由读书"
            "，这次回来取自己的藏书；朋友们即将封闭入口，这是取书的最后一夜。"
            "他打算写一本地下见闻，请你不要告诉侄子自己仍活着。"
        ),
        [8, 17],
        ["meeting"],
    )
    for key, eid, loss, success, cause in [
        ("identity", "recognition", "1d4", "0", "近看食尸鬼并辨认道格拉斯"),
        ("truth", "unpleasant_truth", "1d4", "1d4", "听懂道格拉斯主动加入食尸鬼生活的真相"),
    ]:
        by[key]["sanity_effects"] = [
            {
                "id": eid,
                "encounter": cause,
                "trigger": "entity_revealed",
                "automation": "automatic",
                "kp_enabled": True,
                "audience": "party",
                "repeat": "first_only",
                "success_loss": success,
                "failure_loss": loss,
                "source": "module:" + source["source_hash"],
                "page": 17,
                "basis": "PDF物理17页（印刷16页）对应交谈段明确规定。",
                "mythos": True,
                "involuntary_action": "发出一声惊叫",
            }
        ]
    rule(
        "douglas",
        "approach",
        "走近人影，借现有光线观察并辨认其面貌。",
        ["observe", "converse"],
        "你认出失踪的道格拉斯，但他的面容已向食尸鬼转变。",
        reveal_entity_ids=["identity"],
        set_flags={"recognized": True},
    )
    by["douglas"]["dialogue_topics"] = [
        {
            "keywords": [
                "为什么",
                "怎么",
                "叔叔",
                "书",
                "生活",
                "失踪",
                "地下",
                "朋友",
                "回来",
                "侄子",
                "道格拉斯",
            ],
            "required_entity_ids": ["identity"],
            "reveal_entity_ids": ["truth"],
        }
    ]
    rule(
        "douglas",
        "farewell",
        "交谈结束后让道格拉斯带书离去；他爬入陵墓并用石板封闭入口。调查员仍留在地面。",
        ["converse", "rest"],
        "道格拉斯带着藏书爬入陵墓，拖动巨石板封住入口。他和地下的朋友们离开，不会再来取书。",
        required_entity_ids=["truth"],
        required_sanity=[
            {"entity_id": "identity", "effect_id": "recognition"},
            {"entity_id": "truth", "effect_id": "unpleasant_truth"},
        ],
        set_flags={"departed": True, "talked": True},
    )
    rule(
        "douglas",
        "join_underground",
        "调查员明确选择跟道格拉斯去地下共同生活；不是普通探索或询问信息。",
        ["pass"],
        "你跟随道格拉斯走入地下。入口在身后封闭，从此你在人间失踪。",
        required_entity_ids=["truth"],
        required_facts=["调查员明确自愿永久跟随道格拉斯生活"],
        outcome="地下失踪",
    )
    rule(
        "ending",
        "report_case",
        "道格拉斯已经离去、入口关闭后，向托马斯说明结案；真相是否告诉他由调查员选择。",
        ["converse", "settle"],
        "盗书事件就此结束。托马斯感谢你的调查并按约支付酬金。是否向他透露叔叔的真相，由你决定。",
        required_flags={"departed": True},
        required_sanity=[
            {"entity_id": "identity", "effect_id": "recognition"},
            {"entity_id": "truth", "effect_id": "unpleasant_truth"},
        ],
        outcome="道格拉斯离去，盗书案结束",
        san_rewards=[{"formula": "1d6", "required_flags": {"talked": True}}],
        mythos_reward=3,
    )

    # Source-supported alternative endings remain private host-review branches.
    # Their exact triggering combat/SAN cannot be replaced by narrative prose.
    for key, title, content in [
        ("crowd_loss", "群鬼：失踪", "攻击数十只涌出的食尸鬼，寡不敌众，从此失踪。"),
        (
            "crowd_escape",
            "群鬼：逃离",
            "见群鬼后逃离，食尸鬼不追，带走道格拉斯并永久封闭入口；之后可获托马斯报酬。",
        ),
        (
            "crowd_asylum",
            "群鬼：疗养院",
            "恐惧过度崩溃，数日后在疗养院醒来；托马斯找到调查员并送医，支付酬金，留院一周。",
        ),
    ]:
        by[key] = entity(
            key,
            "clue",
            title,
            content,
            [16, 17],
            ["meeting"],
            access="host_review",
            private="须先有袭击、群鬼及SAN/临时疯狂的实际结算。当前特殊战斗链未自动化；不得选择此分支伪造结束。",
        )
    # Only manuscript numeric values. Missing armor remains null; combat special
    # rules are explicitly listed as gaps instead of forging numeric approval.
    for key, attributes, skills, hp, weapons in [
        (
            "jefferson",
            dict(str=45, con=65, siz=60, dex=50, int=70, app=55, pow=60, edu=65),
            dict(
                brawl=30,
                dodge=25,
                credit_rating=32,
                intimidate=45,
                mechanical_repair=55,
                natural_world=70,
            ),
            12,
            [("fist", "拳击", "1d3"), ("shovel", "铁铲", "1d6")],
        ),
        (
            "douglas",
            dict(str=55, con=65, siz=60, dex=40, int=80, pow=65, edu=85),
            dict(brawl=50, dodge=30, climb=85, stealth=70, jump=75, listen=70, spot_hidden=50),
            12,
            [("claw", "爪击", "1d6"), ("bite", "撕咬", "1d6")],
        ),
    ]:
        by[key]["combat_template"] = {
            "source": "module:" + source["source_hash"] + ":physical-page-18",
            "attributes": attributes,
            "skills": skills,
            "hp": hp,
            "hp_max": hp,
            "damage_bonus": "0",
            "armor": None,
            "weapons": [
                {
                    "id": wid,
                    "name": name,
                    "kind": "melee",
                    "skill": "brawl",
                    "damage": damage,
                    "source": "PDF physical page 18",
                }
                for wid, name, damage in weapons
            ],
            "limitations": [
                "原稿未给常规护甲数值。道格拉斯枪伤减半及拳击累计6伤击晕需特殊裁定，不假称已有自动执行。"
            ],
        }

    transitions = []

    def edge(a, b, **gate):
        transitions.append(
            {
                "source_scene_node_id": ids[a],
                "target_scene_node_id": ids[b],
                "approved": True,
                "source_evidence": [anchors[a].block_id, anchors[b].block_id],
                "condition_summary": "按玩家明确选择移动，保留既有事实和资源。",
                **gate,
            }
        )

    for loc in ("neighbors", "cemetery", "library", "police", "news", "study"):
        edge("home", loc)
        edge(loc, "home")
    edge("cemetery", "tomb", required_flags={"tomb_known": True})
    edge("tomb", "cemetery")
    edge("tomb", "tunnels", required_flags={"tunnel_open": True})
    edge("tunnels", "tomb")
    edge("home", "watch")
    edge("cemetery", "watch")
    edge("watch", "home")
    for loc in ("watch", "tunnels", "tomb"):
        edge(loc, "meeting", required_flags={"meeting_ready": True})
    edge("meeting", "ending", required_flags={"departed": True})

    with KnowledgeRepository(OUT / "source-index.db").connect() as db:
        chunks = [
            dict(r) for r in db.execute("select * from knowledge_chunks order by chunk_index")
        ]
    gaps = [
        "酒的2美元购买、失败后幸运96—100必捕及关押一晚尚无完整自动分支；不预发酒。",
        "开墓门未屏息自动昏迷、醒来遇见道格拉斯的时间/意识分支需裁定；屏息路线可执行。",
        "图书馆/日记孤注失败仍获信息、锁馆/毁物扣5美元及报社按成功等级耗时未全部自动化；保留原文来源，不把普通失败改成成功。",
        "道格拉斯枪伤减半、拳击累计6伤击晕、群鬼后的三种结局及对应SAN原文已校对，当前特殊战斗自动执行不足；不可用旁白冒充真实结算。",
        "普通NPC没有原文战斗数值；不伪造补充或用户审批。装备采用正常角色配置，1922年无手机。",
    ]
    coverage = []
    for page in range(1, 20):
        covered = [b for b in ir.blocks if b.page_reference == page]
        related = [e["key"] for e in entities if page in e["fields"]["source_pages"]]
        gap_indices = {11: [0, 2], 12: [2], 13: [1, 2], 14: [1], 16: [3], 17: [3], 18: [3, 4]}
        page_gaps = [gaps[i] for i in gap_indices.get(page, [])]
        coverage.append(
            {
                "id": f"pdf-{page}",
                "source_orders": [b.source_order for b in covered],
                "entity_keys": related,
                "node_ids": list(dict.fromkeys(b.node_id for b in covered)),
                "status": "host_ruling" if page_gaps else "prepared" if related else "context",
                "gaps": page_gaps,
                "note": "全文已提取并逐页渲染核对；覆盖不等同于全部机制自动执行。",
            }
        )
    package = {
        "format_version": 1,
        "title": "追书人",
        "reviewer": REVIEWER,
        "review_basis": "指定PDF19页完整阅读及图像核对；Codex审阅，不冒称用户人工批准。",
        "knowledge": {"source": source, "chunks": chunks},
        "ir": ir.model_dump(mode="json"),
        "entities": entities,
        "nodes": [
            {"node_id": root.node_id, "patch": {"approved_type": "document", "included": True}}
        ]
        + [
            {
                "node_id": ids[k],
                "patch": {
                    "approved_type": "scene",
                    "included": True,
                    "public_title": title,
                    "public_summary": public,
                    "keeper_summary": private,
                    "initial_scene": k == "home",
                },
            }
            for k, title, _, _, public, private in scenes
        ],
        "transitions": transitions,
        "initial_node_id": ids["home"],
        "initial_entity_key": "home",
        "required_entity_keys": ["home", "thomas", "commission"],
        "coverage": coverage,
        "required_npc_operations": {},
        "original_choices": {
            "year": 1922,
            "investigators": "1-2",
            "source_scope": ("Full Paper Chase including epilogue and NPCs, physical pages 8-18"),
            "map": {
                "physical_page": 13,
                "printed_page": 12,
                "labels": [
                    "1. 金博尔家",
                    "2. 道格拉斯的书房",
                    "3. 道格拉斯最喜欢的墓碑",
                    "4. 食尸鬼隧道",
                ],
                "review": "Codex从渲染图逐字校对。地图内容为KP信息，不自动公开隧道位置。",
            },
        },
        "known_execution_gaps": gaps,
    }
    for entry in entities:
        entry["fields"] = EntityFields.model_validate(entry["fields"]).model_dump(mode="json")
    summary = audit(package)
    write(OUT / "package-reviewed.json", package)
    write(OUT / "package-audit.json", summary)
    print(
        json.dumps(
            {
                "sha256": hashlib.sha256((OUT / "package-reviewed.json").read_bytes()).hexdigest(),
                **summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    build()
