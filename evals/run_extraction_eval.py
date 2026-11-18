"""
Extraction evaluation runner over evals/extraction_gold.jsonl.

Rules:
  - Accuracy metrics are computed STRICTLY from records where `"verified": true`.
  - Unverified draft cases (`verified: false`) are reported separately and NEVER
    counted toward accuracy percentages (if 0 verified cases exist -> "ölçülmedi").
  - If an offline regex fallback or offline cache is used instead of a live LLM call,
    the output explicitly displays an "OFFLINE/SAHTE" banner so it is never mistaken
    for real Gemini evaluation results.

Usage (from repo root):
  python evals/run_extraction_eval.py          # Offline check / draft report
  python evals/run_extraction_eval.py --live   # Live Gemini Flash evaluation
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.llm.base import LLMProvider  # noqa: E402
from app.llm.gemini import OfflineFallbackProvider, get_extraction_provider  # noqa: E402
from app.pipeline.extractor import SCHEMA_BY_TYPE  # noqa: E402
from app.schemas.base import DisclosureEnvelope, RawDisclosure  # noqa: E402

GOLD_PATH = REPO_ROOT / "evals" / "extraction_gold.jsonl"
SAMPLE_EXTRACTIONS_DIR = REPO_ROOT / "data" / "sample_seed" / "extractions"


def _values_match(expected: Any, actual: Any) -> bool:
    if expected is None and actual is None:
        return True
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if isinstance(expected, bool) or isinstance(actual, bool):
            return expected is actual
        tol = max(1e-4, abs(float(expected)) * 1e-4)
        return abs(float(expected) - float(actual)) <= tol
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() == actual.strip().lower()
    return expected == actual


def _extract_record_dict(
    rec: dict[str, Any],
    *,
    provider: LLMProvider,
    use_live_llm: bool,
    sample_extractions_dir: Path,
) -> dict[str, Any]:
    disc_id = rec["disclosure_id"]
    dtype = rec["disclosure_type"]
    ext_path = sample_extractions_dir / f"{disc_id}.json"
    if not use_live_llm and ext_path.exists():
        env = DisclosureEnvelope.model_validate_json(ext_path.read_text(encoding="utf-8"))
        return env.extracted.model_dump()

    raw = RawDisclosure.model_validate(
        {
            "disclosure_id": disc_id,
            "ticker": rec["ticker"],
            "company_name": rec.get("company_name", rec["ticker"]),
            "disclosure_type": dtype,
            "published_at": rec["published_at"],
            "source_url": rec["source_url"],
            "title": rec["title"],
            "raw_text": rec["raw_text"],
            "source": "eval_fixture",
        }
    )
    schema_cls = SCHEMA_BY_TYPE[dtype]
    extracted = provider.generate_structured(
        system_prompt="KAP bildiriminden yapılandırılmış alanları çıkar.",
        user_prompt=raw.raw_text,
        schema_cls=schema_cls,
    )
    return extracted.model_dump()


def run_eval(
    *,
    use_live_llm: bool = False,
    gold_path: Path = GOLD_PATH,
    provider: Optional[LLMProvider] = None,
    sample_extractions_dir: Path = SAMPLE_EXTRACTIONS_DIR,
) -> dict[str, Any]:
    if not gold_path.exists():
        raise FileNotFoundError(f"Missing eval file: {gold_path}")

    active_provider = provider or (
        get_extraction_provider() if use_live_llm else OfflineFallbackProvider()
    )
    is_offline = bool(getattr(active_provider, "is_offline_fallback", False)) or not use_live_llm
    execution_mode = (
        f"LIVE_LLM ({active_provider.model_name})"
        if not is_offline
        else "OFFLINE/SAHTE (Regex Fallback / Önbellek — Demo ve README'de gerçek sonuç olarak KULLANILAMAZ)"
    )

    records = [
        json.loads(line)
        for line in gold_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    verified_records = [r for r in records if r.get("verified") is True]
    unverified_records = [r for r in records if r.get("verified") is not True]

    # Measure accuracy ONLY across verified == True cases
    schema_valid = 0
    total_fields = 0
    matched_fields = 0
    numeric_total = 0
    numeric_matched = 0
    exact_record_matches = 0
    by_type: dict[str, dict[str, int]] = {}

    for rec in verified_records:
        dtype = rec["disclosure_type"]
        bucket = by_type.setdefault(
            dtype, {"cases": 0, "schema_valid": 0, "fields": 0, "matched": 0}
        )
        bucket["cases"] += 1
        actual_dict = _extract_record_dict(
            rec,
            provider=active_provider,
            use_live_llm=not is_offline,
            sample_extractions_dir=sample_extractions_dir,
        )
        schema_valid += 1
        bucket["schema_valid"] += 1

        expected_dict: dict[str, Any] = rec["expected"]
        case_all_match = True
        for field_name, exp_val in expected_dict.items():
            act_val = actual_dict.get(field_name)
            total_fields += 1
            bucket["fields"] += 1
            is_num = isinstance(exp_val, (int, float)) and not isinstance(exp_val, bool)
            if is_num:
                numeric_total += 1
            if _values_match(exp_val, act_val):
                matched_fields += 1
                bucket["matched"] += 1
                if is_num:
                    numeric_matched += 1
            else:
                case_all_match = False
        if case_all_match:
            exact_record_matches += 1

    # Unverified drafts: schema check only, NEVER report accuracy %
    unverified_schema_valid = 0
    for rec in unverified_records:
        _extract_record_dict(
            rec,
            provider=active_provider,
            use_live_llm=not is_offline,
            sample_extractions_dir=sample_extractions_dir,
        )
        unverified_schema_valid += 1

    has_verified = len(verified_records) > 0
    return {
        "execution_mode": execution_mode,
        "provider_model": active_provider.model_name,
        "total_cases": len(records),
        "verified_cases": len(verified_records),
        "unverified_draft_cases": len(unverified_records),
        "schema_validity_pct": (
            round((schema_valid / len(verified_records)) * 100, 2) if has_verified else "ölçülmedi"
        ),
        "field_exact_match_pct": (
            round((matched_fields / max(1, total_fields)) * 100, 2) if has_verified else "ölçülmedi"
        ),
        "numeric_accuracy_pct": (
            round((numeric_matched / max(1, numeric_total)) * 100, 2)
            if (has_verified and numeric_total > 0)
            else "ölçülmedi"
        ),
        "full_record_match_pct": (
            round((exact_record_matches / len(verified_records)) * 100, 2)
            if has_verified
            else "ölçülmedi"
        ),
        "by_type_verified_only": (
            {
                k: {
                    "cases": v["cases"],
                    "schema_valid_pct": round((v["schema_valid"] / max(1, v["cases"])) * 100, 2),
                    "field_match_pct": round((v["matched"] / max(1, v["fields"])) * 100, 2),
                }
                for k, v in by_type.items()
            }
            if has_verified
            else "ölçülmedi (verified:true vaka yok)"
        ),
        "unverified_draft_report": {
            "count": len(unverified_records),
            "schema_valid_count": unverified_schema_valid,
            "accuracy_pct": "ölçülmedi (verified:false — insan doğrulaması bekliyor)",
        },
    }


def main() -> int:
    use_live = "--live" in sys.argv
    report = run_eval(use_live_llm=use_live)
    print("=== KAP Agent Extraction Eval Report ===")
    if "OFFLINE/SAHTE" in report["execution_mode"]:
        print("[!] UYARI: OFFLINE/SAHTE modda calisildi. Bu cikti demo/README icin gercek LLM sonucu DEGILDIR.")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    all_valid = (
        report["unverified_draft_report"]["schema_valid_count"] == report["unverified_draft_cases"]
    )
    return 0 if all_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
