"""Batch cached LLM -> Pydantic extractor for KAP disclosures."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.llm.base import LLMProvider
from app.llm.gemini import get_extraction_provider
from app.schemas.base import (
    DisclosureEnvelope,
    DisclosureMetadata,
    ExtractionPayload,
    RawDisclosure,
)
from app.schemas.dividend import DividendExtraction
from app.schemas.financial import FinancialReportExtraction
from app.schemas.material_event import MaterialEventExtraction

SCHEMA_BY_TYPE = {
    "dividend": DividendExtraction,
    "financial_report": FinancialReportExtraction,
    "material_event": MaterialEventExtraction,
}


def compute_content_sha256(raw: RawDisclosure) -> str:
    """Compute canonical SHA-256 fingerprint for idempotent extraction caching."""
    payload = f"{raw.disclosure_type}|{raw.ticker}|{raw.title}|{raw.raw_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_XBRL_TAG_LINE_RE = re.compile(r"^[A-Za-z0-9_-]+\|(?:https?://\S*)?$")


def prepare_extraction_text(raw_text: str, max_chars: int = 25000) -> str:
    """
    Condense oversized XBRL/HTML KAP disclosures while keeping header metadata,
    balance-sheet totals, and income statement lines intact within `max_chars`.
    """
    if len(raw_text) <= max_chars:
        return raw_text

    cleaned_lines = [
        line.strip()
        for line in raw_text.splitlines()
        if line.strip() and not _XBRL_TAG_LINE_RE.match(line.strip())
    ]
    cleaned = "\n".join(cleaned_lines)
    if len(cleaned) <= max_chars:
        return cleaned

    head_end = min(len(cleaned), 4000)
    sections = [cleaned[:head_end]]
    prev_end = head_end
    anchors = (
        ("TOPLAM VARLIKLAR", -200, 1200),
        ("TOPLAM ÖZKAYNAKLAR", -200, 1000),
        ("KAR VEYA ZARAR KISMI", -600, 11000),
    )
    for marker, back, fwd in anchors:
        idx = cleaned.find(marker)
        if idx == -1 and marker == "KAR VEYA ZARAR KISMI":
            idx = cleaned.find("Hasılat")
        if idx == -1:
            continue
        start = max(prev_end, idx + back)
        end = min(len(cleaned), idx + fwd)
        if end > start:
            sections.append(f"...\n{cleaned[start:end]}")
            prev_end = end

    return "\n".join(sections)[:max_chars]


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.lower().split())


def assess_extraction_quality(
    raw: RawDisclosure,
    extracted: ExtractionPayload,
) -> tuple[float, list[str], str]:
    """Compute confidence score, identify uncertain fields, and format Turkish summary."""
    score = 0.96
    low_fields: list[str] = []

    raw_norm = _normalize_whitespace(raw.raw_text)
    prepared_norm = _normalize_whitespace(prepare_extraction_text(raw.raw_text))
    quote_norm = _normalize_whitespace(extracted.evidence_quote)
    if len(quote_norm) < 8 or (quote_norm[:32] not in raw_norm and quote_norm[:32] not in prepared_norm):
        score -= 0.14
        low_fields.append("evidence_quote")

    if isinstance(extracted, DividendExtraction):
        if extracted.decision_status != "no_dividend":
            if extracted.gross_cash_per_share_try is None:
                score -= 0.12
                low_fields.append("gross_cash_per_share_try")
            if extracted.net_cash_per_share_try is None:
                score -= 0.10
                low_fields.append("net_cash_per_share_try")
            if extracted.ex_dividend_date is None:
                score -= 0.08
                low_fields.append("ex_dividend_date")
        summary = (
            f"{raw.ticker} {extracted.fiscal_year} yılı kar payı ({extracted.decision_status}): "
            f"brüt {extracted.gross_cash_per_share_try or 0:.3f} TL / net "
            f"{extracted.net_cash_per_share_try or 0:.3f} TL."
        )
    elif isinstance(extracted, FinancialReportExtraction):
        for field_name in ("revenue_try", "net_profit_try", "total_assets_try"):
            if getattr(extracted, field_name) is None:
                score -= 0.10
                low_fields.append(field_name)
        summary = (
            f"{raw.ticker} {extracted.period} ({extracted.consolidation}) finansal sonuç: "
            f"hasılat={extracted.revenue_try or 'N/A'}, net kâr={extracted.net_profit_try or 'N/A'}."
        )
    else:
        if extracted.event_category == "new_business_contract":
            if extracted.contract_amount_original is None:
                score -= 0.12
                low_fields.append("contract_amount_original")
            if extracted.revenue_ratio_pct is None:
                score -= 0.07
                low_fields.append("revenue_ratio_pct")
        summary = (
            f"{raw.ticker} özel durum ({extracted.event_category}): "
            f"{extracted.counterparty or raw.title[:120]}"
        )

    clamped = round(max(0.1, min(0.99, score)), 2)
    return clamped, low_fields, summary[:390]


def _build_extraction_prompts(raw: RawDisclosure) -> tuple[str, str]:
    system_prompt = (
        "Sen BIST şirketlerinin KAP bildirimlerinden deterministik ve yüksek doğruluklu "
        "yapılandırılmış veri çıkaran bir finansal veri ayrıştırma motorusun. "
        "Metinde açıkça geçmeyen hiçbir sayıyı uydurma; bulunmayan alanlara null ver. "
        "Finansal raporlarda tüm parasal büyüklükleri tam TL değerine (full TRY) çevir "
        "(ör. Sunum Para Birimi '1.000 TL' veya 'Bin TL' ise 1.000 ile, 'Milyon TL' ise 1.000.000 ile çarp) "
        "ve presentation_scale alanına orijinal tablo ölçeğini yaz; Cari Dönem sütununu kullan ve "
        "net_profit_try için 'DÖNEM KARI (ZARARI)' satırını al. "
        "Özel durumlarda (Payların Geri Alınması) tamamlanan işlemlerde 'Geri Alımın Maliyeti (TL)', "
        "devam eden programlarda 'Ödenecek Azami Tutar (TL)' veya ayrılan fon tutarını "
        "contract_amount_original ve contract_currency='TRY' olarak yaz."
    )
    prepared_text = prepare_extraction_text(raw.raw_text, max_chars=25000)
    user_prompt = (
        f"Şirket: {raw.ticker} ({raw.company_name})\n"
        f"Tür: {raw.disclosure_type}\n"
        f"Tarih: {raw.published_at.isoformat()}\n"
        f"Başlık: {raw.title}\n\n"
        f"Bildirim Metni:\n{prepared_text}"
    )
    return system_prompt, user_prompt


def extract_single_disclosure(
    *,
    raw: RawDisclosure,
    out_dir: Path,
    provider: LLMProvider,
    force: bool = False,
) -> tuple[DisclosureEnvelope, bool]:
    """
    Extract and Pydantic-validate one disclosure once per content hash.
    Returns `(envelope, used_cache)`.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_file = out_dir / f"{raw.disclosure_id}.json"
    content_hash = compute_content_sha256(raw)

    if cache_file.exists() and not force:
        existing = DisclosureEnvelope.model_validate_json(cache_file.read_text(encoding="utf-8"))
        if existing.content_sha256 == content_hash:
            return existing, True

    schema_cls = SCHEMA_BY_TYPE[raw.disclosure_type]
    sys_prompt, usr_prompt = _build_extraction_prompts(raw)
    extracted = provider.generate_structured(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        schema_cls=schema_cls,
    )

    confidence, low_fields, summary_tr = assess_extraction_quality(raw, extracted)
    metadata = DisclosureMetadata(
        disclosure_id=raw.disclosure_id,
        ticker=raw.ticker,
        company_name=raw.company_name,
        disclosure_type=raw.disclosure_type,
        published_at=raw.published_at,
        source_url=raw.source_url,
        title=raw.title,
        summary_tr=summary_tr,
        extraction_confidence=confidence,
        low_confidence_fields=low_fields,
        human_verified=False,
    )
    envelope = DisclosureEnvelope(
        metadata=metadata,
        extracted=extracted,
        content_sha256=content_hash,
        extractor_model=getattr(provider, "last_used_model", provider.model_name),
        extracted_at=datetime.now(timezone.utc),
    )
    cache_file.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
    return envelope, False


