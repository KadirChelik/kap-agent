"""Public rate-limited review flag submission and admin-only approval endpoints."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import ValidationError

from app.config import get_settings
from app.db.connection import get_write_conn
from app.pipeline.extractor import SCHEMA_BY_TYPE
from app.schemas.review import ReviewDecision, ReviewFlagCreate

router = APIRouter(prefix="/api", tags=["reviews"])

_MINUTE_WINDOW_SEC = 60.0
_DAY_WINDOW_SEC = 86_400.0
_MAX_PER_MINUTE = 5
_MAX_PER_DAY = 20

# Maps schema field -> (typed_table, column_name, cast_kind)
_TYPED_COLUMN_MAP: dict[str, tuple[str, str, str]] = {
    "decision_status": ("dividend_events", "decision_status", "str"),
    "gross_cash_per_share_try": ("dividend_events", "gross_cash_per_share_try", "float"),
    "net_cash_per_share_try": ("dividend_events", "net_cash_per_share_try", "float"),
    "total_gross_cash_dividend_try": ("dividend_events", "total_gross_cash_dividend_try", "float"),
    "bonus_share_rate_pct": ("dividend_events", "bonus_share_rate_pct", "float"),
    "ex_dividend_date": ("dividend_events", "ex_dividend_date", "str"),
    "payment_date": ("dividend_events", "payment_date", "str"),
    "period": ("financial_reports", "period", "str"),
    "revenue_try": ("financial_reports", "revenue_try", "float"),
    "gross_profit_try": ("financial_reports", "gross_profit_try", "float"),
    "ebitda_try": ("financial_reports", "ebitda_try", "float"),
    "operating_profit_try": ("financial_reports", "operating_profit_try", "float"),
    "net_profit_try": ("financial_reports", "net_profit_try", "float"),
    "total_assets_try": ("financial_reports", "total_assets_try", "float"),
    "total_equity_try": ("financial_reports", "total_equity_try", "float"),
    "net_debt_try": ("financial_reports", "net_debt_try", "float"),
    "event_category": ("material_events", "event_category", "str"),
    "counterparty": ("material_events", "counterparty", "str"),
    "contract_amount_original": ("material_events", "contract_amount_original", "float"),
    "contract_currency": ("material_events", "contract_currency", "str"),
    "contract_amount_try_equiv": ("material_events", "contract_amount_try_equiv", "float"),
    "revenue_ratio_pct": ("material_events", "revenue_ratio_pct", "float"),
    "expected_impact": ("material_events", "expected_impact", "str"),
}

_TABLE_BY_TYPE: dict[str, str] = {
    "dividend": "dividend_events",
    "financial_report": "financial_reports",
    "material_event": "material_events",
}

_review_hits_by_ip: dict[str, list[float]] = {}


def reset_review_rate_limits() -> None:
    """Clear in-memory rate limit buckets (used in tests)."""
    _review_hits_by_ip.clear()


def _client_ip_hash(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    raw_ip = fwd or (request.client.host if request.client else "unknown")
    return hashlib.sha256(raw_ip.encode("utf-8")).hexdigest()[:12]


def _check_review_rate_limit(ip_hash: str) -> None:
    now = time.monotonic()
    hits = [t for t in _review_hits_by_ip.get(ip_hash, []) if now - t < _DAY_WINDOW_SEC]
    minute_hits = [t for t in hits if now - t < _MINUTE_WINDOW_SEC]
    if len(minute_hits) >= _MAX_PER_MINUTE or len(hits) >= _MAX_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail="Çok fazla düzeltme önerisi gönderildi. Lütfen daha sonra tekrar deneyin.",
        )
    hits.append(now)
    _review_hits_by_ip[ip_hash] = hits


def _resolve_db_path(request: Request) -> Path:
    state_path = getattr(request.app.state, "db_path", None)
    return Path(state_path) if state_path else get_settings().db_path


def _require_admin(request: Request, x_admin_token: Optional[str]) -> None:
    expected = getattr(request.app.state, "admin_token", None) or get_settings().admin_token
    if not x_admin_token or not secrets.compare_digest(x_admin_token.strip(), expected):
        raise HTTPException(status_code=401, detail="Geçersiz veya eksik admin anahtarı.")


@router.post("/reviews", status_code=201)
def submit_review_flag(payload: ReviewFlagCreate, request: Request) -> dict[str, Any]:
    """
    Queue a sanitized user correction suggestion into `review_flags` (`status='pending'`).
    Never mutates main disclosures or analytics tables directly.
    """
    ip_hash = _client_ip_hash(request)
    _check_review_rate_limit(ip_hash)

    db_path = _resolve_db_path(request)
    conn = get_write_conn(db_path)
    try:
        row = conn.execute(
            "SELECT disclosure_type, summary_tr, extracted_json FROM disclosures WHERE disclosure_id = ?",
            (payload.disclosure_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Bildirim bulunamadı.")

        dtype = row["disclosure_type"]
        if payload.field_name != "summary_tr":
            col_meta = _TYPED_COLUMN_MAP.get(payload.field_name)
            expected_table = _TABLE_BY_TYPE.get(dtype)
            if not col_meta or col_meta[0] != expected_table:
                raise HTTPException(
                    status_code=422,
                    detail=f"'{payload.field_name}' alanı '{dtype}' bildirim türü için geçerli değil.",
                )
            extracted_dict = json.loads(row["extracted_json"])
            raw_cur = extracted_dict.get(payload.field_name)
            current_value = None if raw_cur is None else str(raw_cur)
        else:
            current_value = row["summary_tr"]

        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """
            INSERT INTO review_flags (
                disclosure_id, field_name, current_value, suggested_value,
                reason_note, reporter_ip_hash, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                payload.disclosure_id,
                payload.field_name,
                current_value,
                payload.suggested_value,
                payload.reason_note,
                ip_hash,
                now_iso,
            ),
        )
        conn.commit()
        return {
            "flag_id": cur.lastrowid,
            "disclosure_id": payload.disclosure_id,
            "field_name": payload.field_name,
            "current_value": current_value,
            "suggested_value": payload.suggested_value,
            "status": "pending",
            "created_at": now_iso,
        }
    finally:
        conn.close()


