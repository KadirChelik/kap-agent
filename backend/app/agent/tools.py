"""
Read-only SQLite + FTS5 tools exposed to the KAP Agent and MCP server.

All functions enforce strict read-only access via `get_readonly_agent_conn`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from app.agent.sql_guard import validate_and_rewrite_sql
from app.db.connection import get_readonly_agent_conn
from app.schemas.agent import GetDisclosureInput, RunSqlInput, SearchDisclosuresInput

_TOKEN_RE = re.compile(r"[0-9A-Za-zçğıöşüÇĞİÖŞÜ]+")
_TABLE_HINT_RE = re.compile(r"\b(?:from|join)\s+(dividend_events|financial_reports|material_events|disclosures)\b", re.I)


def _build_fts_query(raw_query: str) -> str:
    tokens = [t for t in _TOKEN_RE.findall(raw_query) if len(t) >= 2]
    if not tokens:
        return ""
    return " OR ".join(f'"{tok}"*' for tok in tokens[:8])


def execute_search_disclosures(
    db_path: Path,
    inp: SearchDisclosuresInput,
    *,
    timeout_ms: int = 1500,
) -> list[dict[str, Any]]:
    """Search disclosures by ticker, disclosure_type, date range, and optional FTS5 query."""
    conn = get_readonly_agent_conn(db_path, timeout_ms=timeout_ms)
    try:
        fts_query = _build_fts_query(inp.query) if inp.query else ""
        clauses: list[str] = []
        params: list[Any] = []

        if fts_query:
            sql = (
                "SELECT d.disclosure_id, d.ticker, d.company_name, d.disclosure_type, "
                "d.published_at, d.title, d.summary_tr, d.extraction_confidence, d.source_url "
                "FROM disclosures_fts f "
                "JOIN disclosures d ON d.disclosure_id = f.disclosure_id "
                "WHERE disclosures_fts MATCH ?"
            )
            params.append(fts_query)
        else:
            sql = (
                "SELECT d.disclosure_id, d.ticker, d.company_name, d.disclosure_type, "
                "d.published_at, d.title, d.summary_tr, d.extraction_confidence, d.source_url "
                "FROM disclosures d "
                "WHERE 1=1"
            )

        if inp.ticker:
            clauses.append("d.ticker = ?")
            params.append(inp.ticker.strip().upper())
        if inp.disclosure_type:
            clauses.append("d.disclosure_type = ?")
            params.append(inp.disclosure_type)
        if inp.date_from:
            clauses.append("substr(d.published_at, 1, 10) >= ?")
            params.append(inp.date_from)
        if inp.date_to:
            clauses.append("substr(d.published_at, 1, 10) <= ?")
            params.append(inp.date_to)

        if clauses:
            sql += " AND " + " AND ".join(clauses)

        sql += " ORDER BY d.published_at DESC LIMIT ?"
        params.append(min(25, inp.limit))

        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def execute_get_disclosure(
    db_path: Path,
    inp: GetDisclosureInput,
    *,
    timeout_ms: int = 1500,
) -> Optional[dict[str, Any]]:
    """Return full metadata, extracted structured JSON, and raw text excerpt for one disclosure."""
    conn = get_readonly_agent_conn(db_path, timeout_ms=timeout_ms)
    try:
        row = conn.execute(
            """
            SELECT disclosure_id, ticker, company_name, disclosure_type, published_at,
                   source_url, title, summary_tr, raw_text, extracted_json,
                   extraction_confidence, low_confidence_fields, human_verified
            FROM disclosures
            WHERE disclosure_id = ?
            LIMIT 1
            """,
            (inp.disclosure_id,),
        ).fetchone()
        if row is None:
            return None

        return {
            "disclosure_id": row["disclosure_id"],
            "ticker": row["ticker"],
            "company_name": row["company_name"],
            "disclosure_type": row["disclosure_type"],
            "published_at": row["published_at"],
            "source_url": row["source_url"],
            "title": row["title"],
            "summary_tr": row["summary_tr"],
            "extracted_data": json.loads(row["extracted_json"]),
            "extraction_confidence": row["extraction_confidence"],
            "low_confidence_fields": json.loads(row["low_confidence_fields"] or "[]"),
            "human_verified": bool(row["human_verified"]),
            "raw_text_excerpt": row["raw_text"][:2500],
        }
    finally:
        conn.close()


def execute_run_sql(
    db_path: Path,
    inp: RunSqlInput,
    *,
    timeout_ms: int = 1500,
    max_limit: int = 50,
) -> dict[str, Any]:
    """Validate single-SELECT policy and execute read-only analytical SQL."""
    safe_sql = validate_and_rewrite_sql(inp.sql, max_limit=max_limit)
    conn = get_readonly_agent_conn(db_path, timeout_ms=timeout_ms)
    try:
        cur = conn.execute(safe_sql)
        fetched = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = [dict(r) for r in fetched]

        disclosure_ids: list[str] = []
        seen_ids: set[str] = set()
        for row in rows:
            for key in ("disclosure_id", "disclosure_ids"):
                val = row.get(key)
                if isinstance(val, str) and val.strip():
                    for part in val.split(","):
                        cid = part.strip()
                        if cid and cid not in seen_ids:
                            seen_ids.add(cid)
                            disclosure_ids.append(cid)

        # If an aggregate query returned ticker(s) without disclosure_id, resolve matching IDs
        if not disclosure_ids and rows:
            tickers = [str(r["ticker"]).strip().upper() for r in rows if r.get("ticker")]
            if tickers:
                tables = [m.group(1).lower() for m in _TABLE_HINT_RE.finditer(safe_sql)]
                source_table = next(
                    (t for t in tables if t in ("dividend_events", "financial_reports", "material_events")),
                    "disclosures",
                )
                placeholders = ",".join("?" for _ in tickers)
                id_rows = conn.execute(
                    f"SELECT DISTINCT disclosure_id FROM {source_table} WHERE ticker IN ({placeholders}) LIMIT 50",
                    tickers,
                ).fetchall()
                for id_row in id_rows:
                    cid = str(id_row["disclosure_id"])
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        disclosure_ids.append(cid)

        return {
            "sql_executed": safe_sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "disclosure_ids": disclosure_ids,
        }
    finally:
        conn.close()
