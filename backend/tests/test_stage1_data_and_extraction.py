"""Stage 1 verification suite: schemas, sanitization, caching, SQLite+FTS5 read-only isolation, and eval."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.config import EXCLUDED_BANK_TICKERS, REPO_ROOT, get_settings
from app.db.connection import get_readonly_agent_conn, get_write_conn, init_db
from app.db.seed_loader import load_dataset_into_db
from app.llm.base import LLMProvider
from app.pipeline.extractor import assess_extraction_quality, extract_single_disclosure, prepare_extraction_text
from app.pipeline.fetcher import fetch_disclosures_cli
from app.schemas.base import DisclosureEnvelope, RawDisclosure
from app.schemas.dividend import DividendExtraction
from app.schemas.financial import FinancialReportExtraction
from app.schemas.material_event import MaterialEventExtraction
from app.schemas.review import ReviewFlagCreate

TModel = TypeVar("TModel", bound=BaseModel)


class CountingMockProvider(LLMProvider):
    """Test double that tracks invocation count to prove per-disclosure caching."""

    model_name = "mock-gemini-flash"

    def __init__(self) -> None:
        self.calls = 0

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[TModel],
    ) -> TModel:
        self.calls += 1
        return schema_cls.model_validate(
            {
                "decision_status": "approved",
                "fiscal_year": 2025,
                "gross_cash_per_share_try": 5.0,
                "net_cash_per_share_try": 4.5,
                "total_gross_cash_dividend_try": 1_000_000.0,
                "bonus_share_rate_pct": None,
                "payment_type": "single",
                "ex_dividend_date": "2026-05-10",
                "payment_date": "2026-05-14",
                "evidence_quote": "1 TL nominal değerli paya brüt 5,00 TL net 4,50 TL nakit kar payı.",
            }
        )


def test_pydantic_schemas_and_bank_exclusion() -> None:
    div = DividendExtraction(
        decision_status="approved",
        fiscal_year=2025,
        gross_cash_per_share_try=10.0,
        net_cash_per_share_try=9.0,
        total_gross_cash_dividend_try=5_000_000.0,
        payment_type="single",
        ex_dividend_date="2026-04-03",
        payment_date="2026-04-07",
        evidence_quote="1 TL paya brüt 10,00 TL, net 9,00 TL nakit kar payı dağıtılmasına.",
    )
    assert div.net_cash_per_share_try == 9.0

    # Net cannot exceed gross
    with pytest.raises(ValidationError):
        DividendExtraction(
            decision_status="approved",
            fiscal_year=2025,
            gross_cash_per_share_try=5.0,
            net_cash_per_share_try=6.0,
            payment_type="single",
            evidence_quote="Geçersiz temettü net>brüt testi.",
        )

    # Bad period regex rejected
    with pytest.raises(ValidationError):
        FinancialReportExtraction(
            period="2025-Q4",
            consolidation="consolidated",
            presentation_scale="millions",
            revenue_try=1_000_000.0,
            evidence_quote="Hasılat 1 Milyon TL.",
        )

    mat = MaterialEventExtraction(
        event_category="new_business_contract",
        counterparty="Savunma Sanayii",
        contract_amount_original=10_000_000.0,
        contract_currency="USD",
        contract_amount_try_equiv=360_000_000.0,
        revenue_ratio_pct=4.5,
        expected_impact="positive",
        evidence_quote="10.000.000 USD tutarında yeni sözleşme imzalanmıştır.",
    )
    assert mat.contract_currency == "USD"

    # Excluded banks rejected for financial_report
    for bank in sorted(EXCLUDED_BANK_TICKERS)[:3]:
        with pytest.raises(ValidationError):
            RawDisclosure(
                disclosure_id="999001",
                ticker=bank,
                company_name=f"{bank} Bank A.Ş.",
                disclosure_type="financial_report",
                published_at="2026-03-01T18:00:00+03:00",
                source_url="https://www.kap.org.tr/tr/Bildirim/999001",
                title="2025/12 Finansal Rapor",
                raw_text="BDDK finansal tabloları ve özet kalemler yer almaktadır.",
            )


def test_review_flag_sanitization_and_whitelist() -> None:
    flag = ReviewFlagCreate(
        disclosure_id="1401007",
        field_name="gross_cash_per_share_try",
        suggested_value="<script>alert(1)</script><b>0.55</b>\x00",
        reason_note="Tablo <i>dipnot</i> kontrolü\n\t<b>gerekli</b>.",
    )
    assert "<script>" not in flag.suggested_value
    assert "<b>" not in flag.suggested_value
    assert flag.suggested_value == "0.55"
    assert flag.reason_note == "Tablo dipnot kontrolü gerekli ."

    with pytest.raises(ValidationError):
        ReviewFlagCreate(
            disclosure_id="1401007",
            field_name="is_banking_entity",
            suggested_value="1",
        )


def test_extractor_caches_once_per_content_hash(tmp_path: Path) -> None:
    raw = RawDisclosure(
        disclosure_id="1488001",
        ticker="TUPRS",
        company_name="Tüpraş A.Ş.",
        disclosure_type="dividend",
        published_at="2026-03-27T18:10:00+03:00",
        source_url="https://www.kap.org.tr/tr/Bildirim/1488001",
        title="Kar Payı Dağıtım İşlemlerine İlişkin Bildirim",
        raw_text="1 TL nominal değerli paya brüt 5,00 TL net 4,50 TL nakit kar payı ödenmesine karar verilmiştir.",
    )
    provider = CountingMockProvider()

    env1, cached1 = extract_single_disclosure(raw=raw, out_dir=tmp_path, provider=provider)
    env2, cached2 = extract_single_disclosure(raw=raw, out_dir=tmp_path, provider=provider)

    assert cached1 is False
    assert cached2 is True
    assert provider.calls == 1
    assert env1.content_sha256 == env2.content_sha256


def test_polite_fetcher_respects_user_agent_bank_filter_and_cache(tmp_path: Path) -> None:
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("user-agent", ""))
        if request.url.path.endswith("/tr/api/company/items/BIST/A"):
            return httpx.Response(
                200,
                json=[
                    {"stockCodes": "GARAN", "memberOid": "oid-garan", "memberTitle": "Garanti Bankası A.Ş."},
                    {"stockCodes": "TUPRS", "memberOid": "oid-tuprs", "memberTitle": "Tüpraş A.Ş."},
                ],
            )
        if request.url.path.endswith("/tr/api/disclosure/members/byCriteria"):
            payload = json.loads(request.content.decode("utf-8"))
            oids = payload["mkkMemberOidList"]
            if oids == ["oid-garan"]:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "disclosureIndex": 880001,
                            "subject": "2025/12 Finansal Rapor",
                            "memberTitle": "Garanti Bankası A.Ş.",
                            "publishDate": "01.02.2026 18:00:00",
                        }
                    ],
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "disclosureIndex": 880002,
                        "subject": "Kar Payı Dağıtım İşlemlerine İlişkin Bildirim",
                        "memberTitle": "Tüpraş A.Ş.",
                        "publishDate": "27.03.2026 18:10:00",
                    }
                ],
            )
        if request.url.path.endswith("/Bildirim/880002"):
            return httpx.Response(
                200,
                text="<html><body><script>bad()</script><p>Tüpraş brüt 15,20 TL nakit kar payı açıklamıştır.</p></body></html>",
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        stats1 = fetch_disclosures_cli(
            tickers=["GARAN", "TUPRS"],
            output_dir=tmp_path,
            limit_per_ticker=5,
            delay_sec=0.0,
            user_agent="KAPAgentTest/1.0",
            client=client,
        )
        stats2 = fetch_disclosures_cli(
            tickers=["TUPRS"],
            output_dir=tmp_path,
            limit_per_ticker=5,
            delay_sec=0.0,
            user_agent="KAPAgentTest/1.0",
            client=client,
        )

    assert stats1["fetched"] == 1
    assert stats1["skipped_bank_or_scope"] == 1
    assert stats2["cached"] == 1
    assert all(h == "KAPAgentTest/1.0" for h in seen_headers)
    assert not (tmp_path / "880001.json").exists()
    cached_raw = RawDisclosure.model_validate_json((tmp_path / "880002.json").read_text(encoding="utf-8"))
    assert "bad()" not in cached_raw.raw_text


def test_sample_seed_fts5_and_readonly_agent_connection(tmp_path: Path) -> None:
    settings = get_settings()
    db_path = tmp_path / "test_kap.db"
    counts = load_dataset_into_db(
        raw_dir=settings.sample_raw_dir,
        extractions_dir=settings.sample_extractions_dir,
        db_path=db_path,
    )
    assert counts["disclosures"] == 24
    assert counts["dividend"] == 8
    assert counts["financial_report"] == 8
    assert counts["material_event"] == 8

    # Ensure write connection works for schema verification (e.g. review_flags)
    wconn = get_write_conn(db_path)
    try:
        low_conf = wconn.execute(
            "SELECT disclosure_id FROM disclosures WHERE extraction_confidence < 0.80"
        ).fetchall()
        assert len(low_conf) >= 3
    finally:
        wconn.close()

    # Ensure agent read-only connection supports FTS5 + SELECT and rejects ALL mutations
    ro_conn = get_readonly_agent_conn(db_path, timeout_ms=1500)
    try:
        fts_hits = ro_conn.execute(
            """
            SELECT d.disclosure_id, d.ticker
            FROM disclosures_fts f
            JOIN disclosures d ON d.disclosure_id = f.disclosure_id
            WHERE disclosures_fts MATCH 'radar OR transformator*'
            """
        ).fetchall()
        assert len(fts_hits) >= 2

        top_div = ro_conn.execute(
            "SELECT ticker, net_cash_per_share_try FROM dividend_events ORDER BY net_cash_per_share_try DESC LIMIT 1"
        ).fetchone()
        assert top_div["ticker"] == "FROTO"
        assert top_div["net_cash_per_share_try"] == 39.15

        for forbidden_sql in (
            "INSERT INTO review_flags (disclosure_id, field_name, suggested_value, reporter_ip_hash, created_at) VALUES ('1401001', 'summary_tr', 'x', 'abc', '2026-01-01')",
            "UPDATE disclosures SET summary_tr = 'hacked' WHERE disclosure_id = '1401001'",
            "DROP TABLE disclosures",
        ):
            with pytest.raises(sqlite3.OperationalError):
                ro_conn.execute(forbidden_sql)
    finally:
        ro_conn.close()


def test_extraction_eval_draft_runner(tmp_path: Path) -> None:
    evals_dir = REPO_ROOT / "evals"
    if str(evals_dir) not in sys.path:
        sys.path.insert(0, str(evals_dir))
    import run_extraction_eval  # type: ignore

    # Full 50-case verified gold set runs in OFFLINE/SAHTE mode cleanly
    report = run_extraction_eval.run_eval(use_live_llm=False)
    assert report["total_cases"] == 50
    assert report["verified_cases"] == 50
    assert report["unverified_draft_cases"] == 0
    assert report["schema_validity_pct"] == 100.0
    assert "OFFLINE/SAHTE" in report["execution_mode"]

    # When 0 verified cases exist, accuracy MUST remain "ölçülmedi"
    unverified_draft = tmp_path / "unverified_ext.jsonl"
    first_row = json.loads(
        (REPO_ROOT / "evals" / "extraction_gold.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    first_row["verified"] = False
    unverified_draft.write_text(json.dumps(first_row, ensure_ascii=False) + "\n", encoding="utf-8")
    draft_report = run_extraction_eval.run_eval(use_live_llm=False, gold_path=unverified_draft)
    assert draft_report["verified_cases"] == 0
    assert draft_report["unverified_draft_cases"] == 1
    assert draft_report["schema_validity_pct"] == "ölçülmedi"
    assert draft_report["field_exact_match_pct"] == "ölçülmedi"
    assert draft_report["numeric_accuracy_pct"] == "ölçülmedi"
    assert draft_report["unverified_draft_report"]["schema_valid_count"] == 1


def test_extraction_eval_counts_only_verified_records(tmp_path: Path) -> None:
    evals_dir = REPO_ROOT / "evals"
    if str(evals_dir) not in sys.path:
        sys.path.insert(0, str(evals_dir))
    import run_extraction_eval  # type: ignore

    class FakeLiveProvider(LLMProvider):
        model_name = "mock-live-gemini"
        is_offline_fallback = False

        def generate_structured(self, *, system_prompt: str, user_prompt: str, schema_cls: type[TModel]) -> TModel:
            return schema_cls.model_validate(
                {
                    "decision_status": "approved",
                    "fiscal_year": 2025,
                    "gross_cash_per_share_try": 10.0,
                    "net_cash_per_share_try": 9.0,
                    "total_gross_cash_dividend_try": None,
                    "bonus_share_rate_pct": None,
                    "payment_type": "single",
                    "ex_dividend_date": None,
                    "payment_date": None,
                    "evidence_quote": "1 TL paya brüt 10 TL net 9 TL nakit kar payı.",
                }
            )

    gold_file = tmp_path / "eval_subset.jsonl"
    gold_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "eval_id": "v1",
                        "disclosure_id": "900001",
                        "ticker": "TUPRS",
                        "disclosure_type": "dividend",
                        "published_at": "2026-03-27T18:10:00+03:00",
                        "source_url": "https://www.kap.org.tr/tr/Bildirim/900001",
                        "title": "Kar Payı Dağıtımı",
                        "raw_text": "1 TL paya brüt 10 TL net 9 TL nakit kar payı genel kurulda onaylanmıştır.",
                        "verified": True,
                        "expected": {
                            "decision_status": "approved",
                            "fiscal_year": 2025,
                            "gross_cash_per_share_try": 10.0,
                            "net_cash_per_share_try": 9.0,
                        },
                    }
                ),
                json.dumps(
                    {
                        "eval_id": "u1",
                        "disclosure_id": "900002",
                        "ticker": "FROTO",
                        "disclosure_type": "dividend",
                        "published_at": "2026-03-30T17:45:00+03:00",
                        "source_url": "https://www.kap.org.tr/tr/Bildirim/900002",
                        "title": "Kar Payı Dağıtımı",
                        "raw_text": "1 TL paya brüt 999 TL net 888 TL nakit kar payı genel kurulda onaylanmıştır.",
                        "verified": False,
                        "expected": {
                            "decision_status": "no_dividend",
                            "fiscal_year": 2022,
                            "gross_cash_per_share_try": 999.0,
                            "net_cash_per_share_try": 888.0,
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    report = run_extraction_eval.run_eval(
        use_live_llm=True,
        gold_path=gold_file,
        provider=FakeLiveProvider(),
    )
    assert report["verified_cases"] == 1
    assert report["unverified_draft_cases"] == 1
    assert report["schema_validity_pct"] == 100.0
    assert report["field_exact_match_pct"] == 100.0
    assert report["execution_mode"] == "LIVE_LLM (mock-live-gemini)"


def test_prepare_extraction_text_preserves_xbrl_income_statement() -> None:
    filler = "\n".join(
        [f"ifrs-full_AssetLine{i}|http://www.xbrl.org/2003/role/label\nKalem {i}\n123.456" for i in range(900)]
    )
    oversized = (
        "Şirket X\nSunum Para Birimi\n1.000 TL\nKonsolide\n"
        f"{filler}\n"
        "TOPLAM VARLIKLAR\n500.000.000\n"
        "KAR VEYA ZARAR KISMI\nHasılat\n90.872.870\nDÖNEM KARI (ZARARI)\n11.587.759\n"
    )
    assert len(oversized) > 25000
    digest = prepare_extraction_text(oversized, max_chars=25000)
    assert len(digest) <= 25000
    assert "1.000 TL" in digest
    assert "TOPLAM VARLIKLAR" in digest
    assert "Hasılat" in digest
    assert "90.872.870" in digest
    assert "11.587.759" in digest


def test_cover_notice_not_flagged_as_low_confidence_financial_report() -> None:
    cover_raw = RawDisclosure(
        disclosure_id="999001",
        ticker="ASTOR",
        company_name="Astor Enerji A.Ş.",
        disclosure_type="financial_report",
        published_at="2026-11-09T18:00:00+03:00",
        source_url="https://www.kap.org.tr/tr/Bildirim/999001",
        title="Sorumluluk Beyanı (Konsolide)",
        raw_text="30.09.2026 dönemine ait finansal tablolar tarafımızca incelenmiştir.",
    )
    stmt_raw = cover_raw.model_copy(
        update={
            "disclosure_id": "999002",
            "title": "Finansal Rapor",
            "raw_text": "30.09.2026 Finansal Durum Tablosu ve Gelir Tablosu ektedir.",
        }
    )
    empty_ext = FinancialReportExtraction(
        period="2026/09",
        consolidation="consolidated",
        evidence_quote="30.09.2026 dönemine ait finansal tablolar tarafımızca incelenmiştir.",
    )
    cover_conf, cover_fields, _ = assess_extraction_quality(cover_raw, empty_ext)
    assert cover_conf >= 0.80
    assert cover_fields == []

    stmt_ext = empty_ext.model_copy(
        update={"evidence_quote": "30.09.2026 Finansal Durum Tablosu ve Gelir Tablosu ektedir."}
    )
    stmt_conf, stmt_fields, _ = assess_extraction_quality(stmt_raw, stmt_ext)
    assert stmt_conf < 0.80
    assert "revenue_try" in stmt_fields
