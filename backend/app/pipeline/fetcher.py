"""
Polite KAP CLI fetcher with per-request delay + jitter, local raw JSON cache,
explicit User-Agent header, bank exclusion for V1 scope, and helpers to
promote real disclosures to data/sample_seed/ and generate unverified gold drafts.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx
from bs4 import BeautifulSoup

from app.config import (
    BIST30_NON_BANK_TICKERS,
    EXCLUDED_BANK_TICKERS,
    TICKER_COMPANY_NAMES,
    get_settings,
)
from app.schemas.base import DisclosureEnvelope, DisclosureType, RawDisclosure

KAP_BASE_URL = "https://www.kap.org.tr"
KAP_COMPANY_ITEMS_URL = f"{KAP_BASE_URL}/tr/api/company/items/BIST/A"
KAP_BIST_COMPANIES_HTML_URL = f"{KAP_BASE_URL}/tr/bist-sirketler"
KAP_MEMBERS_QUERY_URL = f"{KAP_BASE_URL}/tr/api/disclosure/members/byCriteria"
KAP_DISCLOSURE_WEB_URL = f"{KAP_BASE_URL}/tr/Bildirim/{{disclosure_id}}"

_TITLE_TYPE_HINTS: tuple[tuple[tuple[str, ...], DisclosureType], ...] = (
    (("kar payı", "temettü", "dividend"), "dividend"),
    (("finansal rapor", "finansal tablo", "bilanço", "faaliyet raporu"), "financial_report"),
    (
        (
            "özel durum",
            "yeni iş ilişkisi",
            "ihale",
            "yatırım",
            "payların geri",
            "geleceğe yönelik",
        ),
        "material_event",
    ),
)

_BIST_COMPANY_HTML_RE = re.compile(
    r'"mkkMemberOid"\s*:\s*"([a-f0-9]+)"\s*,\s*"kapMemberTitle"\s*:\s*"([^"]+)"[^}]*?"stockCode"\s*:\s*"([A-Z0-9,\s]+)"',
    re.I,
)

_EVAL_FIELDS_BY_TYPE: dict[DisclosureType, tuple[str, ...]] = {
    "dividend": (
        "decision_status",
        "fiscal_year",
        "gross_cash_per_share_try",
        "net_cash_per_share_try",
        "ex_dividend_date",
        "payment_date",
    ),
    "financial_report": (
        "period",
        "consolidation",
        "presentation_scale",
        "revenue_try",
        "net_profit_try",
        "ebitda_try",
    ),
    "material_event": (
        "event_category",
        "counterparty",
        "contract_amount_original",
        "contract_currency",
        "expected_impact",
    ),
}


def classify_disclosure_title(
    title: str, disclosure_type_code: str = ""
) -> Optional[DisclosureType]:
    """Map a KAP disclosure title/subject or code into one of the three V1 types."""
    lower = title.lower()
    for keywords, dtype in _TITLE_TYPE_HINTS:
        if any(kw in lower for kw in keywords):
            return dtype
    if disclosure_type_code.strip().upper() == "FR":
        return "financial_report"
    return None


def parse_kap_html_text(html_body: str) -> str:
    """Extract clean normalized plain text from a KAP disclosure HTML page."""
    soup = BeautifulSoup(html_body, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    raw_lines = [line.strip() for line in soup.get_text("\n").splitlines()]
    non_empty = [line for line in raw_lines if len(line) > 1]
    joined = "\n".join(non_empty)
    marker = "Merkezi Saklama ve Veri Depolama Kuruluşu"
    if marker in joined:
        joined = joined.split(marker, 1)[1].lstrip()
    return joined


def parse_kap_publish_datetime(raw_value: Optional[str]) -> datetime:
    """Parse KAP publishDate strings ('DD.MM.YYYY HH:MM:SS' or ISO-8601) into UTC-aware/offset datetime."""
    if not raw_value:
        return datetime.now(timezone.utc)
    cleaned = raw_value.strip()
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone(timedelta(hours=3)))
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.replace(tzinfo=timezone(timedelta(hours=3)))
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def is_allowed_v1_disclosure(ticker: str, disclosure_type: DisclosureType) -> bool:
    """Enforce V1 scope: reject banking entities for financial_report disclosures."""
    normalized = ticker.strip().upper()
    if disclosure_type == "financial_report" and normalized in EXCLUDED_BANK_TICKERS:
        return False
    return True


def _resolve_ticker_oids(http: httpx.Client, headers: dict[str, str]) -> dict[str, dict[str, str]]:
    """Fetch KAP active company directory and index by uppercase stock ticker."""
    mapping: dict[str, dict[str, str]] = {}
    try:
        resp = http.get(KAP_COMPANY_ITEMS_URL, headers=headers)
        resp.raise_for_status()
        rows = resp.json()
        if isinstance(rows, list):
            for row in rows:
                codes_raw = str(row.get("stockCodes") or "").upper()
                oid = str(row.get("memberOid") or "").strip()
                title = str(row.get("memberTitle") or "").strip()
                if not oid or not codes_raw:
                    continue
                for code in (c.strip() for c in codes_raw.split(",")):
                    if code:
                        mapping[code] = {"oid": oid, "name": title or code}
    except (httpx.HTTPError, ValueError):
        pass

    if mapping:
        return mapping

    # Fallback: parse embedded directory JSON from /tr/bist-sirketler HTML
    page_resp = http.get(KAP_BIST_COMPANIES_HTML_URL, headers=headers)
    page_resp.raise_for_status()
    unescaped = page_resp.text.replace('\\"', '"')
    for oid, title, codes_raw in _BIST_COMPANY_HTML_RE.findall(unescaped):
        for code in (c.strip().upper() for c in codes_raw.split(",")):
            if code:
                mapping[code] = {"oid": oid.strip(), "name": title.strip() or code}
    return mapping


def _iter_date_windows(start_iso: str, end_iso: str, window_days: int = 85) -> list[tuple[str, str]]:
    """Split [start_iso, end_iso] into newest-first <=85-day chunks (KAP byCriteria cap)."""
    start_d = date.fromisoformat(start_iso)
    end_d = date.fromisoformat(end_iso)
    if end_d < start_d:
        return [(start_iso, end_iso)]
    windows: list[tuple[str, str]] = []
    cur_end = end_d
    while cur_end >= start_d:
        cur_start = max(start_d, cur_end - timedelta(days=window_days - 1))
        windows.append((cur_start.isoformat(), cur_end.isoformat()))
        cur_end = cur_start - timedelta(days=1)
    return windows


def _build_by_criteria_payload(member_oid: str, start_date: str, end_date: str) -> dict[str, Any]:
    return {
        "fromDate": start_date,
        "toDate": end_date,
        "memberType": "",
        "mkkMemberOidList": [member_oid],
        "inactiveMkkMemberOidList": [],
        "disclosureClass": "",
        "subjectList": [],
        "isLate": "",
        "mainSector": "",
        "sector": "",
        "subSector": "",
        "marketOid": "",
        "index": "",
        "bdkReview": "",
        "bdkMemberOidList": [],
        "year": "",
        "term": "",
        "ruleType": "",
        "period": "",
        "fromSrc": False,
        "srcCategory": "",
        "disclosureIndexList": [],
    }


def fetch_disclosures_cli(
    *,
    tickers: Iterable[str],
    output_dir: Path,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit_per_ticker: int = 20,
    delay_sec: float = 2.5,
    user_agent: str,
    force: bool = False,
    client: Optional[httpx.Client] = None,
) -> dict[str, int]:
    """
    Fetch KAP disclosures politely with caching, User-Agent header, and rate delay.
    Writes validated RawDisclosure JSON files into `output_dir`.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    resolved_end = end_date or today.isoformat()
    resolved_start = start_date or (today - timedelta(days=365)).isoformat()

    headers = {
        "Origin": KAP_BASE_URL,
        "Referer": f"{KAP_BASE_URL}/tr/bildirim-sorgu",
        "User-Agent": user_agent,
        "Accept": "application/json, text/html;q=0.9",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
    }

    stats = {"fetched": 0, "cached": 0, "skipped_bank_or_scope": 0, "errors": 0}
    owns_client = client is None
    http = client or httpx.Client(headers=headers, timeout=25.0, follow_redirects=True)

    try:
        try:
            ticker_directory = _resolve_ticker_oids(http, headers)
        except (httpx.HTTPError, ValueError):
            stats["errors"] += 1
            return stats

        windows = _iter_date_windows(resolved_start, resolved_end, window_days=85)

        for ticker in tickers:
            norm_ticker = ticker.strip().upper()
            entry = ticker_directory.get(norm_ticker)
            if not entry:
                stats["errors"] += 1
                continue

            seen_disc_ids: set[str] = set()
            processed_for_ticker = 0

            for win_start, win_end in windows:
                if processed_for_ticker >= limit_per_ticker:
                    break

                payload = _build_by_criteria_payload(entry["oid"], win_start, win_end)
                try:
                    list_resp = http.post(KAP_MEMBERS_QUERY_URL, json=payload, headers=headers)
                    if list_resp.status_code == 429:
                        time.sleep(max(delay_sec, 3.0) * 2)
                        list_resp = http.post(KAP_MEMBERS_QUERY_URL, json=payload, headers=headers)
                    list_resp.raise_for_status()
                    items = list_resp.json()
                    if isinstance(items, dict):
                        items = items.get("data") or items.get("list") or []
                    if not isinstance(items, list):
                        continue
                except (httpx.HTTPError, ValueError):
                    stats["errors"] += 1
                    continue

                for item in items:
                    if processed_for_ticker >= limit_per_ticker:
                        break

                    disc_id = str(item.get("disclosureIndex", "")).strip()
                    if not disc_id or disc_id in seen_disc_ids:
                        continue
                    seen_disc_ids.add(disc_id)

                    subject = str(
                        item.get("subject") or item.get("disclosureTitle") or item.get("title") or ""
                    ).strip()
                    summary_hint = str(item.get("summary") or "").strip()
                    disc_type_code = str(item.get("disclosureType") or "").strip()
                    title = subject or summary_hint
                    if not title:
                        continue

                    dtype = classify_disclosure_title(f"{subject} {summary_hint}", disc_type_code)
                    if dtype is None or not is_allowed_v1_disclosure(norm_ticker, dtype):
                        stats["skipped_bank_or_scope"] += 1
                        continue

                    processed_for_ticker += 1
                    cache_path = output_dir / f"{disc_id}.json"
                    if cache_path.exists() and not force:
                        stats["cached"] += 1
                        continue

                    if delay_sec > 0:
                        time.sleep(delay_sec + random.uniform(0.1, 0.6))

                    source_url = KAP_DISCLOSURE_WEB_URL.format(disclosure_id=disc_id)
                    try:
                        page_resp = http.get(source_url, headers=headers)
                        if page_resp.status_code == 429:
                            time.sleep(max(delay_sec, 3.0) * 2)
                            page_resp = http.get(source_url, headers=headers)
                        page_resp.raise_for_status()
                        plain_text = parse_kap_html_text(page_resp.text)
                        if len(plain_text) < 20 and summary_hint:
                            plain_text = f"{title}\n{summary_hint}"

                        published_dt = parse_kap_publish_datetime(str(item.get("publishDate") or ""))
                        company_name = str(
                            item.get("memberTitle")
                            or entry.get("name")
                            or TICKER_COMPANY_NAMES.get(norm_ticker, norm_ticker)
                        )
                        record = RawDisclosure(
                            disclosure_id=disc_id,
                            ticker=norm_ticker,
                            company_name=company_name,
                            disclosure_type=dtype,
                            published_at=published_dt,
                            source_url=source_url,
                            title=title[:400],
                            raw_text=plain_text,
                            fetched_at=datetime.now(timezone.utc),
                            source="kap_live",
                        )
                        cache_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
                        stats["fetched"] += 1
                    except Exception:
                        stats["errors"] += 1
    finally:
        if owns_client:
            http.close()

    return stats


