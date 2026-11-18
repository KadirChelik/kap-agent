"""Pydantic v2 schema for dividend (Kar Payı Dağıtımı) disclosures."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DividendExtraction(BaseModel):
    """Structured dividend extraction from a KAP Kar Payı Dağıtım disclosure."""

    model_config = ConfigDict(extra="forbid")

    decision_status: Literal["proposed", "approved", "rejected", "no_dividend"]
    fiscal_year: int = Field(..., ge=2020, le=2030)
    gross_cash_per_share_try: Optional[float] = Field(
        None, ge=0.0, description="1 TL nominal değerli paya brüt nakit kar payı (TL)"
    )
    net_cash_per_share_try: Optional[float] = Field(
        None, ge=0.0, description="1 TL nominal değerli paya net nakit kar payı (TL)"
    )
    total_gross_cash_dividend_try: Optional[float] = Field(None, ge=0.0)
    bonus_share_rate_pct: Optional[float] = Field(None, ge=0.0)
    payment_type: Literal["single", "installments", "none"]
    ex_dividend_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    payment_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    evidence_quote: str = Field(..., min_length=5, max_length=600)

    @model_validator(mode="after")
    def validate_net_not_greater_than_gross(self) -> "DividendExtraction":
        if (
            self.net_cash_per_share_try is not None
            and self.gross_cash_per_share_try is not None
            and self.net_cash_per_share_try > self.gross_cash_per_share_try + 1e-6
        ):
            raise ValueError("net_cash_per_share_try cannot exceed gross_cash_per_share_try.")
        return self
