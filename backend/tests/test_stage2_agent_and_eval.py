"""Stage 2 verification suite: SQL guard, self-healing loop, citation gate, advice guardrail, and agent eval."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from app.agent.citation_gate import (
    NO_EVIDENCE_TEXT,
    enforce_citation_gate,
    is_investment_advice_query,
)
from app.agent.orchestrator import run_kap_agent
from app.agent.sql_guard import SQLGuardError, validate_and_rewrite_sql
from app.config import REPO_ROOT, get_settings
from app.db.seed_loader import load_dataset_into_db
from app.llm.base import LLMProvider
from app.schemas.agent import AgentTurnDecision

TModel = TypeVar("TModel", bound=BaseModel)


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    settings = get_settings()
    db_path = tmp_path / "agent_test.db"
    load_dataset_into_db(
        raw_dir=settings.sample_raw_dir,
        extractions_dir=settings.sample_extractions_dir,
        db_path=db_path,
    )
    return db_path


def test_sql_guard_policies() -> None:
    safe_default = validate_and_rewrite_sql("SELECT disclosure_id, ticker FROM disclosures")
    assert safe_default.endswith("LIMIT 50")

    clamped = validate_and_rewrite_sql(
        "SELECT ticker, net_cash_per_share_try FROM dividend_events ORDER BY net_cash_per_share_try DESC LIMIT 500;"
    )
    assert clamped.endswith("LIMIT 50")
    assert not clamped.endswith(";")

    clamped_offset = validate_and_rewrite_sql(
        "SELECT ticker FROM dividend_events LIMIT 200 OFFSET 2"
    )
    assert clamped_offset.endswith("LIMIT 50 OFFSET 2")

    # Quoted literal containing 'update' should NOT trigger keyword rejection
    literal_ok = validate_and_rewrite_sql(
        "SELECT disclosure_id FROM disclosures WHERE summary_tr LIKE '%update%'"
    )
    assert literal_ok.endswith("LIMIT 50")

    forbidden_queries = [
        "SELECT * FROM disclosures; DROP TABLE disclosures;",
        "SELECT * FROM disclosures -- inline comment",
        "SELECT /* block */ * FROM disclosures",
        "WITH cte AS (SELECT * FROM disclosures) SELECT * FROM cte",
        "INSERT INTO disclosures (disclosure_id) VALUES ('1')",
        "UPDATE disclosures SET ticker='X' WHERE disclosure_id='1401001'",
        "DELETE FROM dividend_events",
        "DROP TABLE dividend_events",
        "PRAGMA table_info(disclosures)",
        "ATTACH DATABASE 'other.db' AS other",
        "SELECT * FROM review_flags",
        "SELECT name FROM sqlite_master WHERE type='table'",
        "SELECT * FROM dividend_events LIMIT 0",
    ]
    for bad_sql in forbidden_queries:
        with pytest.raises(SQLGuardError):
            validate_and_rewrite_sql(bad_sql)


def test_sql_self_healing_loop(seeded_db: Path) -> None:
    class SelfHealingMockProvider(LLMProvider):
        model_name = "mock-self-healing-gemini"
        is_offline_fallback = False

        def __init__(self) -> None:
            self.step = 0

        def generate_structured(self, *, system_prompt: str, user_prompt: str, schema_cls: type[TModel]) -> TModel:
            raise NotImplementedError

        def plan_agent_step(self, *, system_prompt: str, transcript: str) -> AgentTurnDecision:
            self.step += 1
            if self.step == 1:
                return AgentTurnDecision(
                    action="call_tool",
                    thought="İlk denemede hatalı sütun adı (net_dividend) ile sorgu üretiyorum.",
                    tool_name="run_sql",
                    tool_args={
                        "sql": "SELECT disclosure_id, ticker, net_dividend FROM dividend_events ORDER BY net_dividend DESC",
                        "purpose": "En yüksek net temettü sıralaması",
                    },
                )
            if self.step == 2:
                assert "no such column: net_dividend" in transcript
                return AgentTurnDecision(
                    action="call_tool",
                    thought="SQL hatasını okudum, sütun adını net_cash_per_share_try olarak düzeltiyorum.",
                    tool_name="run_sql",
                    tool_args={
                        "sql": "SELECT disclosure_id, ticker, net_cash_per_share_try FROM dividend_events ORDER BY net_cash_per_share_try DESC LIMIT 3",
                        "purpose": "Düzeltilmiş net temettü sıralaması",
                    },
                )
            return AgentTurnDecision(
                action="final_answer",
                thought="Doğru sonuçları kullanıcıya kaynak göstererek sunuyorum.",
                answer_text="2025 yılında hisse başına en yüksek net nakit temettü FROTO (39.15 TL) tarafından açıklanmıştır [kap:1401002].",
            )

    res = run_kap_agent(
        "En yüksek net nakit temettü hangi şirkette?",
        db_path=seeded_db,
        provider=SelfHealingMockProvider(),
    )
    assert res.sql_attempts == 2
    assert res.sql_retries == 1
    assert res.first_try_sql_success is False
    assert any(step.step_type == "sql_retry" and step.is_error for step in res.trace)
    assert res.no_evidence_found is False
    assert [c.disclosure_id for c in res.citations] == ["1401002"]
    assert res.artifact is not None
    assert res.artifact.rows[0]["ticker"] == "FROTO"


def test_citation_gate_blocks_hallucinated_and_uncited_ids(seeded_db: Path) -> None:
    # Uncited answer rejected
    ans1, cites1, no_ev1, stripped1 = enforce_citation_gate(
        answer_text="FROTO 39.15 TL temettü dağıtacak.",
        observed_disclosure_ids={"1401002"},
        db_path=seeded_db,
    )
    assert ans1 == NO_EVIDENCE_TEXT
    assert cites1 == []
    assert no_ev1 is True
    assert stripped1 == 0

    # Hallucinated / unobserved ID rejected cleanly
    ans2, cites2, no_ev2, stripped2 = enforce_citation_gate(
        answer_text="FROTO 39.15 TL temettü dağıtacak [kap:9999999].",
        observed_disclosure_ids={"1401002"},
        db_path=seeded_db,
    )
    assert ans2 == NO_EVIDENCE_TEXT
    assert cites2 == []
    assert no_ev2 is True
    assert stripped2 == 1

    # Valid observed + one fake mixed -> keeps valid citation, strips fake
    ans3, cites3, no_ev3, stripped3 = enforce_citation_gate(
        answer_text="FROTO 39.15 TL [kap:1401002] ve ek bilgi [kap:9999999].",
        observed_disclosure_ids={"1401002"},
        db_path=seeded_db,
    )
    assert no_ev3 is False
    assert stripped3 == 1
    assert "[kap:1401002]" in ans3
    assert "9999999" not in ans3
    assert [c.disclosure_id for c in cites3] == ["1401002"]


def test_advice_refusal_guardrail_zero_llm_calls(seeded_db: Path) -> None:
    class SpyProvider(LLMProvider):
        model_name = "spy-gemini"
        is_offline_fallback = False

        def __init__(self) -> None:
            self.calls = 0

        def generate_structured(self, *, system_prompt: str, user_prompt: str, schema_cls: type[TModel]) -> TModel:
            raise NotImplementedError

        def plan_agent_step(self, *, system_prompt: str, transcript: str) -> AgentTurnDecision:
            self.calls += 1
            return AgentTurnDecision(action="final_answer", thought="x", answer_text="y")

    spy = SpyProvider()
    res = run_kap_agent("THYAO hissesi bu fiyattan alınır mı?", db_path=seeded_db, provider=spy)
    assert res.refused_as_advice is True
    assert res.mode == "guardrail_refusal"
    assert spy.calls == 0

    # Factual dividend approval question must NOT trigger advice refusal
    assert is_investment_advice_query("TUPRS temettü kararı onaylandı mı?") is False
    assert is_investment_advice_query("FROTO için hedef fiyat nedir?") is True


def test_agent_eval_runner_unverified_and_verified_modes(seeded_db: Path, tmp_path: Path) -> None:
    evals_dir = REPO_ROOT / "evals"
    if str(evals_dir) not in sys.path:
        sys.path.insert(0, str(evals_dir))
    import run_agent_eval  # type: ignore

    # Full 30-case unverified draft report
    draft_report = run_agent_eval.run_eval(use_live_llm=False, db_path=seeded_db)
    assert draft_report["total_cases"] == 30
    assert draft_report["verified_cases"] == 0
    assert draft_report["unverified_draft_cases"] == 30
    assert draft_report["first_try_sql_success_pct"] == "ölçülmedi"
    assert draft_report["citation_precision_pct"] == "ölçülmedi"
    assert draft_report["advice_refusal_pct"] == "ölçülmedi"
    assert "OFFLINE/SAHTE" in draft_report["execution_mode"]
    assert draft_report["unverified_draft_report"]["ran_without_crash"] == 30

    # Mixed verified (1 live case) + unverified (1 broken case) -> computes ONLY from verified
    class VerifiedMockProvider(LLMProvider):
        model_name = "mock-live-gemini"
        is_offline_fallback = False

        def generate_structured(self, *, system_prompt: str, user_prompt: str, schema_cls: type[TModel]) -> TModel:
            raise NotImplementedError

        def plan_agent_step(self, *, system_prompt: str, transcript: str) -> AgentTurnDecision:
            if "[TOOL_RESULT" not in transcript:
                return AgentTurnDecision(
                    action="call_tool",
                    thought="Querying dividend_events",
                    tool_name="run_sql",
                    tool_args={
                        "sql": "SELECT disclosure_id, ticker, net_cash_per_share_try FROM dividend_events WHERE ticker='FROTO'",
                        "purpose": "FROTO dividend lookup",
                    },
                )
            return AgentTurnDecision(
                action="final_answer",
                thought="Answering with citation",
                answer_text="FROTO net nakit temettüsü 39.15 TL'dir [kap:1401002].",
            )

    subset_gold = tmp_path / "agent_subset.jsonl"
    subset_gold.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "eval_id": "v1",
                        "question": "FROTO net temettü nedir?",
                        "category": "dividend_lookup",
                        "verified": True,
                        "expected_sql_tables": ["dividend_events"],
                        "expected_answer_contains": ["FROTO", "39.15"],
                        "expected_disclosure_ids": ["1401002"],
                        "should_refuse_as_advice": False,
                    }
                ),
                json.dumps(
                    {
                        "eval_id": "u1",
                        "question": "ASELS net kârı nedir?",
                        "category": "financial_lookup",
                        "verified": False,
                        "expected_sql_tables": ["financial_reports"],
                        "expected_answer_contains": ["NONEXISTENT_TOKEN_999"],
                        "expected_disclosure_ids": ["9999999"],
                        "should_refuse_as_advice": False,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    verified_report = run_agent_eval.run_eval(
        use_live_llm=True,
        gold_path=subset_gold,
        db_path=seeded_db,
        provider=VerifiedMockProvider(),
    )
    assert verified_report["execution_mode"] == "LIVE_LLM (mock-live-gemini)"
    assert verified_report["verified_cases"] == 1
    assert verified_report["unverified_draft_cases"] == 1
    assert verified_report["first_try_sql_success_pct"] == 100.0
    assert verified_report["citation_precision_pct"] == 100.0
    assert verified_report["citation_recall_pct"] == 100.0
    assert verified_report["answer_contains_match_pct"] == 100.0
