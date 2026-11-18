"""Read-only disclosure browsing and detail inspection endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from app.agent.tools import execute_get_disclosure
from app.config import get_settings
from app.db.connection import get_readonly_agent_conn
from app.schemas.agent import GetDisclosureInput

router = APIRouter(prefix="/api", tags=["disclosures"])


def _resolve_db_path(request: Request) -> Path:
    state_path = getattr(request.app.state, "db_path", None)
    return Path(state_path) if state_path else get_settings().db_path


@router.get("/disclosures")
def list_disclosures(
    request: Request,
    ticker: Optional[str] = Query(default=None, max_length=10),
    disclosure_type: Optional[str] = Query(default=None),
    low_confidence_only: bool = Query(default=False),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """List disclosures using the strict read-only agent connection."""
    db_path = _resolve_db_path(request)
    conn = get_readonly_agent_conn(db_path, timeout_ms=1500)
    try:
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.strip().upper())
        if disclosure_type:
            clauses.append("disclosure_type = ?")
            params.append(disclosure_type.strip())
        if low_confidence_only:
            clauses.append("extraction_confidence < 0.80")

        sql = (
            "SELECT disclosure_id, ticker, company_name, disclosure_type, published_at, "
            "source_url, title, summary_tr, extraction_confidence, low_confidence_fields, human_verified "
            f"FROM disclosures WHERE {' AND '.join(clauses)} "
            "ORDER BY published_at DESC LIMIT ?"
        )
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        items = [
            {
                "disclosure_id": r["disclosure_id"],
                "ticker": r["ticker"],
                "company_name": r["company_name"],
                "disclosure_type": r["disclosure_type"],
                "published_at": r["published_at"],
                "source_url": r["source_url"],
                "title": r["title"],
                "summary_tr": r["summary_tr"],
                "extraction_confidence": float(r["extraction_confidence"]),
                "low_confidence_fields": json.loads(r["low_confidence_fields"] or "[]"),
                "human_verified": bool(r["human_verified"]),
            }
            for r in rows
        ]
        return {"count": len(items), "items": items}
    finally:
        conn.close()


@router.get("/disclosures/{disclosure_id}")
def get_disclosure_detail(disclosure_id: str, request: Request) -> dict[str, Any]:
    """Fetch full disclosure metadata, extracted fields, and source text excerpt."""
    db_path = _resolve_db_path(request)
    try:
        inp = GetDisclosureInput(disclosure_id=disclosure_id)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Geçersiz bildirim kimliği formatı.") from exc
    detail = execute_get_disclosure(db_path, inp, timeout_ms=1500)
    if detail is None:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı.")
    return detail
