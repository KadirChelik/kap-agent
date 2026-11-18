"""Sanitized review flag models for human-in-the-loop extraction corrections."""

from __future__ import annotations

import html
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_REVIEW_FIELDS: frozenset[str] = frozenset(
    {
        "summary_tr",
        "decision_status",
        "gross_cash_per_share_try",
        "net_cash_per_share_try",
        "total_gross_cash_dividend_try",
        "bonus_share_rate_pct",
        "ex_dividend_date",
        "payment_date",
        "period",
        "revenue_try",
        "gross_profit_try",
        "ebitda_try",
        "operating_profit_try",
        "net_profit_try",
        "total_assets_try",
        "total_equity_try",
        "net_debt_try",
        "event_category",
        "counterparty",
        "contract_amount_original",
        "contract_currency",
        "contract_amount_try_equiv",
        "revenue_ratio_pct",
        "expected_impact",
    }
)

_SCRIPT_OR_STYLE_RE = re.compile(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", re.I | re.S)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
_MULTI_SPACE_RE = re.compile(r"\s+")


def sanitize_user_text(raw: str, max_len: int) -> str:
    """Strip HTML/script payloads and control characters from untrusted user text."""
    unescaped = html.unescape(raw)
    without_scripts = _SCRIPT_OR_STYLE_RE.sub(" ", unescaped)
    without_tags = _HTML_TAG_RE.sub(" ", without_scripts)
    without_ctrl = _CONTROL_CHARS_RE.sub(" ", without_tags)
    collapsed = _MULTI_SPACE_RE.sub(" ", without_ctrl).strip()
    return collapsed[:max_len]


def normalize_evidence_quote(value: object, max_len: int = 600) -> object:
    """Trim verbose LLM quotes to schema bounds and provide a non-empty fallback."""
    if not isinstance(value, str):
        return value
    cleaned = _MULTI_SPACE_RE.sub(" ", value).strip()
    if len(cleaned) < 5:
        return "KAP bildirim metni dayanağı."
    return cleaned[:max_len]


class ReviewFlagCreate(BaseModel):
    """Untrusted public payload to flag a low-confidence or inaccurate field."""

    model_config = ConfigDict(extra="forbid")

    disclosure_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    field_name: str
    suggested_value: str = Field(..., min_length=1, max_length=120)
    reason_note: Optional[str] = Field(None, max_length=400)

    @field_validator("field_name")
    @classmethod
    def check_field_whitelist(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in ALLOWED_REVIEW_FIELDS:
            raise ValueError(f"Unsupported review field '{cleaned}'.")
        return cleaned

    @field_validator("suggested_value", mode="before")
    @classmethod
    def sanitize_suggested_value(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("suggested_value must be a string.")
        cleaned = sanitize_user_text(value, max_len=120)
        if not cleaned:
            raise ValueError("suggested_value cannot be empty after sanitization.")
        return cleaned

    @field_validator("reason_note", mode="before")
    @classmethod
    def sanitize_reason_note(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("reason_note must be a string.")
        cleaned = sanitize_user_text(value, max_len=400)
        return cleaned or None


class ReviewDecision(BaseModel):
    """Admin-only decision payload to approve or reject a flagged field."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    reviewer_note: Optional[str] = Field(None, max_length=300)

    @field_validator("reviewer_note", mode="before")
    @classmethod
    def sanitize_reviewer_note(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("reviewer_note must be a string.")
        cleaned = sanitize_user_text(value, max_len=300)
        return cleaned or None
