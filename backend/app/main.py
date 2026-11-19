"""FastAPI application factory, startup auto-seed, CORS, and health diagnostics."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.quota_guard import (
    DEMO_QUESTIONS,
    ChatQuotaManager,
    hash_client_ip,
)
from app.api.routes_chat import router as chat_router
from app.api.routes_disclosures import router as disclosures_router
from app.api.routes_reviews import router as reviews_router
from app.config import get_settings
from app.db.connection import get_write_conn
from app.db.seed_loader import load_dataset_into_db
from app.llm.gemini import get_extraction_provider


def create_app(
    *,
    db_path: Optional[Path] = None,
    admin_token: Optional[str] = None,
    global_rate_limit_per_min: Optional[int] = None,
) -> FastAPI:
    settings = get_settings()
    resolved_db = Path(db_path) if db_path else settings.db_path
    resolved_global_rpm = (
        global_rate_limit_per_min
        if global_rate_limit_per_min is not None
        else settings.api_global_rate_limit_per_min
    )

    # Seed from tracked sample_seed on first boot so local demo works immediately
    if not resolved_db.exists() and settings.sample_raw_dir.exists():
        load_dataset_into_db(
            raw_dir=settings.sample_raw_dir,
            extractions_dir=settings.sample_extractions_dir,
            db_path=resolved_db,
        )

    application = FastAPI(
        title="KAP Agent API",
        version="0.1.0",
        description="Structured BIST KAP disclosure analysis agent (read-only SQLite+FTS5).",
    )
    application.state.db_path = resolved_db
    application.state.admin_token = admin_token or settings.admin_token
    application.state.llm_provider = None
    application.state.quota_manager = ChatQuotaManager()
    application.state.global_api_hits = {}
    application.state.global_rate_limit_per_min = resolved_global_rpm

    @application.middleware("http")
    async def global_api_rate_limit_middleware(request: Request, call_next):
        if request.method == "OPTIONS" or not request.url.path.startswith("/api/"):
            return await call_next(request)

        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        raw_ip = fwd or (request.client.host if request.client else "unknown")
        ip_hash = hash_client_ip(raw_ip)

        now = time.monotonic()
        cutoff = now - 60.0
        hits_store: dict[str, list[float]] = application.state.global_api_hits
        recent = [t for t in hits_store.get(ip_hash, []) if t >= cutoff]
        limit = int(getattr(application.state, "global_rate_limit_per_min", resolved_global_rpm))
        if len(recent) >= limit:
            hits_store[ip_hash] = recent
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Global API hız sınırı aşıldı ({limit} istek/dakika). Lütfen biraz bekleyin."
                },
                headers={"Retry-After": "60"},
            )
        recent.append(now)
        hits_store[ip_hash] = recent
        return await call_next(request)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Admin-Token"],
    )

    @application.get("/api/health")
    def health_check(request: Request) -> dict[str, Any]:
        active_db = Path(getattr(request.app.state, "db_path", resolved_db))
        provider = getattr(request.app.state, "llm_provider", None) or get_extraction_provider(settings)
        is_offline = bool(getattr(provider, "is_offline_fallback", False))
        quota_mgr = getattr(request.app.state, "quota_manager", application.state.quota_manager)
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        raw_ip = fwd or (request.client.host if request.client else "unknown")
        remaining_quota = quota_mgr.get_quota_status(hash_client_ip(raw_ip))

        disclosures_count = 0
        low_conf_count = 0
        pending_reviews_count = 0
        if active_db.exists():
            conn = get_write_conn(active_db)
            try:
                disclosures_count = int(
                    conn.execute("SELECT COUNT(*) FROM disclosures").fetchone()[0]
                )
                low_conf_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM disclosures WHERE extraction_confidence < 0.80"
                    ).fetchone()[0]
                )
                pending_reviews_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM review_flags WHERE status = 'pending'"
                    ).fetchone()[0]
                )
            finally:
                conn.close()

        return {
            "status": "ok",
            "disclosures_count": disclosures_count,
            "low_confidence_count": low_conf_count,
            "pending_reviews_count": pending_reviews_count,
            "has_live_gemini": not is_offline,
            "is_offline_fallback": is_offline,
            "provider_model": getattr(provider, "last_used_model", provider.model_name),
            "execution_mode": (
                f"LIVE_LLM ({provider.model_name})"
                if not is_offline
                else "OFFLINE/SAHTE (Canlı LLM API anahtarı yok — Demo/Test Modu)"
            ),
            "demo_questions": list(DEMO_QUESTIONS),
            "remaining_quota": remaining_quota,
            "rate_limits": {
                "api_per_min": int(getattr(application.state, "global_rate_limit_per_min", resolved_global_rpm)),
                "chat_ip_min": quota_mgr.max_per_minute,
                "chat_ip_day": quota_mgr.max_per_ip_day,
                "chat_global_day": quota_mgr.max_global_day,
                "reviews_ip_min": 5,
            },
        }

    application.include_router(chat_router)
    application.include_router(disclosures_router)
    application.include_router(reviews_router)
    return application


app = create_app()
