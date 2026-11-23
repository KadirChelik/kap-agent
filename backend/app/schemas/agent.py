"""Pydantic schemas for KAP Agent tool inputs, trace events, citations, and turn decisions."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.base import DisclosureType

ToolName = Literal["search_disclosures", "get_disclosure", "run_sql"]


class SearchDisclosuresInput(BaseModel):
    """Input arguments for FTS5 + metadata search tool."""

    model_config = ConfigDict(extra="forbid")

    ticker: Optional[str] = Field(None, max_length=10)
    disclosure_type: Optional[DisclosureType] = None
    query: Optional[str] = Field(None, max_length=200)
    date_from: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    limit: int = Field(default=10, ge=1, le=25)


class GetDisclosureInput(BaseModel):
    """Input argument to inspect full text and structured extraction of a single disclosure."""

    model_config = ConfigDict(extra="forbid")

    disclosure_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


class RunSqlInput(BaseModel):
    """Input arguments for read-only analytical SQL query tool."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(..., min_length=6, max_length=2000)
    purpose: str = Field(default="Analitik sorgu", max_length=200)

    @model_validator(mode="before")
    @classmethod
    def _normalize_sql_alias(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        copied = dict(data)
        if not str(copied.get("sql") or "").strip():
            for alias in ("query", "sql_query", "statement"):
                val = copied.get(alias)
                if isinstance(val, str) and val.strip():
                    copied["sql"] = copied.pop(alias)
                    break
        return copied


class ChartSpec(BaseModel):
    """Declarative chart hint rendered alongside tabular query outputs."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["bar", "line"] = "bar"
    title: str = Field(..., max_length=160)
    x_key: str = Field(..., max_length=80)
    y_key: str = Field(..., max_length=80)


class ResultArtifact(BaseModel):
    """Structured table & optional chart produced by a successful run_sql query."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[dict[str, Any]]
    sql_query: Optional[str] = None
    chart_spec: Optional[ChartSpec] = None


class TraceStep(BaseModel):
    """Observable execution telemetry entry for UI Trace Panel and Evals."""

    model_config = ConfigDict(extra="forbid")

    step_index: int
    step_type: Literal["tool_call", "tool_result", "sql_retry", "guardrail", "citation_gate"]
    tool_name: Optional[str] = None
    input_summary: str
    output_summary: str
    is_error: bool = False
    duration_ms: float = Field(..., ge=0.0)


class CitedDisclosure(BaseModel):
    """Verified disclosure metadata surfaced in chat citation badges & side drawer."""

    model_config = ConfigDict(extra="forbid")

    disclosure_id: str
    ticker: str
    company_name: str
    disclosure_type: DisclosureType
    published_at: str
    title: str
    source_url: str
    extraction_confidence: float
    low_confidence_fields: list[str] = Field(default_factory=list)


class AgentTurnDecision(BaseModel):
    """Structured turn decision emitted by LLMProvider during agent reasoning."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["call_tool", "final_answer"]
    thought: str = Field(..., max_length=600)
    tool_name: Optional[ToolName] = None
    tool_args: Optional[dict[str, Any]] = None
    answer_text: Optional[str] = None
    chart_spec: Optional[ChartSpec] = None

    @model_validator(mode="after")
    def check_required_fields_for_action(self) -> "AgentTurnDecision":
        if self.action == "call_tool" and self.tool_name is None:
            raise ValueError("tool_name is required when action='call_tool'.")
        if self.action == "final_answer" and not (self.answer_text or "").strip():
            raise ValueError("answer_text is required when action='final_answer'.")
        return self


class AgentRunResult(BaseModel):
    """Complete output of one KAP Agent question turn."""

    model_config = ConfigDict(extra="forbid")

    question: str
    answer_text: str
    citations: list[CitedDisclosure] = Field(default_factory=list)
    artifact: Optional[ResultArtifact] = None
    trace: list[TraceStep] = Field(default_factory=list)
    sql_attempts: int = 0
    sql_retries: int = 0
    first_try_sql_success: Optional[bool] = None
    refused_as_advice: bool = False
    no_evidence_found: bool = False
    quota_exhausted: bool = False
    cached_hit: bool = False
    mode: Literal["live_llm", "offline_fallback", "guardrail_refusal", "cached_demo"] = "live_llm"
    provider_model: str
    total_duration_ms: float = 0.0
