import hashlib
import math
import re
from collections import Counter
from pathlib import Path

from app.knowledge.schemas import RetrievedEvidence
from app.knowledge.text import TERMS, normalize, tokens


class KnowledgeRetriever:
    def __init__(self, repository):
        self.repository = repository

    def search(
        self,
        query,
        *,
        refs,
        run_id,
        kind="rules",
        keeper=False,
        edition="coc7",
        top_k=4,
        scene_id=None,
        entity_id=None,
    ):
        if kind == "module" and not keeper:
            raise PermissionError("keeper_only")
        query_text = normalize(query)
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
            query_text = query_text.replace(noise, " ")
        terms = list(dict.fromkeys(tokens(query_text)))[:100]
        concepts = [term for term in TERMS if term in query_text]
        if not terms or not refs or not self.repository.path.exists():
            return []
        allowed = {}
        for ref in refs:
            source = self.repository.source(ref["source_id"], ref["source_hash"])
            if source and source.edition == edition and source.chunk_count:
                if (kind == "module") == (source.kind == "module") and (
                    keeper or source.visibility == "public_rules"
                ):
                    allowed[(source.source_id, source.source_hash)] = source
        if not allowed:
            return []
        # SQL source/version and visibility constraints precede ranking and LIMIT.
        filters = " OR ".join("(c.source_id=? AND c.source_hash=?)" for _ in allowed)
        params = [value for pair in allowed for value in pair]
        metadata_filter = ""
        for key, value in (("scene_id", scene_id), ("entity_id", entity_id)):
            if value:
                # Unstructured pages carry no scene tag. Exclude conflicting known tags.
                metadata_filter += f"AND (c.{key} IS NULL OR c.{key}=?) "
                params.append(value)
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with self.repository.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT c.*, bm25(knowledge_fts) AS bm FROM knowledge_fts "
                    "JOIN knowledge_chunks c ON c.chunk_id=knowledge_fts.chunk_id "
                    f"WHERE knowledge_fts MATCH ? AND ({filters}) "
                    + metadata_filter
                    + ("" if keeper else "AND c.visibility='public_rules' ")
                    + "ORDER BY bm, c.chunk_id LIMIT 120",
                    [expression, *params],
                )
            ]
            query_text = normalize(query_text)
            scored = []
            for row in rows:
                overlap = set(terms) & set(tokens(row["normalized_text"]))
                coverage = len(overlap) / len(terms)
                # Weak isolated n-grams are not sufficient evidence for a long question.
                if len(terms) >= 6 and coverage < 0.16:
                    continue
                score = math.log1p(max(0, -float(row["bm"]))) * 3 + coverage * 8
                concept_hits = sum(term in row["normalized_text"] for term in concepts)
                if concepts and not concept_hits:
                    continue
                score += 14 * concept_hits / max(1, len(concepts))
                section = normalize(row["section"] or "")
                score += 6 * sum(term in section for term in concepts)
                headings = " ".join(re.findall(r"(?m)^\s*\d+\.\d+[^\n]{2,70}", row["display_text"]))
                score += 18 * sum(term in normalize(headings) for term in concepts)
                score += 12 if query_text in row["normalized_text"] else 0
                score += 3 if scene_id and row["scene_id"] == scene_id else 0
                score += 3 if entity_id and row["entity_id"] == entity_id else 0
                scored.append((score, row))
            scored.sort(key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
            # One adjacent chunk can supplement a high ranking passage on the same page.
            seen = {r["chunk_id"] for _, r in scored}
            for score, row in scored[:2]:
                if row["next_id"] and row["next_id"] not in seen:
                    neighbor = db.execute(
                        "SELECT * FROM knowledge_chunks WHERE chunk_id=? AND source_id=? "
                        "AND source_hash=? AND file_reference=? AND physical_page IS ?",
                        (
                            row["next_id"],
                            row["source_id"],
                            row["source_hash"],
                            row["file_reference"],
                            row["physical_page"],
                        ),
                    ).fetchone()
                    if (
                        neighbor
                        and (keeper or neighbor["visibility"] == "public_rules")
                        and (not scene_id or neighbor["scene_id"] in (None, scene_id))
                        and (not entity_id or neighbor["entity_id"] in (None, entity_id))
                    ):
                        scored.append((score * 0.65, dict(neighbor)))
                        seen.add(neighbor["chunk_id"])
        scored.sort(key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
        results, duplicates, pages = [], set(), Counter()
        for score, row in scored:
            fingerprint = row["normalized_text"]
            page_key = (row["source_id"], row["file_reference"], row["physical_page"])
            if fingerprint in duplicates or (row["physical_page"] and pages[page_key] >= 2):
                continue
            source = allowed[(row["source_id"], row["source_hash"])]
            evidence_id = (
                "ev_" + hashlib.sha256(f"{run_id}:{row['chunk_id']}".encode()).hexdigest()[:32]
            )
            text = row["display_text"]
            # Center bounded excerpt around matching terms, without exposing a full chunk.
            hits = [text.lower().find(term) for term in terms if term in text.lower()]
            start = max(0, min(hits, default=0) - 60)
            excerpt = text[start : start + 420]
            results.append(
                RetrievedEvidence(
                    evidence_id=evidence_id,
                    chunk_id=row["chunk_id"],
                    source_id=source.source_id,
                    source_hash=source.source_hash,
                    source_title=source.title,
                    source_kind=source.kind,
                    edition=source.edition,
                    section=row["section"],
                    physical_page=row["physical_page"],
                    page_label=row["page_label"],
                    page_kind=row["page_kind"],
                    file_name=Path(row["file_reference"]).name,
                    excerpt=excerpt,
                    score=round(score, 5) if math.isfinite(score) else 0,
                    rank=len(results) + 1,
                    visibility=row["visibility"],
                    module_id=row["module_id"],
                    scene_id=row["scene_id"],
                    entity_id=row["entity_id"],
                ).model_dump()
            )
            duplicates.add(fingerprint)
            pages[page_key] += 1
            if len(results) >= min(max(top_k, 1), 6):
                break
        return results
