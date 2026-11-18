"""KAP Agent orchestrator: tool-calling loop, SQL self-healing, and citation gating."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from app.agent.citation_gate import (
    ADVICE_REFUSAL_TEXT,
    NO_EVIDENCE_TEXT,
    enforce_citation_gate,
    is_investment_advice_query,
)
from app.agent.prompts import AGENT_SYSTEM_PROMPT, SQL_SCHEMA_CHEATSHEET
from app.agent.sql_guard import SQLGuardError
from app.agent.tools import (
    execute_get_disclosure,
    execute_run_sql,
    execute_search_disclosures,
)
from app.config import get_settings
from app.llm.base import LLMProvider
from app.llm.gemini import get_extraction_provider
from app.schemas.agent import (
    AgentRunResult,
    GetDisclosureInput,
    ResultArtifact,
    RunSqlInput,
    SearchDisclosuresInput,
    TraceStep,
)


def _summarize_tool_output(tool_name: str, result: Any) -> str:
    if tool_name == "run_sql" and isinstance(result, dict):
        return f"{result.get('row_count', 0)} satır döndü ({len(result.get('disclosure_ids', []))} kaynak ID)."
    if tool_name == "search_disclosures" and isinstance(result, list):
        ids = [str(r.get("disclosure_id", "")) for r in result[:5]]
        return f"{len(result)} bildirim bulundu{f': {ids}' if ids else ''}."
    if tool_name == "get_disclosure":
        return "Bildirim detayı yüklendi." if result else "Bildirim bulunamadı."
    return "Tamamlandı."


def run_kap_agent(
    question: str,
    *,
    db_path: Optional[Path] = None,
    provider: Optional[LLMProvider] = None,
    max_steps: int = 5,
    max_sql_retries: int = 3,
    sql_timeout_ms: int = 1500,
) -> AgentRunResult:
    """
    Execute one full KAP Agent turn with:
      1. Investment advice guardrail pre-check
      2. Bounded tool-calling loop (`search_disclosures`, `get_disclosure`, `run_sql`)
      3. Self-healing SQL repair loop (up to `max_sql_retries` retries)
      4. Strict `[kap:ID]` citation gate against observed tool outputs
    """
    t0 = time.perf_counter()
    settings = get_settings()
    resolved_db = db_path or settings.db_path
    active_provider = provider or get_extraction_provider(settings)
    is_offline = bool(getattr(active_provider, "is_offline_fallback", False))

    trace: list[TraceStep] = []

    if is_investment_advice_query(question):
        trace.append(
            TraceStep(
                step_index=1,
                step_type="guardrail",
                tool_name=None,
                input_summary=question[:160],
                output_summary="Yatırım tavsiyesi / al-sat önerisi talebi engellendi.",
                is_error=False,
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        )
        return AgentRunResult(
            question=question,
            answer_text=ADVICE_REFUSAL_TEXT,
            citations=[],
            artifact=None,
            trace=trace,
            sql_attempts=0,
            sql_retries=0,
            first_try_sql_success=None,
            refused_as_advice=True,
            no_evidence_found=False,
            mode="guardrail_refusal",
            provider_model=active_provider.model_name,
            total_duration_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    transcript_parts: list[str] = [f"SORU: {question.strip()}", "GECMIS ADIMLAR:"]
    observed_disclosure_ids: set[str] = set()
    last_artifact: Optional[ResultArtifact] = None
    sql_attempts = 0
    sql_retries = 0
    first_try_sql_success: Optional[bool] = None
    step_counter = 0

    for _ in range(max_steps):
        decision = active_provider.plan_agent_step(
            system_prompt=AGENT_SYSTEM_PROMPT,
            transcript="\n".join(transcript_parts),
        )

        if decision.action == "final_answer":
            gate_t0 = time.perf_counter()
            cleaned_answer, citations, no_evidence, stripped_count = enforce_citation_gate(
                answer_text=decision.answer_text or "",
                observed_disclosure_ids=observed_disclosure_ids,
                db_path=resolved_db,
            )
            step_counter += 1
            trace.append(
                TraceStep(
                    step_index=step_counter,
                    step_type="citation_gate",
                    tool_name=None,
                    input_summary=f"Cited candidates validated against {len(observed_disclosure_ids)} observed IDs",
                    output_summary=(
                        f"{len(citations)} kaynak doğrulandı"
                        + (f", {stripped_count} dayanaksız referans temizlendi" if stripped_count else "")
                    ),
                    is_error=no_evidence and stripped_count > 0,
                    duration_ms=round((time.perf_counter() - gate_t0) * 1000, 2),
                )
            )

            if (
                last_artifact is not None
                and decision.chart_spec is not None
                and decision.chart_spec.x_key in last_artifact.columns
                and decision.chart_spec.y_key in last_artifact.columns
            ):
                last_artifact = last_artifact.model_copy(update={"chart_spec": decision.chart_spec})

            return AgentRunResult(
                question=question,
                answer_text=cleaned_answer,
                citations=citations,
                artifact=last_artifact if not no_evidence else None,
                trace=trace,
                sql_attempts=sql_attempts,
                sql_retries=sql_retries,
                first_try_sql_success=first_try_sql_success,
                refused_as_advice=False,
                no_evidence_found=no_evidence,
                mode="offline_fallback" if is_offline else "live_llm",
                provider_model=active_provider.model_name,
                total_duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        # Tool call execution
        tool_name = decision.tool_name or "search_disclosures"
        tool_args = decision.tool_args or {}
        step_counter += 1
        call_summary = json.dumps(tool_args, ensure_ascii=False)[:260]
        trace.append(
            TraceStep(
                step_index=step_counter,
                step_type="tool_call",
                tool_name=tool_name,
                input_summary=call_summary,
                output_summary=decision.thought[:180],
                is_error=False,
                duration_ms=0.0,
            )
        )

        tool_t0 = time.perf_counter()
        if tool_name == "search_disclosures":
            try:
                inp = SearchDisclosuresInput.model_validate(tool_args)
                rows = execute_search_disclosures(
                    resolved_db, inp, timeout_ms=sql_timeout_ms
                )
                for row in rows:
                    cid = str(row.get("disclosure_id") or "").strip()
                    if cid:
                        observed_disclosure_ids.add(cid)
                tool_output_obj: Any = rows
                is_err = False
                step_type = "tool_result"
            except (ValidationError, sqlite3.Error) as exc:
                tool_output_obj = {"error": str(exc)}
                is_err = True
                step_type = "tool_result"

        elif tool_name == "get_disclosure":
            try:
                inp = GetDisclosureInput.model_validate(tool_args)
                detail = execute_get_disclosure(
                    resolved_db, inp, timeout_ms=sql_timeout_ms
                )
                if detail and detail.get("disclosure_id"):
                    observed_disclosure_ids.add(str(detail["disclosure_id"]))
                tool_output_obj = detail or {"error": f"Disclosure not found: {inp.disclosure_id}"}
                is_err = detail is None
                step_type = "tool_result"
            except (ValidationError, sqlite3.Error) as exc:
                tool_output_obj = {"error": str(exc)}
                is_err = True
                step_type = "tool_result"

        else:  # run_sql
            sql_attempts += 1
            try:
                if sql_retries >= max_sql_retries:
                    raise SQLGuardError(
                        f"Maximum SQL self-healing retries ({max_sql_retries}) exceeded."
                    )
                inp = RunSqlInput.model_validate(tool_args)
                res = execute_run_sql(
                    resolved_db,
                    inp,
                    timeout_ms=sql_timeout_ms,
                    max_limit=settings.sql_max_limit,
                )
                if first_try_sql_success is None:
                    first_try_sql_success = True
                for cid in res.get("disclosure_ids", []):
                    observed_disclosure_ids.add(str(cid))
                last_artifact = ResultArtifact(
                    columns=res["columns"],
                    rows=res["rows"],
                    sql_query=res["sql_executed"],
                    chart_spec=None,
                )
                tool_output_obj = res
                is_err = False
                step_type = "tool_result"
            except (ValidationError, SQLGuardError, sqlite3.Error) as exc:
                if first_try_sql_success is None:
                    first_try_sql_success = False
                sql_retries += 1
                is_err = True
                step_type = "sql_retry"
                tool_output_obj = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "retries_used": sql_retries,
                    "max_sql_retries": max_sql_retries,
                    "schema_hint": SQL_SCHEMA_CHEATSHEET,
                }

        duration_ms = round((time.perf_counter() - tool_t0) * 1000, 2)
        step_counter += 1
        trace.append(
            TraceStep(
                step_index=step_counter,
                step_type=step_type,  # type: ignore[arg-type]
                tool_name=tool_name,
                input_summary=call_summary,
                output_summary=(
                    str(tool_output_obj.get("error"))[:260]
                    if is_err and isinstance(tool_output_obj, dict)
                    else _summarize_tool_output(tool_name, tool_output_obj)
                ),
                is_error=is_err,
                duration_ms=duration_ms,
            )
        )

        transcript_parts.append(f"[TOOL_RESULT tool={tool_name}]")
        transcript_parts.append(json.dumps(tool_output_obj, ensure_ascii=False))

    # Max steps exhausted -> fail closed unless grounded citations exist
    return AgentRunResult(
        question=question,
        answer_text=NO_EVIDENCE_TEXT,
        citations=[],
        artifact=None,
        trace=trace,
        sql_attempts=sql_attempts,
        sql_retries=sql_retries,
        first_try_sql_success=first_try_sql_success,
        refused_as_advice=False,
        no_evidence_found=True,
        mode="offline_fallback" if is_offline else "live_llm",
        provider_model=active_provider.model_name,
        total_duration_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
