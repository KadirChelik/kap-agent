"""Stage 3 backend API tests: health, disclosures, SSE/JSON chat, sanitized reviews, rate limits, admin approval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes_reviews import reset_review_rate_limits
from app.main import create_app


@pytest.fixture()
def api_client(tmp_path: Path) -> TestClient:
    reset_review_rate_limits()
    db_path = tmp_path / "api_test.db"
    app = create_app(db_path=db_path, admin_token="secret-admin-key")
    return TestClient(app)


def test_health_and_low_confidence_disclosures(api_client: TestClient) -> None:
    health_resp = api_client.get("/api/health")
    assert health_resp.status_code == 200
    health = health_resp.json()
    assert health["status"] == "ok"
    assert health["disclosures_count"] == 24
    assert health["low_confidence_count"] == 3

    low_resp = api_client.get("/api/disclosures?low_confidence_only=true")
    assert low_resp.status_code == 200
    low_data = low_resp.json()
    assert low_data["count"] == 3
    ids = {item["disclosure_id"] for item in low_data["items"]}
    assert ids == {"1401007", "1402007", "1403006"}

    detail_resp = api_client.get("/api/disclosures/1401007")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["ticker"] == "EREGL"


def test_chat_json_and_sse_streaming(api_client: TestClient) -> None:
    # Non-streaming JSON mode
    json_resp = api_client.post(
        "/api/chat",
        json={"question": "2025 yılı hisse başına en yüksek net nakit temettü kimde?", "stream": False},
    )
    assert json_resp.status_code == 200
    body = json_resp.json()
    assert "FROTO" in body["answer_text"]
    assert any(c["disclosure_id"] == "1401002" for c in body["citations"])
    assert body["artifact"] is not None
    assert body["artifact"]["rows"][0]["ticker"] == "FROTO"
    assert len(body["trace"]) >= 2

    # Streaming SSE mode
    sse_resp = api_client.post(
        "/api/chat",
        json={"question": "THYAO 2025/12 finansal sonuçları nedir?", "stream": True},
    )
    assert sse_resp.status_code == 200
    assert sse_resp.headers["content-type"].startswith("text/event-stream")
    raw_sse = sse_resp.text
    for expected_evt in ("event: trace_step", "event: artifact", "event: delta", "event: citations", "event: done"):
        assert expected_evt in raw_sse
    assert "1402001" in raw_sse


def test_review_flag_sanitization_isolation_and_rate_limit(api_client: TestClient) -> None:
    # Submit sanitized flag
    resp = api_client.post(
        "/api/reviews",
        json={
            "disclosure_id": "1401007",
            "field_name": "ex_dividend_date",
            "suggested_value": "<script>alert(1)</script><b>2026-06-01</b>",
            "reason_note": "Genel kurul <img src=x onerror=alert(1)> tutanağında tarih kesinleşti.",
        },
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["status"] == "pending"
    assert created["suggested_value"] == "2026-06-01"

    # Main disclosure table MUST remain untouched while status='pending'
    detail_before = api_client.get("/api/disclosures/1401007").json()
    assert detail_before["extracted_data"]["ex_dividend_date"] is None
    assert detail_before["human_verified"] is False
    assert "ex_dividend_date" in detail_before["low_confidence_fields"]

    # Exceed per-minute rate limit (limit=5 -> 6th fails with 429)
    for idx in range(4):
        r = api_client.post(
            "/api/reviews",
            json={
                "disclosure_id": "1401007",
                "field_name": "payment_date",
                "suggested_value": f"2026-06-0{idx + 2}",
            },
        )
        assert r.status_code == 201

    limited = api_client.post(
        "/api/reviews",
        json={
            "disclosure_id": "1401007",
            "field_name": "payment_date",
            "suggested_value": "2026-06-09",
        },
    )
    assert limited.status_code == 429


def test_admin_auth_and_approval_mutates_only_on_decision(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/reviews",
        json={
            "disclosure_id": "1401007",
            "field_name": "ex_dividend_date",
            "suggested_value": "2026-06-01",
            "reason_note": "Genel kurul kesin tarihi.",
        },
    ).json()
    flag_id = created["flag_id"]

    # Missing or wrong admin token rejected cleanly
    assert api_client.get("/api/admin/reviews").status_code == 401
    assert api_client.get("/api/admin/reviews", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert (
        api_client.post(
            f"/api/admin/reviews/{flag_id}/decide",
            headers={"X-Admin-Token": "wrong"},
            json={"action": "approve"},
        ).status_code
        == 401
    )

    # Valid admin lists pending items
    admin_list = api_client.get(
        "/api/admin/reviews?status=pending",
        headers={"X-Admin-Token": "secret-admin-key"},
    )
    assert admin_list.status_code == 200
    assert any(item["flag_id"] == flag_id for item in admin_list.json()["items"])

    # Admin approves -> mutates main tables + marks human_verified = True
    approve_resp = api_client.post(
        f"/api/admin/reviews/{flag_id}/decide",
        headers={"X-Admin-Token": "secret-admin-key"},
        json={"action": "approve", "reviewer_note": "KAP metni ile doğrulandı."},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"

    detail_after = api_client.get("/api/disclosures/1401007").json()
    assert detail_after["extracted_data"]["ex_dividend_date"] == "2026-06-01"
    assert detail_after["human_verified"] is True
    assert "ex_dividend_date" not in detail_after["low_confidence_fields"]
