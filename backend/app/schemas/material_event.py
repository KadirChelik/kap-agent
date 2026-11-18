"""Pydantic v2 schema for material events (Özel Durum Açıklamaları)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MaterialEventExtraction(BaseModel):
    """Structured extraction for contracts, capex investments, guidance, and buybacks."""

    model_config = ConfigDict(extra="forbid")

    event_category: Literal[
        "new_business_contract",
        "investment_capex",
        "guidance_revision",
        "share_buyback",
        "other",
    ]
    counterparty: Optional[str] = Field(None, max_length=250)
    contract_amount_original: Optional[float] = Field(None, ge=0.0)
    contract_currency: Optional[Literal["TRY", "USD", "EUR", "GBP"]] = None
    contract_amount_try_equiv: Optional[float] = Field(None, ge=0.0)
    revenue_ratio_pct: Optional[float] = Field(None, ge=0.0)
    expected_impact: Literal["positive", "neutral", "negative", "unspecified"] = "unspecified"
    evidence_quote: str = Field(..., min_length=5, max_length=600)
