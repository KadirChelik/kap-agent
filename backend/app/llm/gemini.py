"""Google Gemini Flash structured-output adapter with exponential backoff and offline fallback."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import (
    BIST30_NON_BANK_TICKERS,
    EXCLUDED_BANK_TICKERS,
    Settings,
    get_settings,
)
from app.llm.base import LLMProvider, LLMProviderError
from app.schemas.agent import AgentTurnDecision, ChartSpec
from app.schemas.dividend import DividendExtraction
from app.schemas.financial import FinancialReportExtraction
from app.schemas.material_event import MaterialEventExtraction

TModel = TypeVar("TModel", bound=BaseModel)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.S)


def _strip_code_fences(raw_text: str) -> str:
    return _CODE_FENCE_RE.sub("", raw_text.strip()).strip()


def _parse_tr_number(raw: str) -> Optional[float]:
    """Parse Turkish formatted number like '1.234.567,89' or '12,50' into float."""
    cleaned = raw.strip().replace("\u00a0", "").replace(" ", "")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


class GeminiFlashProvider(LLMProvider):
    """Calls Gemini Flash REST generateContent in strict JSON mode with exponential backoff."""

    is_offline_fallback: bool = False

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = "gemini-3-flash-preview",
        timeout_sec: float = 30.0,
        max_retries: int = 3,
        client: Optional[httpx.Client] = None,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiFlashProvider requires a non-empty GEMINI_API_KEY.")
        self.api_key = api_key
        self.model_name = model_name
        self.last_used_model = model_name
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self._client = client

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[TModel],
    ) -> TModel:
        schema_json = json.dumps(schema_cls.model_json_schema(), ensure_ascii=False)
        payload: dict[str, Any] = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            f"{system_prompt}\n\n"
                            f"Yanıtını SADECE aşağıdaki JSON Schema'ya uyan geçerli JSON olarak döndür:\n"
                            f"{schema_json}"
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }

        models_to_try = [self.model_name] + (
            ["gemini-3.1-flash-lite-preview"]
            if self.model_name != "gemini-3.1-flash-lite-preview"
            else []
        )
        last_err: Exception | None = None
        client = self._client or httpx.Client(timeout=self.timeout_sec)
        owns_client = self._client is None
        try:
            for m_idx, candidate_model in enumerate(models_to_try):
                endpoint = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{candidate_model}:generateContent"
                )
                has_fallback = m_idx < len(models_to_try) - 1
                for attempt in range(self.max_retries):
                    try:
                        resp = client.post(
                            endpoint,
                            headers={"x-goog-api-key": self.api_key},
                            json=payload,
                        )
                        if resp.status_code in (404, 429) and has_fallback:
                            last_err = LLMProviderError(
                                f"Model '{candidate_model}' HTTP {resp.status_code}; trying fallback."
                            )
                            break
                        if resp.status_code in (429, 500, 502, 503, 504):
                            raise LLMProviderError(
                                f"Transient Gemini HTTP {resp.status_code}: {resp.text[:200]}"
                            )
                        resp.raise_for_status()
                        data = resp.json()
                        raw_text = (
                            data.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [{}])[0]
                            .get("text", "")
                        )
                        if not raw_text:
                            raise LLMProviderError("Empty text response from Gemini.")
                        cleaned = _strip_code_fences(raw_text)
                        decoded = json.loads(cleaned)
                        if isinstance(decoded, list) and decoded and isinstance(decoded[0], dict):
                            decoded = decoded[0]
                        parsed = schema_cls.model_validate(decoded)
                        self.last_used_model = candidate_model
                        return parsed
                    except (httpx.HTTPError, LLMProviderError, ValidationError, ValueError, KeyError, IndexError) as exc:
                        last_err = exc
                        if attempt < self.max_retries - 1:
                            time.sleep(0.8 * (2**attempt))
                            continue
                        break
        finally:
            if owns_client:
                client.close()

        raise LLMProviderError(f"Gemini structured generation failed: {last_err}") from last_err

    def plan_agent_step(
        self,
        *,
        system_prompt: str,
        transcript: str,
    ) -> AgentTurnDecision:
        return self.generate_structured(
            system_prompt=system_prompt,
            user_prompt=transcript,
            schema_cls=AgentTurnDecision,
        )


class OfflineFallbackProvider(LLMProvider):
    """
    Deterministic regex/rule-based structured extractor used only when running
    offline without GEMINI_API_KEY (or in unit tests verifying caching/fallback).
    """

    model_name = "OFFLINE/SAHTE (Regex Fallback - Gercek LLM Degil)"
    is_offline_fallback: bool = True

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[TModel],
    ) -> TModel:
        if schema_cls is DividendExtraction:
            return schema_cls.model_validate(self._extract_dividend(user_prompt))
        if schema_cls is FinancialReportExtraction:
            return schema_cls.model_validate(self._extract_financial(user_prompt))
        if schema_cls is MaterialEventExtraction:
            return schema_cls.model_validate(self._extract_material_event(user_prompt))
        raise LLMProviderError(f"Unsupported schema class for offline fallback: {schema_cls.__name__}")

    @staticmethod
    def _first_sentence(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        body = " ".join(lines)
        return body[:260] if body else "KAP bildirimi metni."

    def _extract_dividend(self, text: str) -> dict[str, Any]:
        lower = text.lower()
        status = "proposed"
        if "dağıtılmamasına" in lower or "dağıtılmaması" in lower:
            status = "no_dividend"
        elif "genel kurul" in lower and ("onaylan" in lower or "kabul edil" in lower):
            status = "approved"

        year_match = re.search(r"(202[0-9])\s*(?:yılı|hesap\s*dönemi)", text, re.I)
        fiscal_year = int(year_match.group(1)) if year_match else 2025

        gross_match = re.search(r"brüt[^0-9]{0,25}(\d+(?:[.,]\d+)?)\s*TL", text, re.I)
        net_match = re.search(r"net[^0-9]{0,25}(\d+(?:[.,]\d+)?)\s*TL", text, re.I)
        gross = _parse_tr_number(gross_match.group(1)) if gross_match else None
        net = _parse_tr_number(net_match.group(1)) if net_match else None
        if status == "no_dividend":
            gross = 0.0
            net = 0.0

        payment_type = "none" if status == "no_dividend" else (
            "installments" if "taksit" in lower else "single"
        )
        ex_date = re.search(r"hak\s*kullanım[^\d]*(\d{4}-\d{2}-\d{2})", text, re.I)
        pay_date = re.search(r"ödeme\s*tarih[^\d]*(\d{4}-\d{2}-\d{2})", text, re.I)

        return {
            "decision_status": status,
            "fiscal_year": fiscal_year,
            "gross_cash_per_share_try": gross,
            "net_cash_per_share_try": net,
            "total_gross_cash_dividend_try": None,
            "bonus_share_rate_pct": None,
            "payment_type": payment_type,
            "ex_dividend_date": ex_date.group(1) if ex_date else None,
            "payment_date": pay_date.group(1) if pay_date else None,
            "evidence_quote": self._first_sentence(text),
        }

    def _extract_financial(self, text: str) -> dict[str, Any]:
        period_match = re.search(r"(20\d{2}/(?:03|06|09|12))", text)
        period = period_match.group(1) if period_match else "2025/12"
        lower = text.lower()
        consolidation = "unconsolidated" if "solo" in lower or "konsolide olmayan" in lower else "consolidated"
        scale = "millions" if "milyon" in lower else ("thousands" if "bin tl" in lower else "full_try")

        rev_match = re.search(r"(?:hasılat|satış\s*gelirleri)[^0-9]{0,20}([\d.,]+)", text, re.I)
        net_match = re.search(r"(?:net\s*dönem\s*kârı|net\s*karı)[^0-9-]{0,20}(-?[\d.,]+)", text, re.I)
        return {
            "period": period,
            "consolidation": consolidation,
            "currency": "TRY",
            "presentation_scale": scale,
            "revenue_try": _parse_tr_number(rev_match.group(1)) if rev_match else None,
            "gross_profit_try": None,
            "ebitda_try": None,
            "operating_profit_try": None,
            "net_profit_try": _parse_tr_number(net_match.group(1)) if net_match else None,
            "total_assets_try": None,
            "total_equity_try": None,
            "net_debt_try": None,
            "evidence_quote": self._first_sentence(text),
        }

    def _extract_material_event(self, text: str) -> dict[str, Any]:
        lower = text.lower()
        category = "other"
        if any(tok in lower for tok in ("ihale", "sözleşme", "yeni iş ilişkisi", "sipariş")):
            category = "new_business_contract"
        elif any(tok in lower for tok in ("yatırım", "tesis", "kapasite")):
            category = "investment_capex"
        elif "geri alım" in lower:
            category = "share_buyback"
        elif "beklenti" in lower or "guidance" in lower:
            category = "guidance_revision"

        amt_match = re.search(r"([\d.,]+)\s*(USD|EUR|TRY|TL|GBP)", text, re.I)
        amount = _parse_tr_number(amt_match.group(1)) if amt_match else None
        curr_raw = amt_match.group(2).upper() if amt_match else None
        curr = "TRY" if curr_raw == "TL" else curr_raw
        impact = "positive" if category in ("new_business_contract", "investment_capex") else "unspecified"
        return {
            "event_category": category,
            "counterparty": None,
            "contract_amount_original": amount,
            "contract_currency": curr if curr in ("TRY", "USD", "EUR", "GBP") else None,
            "contract_amount_try_equiv": amount if curr == "TRY" else None,
            "revenue_ratio_pct": None,
            "expected_impact": impact,
            "evidence_quote": self._first_sentence(text),
        }

    def plan_agent_step(
        self,
        *,
        system_prompt: str,
        transcript: str,
    ) -> AgentTurnDecision:
        """
        Deterministic offline agent planner used when GEMINI_API_KEY is unset or
        in OFFLINE/SAHTE eval checks. Never runs on live Gemini.
        """
        question_match = re.search(r"^SORU:\s*(.+)$", transcript, re.M)
        question = question_match.group(1).strip() if question_match else transcript
        lower = question.lower()

        tool_blocks = re.findall(
            r"\[TOOL_RESULT tool=([^\]]+)\]\s*(\{.*?\}|\[.*?\])(?=\n\[TOOL_RESULT|\Z)",
            transcript,
            re.S,
        )

        if not tool_blocks:
            return self._choose_initial_tool(question, lower)

        tool_name, raw_json = tool_blocks[-1]
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            parsed = {}

        return self._synthesize_from_tool_result(lower, tool_name, parsed)

    def _choose_initial_tool(self, question: str, lower: str) -> AgentTurnDecision:
        all_known = set(BIST30_NON_BANK_TICKERS) | set(EXCLUDED_BANK_TICKERS)
        found_tickers = [t for t in re.findall(r"\b[A-Z]{4,6}\b", question) if t in all_known]
        # Also catch unknown candidate ticker words alongside stock nouns
        if not found_tickers:
            for tok in re.findall(r"\b[A-Z]{4,6}\b", question):
                if tok not in {"BIST", "FAVO", "FAVOK", "KAP", "TRY", "USD", "EUR"}:
                    found_tickers.append(tok)
                    break

        bank_hits = [t for t in found_tickers if t in EXCLUDED_BANK_TICKERS]
        if bank_hits:
            return AgentTurnDecision(
                action="call_tool",
                thought="Banka finansalları V1 kapsamı dışındadır; doğrulama için arama yapılıyor.",
                tool_name="search_disclosures",
                tool_args={"ticker": bank_hits[0], "disclosure_type": "financial_report", "limit": 5},
            )

        if len(found_tickers) == 1:
            ticker_filter = f" WHERE ticker = '{found_tickers[0]}'"
        elif len(found_tickers) >= 2:
            quoted = ", ".join(f"'{t}'" for t in found_tickers)
            ticker_filter = f" WHERE ticker IN ({quoted})"
        else:
            ticker_filter = ""

        if any(k in lower for k in ("temettü", "kar payı", "nakit dağıt", "bedelsiz", "hak kullanım")):
            order = "ASC" if any(k in lower for k in ("en düşük", "az")) else "DESC"
            sql = (
                "SELECT disclosure_id, ticker, fiscal_year, decision_status, "
                "gross_cash_per_share_try, net_cash_per_share_try, payment_type, "
                "bonus_share_rate_pct, ex_dividend_date, payment_date "
                f"FROM dividend_events{ticker_filter} "
                f"ORDER BY COALESCE(net_cash_per_share_try, 0) {order}"
            )
            return AgentTurnDecision(
                action="call_tool",
                thought="Temettü sorusu için dividend_events tablosunda SQL sorgusu çalıştırılıyor.",
                tool_name="run_sql",
                tool_args={"sql": sql, "purpose": "Temettü kayıtlarını karşılaştır"},
            )

        if any(k in lower for k in ("hasılat", "ciro", "net kâr", "net kar", "zarar", "favök", "ebitda", "varlık", "özkaynak", "net borç", "finansal", "2026/03", "2025/12")):
            sql = (
                "SELECT disclosure_id, ticker, period, consolidation, revenue_try, "
                "ebitda_try, operating_profit_try, net_profit_try, total_assets_try, total_equity_try, net_debt_try "
                f"FROM financial_reports{ticker_filter} "
                "ORDER BY net_profit_try DESC"
            )
            return AgentTurnDecision(
                action="call_tool",
                thought="Finansal rapor analizi için financial_reports tablosu sorgulanıyor.",
                tool_name="run_sql",
                tool_args={"sql": sql, "purpose": "Finansal rapor verilerini getir"},
            )

        if any(k in lower for k in ("sözleşme", "ihale", "yatırım", "kapasite", "geri alım", "beklenti", "özel durum", "eur", "usd", "hasılata oranı")):
            sql = (
                "SELECT disclosure_id, ticker, event_category, counterparty, "
                "contract_amount_original, contract_currency, contract_amount_try_equiv, "
                "revenue_ratio_pct, expected_impact "
                f"FROM material_events{ticker_filter} "
                "ORDER BY COALESCE(contract_amount_try_equiv, 0) DESC"
            )
            return AgentTurnDecision(
                action="call_tool",
                thought="Özel durum / sözleşme / yatırım verileri için material_events sorgulanıyor.",
                tool_name="run_sql",
                tool_args={"sql": sql, "purpose": "Özel durum kayıtlarını getir"},
            )

        if found_tickers:
            return AgentTurnDecision(
                action="call_tool",
                thought="İlgili şirket bildirimleri FTS5 / metadata üzerinden aranıyor.",
                tool_name="search_disclosures",
                tool_args={"ticker": found_tickers[0], "limit": 10},
            )

        return AgentTurnDecision(
            action="call_tool",
            thought="Anahtar kelimelerle bildirim araması yapılıyor.",
            tool_name="search_disclosures",
            tool_args={"query": question[:120], "limit": 10},
        )

    def _synthesize_from_tool_result(
        self,
        lower: str,
        tool_name: str,
        parsed: Any,
    ) -> AgentTurnDecision:
        if isinstance(parsed, dict) and parsed.get("error"):
            return AgentTurnDecision(
                action="final_answer",
                thought="Araç hatası döndüğü için güvenli yanıt veriliyor.",
                answer_text="Mevcut KAP bildirim veri setinde bu soruyu doğrulayacak kayıt bulunamadı.",
            )

        if tool_name == "run_sql" and isinstance(parsed, dict):
            rows = parsed.get("rows") or []
            if not rows:
                return AgentTurnDecision(
                    action="final_answer",
                    thought="SQL boş sonuç döndürdü.",
                    answer_text="Mevcut KAP bildirim veri setinde bu soruyu doğrulayacak kayıt bulunamadı.",
                )

            if "zarar" in lower:
                loss_rows = [r for r in rows if isinstance(r.get("net_profit_try"), (int, float)) and r["net_profit_try"] < 0]
                if loss_rows:
                    rows = loss_rows
            elif "dağıtmayan" in lower or "dağıtmama" in lower:
                nd_rows = [r for r in rows if r.get("decision_status") == "no_dividend"]
                if nd_rows:
                    rows = nd_rows
            elif "taksit" in lower:
                inst = [r for r in rows if r.get("payment_type") == "installments"]
                if inst:
                    rows = inst
            elif "bedelsiz" in lower:
                bonus = [r for r in rows if (r.get("bonus_share_rate_pct") or 0) > 0]
                if bonus:
                    rows = bonus
            elif "geri alım" in lower:
                sb = [r for r in rows if r.get("event_category") == "share_buyback"]
                if sb:
                    rows = sb
            elif "beklenti" in lower or "reviz" in lower:
                gr = [r for r in rows if r.get("event_category") == "guidance_revision"]
                if gr:
                    rows = gr

            top_rows = rows[:5]
            lines = []
            for row in top_rows:
                cid = row.get("disclosure_id")
                ticker = row.get("ticker", "")
                cite = f" [kap:{cid}]" if cid else ""
                if "net_cash_per_share_try" in row:
                    lines.append(
                        f"- {ticker}: {row.get('fiscal_year')} net {row.get('net_cash_per_share_try')} TL / brüt {row.get('gross_cash_per_share_try')} TL ({row.get('decision_status')}, ödeme={row.get('payment_type')}){cite}"
                    )
                elif "net_profit_try" in row:
                    lines.append(
                        f"- {ticker} ({row.get('period')}): Hasılat {row.get('revenue_try')}, Net Kâr {row.get('net_profit_try')}{cite}"
                    )
                else:
                    lines.append(
                        f"- {ticker} ({row.get('event_category')}): {row.get('contract_amount_original') or ''} {row.get('contract_currency') or ''} (TRY eşdeğeri: {row.get('contract_amount_try_equiv')}){cite}"
                    )

            chart_spec: Optional[ChartSpec] = None
            cols = list(top_rows[0].keys())
            if "ticker" in cols:
                for y_col in ("net_cash_per_share_try", "net_profit_try", "revenue_try", "contract_amount_try_equiv"):
                    if y_col in cols and len(top_rows) >= 2:
                        chart_spec = ChartSpec(
                            type="bar",
                            title="KAP Karşılaştırma Özeti",
                            x_key="ticker",
                            y_key=y_col,
                        )
                        break

            return AgentTurnDecision(
                action="final_answer",
                thought="SQL sonuçlarından kaynaklı yanıt oluşturuldu.",
                answer_text="KAP bildirim kayıtlarına göre sonuçlar:\n" + "\n".join(lines),
                chart_spec=chart_spec,
            )

        if tool_name == "search_disclosures" and isinstance(parsed, list):
            if not parsed:
                return AgentTurnDecision(
                    action="final_answer",
                    thought="Aramada eşleşen bildirim çıkmadı.",
                    answer_text="Mevcut KAP bildirim veri setinde bu soruyu doğrulayacak kayıt bulunamadı.",
                )
            lines = [
                f"- {r.get('ticker')}: {r.get('summary_tr') or r.get('title')} [kap:{r.get('disclosure_id')}]"
                for r in parsed[:5]
                if r.get("disclosure_id")
            ]
            return AgentTurnDecision(
                action="final_answer",
                thought="Arama sonuçlarından kaynaklı özet hazırlanıyor.",
                answer_text="İlgili KAP bildirimleri:\n" + "\n".join(lines),
            )

        if tool_name == "get_disclosure" and isinstance(parsed, dict) and parsed.get("disclosure_id"):
            cid = parsed["disclosure_id"]
            return AgentTurnDecision(
                action="final_answer",
                thought="Tekil bildirim detayından yanıt oluşturuluyor.",
                answer_text=f"{parsed.get('ticker')}: {parsed.get('summary_tr')} [kap:{cid}]",
            )

        return AgentTurnDecision(
            action="final_answer",
            thought="Yeterli doğrulanabilir veri bulunamadı.",
            answer_text="Mevcut KAP bildirim veri setinde bu soruyu doğrulayacak kayıt bulunamadı.",
        )


def get_extraction_provider(settings: Optional[Settings] = None) -> LLMProvider:
    """Return GeminiFlashProvider when GEMINI_API_KEY is configured, else OfflineFallbackProvider."""
    cfg = settings or get_settings()
    if cfg.gemini_api_key:
        return GeminiFlashProvider(api_key=cfg.gemini_api_key, model_name=cfg.gemini_model)
    return OfflineFallbackProvider()
