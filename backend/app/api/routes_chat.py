"""Streaming SSE and JSON chat endpoints with quota protection and Demo/Replay fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.quota_guard import (
    ChatQuotaManager,
    default_quota_manager,
    hash_client_ip,
)
from app.config import get_settings
from app.schemas.agent import AgentRunResult
from app.schemas.review import sanitize_user_text

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    """Validated user question payload for POST /api/chat."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=2, max_length=500)
    stream: bool = True

    @field_validator("question", mode="before")
    @classmethod
    def sanitize_question(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("question must be a string.")
        cleaned = sanitize_user_text(value, max_len=500)
        if len(cleaned) < 2:
            raise ValueError("Soru en az 2 karakter olmalıdır.")
        return cleaned


def _resolve_db_path(request: Request) -> Path:
    state_path = getattr(request.app.state, "db_path", None)
    return Path(state_path) if state_path else get_settings().db_path


def _resolve_quota_manager(request: Request) -> ChatQuotaManager:
    qm = getattr(request.app.state, "quota_manager", None)
    return qm if isinstance(qm, ChatQuotaManager) else default_quota_manager


def _client_ip_hash(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    raw_ip = fwd or (request.client.host if request.client else "unknown")
    return hash_client_ip(raw_ip)


def _format_sse(event_name: str, payload: object) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _iter_answer_chunks(text: str, chunk_size: int = 36) -> Iterator[str]:
    if not text:
        return
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]


def _stream_agent_result(
    result: AgentRunResult,
    *,
    remaining_quota: dict[str, int],
) -> Iterator[str]:
    for step in result.trace:
        yield _format_sse("trace_step", step.model_dump())

    if result.artifact is not None:
        yield _format_sse("artifact", result.artifact.model_dump())

    for chunk in _iter_answer_chunks(result.answer_text):
        yield _format_sse("delta", {"text": chunk})

    yield _format_sse(
        "citations",
        {"citations": [c.model_dump() for c in result.citations]},
    )
    yield _format_sse(
        "done",
        {
            "mode": result.mode,
            "provider_model": result.provider_model,
            "is_offline_fallback": result.mode == "offline_fallback",
            "quota_exhausted": result.quota_exhausted,
            "cached_hit": result.cached_hit,
            "remaining_quota": remaining_quota,
            "sql_attempts": result.sql_attempts,
            "sql_retries": result.sql_retries,
            "first_try_sql_success": result.first_try_sql_success,
            "refused_as_advice": result.refused_as_advice,
            "no_evidence_found": result.no_evidence_found,
            "total_duration_ms": result.total_duration_ms,
        },
    )


@router.post("/chat")
def chat_endpoint(payload: ChatRequest, request: Request):
    """Execute KAP Agent through quota/cache guard and return SSE or JSON."""
    db_path = _resolve_db_path(request)
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="Database is not initialized yet.")

    provider_override = getattr(request.app.state, "llm_provider", None)
    quota_manager = _resolve_quota_manager(request)
    ip_hash = _client_ip_hash(request)

    result = quota_manager.run_with_quota_and_demo(
        question=payload.question,
        ip_hash=ip_hash,
        db_path=db_path,
        provider=provider_override,
    )
    remaining_quota = quota_manager.get_quota_status(ip_hash)

    if not payload.stream:
        out = result.model_dump()
        out["is_offline_fallback"] = result.mode == "offline_fallback"
        out["remaining_quota"] = remaining_quota
        return out

    return StreamingResponse(
        _stream_agent_result(result, remaining_quota=remaining_quota),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
