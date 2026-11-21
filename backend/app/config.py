"""Central configuration and domain universe constants for KAP Agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env", override=False)

# Selected 18 non-bank BIST 30 real-sector companies for V1.
BIST30_NON_BANK_TICKERS: tuple[str, ...] = (
    "THYAO",
    "TUPRS",
    "FROTO",
    "ASELS",
    "BIMAS",
    "EREGL",
    "SISE",
    "TCELL",
    "TTKOM",
    "KCHOL",
    "SAHOL",
    "TOASO",
    "ARCLK",
    "MGROS",
    "PGSUS",
    "ENKAI",
    "ASTOR",
    "KONTR",
)

# Banks are explicitly excluded from the V1 financial_report schema (BDDK format differs).
EXCLUDED_BANK_TICKERS: frozenset[str] = frozenset(
    {
        "GARAN",
        "AKBNK",
        "ISCTR",
        "YKBNK",
        "VAKBN",
        "HALKB",
        "TSKB",
        "ALBRK",
        "SKBNK",
    }
)

TICKER_COMPANY_NAMES: dict[str, str] = {
    "THYAO": "Türk Hava Yolları A.O.",
    "TUPRS": "Tüpraş - Türkiye Petrol Rafinerileri A.Ş.",
    "FROTO": "Ford Otomotiv Sanayi A.Ş.",
    "ASELS": "Aselsan Elektronik Sanayi ve Ticaret A.Ş.",
    "BIMAS": "BİM Birleşik Mağazalar A.Ş.",
    "EREGL": "Ereğli Demir ve Çelik Fabrikaları T.A.Ş.",
    "SISE": "Türkiye Şişe ve Cam Fabrikaları A.Ş.",
    "TCELL": "Turkcell İletişim Hizmetleri A.Ş.",
    "TTKOM": "Türk Telekomünikasyon A.Ş.",
    "KCHOL": "Koç Holding A.Ş.",
    "SAHOL": "Hacı Ömer Sabancı Holding A.Ş.",
    "TOASO": "Tofaş Türk Otomobil Fabrikası A.Ş.",
    "ARCLK": "Arçelik A.Ş.",
    "MGROS": "Migros Ticaret A.Ş.",
    "PGSUS": "Pegasus Hava Taşımacılığı A.Ş.",
    "ENKAI": "Enka İnşaat ve Sanayi A.Ş.",
    "ASTOR": "Astor Enerji A.Ş.",
    "KONTR": "Kontrolmatik Teknoloji Enerji ve Mühendislik A.Ş.",
}


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    repo_root: Path
    db_path: Path
    sample_raw_dir: Path
    sample_extractions_dir: Path
    raw_cache_dir: Path
    extractions_cache_dir: Path
    gemini_api_key: str
    gemini_model: str
    groq_api_key: str
    groq_model: str
    openrouter_api_key: str
    openrouter_model: str
    llm_min_interval_sec: float
    api_global_rate_limit_per_min: int
    admin_token: str
    kap_user_agent: str
    kap_auto_sync_enabled: bool = True
    kap_sync_interval_sec: int = 900
    kap_sync_lookback_days: int = 14
    kap_sync_limit_per_ticker: int = 5
    sql_timeout_ms: int = 1500
    sql_max_limit: int = 50
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    )
    cors_origin_regex: str = r"https://.*\.vercel\.app"


def _resolve_repo_path(raw_value: str) -> Path:
    candidate = Path(raw_value)
    return candidate if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()


def _parse_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    parts = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    return parts or default


def _parse_float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def _parse_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def get_settings() -> Settings:
    """Build current settings snapshot from environment variables."""
    return Settings(
        repo_root=REPO_ROOT,
        db_path=_resolve_repo_path(os.getenv("KAP_DB_PATH", "data/kap_agent.db")),
        sample_raw_dir=REPO_ROOT / "data" / "sample_seed" / "raw",
        sample_extractions_dir=REPO_ROOT / "data" / "sample_seed" / "extractions",
        raw_cache_dir=REPO_ROOT / "data" / "raw",
        extractions_cache_dir=REPO_ROOT / "data" / "extractions",
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3-flash-preview").strip() or "gemini-3-flash-preview",
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b",
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free",
        llm_min_interval_sec=_parse_float_env("LLM_MIN_INTERVAL_SEC", 1.2),
        api_global_rate_limit_per_min=_parse_int_env("API_GLOBAL_RATE_LIMIT_PER_MIN", 60),
        admin_token=os.getenv("ADMIN_TOKEN", "dev-admin-token").strip() or "dev-admin-token",
        kap_user_agent=os.getenv(
            "KAP_USER_AGENT",
            "KAPAgentPortfolio/1.0 (+https://github.com/KadirChelik/kap-agent)",
        ).strip(),
        kap_auto_sync_enabled=os.getenv("KAP_AUTO_SYNC_ENABLED", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        kap_sync_interval_sec=max(60, _parse_int_env("KAP_SYNC_INTERVAL_SEC", 900)),
        kap_sync_lookback_days=_parse_int_env("KAP_SYNC_LOOKBACK_DAYS", 14),
        kap_sync_limit_per_ticker=_parse_int_env("KAP_SYNC_LIMIT_PER_TICKER", 5),
        cors_origins=_parse_csv_env(
            "CORS_ORIGINS",
            ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173"),
        ),
        cors_origin_regex=os.getenv("CORS_ORIGIN_REGEX", r"https://.*\.vercel\.app").strip(),
    )
