"""Load validated raw disclosures and structured extractions into SQLite + FTS5."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.db.connection import get_write_conn, init_db
from app.schemas.base import DisclosureEnvelope, RawDisclosure
from app.schemas.dividend import DividendExtraction
from app.schemas.financial import FinancialReportExtraction
from app.schemas.material_event import MaterialEventExtraction


def load_dataset_into_db(
    *,
    raw_dir: Path,
    extractions_dir: Path,
    db_path: Path,
    reset: bool = False,
) -> dict[str, int]:
    """
    Validate all raw + extraction JSON pairs and upsert them into SQLite + FTS5.
    When `reset=True`, clears existing tables before loading.
    Returns row counts loaded per table.
    """
    init_db(db_path)
    counts = {
        "disclosures": 0,
        "dividend": 0,
        "financial_report": 0,
        "material_event": 0,
        "skipped_missing_extraction": 0,
    }

    conn = get_write_conn(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        if reset:
            for tbl in ("review_flags", "dividend_events", "financial_reports", "material_events", "disclosures"):
                conn.execute(f"DELETE FROM {tbl}")
            conn.commit()
        for raw_file in sorted(Path(raw_dir).glob("*.json")):
            raw = RawDisclosure.model_validate_json(raw_file.read_text(encoding="utf-8"))
            ext_file = Path(extractions_dir) / f"{raw.disclosure_id}.json"
            if not ext_file.exists():
                counts["skipped_missing_extraction"] += 1
                continue

            env = DisclosureEnvelope.model_validate_json(ext_file.read_text(encoding="utf-8"))
            meta = env.metadata
            extracted = env.extracted

            conn.execute(
                """
                INSERT INTO disclosures (
                    disclosure_id, ticker, company_name, disclosure_type, published_at,
                    source_url, title, summary_tr, raw_text, extracted_json,
                    extraction_confidence, low_confidence_fields, human_verified,
                    is_banking_entity, content_sha256, extractor_model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(disclosure_id) DO UPDATE SET
                    ticker = excluded.ticker,
                    company_name = excluded.company_name,
                    disclosure_type = excluded.disclosure_type,
                    published_at = excluded.published_at,
                    source_url = excluded.source_url,
                    title = excluded.title,
                    summary_tr = excluded.summary_tr,
                    raw_text = excluded.raw_text,
                    extracted_json = excluded.extracted_json,
                    extraction_confidence = excluded.extraction_confidence,
                    low_confidence_fields = excluded.low_confidence_fields,
                    human_verified = excluded.human_verified,
                    content_sha256 = excluded.content_sha256,
                    extractor_model = excluded.extractor_model
                """,
                (
                    meta.disclosure_id,
                    meta.ticker,
                    meta.company_name,
                    meta.disclosure_type,
                    meta.published_at.isoformat(),
                    str(meta.source_url),
                    meta.title,
                    meta.summary_tr,
                    raw.raw_text,
                    extracted.model_dump_json(),
                    meta.extraction_confidence,
                    json.dumps(meta.low_confidence_fields, ensure_ascii=False),
                    1 if meta.human_verified else 0,
                    env.content_sha256,
                    env.extractor_model,
                    now_iso,
                ),
            )
            counts["disclosures"] += 1

            if isinstance(extracted, DividendExtraction):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO dividend_events (
                        disclosure_id, ticker, decision_status, fiscal_year,
                        gross_cash_per_share_try, net_cash_per_share_try,
                        total_gross_cash_dividend_try, bonus_share_rate_pct,
                        payment_type, ex_dividend_date, payment_date, evidence_quote
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meta.disclosure_id,
                        meta.ticker,
                        extracted.decision_status,
                        extracted.fiscal_year,
                        extracted.gross_cash_per_share_try,
                        extracted.net_cash_per_share_try,
                        extracted.total_gross_cash_dividend_try,
                        extracted.bonus_share_rate_pct,
                        extracted.payment_type,
                        extracted.ex_dividend_date,
                        extracted.payment_date,
                        extracted.evidence_quote,
                    ),
                )
                counts["dividend"] += 1
            elif isinstance(extracted, FinancialReportExtraction):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO financial_reports (
                        disclosure_id, ticker, period, consolidation, currency,
                        presentation_scale, revenue_try, gross_profit_try, ebitda_try,
                        operating_profit_try, net_profit_try, total_assets_try,
                        total_equity_try, net_debt_try, evidence_quote
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meta.disclosure_id,
                        meta.ticker,
                        extracted.period,
                        extracted.consolidation,
                        extracted.currency,
                        extracted.presentation_scale,
                        extracted.revenue_try,
                        extracted.gross_profit_try,
                        extracted.ebitda_try,
                        extracted.operating_profit_try,
                        extracted.net_profit_try,
                        extracted.total_assets_try,
                        extracted.total_equity_try,
                        extracted.net_debt_try,
                        extracted.evidence_quote,
                    ),
                )
                counts["financial_report"] += 1
            elif isinstance(extracted, MaterialEventExtraction):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO material_events (
                        disclosure_id, ticker, event_category, counterparty,
                        contract_amount_original, contract_currency,
                        contract_amount_try_equiv, revenue_ratio_pct,
                        expected_impact, evidence_quote
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meta.disclosure_id,
                        meta.ticker,
                        extracted.event_category,
                        extracted.counterparty,
                        extracted.contract_amount_original,
                        extracted.contract_currency,
                        extracted.contract_amount_try_equiv,
                        extracted.revenue_ratio_pct,
                        extracted.expected_impact,
                        extracted.evidence_quote,
                    ),
                )
                counts["material_event"] += 1

        conn.commit()
    finally:
        conn.close()

    return counts


def main(argv: Optional[list[str]] = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Load validated KAP extractions into SQLite + FTS5.")
    parser.add_argument("--dataset", choices=("sample", "full"), default="sample")
    parser.add_argument("--db-path", type=Path, default=settings.db_path)
    parser.add_argument("--reset", action="store_true", help="Clear existing DB rows before loading.")
    args = parser.parse_args(argv)

    if args.dataset == "sample":
        raw_dir = settings.sample_raw_dir
        ext_dir = settings.sample_extractions_dir
    else:
        raw_dir = settings.raw_cache_dir
        ext_dir = settings.extractions_cache_dir

    counts = load_dataset_into_db(
        raw_dir=raw_dir,
        extractions_dir=ext_dir,
        db_path=args.db_path,
        reset=args.reset,
    )
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
