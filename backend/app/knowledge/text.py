"""Identical deterministic normalization/tokenization for queries and documents."""

import re
import unicodedata
from collections import Counter

TERMS = (
    "奖励骰",
    "惩罚骰",
    "大成功",
    "大失败",
    "困难成功",
    "极难成功",
    "普通成功",
    "常规成功",
    "技能检定",
    "属性检定",
    "信用评级",
    "教育增强",
    "理智",
    "侦查",
    "百分骰",
    "职业技能点",
    "兴趣技能点",
    "普通",
    "困难",
    "极难",
    "力量",
    "体质",
    "敏捷",
    "外貌",
    "意志",
)


def normalize(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    value = normalize(text)
    result = re.findall(r"[a-z0-9]+(?:[%._-][a-z0-9]+)*", value)
    for group in re.findall(r"[\u3400-\u9fff]+", value):
        for width in (2, 3):
            result.extend(group[i : i + width] for i in range(len(group) - width + 1))
    result.extend(term for term in TERMS if term in value)
    return result


def clean_pages(pages):
    """Only repeated *edge* lines, in at least 60% of >=3 pages, are removed."""
    frequency = Counter()
    for page in pages:
        lines = [s.strip() for s in page["text"].splitlines() if s.strip()]
        frequency.update(set(lines[:1] + lines[-1:]))
    repeated = {
        line
        for line, n in frequency.items()
        if len(pages) >= 3 and n >= max(3, len(pages) * 0.6) and len(line) <= 90
    }
    for page in pages:
        lines = page["text"].splitlines()
        for i in (0, len(lines) - 1):
            if lines and lines[i].strip() in repeated:
                lines[i] = ""
        yield {**page, "text": "\n".join(lines).strip()}


def split_page(text, size=800, overlap=100):
    """Page-local offsets: preserve paragraph/list boundaries and a small overlap."""
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            candidates = [text.rfind(mark, start + size // 2, end) for mark in ("\n\n", "\n", "。")]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        if text[start:end].strip():
            yield start, end, text[start:end].strip()
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
