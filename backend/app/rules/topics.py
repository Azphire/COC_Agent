"""Versioned, verified mechanics; selection never maps questions to PDF pages."""

from dataclasses import asdict, dataclass

from app.knowledge.lexical import decompose
from app.knowledge.text import normalize, tokens


@dataclass(frozen=True)
class RuleTopic:
    mechanic_id: str
    concepts: tuple[str, ...]
    pages: tuple[int, ...]
    section: str
    applies_when: str
    explanation: str
    source_title: str = "克苏鲁的呼唤第七版规则书1907.pdf"
    edition: str = "coc7"
    source_version: str = "1907"


class RuleTopicRegistry:
    source_hash = "bc8455d443ec4c64ae86ec2d6e6637889eff2cd59a4cc4686076e95b130d08dc"
    topics = (
        RuleTopic(
            "coc7.skill_check",
            ("技能检定", "属性检定", "侦查", "检定"),
            (72, 73),
            "5.1–5.2",
            "已有真实挑战且目标明确的属性或技能检定",
            "普通日常事务无需掷骰。有挑战或冲突时先明确目标，再按角色的技能或属性检定。",
        ),
        RuleTopic(
            "coc7.difficulty",
            (
                "普通",
                "常规",
                "困难",
                "极难",
                "难度",
                "普通成功",
                "常规成功",
                "困难成功",
                "极难成功",
                "常规难度",
                "困难难度",
                "极难难度",
            ),
            (73,),
            "5.2",
            "确定检定难度与成功上限",
            "普通检定使用全值，困难使用半值，极难使用五分之一；取整向下。",
        ),
        RuleTopic(
            "coc7.critical_fumble",
            ("大成功", "大失败"),
            (77,),
            "5.5",
            "判定百分骰的极端结果，按本地1907译本成功上限口径",
            "01为大成功；本次成功上限至少50时100为大失败，否则96至100为大失败。",
        ),
        RuleTopic(
            "coc7.bonus_penalty",
            ("奖励骰", "惩罚骰", "奖惩骰"),
            (79,),
            "5.8",
            "使用各0至2枚奖惩骰，先相互抵消",
            "奖惩骰共享个位并增加十位骰，奖励取较小百分结果，惩罚取较大结果。",
        ),
    )

    @classmethod
    def covers_question(cls, question):
        """Only simple questions wholly expressed by the verified topics skip RAG."""
        import re

        if not cls.select([question]):
            return False
        residual = question
        terms = sorted({c for t in cls.topics for c in t.concepts}, key=len, reverse=True)
        for term in terms:
            residual = residual.replace(term, "")
        for phrase in (
            "请问",
            "请解释",
            "解释一下",
            "说明一下",
            "如何",
            "怎么",
            "怎样",
            "有什么区别",
            "有什么不同",
            "分别",
            "使用",
            "计算",
            "判定",
            "规则",
            "是什么意思",
            "含义",
            "的",
            "和",
            "与",
            "是",
            "什么",
            "呢",
            "吗",
        ):
            residual = residual.replace(phrase, "")
        return not re.sub(r"[\s，,？?。！!：:]", "", residual)

    @classmethod
    def select(cls, concepts, has_check=False):
        text = " ".join(concepts)
        return [
            asdict(t)
            for t in cls.topics
            if any(term in text for term in t.concepts)
            or has_check
            and t.mechanic_id in {"coc7.skill_check", "coc7.difficulty"}
        ]

    @classmethod
    def concepts(cls, text, planned=()):
        import re

        names = {t.mechanic_id: t.concepts[0] for t in cls.topics}
        queries = [
            names.get(q, q)
            for q in planned
            if q in names or not re.search(r"[a-z][a-z0-9]*[._][a-z0-9_]+", q, re.I)
        ]
        return list(dict.fromkeys(c for q in [*queries, text] for c in decompose(q)[1]))[:6]

    @classmethod
    def unstructured(cls, concepts):
        return [
            c for c in concepts if not any(c == term for t in cls.topics for term in t.concepts)
        ]

    @classmethod
    def evidence(cls, concepts, refs, repository, run_id):
        import hashlib

        source = next(
            (
                repository.source(r["source_id"], r["source_hash"])
                for r in refs
                if r["source_hash"] == cls.source_hash
            ),
            None,
        )
        if not source or source.edition != "coc7" or source.visibility != "public_rules":
            return []
        return [
            {
                "evidence_id": "topic_"
                + hashlib.sha256((run_id + t["mechanic_id"]).encode()).hexdigest()[:32],
                "chunk_id": t["mechanic_id"],
                "mechanic_id": t["mechanic_id"],
                "source_id": source.source_id,
                "source_hash": source.source_hash,
                "source_title": t["source_title"],
                "source_kind": "rulebook",
                "edition": "coc7",
                "source_version": t["source_version"],
                "section": t["section"],
                "physical_page": t["pages"][0],
                "pages": list(t["pages"]),
                "page_kind": "pdf",
                "page_label": str(t["pages"][0] - 1),
                "file_name": t["source_title"],
                "excerpt": t["explanation"],
                "applies_when": t["applies_when"],
                "score": 1,
                "rank": i + 1,
                "visibility": "public_rules",
            }
            for i, t in enumerate(cls.select(concepts))
        ]


def relevant_evidence(concept, excerpt):
    query, text = normalize(concept), normalize(excerpt)
    if query in text:
        return True
    wanted = set(tokens(query))
    return bool(wanted) and len(wanted & set(tokens(text))) / len(wanted) >= 0.6


def rule_question_text(text):
    """Conservative compatibility detection; explicit request category is authoritative."""
    import re

    return "规则" in text and not re.search(r"我(?:向|问|询问)|交谈|NPC|npc|回顾|回想", text)
