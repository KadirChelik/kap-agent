"""Stage 4 verification suite: quota guard, query caching, demo fallback, and MCP stdio server."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.quota_guard import (
    DEMO_QUESTIONS,
    ChatQuotaManager,
    reset_chat_quota_and_cache,
)
from app.config import get_settings
from app.db.seed_loader import load_dataset_into_db
from app.llm.base import LLMProvider, LLMProviderError
from app.llm.gemini import FallbackChainProvider, OpenAICompatibleProvider
from app.main import create_app
from app.mcp.server import handle_mcp_request
from app.schemas.agent import AgentTurnDecision
from app.schemas.dividend import DividendExtraction

TModel = TypeVar("TModel", bound=BaseModel)


class CountingLiveMockProvider(LLMProvider):
    model_name = "mock-gemini-2.5-flash"
    is_offline_fallback = False

    def __init__(self) -> None:
        self.calls = 0

    def generate_structured(self, *, system_prompt: str, user_prompt: str, schema_cls: type[TModel]) -> TModel:
        raise NotImplementedError

    def plan_agent_step(self, *, system_prompt: str, transcript: str) -> AgentTurnDecision:
        self.calls += 1
        if "[TOOL_RESULT" not in transcript:
            return AgentTurnDecision(
                action="call_tool",
                thought="Querying dividend_events",
                tool_name="run_sql",
                tool_args={
                    "sql": "SELECT disclosure_id, ticker, net_cash_per_share_try FROM dividend_events ORDER BY net_cash_per_share_try DESC LIMIT 3",
                    "purpose": "Top 3 net dividends",
                },
            )
        return AgentTurnDecision(
            action="final_answer",
            thought="Responding with grounded citations",
            answer_text="FROTO (39.15 TL) [kap:1401002], TOASO (18.00 TL) [kap:1401003], TUPRS (13.68 TL) [kap:1401001].",
        )


def test_quota_cache_hit_and_demo_mode_fallback(tmp_path: Path) -> None:
    reset_chat_quota_and_cache()
    app = create_app(db_path=tmp_path / "stage4.db", admin_token="admin-test")
    provider = CountingLiveMockProvider()
    qm = ChatQuotaManager(max_per_minute=2, max_per_ip_day=2, max_global_day=5)
    app.state.llm_provider = provider
    app.state.quota_manager = qm
    client = TestClient(app)

    # First call uses live provider and warms cache
    q1 = "ASELS 2025/12 finansalları nedir?"
    r1 = client.post("/api/chat", json={"question": q1, "stream": False})
    assert r1.status_code == 200
    assert r1.json()["cached_hit"] is False
    calls_after_first = provider.calls

    # Repeat identical question (different spacing/case) hits cache with 0 LLM calls
    r1_repeat = client.post(
        "/api/chat",
        json={"question": "  asels 2025/12 finansalları nedir? ", "stream": False},
    )
    assert r1_repeat.status_code == 200
    assert r1_repeat.json()["cached_hit"] is True
    assert r1_repeat.json()["mode"] == "cached_demo"
    assert provider.calls == calls_after_first

    # Consume second IP quota slot with another uncached question
    r2 = client.post(
        "/api/chat",
        json={"question": "TUPRS temettü detayları nedir?", "stream": False},
    )
    assert r2.status_code == 200

    # Quota is now 0 -> uncached random question gets polite Demo Mode guidance (0 LLM calls)
    calls_before_limit = provider.calls
    r_limited = client.post(
        "/api/chat",
        json={"question": "SISE son yatırım açıklaması nedir?", "stream": False},
    )
    assert r_limited.status_code == 200
    body_limited = r_limited.json()
    assert body_limited["quota_exhausted"] is True
    assert body_limited["mode"] == "cached_demo"
    assert "Demo Modu" in body_limited["answer_text"]
    assert provider.calls == calls_before_limit

    # Preset DEMO_QUESTIONS still work with full SQL/citations in Demo Mode when quota=0
    r_demo = client.post(
        "/api/chat",
        json={"question": DEMO_QUESTIONS[0], "stream": False},
    )
    assert r_demo.status_code == 200
    body_demo = r_demo.json()
    assert body_demo["quota_exhausted"] is True
    assert body_demo["mode"] == "cached_demo"
    assert len(body_demo["citations"]) >= 1
    assert provider.calls == calls_before_limit


def test_mcp_server_initialize_tools_and_readonly_safety(tmp_path: Path) -> None:
    settings = get_settings()
    db_path = tmp_path / "mcp_test.db"
    load_dataset_into_db(
        raw_dir=settings.sample_raw_dir,
        extractions_dir=settings.sample_extractions_dir,
        db_path=db_path,
    )

    init_resp = handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, db_path=db_path)
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "kap-agent-mcp"

    # Notification returns None
    assert handle_mcp_request({"jsonrpc": "2.0", "method": "notifications/initialized"}, db_path=db_path) is None

    list_resp = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, db_path=db_path)
    assert list_resp is not None
    tool_names = [t["name"] for t in list_resp["result"]["tools"]]
    assert tool_names == ["search_disclosures", "get_disclosure", "run_sql"]

    # Valid run_sql tool call
    sql_ok = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "run_sql",
                "arguments": {"sql": "SELECT disclosure_id, ticker, net_cash_per_share_try FROM dividend_events ORDER BY net_cash_per_share_try DESC"},
            },
        },
        db_path=db_path,
    )
    assert sql_ok is not None
    assert sql_ok["result"]["isError"] is False
    assert "FROTO" in sql_ok["result"]["content"][0]["text"]

    # Forbidden SQL query returns MCP isError=True cleanly
    sql_blocked = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "run_sql",
                "arguments": {"sql": "SELECT * FROM review_flags"},
            },
        },
        db_path=db_path,
    )
    assert sql_blocked is not None
    assert sql_blocked["result"]["isError"] is True
    assert "SQLGuardError" in sql_blocked["result"]["content"][0]["text"]


def test_fallback_chain_fails_over_on_quota_error() -> None:
    class FailingPrimaryProvider(LLMProvider):
        model_name = "gemini-primary-exhausted"
        is_offline_fallback = False

        def generate_structured(self, *, system_prompt: str, user_prompt: str, schema_cls: type[TModel]) -> TModel:
            raise LLMProviderError("HTTP 429 quota exceeded")

    def groq_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-groq-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"decision_status": "approved", "fiscal_year": 2025, '
                                '"gross_cash_per_share_try": 5.0, "net_cash_per_share_try": 4.25, '
                                '"payment_type": "single", "evidence_quote": "Net 4.25 TL temettü."}'
                            )
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(groq_handler)
    with httpx.Client(transport=transport) as client:
        groq = OpenAICompatibleProvider(
            api_key="test-groq-key",
            model_name="openai/gpt-oss-120b",
            endpoint_url="https://api.groq.com/openai/v1/chat/completions",
            provider_label="groq",
            client=client,
        )
        chain = FallbackChainProvider([FailingPrimaryProvider(), groq])
        res = chain.generate_structured(
            system_prompt="Extract",
            user_prompt="Test",
            schema_cls=DividendExtraction,
        )
        assert isinstance(res, DividendExtraction)
        assert res.net_cash_per_share_try == 4.25
        assert chain.last_used_model == "groq:openai/gpt-oss-120b"


def test_global_api_rate_limit_middleware(tmp_path: Path) -> None:
    app = create_app(
        db_path=tmp_path / "ratelimit.db",
        admin_token="admin",
        global_rate_limit_per_min=3,
    )
    client = TestClient(app)

    for _ in range(3):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    limited = client.get("/api/health")
    assert limited.status_code == 429
    assert limited.headers.get("retry-after") == "60"
