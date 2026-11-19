"""Incremental KAP sync orchestrator: fetch new disclosures, extract via LLM, and upsert SQLite+FTS5."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import BIST30_NON_BANK_TICKERS, Settings, get_settings
from app.db.connection import get_write_conn, init_db
from app.db.seed_loader import load_dataset_into_db
from app.llm.base import LLMProvider
from app.llm.gemini import get_extraction_provider
from app.pipeline.extractor import extract_single_disclosure
from app.pipeline.fetcher import fetch_disclosures_cli
from app.schemas.base import RawDisclosure

_SYNC_LOCK = threading.Lock()


def _existing_db_disclosure_ids(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    init_db(db_path)
    conn = get_write_conn(db_path)
    try:
        rows = conn.execute("SELECT disclosure_id FROM disclosures").fetchall()
        return {str(row[0]) for row in rows}
    finally:
        conn.close()


def run_incremental_sync(
    *,
    settings: Optional[Settings] = None,
    tickers: Optional[list[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    lookback_days: Optional[int] = None,
    limit_per_ticker: Optional[int] = None,
    delay_sec: float = 0.5,
    db_path: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
    extractions_dir: Optional[Path] = None,
    provider: Optional[LLMProvider] = None,
    client: Optional[httpx.Client] = None,
) -> dict[str, Any]:
    """
    Fetch recent KAP disclosures, run cached LLM extraction only for unextracted items,
    and incrementally upsert any new or missing records into SQLite + FTS5.
    """
    cfg = settings or get_settings()
    resolved_db = Path(db_path) if db_path else cfg.db_path
    resolved_raw = Path(raw_dir) if raw_dir else cfg.raw_cache_dir
    resolved_ext = Path(extractions_dir) if extractions_dir else cfg.extractions_cache_dir
    resolved_raw.mkdir(parents=True, exist_ok=True)
    resolved_ext.mkdir(parents=True, exist_ok=True)

    today = date.today()
    resolved_end = end_date or today.isoformat()
    resolved_lookback = lookback_days if lookback_days is not None else cfg.kap_sync_lookback_days
    resolved_start = start_date or (today - timedelta(days=max(1, resolved_lookback))).isoformat()
    resolved_limit = limit_per_ticker if limit_per_ticker is not None else cfg.kap_sync_limit_per_ticker
    target_tickers = tickers or list(BIST30_NON_BANK_TICKERS)

    with _SYNC_LOCK:
        fetch_stats = fetch_disclosures_cli(
            tickers=target_tickers,
            output_dir=resolved_raw,
            start_date=resolved_start,
            end_date=resolved_end,
            limit_per_ticker=resolved_limit,
            delay_sec=delay_sec,
            user_agent=cfg.kap_user_agent,
            client=client,
        )

        db_ids = _existing_db_disclosure_ids(resolved_db)
        raw_files = sorted(resolved_raw.glob("*.json"))
        missing_ext = [p for p in raw_files if not (resolved_ext / p.name).exists()]

        extracted_now = 0
        failed_extractions = 0
        if missing_ext:
            active_provider = provider or get_extraction_provider(cfg)
            for raw_file in missing_ext:
                try:
                    raw = RawDisclosure.model_validate_json(raw_file.read_text(encoding="utf-8"))
                    _, used_cache = extract_single_disclosure(
                        raw=raw,
                        out_dir=resolved_ext,
                        provider=active_provider,
                    )
                    if not used_cache:
                        extracted_now += 1
                except Exception:
                    failed_extractions += 1

        pending_db_ids = {
            p.stem for p in raw_files if p.stem not in db_ids and (resolved_ext / p.name).exists()
        }
        upserted = 0
        if pending_db_ids:
            load_stats = load_dataset_into_db(
                raw_dir=resolved_raw,
                extractions_dir=resolved_ext,
                db_path=resolved_db,
                reset=False,
                disclosure_ids=pending_db_ids,
            )
            upserted = int(load_stats["disclosures"])

        return {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "start_date": resolved_start,
            "end_date": resolved_end,
            "fetched": int(fetch_stats["fetched"]),
            "cached_raw": int(fetch_stats["cached"]),
            "fetch_errors": int(fetch_stats["errors"]),
            "extracted": extracted_now,
            "failed_extractions": failed_extractions,
            "loaded_to_db": upserted,
        }
