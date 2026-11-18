"""Pydantic v2 schema for non-bank corporate financial reports."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.review import normalize_evidence_quote


class FinancialReportExtraction(BaseModel):
    """Normalized TRY financial summary extracted from KAP non-bank reports."""

    model_config = ConfigDict(extra="forbid")

    period: str = Field(..., pattern=r"^20\d{2}/(03|06|09|12)$")
    consolidation: Literal["consolidated", "unconsolidated"]
    currency: Literal["TRY", "USD", "EUR"] = "TRY"
    presentation_scale: Literal["full_try", "thousands", "millions"] = "full_try"
    revenue_try: Optional[float] = None
    gross_profit_try: Optional[float] = None
    ebitda_try: Optional[float] = None
    operating_profit_try: Optional[float] = None
    net_profit_try: Optional[float] = None
    total_assets_try: Optional[float] = None
    total_equity_try: Optional[float] = None
    net_debt_try: Optional[float] = None
    evidence_quote: str = Field(..., min_length=5, max_length=600)

    @field_validator("presentation_scale", mode="before")
    @classmethod
    def _default_scale_when_missing(cls, v: object) -> object:
        return "full_try" if v is None or v == "" else v

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _trim_quote(cls, v: object) -> object:
        return normalize_evidence_quote(v)
