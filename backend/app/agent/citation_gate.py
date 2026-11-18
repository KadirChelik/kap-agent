"""Investment advice refusal detector and strict [kap:ID] citation gate."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.db.connection import get_readonly_agent_conn
from app.schemas.agent import CitedDisclosure

_ADVICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(alınır\s*mı|alinir\s*mi|satılır\s*mı|satilir\s*mi|tutulur\s*mu|"
        r"almalı\s*mıyım|almali\s*miyim|satmalı\s*mıyım|satmali\s*miyim|"
        r"alayım\s*mı|alayim\s*mi|satayım\s*mı|satayim\s*mi|gireyim\s*mi)\b",
        re.I,
    ),
    re.compile(
        r"(al[\s/-]*sat\s*(tavsiy|öner)|hedef\s*fiyat|yatırım\s*tavsiy|"
        r"hangi\s*hisseyi\s*(al|seç|öner)|yükselecek\s*mi|düşecek\s*mi|"
        r"tavan\s*yapar|portföy\s*öner|ucuz\s*mu\s*pahalı\s*mı|"
        r"fiyatı\s*ne\s*olur|kazandırır\s*mı)",
        re.I,
    ),
    re.compile(
        r"\b(should\s+i\s+(buy|sell|hold|invest)|buy\s+or\s+sell|stock\s+pick|price\s+target)\b",
        re.I,
    ),
)

_CITE_BLOCK_RE = re.compile(r"\[kap:([^\]]+)\]", re.I)
_CITE_ID_TOKEN_RE = re.compile(r"(?:kap:)?([A-Za-z0-9_-]+)")

ADVICE_REFUSAL_TEXT = (
    "Yatırım danışmanlığı, hedef fiyat tahmini veya al-sat-tut önerisi veremiyorum. "
    "Yalnızca mevcut KAP bildirimlerindeki temettü, finansal rapor ve özel durum "
    "açıklamalarını kaynak göstererek analiz edebilirim."
)

NO_EVIDENCE_TEXT = "Mevcut KAP bildirim veri setinde bu soruyu doğrulayacak kayıt bulunamadı."


def is_investment_advice_query(question: str) -> bool:
    """Detect whether a user question requests buy/sell/target-price investment advice."""
    text = question.strip()
    return any(pat.search(text) for pat in _ADVICE_PATTERNS)


def extract_cited_ids(text: str) -> list[str]:
    """Extract ordered unique disclosure IDs from `[kap:ID]` or `[kap:ID1, ID2]` tags."""
    ordered: list[str] = []
    seen: set[str] = set()
    for match in _CITE_BLOCK_RE.finditer(text):
        inner = match.group(1)
        for id_match in _CITE_ID_TOKEN_RE.finditer(inner):
            disc_id = id_match.group(1).strip()
            if disc_id and disc_id not in seen:
                seen.add(disc_id)
                ordered.append(disc_id)
    return ordered


def _load_verified_disclosures_by_ids(
    db_path: Path,
    disclosure_ids: list[str],
) -> dict[str, CitedDisclosure]:
    if not disclosure_ids:
        return {}
    conn = get_readonly_agent_conn(db_path, timeout_ms=1000)
    try:
        placeholders = ",".join("?" for _ in disclosure_ids)
        rows = conn.execute(
            f"""
            SELECT disclosure_id, ticker, company_name, disclosure_type, published_at,
                   title, source_url, extraction_confidence, low_confidence_fields
            FROM disclosures
            WHERE disclosure_id IN ({placeholders})
            """,
            disclosure_ids,
        ).fetchall()
        result: dict[str, CitedDisclosure] = {}
        for row in rows:
            result[row["disclosure_id"]] = CitedDisclosure(
                disclosure_id=row["disclosure_id"],
                ticker=row["ticker"],
                company_name=row["company_name"],
                disclosure_type=row["disclosure_type"],
                published_at=row["published_at"],
                title=row["title"],
                source_url=row["source_url"],
                extraction_confidence=float(row["extraction_confidence"]),
                low_confidence_fields=json.loads(row["low_confidence_fields"] or "[]"),
            )
        return result
    finally:
        conn.close()


def enforce_citation_gate(
    *,
    answer_text: str,
    observed_disclosure_ids: set[str],
    db_path: Path,
) -> tuple[str, list[CitedDisclosure], bool, int]:
    """
    Validate `[kap:ID]` citations against tool-observed IDs and SQLite records.
    Returns `(cleaned_answer, verified_citations, no_evidence_found, stripped_ungrounded_count)`.
    """
    raw_cited = extract_cited_ids(answer_text)
    if not raw_cited:
        return NO_EVIDENCE_TEXT, [], True, 0

    observed_matches = [cid for cid in raw_cited if cid in observed_disclosure_ids]
    db_map = _load_verified_disclosures_by_ids(db_path, observed_matches)
    valid_ids = [cid for cid in observed_matches if cid in db_map]
    valid_set = set(valid_ids)

    stripped_count = len(raw_cited) - len(valid_ids)

    if not valid_ids:
        return NO_EVIDENCE_TEXT, [], True, stripped_count

    def _filter_cite_block(match: re.Match[str]) -> str:
        ids_in_block = _CITE_ID_TOKEN_RE.findall(match.group(1))
        kept = [cid for cid in ids_in_block if cid in valid_set]
        if not kept:
            return ""
        return " ".join(f"[kap:{cid}]" for cid in kept)

    cleaned_answer = _CITE_BLOCK_RE.sub(_filter_cite_block, answer_text)
    cleaned_answer = re.sub(r" {2,}", " ", cleaned_answer).strip()

    citations = [db_map[cid] for cid in valid_ids]
    return cleaned_answer, citations, False, stripped_count