@router.get("/admin/reviews")
def list_admin_reviews(
    request: Request,
    status: Optional[str] = Query(default=None, pattern=r"^(pending|approved|rejected)$"),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin-only listing of user correction suggestions in `review_flags`."""
    _require_admin(request, x_admin_token)
    db_path = _resolve_db_path(request)
    conn = get_write_conn(db_path)
    try:
        sql = (
            "SELECT r.flag_id, r.disclosure_id, d.ticker, d.disclosure_type, d.title, d.source_url, "
            "r.field_name, r.current_value, r.suggested_value, r.reason_note, "
            "r.status, r.reviewer_note, r.created_at, r.reviewed_at "
            "FROM review_flags r "
            "JOIN disclosures d ON d.disclosure_id = r.disclosure_id "
        )
        params: list[Any] = []
        if status:
            sql += "WHERE r.status = ? "
            params.append(status)
        sql += "ORDER BY CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END, r.created_at DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.post("/admin/reviews/{flag_id}/decide")
def decide_admin_review(
    flag_id: int,
    decision: ReviewDecision,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin-only approval or rejection of a pending review flag."""
    _require_admin(request, x_admin_token)
    db_path = _resolve_db_path(request)
    conn = get_write_conn(db_path)
    try:
        flag = conn.execute(
            "SELECT * FROM review_flags WHERE flag_id = ?",
            (flag_id,),
        ).fetchone()
        if flag is None:
            raise HTTPException(status_code=404, detail="İnceleme kaydı bulunamadı.")
        if flag["status"] != "pending":
            raise HTTPException(status_code=409, detail="Bu kayıt zaten sonuçlandırılmış.")

        now_iso = datetime.now(timezone.utc).isoformat()
        if decision.action == "reject":
            conn.execute(
                """
                UPDATE review_flags
                SET status = 'rejected', reviewer_note = ?, reviewed_at = ?
                WHERE flag_id = ?
                """,
                (decision.reviewer_note, now_iso, flag_id),
            )
            conn.commit()
            return {"flag_id": flag_id, "status": "rejected", "reviewed_at": now_iso}

        # Approve: validate suggested value against the disclosure's Pydantic model first
        disc = conn.execute(
            "SELECT disclosure_type, extracted_json, low_confidence_fields FROM disclosures WHERE disclosure_id = ?",
            (flag["disclosure_id"],),
        ).fetchone()
        if disc is None:
            raise HTTPException(status_code=404, detail="İlgili bildirim bulunamadı.")

        field_name = flag["field_name"]
        suggested_raw = flag["suggested_value"]
        low_fields = [f for f in json.loads(disc["low_confidence_fields"] or "[]") if f != field_name]

        if field_name == "summary_tr":
            conn.execute(
                """
                UPDATE disclosures
                SET summary_tr = ?, low_confidence_fields = ?, human_verified = 1
                WHERE disclosure_id = ?
                """,
                (suggested_raw, json.dumps(low_fields, ensure_ascii=False), flag["disclosure_id"]),
            )
        else:
            col_meta = _TYPED_COLUMN_MAP.get(field_name)
            if not col_meta:
                raise HTTPException(status_code=422, detail=f"Desteklenmeyen alan: {field_name}")
            table_name, col_name, cast_kind = col_meta
            if cast_kind == "float":
                try:
                    casted_val: Any = float(suggested_raw.replace(",", "."))
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail="Önerilen değer geçerli bir sayı değil.") from exc
            else:
                casted_val = suggested_raw

            extracted_dict = json.loads(disc["extracted_json"])
            extracted_dict[field_name] = casted_val
            schema_cls = SCHEMA_BY_TYPE[disc["disclosure_type"]]
            try:
                validated = schema_cls.model_validate(extracted_dict)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=f"Şema doğrulama hatası: {exc}") from exc

            conn.execute(
                """
                UPDATE disclosures
                SET extracted_json = ?, low_confidence_fields = ?, human_verified = 1
                WHERE disclosure_id = ?
                """,
                (
                    validated.model_dump_json(),
                    json.dumps(low_fields, ensure_ascii=False),
                    flag["disclosure_id"],
                ),
            )
            conn.execute(
                f"UPDATE {table_name} SET {col_name} = ? WHERE disclosure_id = ?",
                (casted_val, flag["disclosure_id"]),
            )

        conn.execute(
            """
            UPDATE review_flags
            SET status = 'approved', reviewer_note = ?, reviewed_at = ?
            WHERE flag_id = ?
            """,
            (decision.reviewer_note, now_iso, flag_id),
        )
        conn.commit()
        return {"flag_id": flag_id, "status": "approved", "reviewed_at": now_iso}
    finally:
        conn.close()
