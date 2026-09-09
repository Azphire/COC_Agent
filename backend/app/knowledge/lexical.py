"""Deterministic lexical channels; vocabulary never contains source/page identifiers."""

import re
from collections import defaultdict

from app.knowledge.text import TERMS, normalize, tokens

# Rule/card terminology equivalences; both forms are searched, no source is excluded.
ALIASES = {
    "普通成功": "常规难度",
    "常规成功": "常规难度",
    "困难成功": "困难难度",
    "极难成功": "极难难度",
    "百分骰": "百分比骰子",
    "个人兴趣": "兴趣技能点",
    "本职技能": "职业技能点",
    "教育属性": "教育增强",
}


def decompose(query):
    query = normalize(query)
    for noise in (
        "应该怎样",
        "应该如何",
        "如何判定",
        "如何生成",
        "如何",
        "怎样",
        "怎么",
        "请问",
        "应该",
    ):
        query = query.replace(noise, " ")
    protected = list(
        dict.fromkeys(
            sorted(
                (t for t in (*TERMS, *ALIASES, *ALIASES.values()) if t in query),
                key=lambda t: (-len(t), t),
            )
            + re.findall(r"\d*d\d+(?:[+-]\d+)?|[a-z][a-z_]+", query)
        )
    )
    concepts = [t for t in protected if not any(t != other and t in other for other in protected)]
    pieces = [
        p.strip()
        for p in re.split(r"[，,。；;？！?！、]|以及|并且|和|与", query)
        if len(p.strip()) > 1
    ]
    subqueries = list(dict.fromkeys(concepts + [ALIASES[t] for t in concepts if t in ALIASES]))
    if not subqueries:
        subqueries = pieces
    return query, subqueries[:12]


def query_channels(query):
    cleaned, concepts = decompose(query)
    all_tokens = list(dict.fromkeys(tokens(cleaned + " " + " ".join(concepts))))[:140]
    return {
        "bigram": [t for t in all_tokens if len(t) == 2 or re.search(r"[a-z0-9]", t)],
        "trigram": [t for t in all_tokens if len(t) == 3 or re.search(r"[a-z0-9]", t)],
        "phrase": list(dict.fromkeys([cleaned, *concepts])),
        "title": concepts,
    }


def weighted_rrf(rankings, weights=None, constant=30):
    scores = defaultdict(float)
    for name, ids in rankings.items():
        for rank, chunk_id in enumerate(dict.fromkeys(ids), 1):
            scores[chunk_id] += (weights or {}).get(name, 1.0) / (constant + rank)
    return dict(scores)
