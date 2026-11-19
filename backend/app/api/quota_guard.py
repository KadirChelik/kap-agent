"""
Chat quota protection, question cache, and graceful Demo/Replay fallback.

Protects free-tier Gemini API keys against abuse with:
  - Normalized question response caching (repeat questions cost 0 tokens)
  - Per-IP sliding window limits (minute + 24h) and a global daily cap
  - Automatic fallback to Demo Mode replay for canonical preset questions
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Optional

from app.agent.citation_gate import is_investment_advice_query
from app.agent.orchestrator import run_kap_agent
from app.llm.base import LLMProvider, LLMProviderError
from app.llm.gemini import OfflineFallbackProvider
from app.schemas.agent import AgentRunResult, TraceStep

DEMO_QUESTIONS: tuple[str, ...] = (
    "Hisse başına en yüksek net nakit temettü dağıtan 5 şirket hangileridir?",
    "2026/09 döneminde hasılatı en yüksek 5 şirketi ve net kârlarını sırala",
    "ASELS ve FROTO'nun 2025/09 ile 2026/09 hasılat ve net kâr gelişimini karşılaştır",
    "2026 yılındaki pay geri alım, yatırım ve beklenti revizyonu bildirimlerini özetle",
)

_NORM_SPACE_RE = re.compile(r"\s+")
_EDGE_PUNCT_RE = re.compile(r"^[?.,!:\s]+|[?.,!:\s]+$")


def normalize_question_key(question: str) -> str:
    """Canonical lowercase key for cache and demo-question matching."""
    collapsed = _NORM_SPACE_RE.sub(" ", question.strip().lower())
    return _EDGE_PUNCT_RE.sub("", collapsed)


_DEMO_KEYS: frozenset[str] = frozenset(normalize_question_key(q) for q in DEMO_QUESTIONS)


class ChatQuotaManager:
    """In-memory query cache + per-IP & daily quota limiter for POST /api/chat."""

    def __init__(
        self,
        *,
        max_per_minute: int = 10,
        max_per_ip_day: int = 30,
        max_global_day: int = 200,
        cache_ttl_sec: float = 86_400.0,
        max_cache_items: int = 128,
    ) -> None:
        self.max_per_minute = max_per_minute
        self.max_per_ip_day = max_per_ip_day
        self.max_global_day = max_global_day
        self.cache_ttl_sec = cache_ttl_sec
        self.max_cache_items = max_cache_items
        self._ip_hits: dict[str, list[float]] = {}
        self._global_hits: list[float] = []
        self._cache: dict[str, tuple[float, AgentRunResult]] = {}

    def reset(self) -> None:
        self._ip_hits.clear()
        self._global_hits.clear()
        self._cache.clear()

    def clear_cache(self) -> None:
        """Clear cached question responses after fresh KAP disclosures are ingested."""
        self._cache.clear()

    def configure_limits(
        self,
        *,
        max_per_minute: Optional[int] = None,
        max_per_ip_day: Optional[int] = None,
        max_global_day: Optional[int] = None,
    ) -> None:
        if max_per_minute is not None:
            self.max_per_minute = max_per_minute
        if max_per_ip_day is not None:
            self.max_per_ip_day = max_per_ip_day
        if max_global_day is not None:
            self.max_global_day = max_global_day

    def _prune(self, ip_hash: str, now: float) -> list[float]:
        day_cutoff = now - 86_400.0
        self._global_hits = [t for t in self._global_hits if t >= day_cutoff]
        ip_list = [t for t in self._ip_hits.get(ip_hash, []) if t >= day_cutoff]
        self._ip_hits[ip_hash] = ip_list
        return ip_list

    def get_quota_status(self, ip_hash: str) -> dict[str, int]:
        now = time.monotonic()
        ip_list = self._prune(ip_hash, now)
        min_cutoff = now - 60.0
        ip_min_used = sum(1 for t in ip_list if t >= min_cutoff)
        return {
            "remaining_ip_minute": max(0, self.max_per_minute - ip_min_used),
            "remaining_ip_day": max(0, self.max_per_ip_day - len(ip_list)),
            "remaining_global_day": max(0, self.max_global_day - len(self._global_hits)),
        }

    def _has_live_quota(self, ip_hash: str) -> bool:
        status = self.get_quota_status(ip_hash)
        return (
            status["remaining_ip_minute"] > 0
            and status["remaining_ip_day"] > 0
            and status["remaining_global_day"] > 0
        )

    def _record_hit(self, ip_hash: str) -> None:
        now = time.monotonic()
        self._prune(ip_hash, now)
        self._ip_hits.setdefault(ip_hash, []).append(now)
        self._global_hits.append(now)

    def _get_cached(self, key: str) -> Optional[AgentRunResult]:
        entry = self._cache.get(key)
        if not entry:
            return None
        ts, res = entry
        if time.monotonic() - ts > self.cache_ttl_sec:
            self._cache.pop(key, None)
            return None
        return res

    def _put_cached(self, key: str, res: AgentRunResult) -> None:
        if len(self._cache) >= self.max_cache_items:
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key, None)
        self._cache[key] = (time.monotonic(), res)

    def run_with_quota_and_demo(
        self,
        *,
        question: str,
        ip_hash: str,
        db_path: Path,
        provider: Optional[LLMProvider] = None,
    ) -> AgentRunResult:
        key = normalize_question_key(question)

        cached = self._get_cached(key)
        if cached is not None:
            return cached.model_copy(
                update={
                    "cached_hit": True,
                    "mode": "cached_demo",
                }
            )

        # Guardrail advice checks never burn LLM quota
        if is_investment_advice_query(question):
            return run_kap_agent(question, db_path=db_path, provider=provider)

        if not self._has_live_quota(ip_hash):
            return self._demo_fallback(question=question, key=key, db_path=db_path)

        self._record_hit(ip_hash)
        try:
            result = run_kap_agent(question, db_path=db_path, provider=provider)
        except LLMProviderError:
            return self._demo_fallback(question=question, key=key, db_path=db_path)

        if not result.refused_as_advice and not result.no_evidence_found:
            self._put_cached(key, result)
        return result

    def _demo_fallback(self, *, question: str, key: str, db_path: Path) -> AgentRunResult:
        if key in _DEMO_KEYS:
            demo_res = run_kap_agent(
                question,
                db_path=db_path,
                provider=OfflineFallbackProvider(),
            ).model_copy(
                update={
                    "mode": "cached_demo",
                    "quota_exhausted": True,
                    "cached_hit": True,
                }
            )
            self._put_cached(key, demo_res)
            return demo_res

        suggestions = "\n".join(f"- {q}" for q in DEMO_QUESTIONS[:3])
        return AgentRunResult(
            question=question,
            answer_text=(
                "Günlük canlı sorgu kotası doldu; Kota Koruması nedeniyle yalnızca Demo Modu "
                "etkin. Aşağıdaki hazır örnek sorulardan birini seçerek tüm SQL + Trace + "
                f"Kaynak doğrulama akışını ücretsiz deneyebilirsiniz:\n{suggestions}"
            ),
            citations=[],
            artifact=None,
            trace=[
                TraceStep(
                    step_index=1,
                    step_type="guardrail",
                    tool_name=None,
                    input_summary="Kota ve Rate-Limit kontrolü",
                    output_summary="Canlı kota doldu -> Demo Modu yanıtı döndürüldü (0 LLM maliyeti).",
                    is_error=False,
                    duration_ms=0.2,
                )
            ],
            sql_attempts=0,
            sql_retries=0,
            first_try_sql_success=None,
            refused_as_advice=False,
            no_evidence_found=True,
            quota_exhausted=True,
            cached_hit=False,
            mode="cached_demo",
            provider_model="DEMO_REPLAY (Kota Koruması Aktif)",
            total_duration_ms=0.5,
        )


def hash_client_ip(raw_ip: str) -> str:
    return hashlib.sha256((raw_ip or "unknown").encode("utf-8")).hexdigest()[:12]


default_quota_manager = ChatQuotaManager()


def reset_chat_quota_and_cache() -> None:
    """Reset default quota manager counters and query cache cleanly."""
    default_quota_manager.reset()