def extract_batch(
    *,
    raw_dir: Path,
    out_dir: Path,
    provider: Optional[LLMProvider] = None,
    force: bool = False,
) -> dict[str, int]:
    """Run batch extraction over all raw disclosures in `raw_dir`, hitting cache first."""
    active_provider = provider or get_extraction_provider()
    stats = {"total": 0, "cached": 0, "extracted": 0, "failed": 0}
    raw_files = sorted(Path(raw_dir).glob("*.json"))

    for raw_file in raw_files:
        stats["total"] += 1
        try:
            raw = RawDisclosure.model_validate_json(raw_file.read_text(encoding="utf-8"))
            _, used_cache = extract_single_disclosure(
                raw=raw,
                out_dir=out_dir,
                provider=active_provider,
                force=force,
            )
            if used_cache:
                stats["cached"] += 1
            else:
                stats["extracted"] += 1
                if not getattr(active_provider, "is_offline_fallback", False):
                    time.sleep(0.4)
        except Exception:
            stats["failed"] += 1

    return stats


def main(argv: Optional[list[str]] = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Batch cached LLM -> Pydantic disclosure extractor.")
    parser.add_argument("--raw-dir", type=Path, default=settings.raw_cache_dir)
    parser.add_argument("--out-dir", type=Path, default=settings.extractions_cache_dir)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    result = extract_batch(raw_dir=args.raw_dir, out_dir=args.out_dir, force=args.force)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
