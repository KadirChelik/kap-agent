"""
Agent evaluation runner over evals/agent_gold.jsonl (30 questions).

Rules:
  - Accuracy metrics (First-Try SQL %, Avg SQL Retries, Citation Precision/Recall %,
    Advice Refusal %, Answer Match %, Latency p50/p95 ms) are computed STRICTLY from
    records where `"verified": true`.
  - When 0 verified cases exist, accuracy metrics report `"ölçülmedi"`.
  - When running with OfflineFallbackProvider (without --live or without GEMINI_API_KEY),
    the output explicitly states `OFFLINE/SAHTE` so it is never confused with real Gemini evals.

Usage (from repo root):
  python evals/run_agent_eval.py          # Offline draft check
  python evals/run_agent_eval.py --live   # Live Gemini Flash agent evaluation
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent.orchestrator import run_kap_agent  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm.base import LLMProvider  # noqa: E402
from app.llm.gemini import OfflineFallbackProvider, get_extraction_provider  # noqa: E402

GOLD_PATH = REPO_ROOT / "evals" / "agent_gold.jsonl"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return round(ordered[idx], 2)


def run_eval(
    *,
    use_live_llm: bool = False,
    gold_path: Path = GOLD_PATH,
    db_path: Optional[Path] = None,
    provider: Optional[LLMProvider] = None,
) -> dict[str, Any]:
    if not gold_path.exists():
        raise FileNotFoundError(f"Missing eval file: {gold_path}")

    settings = get_settings()
    resolved_db = db_path or settings.db_path
    active_provider = provider or (
        get_extraction_provider(settings) if use_live_llm else OfflineFallbackProvider()
    )
    is_offline = bool(getattr(active_provider, "is_offline_fallback", False)) or not use_live_llm
    execution_mode = (
        f"LIVE_LLM ({active_provider.model_name})"
        if not is_offline
        else "OFFLINE/SAHTE (Heuristic Planner — Demo ve README'de gerçek LLM sonucu olarak KULLANILAMAZ)"
    )

    records = [
        json.loads(line)
        for line in gold_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    verified_records = [r for r in records if r.get("verified") is True]
    unverified_records = [r for r in records if r.get("verified") is not True]

    # Execute unverified drafts strictly for pipeline sanity & crash check (never scored as accuracy)
    unverified_ran = 0
    for rec in unverified_records:
        run_kap_agent(rec["question"], db_path=resolved_db, provider=active_provider)
        unverified_ran += 1

    # Measure metrics ONLY from verified == True cases
    if not verified_records:
        return {
            "execution_mode": execution_mode,
            "provider_model": active_provider.model_name,
            "total_cases": len(records),
            "verified_cases": 0,
            "unverified_draft_cases": len(unverified_records),
            "first_try_sql_success_pct": "ölçülmedi",
            "avg_sql_retries": "ölçülmedi",
            "citation_precision_pct": "ölçülmedi",
            "citation_recall_pct": "ölçülmedi",
            "advice_refusal_pct": "ölçülmedi",
            "answer_contains_match_pct": "ölçülmedi",
            "latency_p50_ms": "ölçülmedi",
            "latency_p95_ms": "ölçülmedi",
            "unverified_draft_report": {
                "count": len(unverified_records),
                "ran_without_crash": unverified_ran,
                "accuracy_pct": "ölçülmedi (verified:false — insan doğrulaması bekliyor)",
            },
        }

    sql_first_try_flags: list[bool] = []
    sql_retry_counts: list[int] = []
    latencies: list[float] = []
    advice_cases = 0
    advice_passed = 0
    answer_matched = 0
    citation_tp = 0
    citation_fp = 0
    citation_fn = 0

    for rec in verified_records:
        res = run_kap_agent(rec["question"], db_path=resolved_db, provider=active_provider)
        latencies.append(res.total_duration_ms)
        sql_retry_counts.append(res.sql_retries)
        if res.first_try_sql_success is not None:
            sql_first_try_flags.append(res.first_try_sql_success)

        if rec.get("should_refuse_as_advice"):
            advice_cases += 1
            if res.refused_as_advice:
                advice_passed += 1

        lower_ans = res.answer_text.lower()
        expected_tokens = rec.get("expected_answer_contains") or []
        if all(tok.lower() in lower_ans for tok in expected_tokens):
            answer_matched += 1

        expected_ids = set(rec.get("expected_disclosure_ids") or [])
        actual_ids = {c.disclosure_id for c in res.citations}
        if expected_ids or actual_ids:
            citation_tp += len(expected_ids & actual_ids)
            citation_fp += len(actual_ids - expected_ids)
            citation_fn += len(expected_ids - actual_ids)

    precision_denom = citation_tp + citation_fp
    recall_denom = citation_tp + citation_fn

    return {
        "execution_mode": execution_mode,
        "provider_model": active_provider.model_name,
        "total_cases": len(records),
        "verified_cases": len(verified_records),
        "unverified_draft_cases": len(unverified_records),
        "first_try_sql_success_pct": (
            round((sum(1 for f in sql_first_try_flags if f) / len(sql_first_try_flags)) * 100, 2)
            if sql_first_try_flags
            else "ölçülmedi"
        ),
        "avg_sql_retries": round(statistics.mean(sql_retry_counts), 2) if sql_retry_counts else 0.0,
        "citation_precision_pct": (
            round((citation_tp / precision_denom) * 100, 2) if precision_denom > 0 else 100.0
        ),
        "citation_recall_pct": (
            round((citation_tp / recall_denom) * 100, 2) if recall_denom > 0 else 100.0
        ),
        "advice_refusal_pct": (
            round((advice_passed / advice_cases) * 100, 2) if advice_cases > 0 else "ölçülmedi"
        ),
        "answer_contains_match_pct": round((answer_matched / len(verified_records)) * 100, 2),
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
        "unverified_draft_report": {
            "count": len(unverified_records),
            "ran_without_crash": unverified_ran,
            "accuracy_pct": "ölçülmedi (verified:false — insan doğrulaması bekliyor)",
        },
    }


def main() -> int:
    use_live = "--live" in sys.argv
    report = run_eval(use_live_llm=use_live)
    print("=== KAP Agent Eval Report ===")
    if "OFFLINE/SAHTE" in report["execution_mode"]:
        print("[!] UYARI: OFFLINE/SAHTE modda calisildi. Bu cikti demo/README icin gercek LLM sonucu DEGILDIR.")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
