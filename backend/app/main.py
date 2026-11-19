"""FastAPI application factory, startup auto-seed, continuous KAP sync, and health diagnostics."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
from app.pipeline.sync import run_incremental_sync


def create_app(
    *,
    db_path: Optional[Path] = None,
    admin_token: Optional[str] = None,
    global_rate_limit_per_min: Optional[int] = None,
    enable_auto_sync: Optional[bool] = None,
) -> FastAPI:
    settings = get_settings()
    resolved_db = Path(db_path) if db_path else settings.db_path
    resolved_global_rpm = (
        global_rate_limit_per_min
        if global_rate_limit_per_min is not None
        else settings.api_global_rate_limit_per_min
    )
    auto_sync_enabled = (
        enable_auto_sync
        if enable_auto_sync is not None
        else bool(
            db_path is None
            and not os.getenv("PYTEST_CURRENT_TEST")
            and settings.kap_auto_sync_enabled
        )
    )

    # Seed from tracked sample_seed on first boot so local demo works immediately
    if not resolved_db.exists() and settings.sample_raw_dir.exists():
        load_dataset_into_db(
            raw_dir=settings.sample_raw_dir,
            extractions_dir=settings.sample_extractions_dir,
            db_path=resolved_db,
        )

    sync_async_lock = asyncio.Lock()
    sync_state: dict[str, Any] = {
        "auto_sync_enabled": auto_sync_enabled,
        "interval_sec": settings.kap_sync_interval_sec,
        "lookback_days": settings.kap_sync_lookback_days,
        "running": False,
        "last_sync_at": None,
        "last_sync_monotonic": 0.0,
        "last_status": "idle",
        "last_result": None,
        "last_error": None,
    }

    async def execute_sync(app_instance: FastAPI) -> dict[str, Any]:
        async with sync_async_lock:
            sync_state["running"] = True
            active_db = Path(getattr(app_instance.state, "db_path", resolved_db))
            active_provider = getattr(app_instance.state, "llm_provider", None)
            # Cold-start deploys start with <=24 sample rows: scan 45 days on first boot
            lookback_override = settings.kap_sync_lookback_days
            limit_override = settings.kap_sync_limit_per_ticker
            if active_db.exists():
                chk = get_write_conn(active_db)
                try:
                    row_cnt = int(chk.execute("SELECT COUNT(*) FROM disclosures").fetchone()[0])
                    if row_cnt <= 24:
                        lookback_override = max(45, settings.kap_sync_lookback_days)
                        limit_override = max(8, settings.kap_sync_limit_per_ticker)
                finally:
                    chk.close()
            try:
                result = await asyncio.to_thread(
                    run_incremental_sync,
                    settings=settings,
                    lookback_days=lookback_override,
                    limit_per_ticker=limit_override,
                    db_path=active_db,
                    provider=active_provider,
                )
                if int(result.get("loaded_to_db") or 0) > 0:
                    app_instance.state.quota_manager.clear_cache()
                sync_state["last_sync_at"] = result.get("synced_at") or datetime.now(timezone.utc).isoformat()
                sync_state["last_sync_monotonic"] = time.monotonic()
                sync_state["last_status"] = "ok"
                sync_state["last_result"] = result
                sync_state["last_error"] = None
            except Exception as exc:
                sync_state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
                sync_state["last_sync_monotonic"] = time.monotonic()
                sync_state["last_status"] = "error"
                sync_state["last_error"] = str(exc)[:300]
            finally:
                sync_state["running"] = False
            return public_sync_view()

    def public_sync_view() -> dict[str, Any]:
        return {
            k: v for k, v in sync_state.items() if k != "last_sync_monotonic"
        }

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        bg_task: Optional[asyncio.Task[None]] = None
        if auto_sync_enabled:
            async def _sync_loop() -> None:
                await asyncio.sleep(3)
                while True:
                    await execute_sync(app_instance)
                    await asyncio.sleep(settings.kap_sync_interval_sec)

            bg_task = asyncio.create_task(_sync_loop())
        try:
            yield
        finally:
            if bg_task is not None:
                bg_task.cancel()
                try:
                    await bg_task
                except asyncio.CancelledError:
                    pass

    application = FastAPI(
        title="KAP Agent API",
        version="0.1.0",
        description="Structured BIST KAP disclosure analysis agent (read-only SQLite+FTS5).",
        lifespan=lifespan,
    )
    application.state.db_path = resolved_db
    application.state.admin_token = admin_token or settings.admin_token
    application.state.llm_provider = None
    application.state.quota_manager = ChatQuotaManager()
    application.state.global_api_hits = {}
    application.state.global_rate_limit_per_min = resolved_global_rpm
    application.state.sync_state = sync_state

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

    @application.post("/api/sync")
    async def trigger_kap_sync(request: Request) -> dict[str, Any]:
        cooldown_sec = 60.0
        elapsed = time.monotonic() - float(sync_state["last_sync_monotonic"] or 0.0)
        if sync_state["last_sync_monotonic"] and elapsed < cooldown_sec:
            return {
                "triggered": False,
                "cooldown_active": True,
                "retry_after_sec": int(max(1, round(cooldown_sec - elapsed))),
                "sync": public_sync_view(),
            }
        view = await execute_sync(request.app)
        return {
            "triggered": True,
            "cooldown_active": False,
            "retry_after_sec": 0,
            "sync": view,
        }

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
        latest_disclosure_at: Optional[str] = None
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
                latest_row = conn.execute("SELECT MAX(published_at) FROM disclosures").fetchone()
                latest_disclosure_at = str(latest_row[0]) if latest_row and latest_row[0] else None
            finally:
                conn.close()

        return {
            "status": "ok",
            "disclosures_count": disclosures_count,
            "low_confidence_count": low_conf_count,
            "pending_reviews_count": pending_reviews_count,
            "latest_disclosure_at": latest_disclosure_at,
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
            "sync": public_sync_view(),
        }

    application.include_router(chat_router)
    application.include_router(disclosures_router)
    application.include_router(reviews_router)
    return application


app = create_app()