def promote_real_to_sample(
    *,
    raw_dir: Path,
    extractions_dir: Path,
    sample_raw_dir: Path,
    sample_extractions_dir: Path,
    limit: int = 24,
) -> dict[str, int]:
    """
    Replace synthetic data/sample_seed files with up to `limit` real fetched disclosures
    balanced across dividend, financial_report, and material_event.
    """
    buckets: dict[DisclosureType, list[Path]] = {
        "dividend": [],
        "financial_report": [],
        "material_event": [],
    }
    for fpath in sorted(Path(raw_dir).glob("*.json")):
        raw = RawDisclosure.model_validate_json(fpath.read_text(encoding="utf-8"))
        if raw.source == "kap_live":
            buckets[raw.disclosure_type].append(fpath)

    per_type = max(1, limit // 3)
    chosen: list[Path] = []
    leftovers: list[Path] = []
    for dtype in ("dividend", "financial_report", "material_event"):
        chosen.extend(buckets[dtype][:per_type])
        leftovers.extend(buckets[dtype][per_type:])
    if len(chosen) < limit:
        chosen.extend(leftovers[: limit - len(chosen)])

    if not chosen:
        return {"promoted_raw": 0, "promoted_extractions": 0}

    sample_raw_dir.mkdir(parents=True, exist_ok=True)
    sample_extractions_dir.mkdir(parents=True, exist_ok=True)
    for existing in sample_raw_dir.glob("*.json"):
        existing.unlink()
    for existing in sample_extractions_dir.glob("*.json"):
        existing.unlink()

    copied_ext = 0
    for src_raw in chosen:
        shutil.copy2(src_raw, sample_raw_dir / src_raw.name)
        src_ext = Path(extractions_dir) / src_raw.name
        if src_ext.exists():
            shutil.copy2(src_ext, sample_extractions_dir / src_ext.name)
            copied_ext += 1

    return {"promoted_raw": len(chosen), "promoted_extractions": copied_ext}


def build_gold_draft_from_dir(
    *,
    raw_dir: Path,
    extractions_dir: Path,
    gold_path: Path,
    max_cases: int = 50,
) -> int:
    """
    Generate an unverified (`"verified": false`) extraction_gold.jsonl draft from
    fetched disclosures and cached extractions so a human reviewer can verify it.
    """
    rows: list[dict[str, Any]] = []
    for raw_file in sorted(Path(raw_dir).glob("*.json")):
        if len(rows) >= max_cases:
            break
        ext_file = Path(extractions_dir) / raw_file.name
        if not ext_file.exists():
            continue
        raw = RawDisclosure.model_validate_json(raw_file.read_text(encoding="utf-8"))
        env = DisclosureEnvelope.model_validate_json(ext_file.read_text(encoding="utf-8"))
        ext_dict = env.extracted.model_dump()
        fields = _EVAL_FIELDS_BY_TYPE[raw.disclosure_type]
        rows.append(
            {
                "eval_id": f"ext_{len(rows) + 1:03d}",
                "disclosure_id": raw.disclosure_id,
                "ticker": raw.ticker,
                "company_name": raw.company_name,
                "disclosure_type": raw.disclosure_type,
                "published_at": raw.published_at.isoformat(),
                "source_url": str(raw.source_url),
                "title": raw.title,
                "raw_text": raw.raw_text,
                "verified": False,
                "expected": {k: ext_dict.get(k) for k in fields},
            }
        )

    gold_path.parent.mkdir(parents=True, exist_ok=True)
    gold_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return len(rows)


def main(argv: Optional[list[str]] = None) -> int:
    settings = get_settings()
    today = date.today()
    parser = argparse.ArgumentParser(description="Polite KAP disclosure CLI fetcher.")
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=list(BIST30_NON_BANK_TICKERS),
        help="Target BIST tickers (banking tickers excluded for financial reports).",
    )
    parser.add_argument(
        "--start-date",
        default=(today - timedelta(days=365)).isoformat(),
        help="Query start date YYYY-MM-DD (default: 1 year ago).",
    )
    parser.add_argument(
        "--end-date",
        default=today.isoformat(),
        help="Query end date YYYY-MM-DD (default: today).",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max disclosures per ticker.")
    parser.add_argument(
        "--delay-sec",
        type=float,
        default=2.5,
        help="Minimum delay in seconds between detail fetches.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=settings.raw_cache_dir,
        help="Directory for raw cached disclosure JSON files.",
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch already cached disclosures.")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip HTTP fetching (useful with --promote-to-sample or --build-gold-draft).",
    )
    parser.add_argument(
        "--promote-to-sample",
        type=int,
        default=0,
        help="Copy up to N real fetched disclosures from output-dir to data/sample_seed/.",
    )
    parser.add_argument(
        "--build-gold-draft",
        type=Path,
        default=None,
        help="Write up to 50 unverified (verified:false) extraction gold draft rows to PATH.",
    )
    args = parser.parse_args(argv)

    result: dict[str, Any] = {}
    if not args.skip_fetch:
        result["fetch"] = fetch_disclosures_cli(
            tickers=args.tickers,
            output_dir=args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            limit_per_ticker=args.limit,
            delay_sec=args.delay_sec,
            user_agent=settings.kap_user_agent,
            force=args.force,
        )
    if args.promote_to_sample > 0:
        result["promote_sample"] = promote_real_to_sample(
            raw_dir=args.output_dir,
            extractions_dir=settings.extractions_cache_dir,
            sample_raw_dir=settings.sample_raw_dir,
            sample_extractions_dir=settings.sample_extractions_dir,
            limit=args.promote_to_sample,
        )
    if args.build_gold_draft is not None:
        result["gold_draft_count"] = build_gold_draft_from_dir(
            raw_dir=args.output_dir,
            extractions_dir=settings.extractions_cache_dir,
            gold_path=args.build_gold_draft,
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
