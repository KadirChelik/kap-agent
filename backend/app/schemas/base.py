"""Common disclosure envelope and metadata models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.config import EXCLUDED_BANK_TICKERS
from app.schemas.dividend import DividendExtraction
from app.schemas.financial import FinancialReportExtraction
from app.schemas.material_event import MaterialEventExtraction

DisclosureType = Literal["dividend", "financial_report", "material_event"]
ExtractionPayload = Union[
    DividendExtraction,
    FinancialReportExtraction,
    MaterialEventExtraction,
]


def _normalize_ticker(value: str) -> str:
    return value.strip().upper()


class RawDisclosure(BaseModel):
    """Raw disclosure persisted by the KAP CLI fetcher or sample seed."""

    model_config = ConfigDict(extra="forbid")

    disclosure_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    ticker: str = Field(..., pattern=r"^[A-Z]{4,6}$")
    company_name: str = Field(..., min_length=2, max_length=200)
    disclosure_type: DisclosureType
    published_at: datetime
    source_url: HttpUrl
    title: str = Field(..., min_length=3, max_length=400)
    raw_text: str = Field(..., min_length=20)
    fetched_at: datetime | None = None
    source: str = Field(default="kap_live", max_length=40)

    @field_validator("ticker", mode="before")
    @classmethod
    def uppercase_ticker(cls, v: object) -> object:
        return _normalize_ticker(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def reject_banks_for_financial_reports(self) -> "RawDisclosure":
        if self.disclosure_type == "financial_report" and self.ticker in EXCLUDED_BANK_TICKERS:
            raise ValueError(
                f"Banking ticker '{self.ticker}' is excluded from V1 financial_report schema."
            )
        return self


class DisclosureMetadata(BaseModel):
    """Normalized disclosure header stored alongside structured extractions."""

    model_config = ConfigDict(extra="forbid")

    disclosure_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    ticker: str = Field(..., pattern=r"^[A-Z]{4,6}$")
    company_name: str = Field(..., min_length=2, max_length=200)
    disclosure_type: DisclosureType
    published_at: datetime
    source_url: HttpUrl
    title: str = Field(..., min_length=3, max_length=400)
    summary_tr: str = Field(..., min_length=5, max_length=400)
    extraction_confidence: float = Field(..., ge=0.0, le=1.0)
    low_confidence_fields: list[str] = Field(default_factory=list)
    human_verified: bool = False

    @field_validator("ticker", mode="before")
    @classmethod
    def uppercase_ticker(cls, v: object) -> object:
        return _normalize_ticker(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def reject_banks_for_financial_reports(self) -> "DisclosureMetadata":
        if self.disclosure_type == "financial_report" and self.ticker in EXCLUDED_BANK_TICKERS:
            raise ValueError(
                f"Banking ticker '{self.ticker}' is excluded from V1 financial_report schema."
            )
        return self


class DisclosureEnvelope(BaseModel):
    """Cached extraction artifact stored per disclosure in data/extractions/."""

    model_config = ConfigDict(extra="forbid")

    metadata: DisclosureMetadata
    extracted: ExtractionPayload
    content_sha256: str = Field(..., min_length=16, max_length=64)
    extractor_model: str = Field(default="gemini-2.5-flash", max_length=80)
    extracted_at: datetime

    @model_validator(mode="after")
    def verify_type_matches_payload(self) -> "DisclosureEnvelope":
        expected_type = {
            "dividend": DividendExtraction,
            "financial_report": FinancialReportExtraction,
            "material_event": MaterialEventExtraction,
        }[self.metadata.disclosure_type]
        if not isinstance(self.extracted, expected_type):
            raise ValueError(
                f"disclosure_type='{self.metadata.disclosure_type}' requires {expected_type.__name__}."
            )
        return self


# Backward-friendly alias for callers importing ExtractionBundle
ExtractionBundle = DisclosureEnvelope
