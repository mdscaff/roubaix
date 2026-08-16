"""API contract tests for POST /answer.

The endpoint previously had no response model, no error handling, and no test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app, orchestrator


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_healthz_reports_cognee_status(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert "cognee" in body


def test_healthz_reports_memgraph_stats(client: TestClient) -> None:
    """Tier 0's residency is operational state; it must be observable."""
    memgraph = client.get("/healthz").json()["memgraph"]
    for field in ("enabled", "nodes", "edges"):
        assert field in memgraph
    assert isinstance(memgraph["nodes"], int)


def test_answer_returns_the_documented_contract(client: TestClient) -> None:
    response = client.post("/answer", json={"query": "What port does billing expose?"})
    assert response.status_code == 200
    body = response.json()
    for field in ("answer", "accepted", "route", "retrieval_mode", "telemetry"):
        assert field in body
    assert "mode" in body["route"]
    assert "signals" in body["route"]


def test_answer_rejects_a_malformed_body(client: TestClient) -> None:
    assert client.post("/answer", json={"not_a_query": 1}).status_code == 422


def test_answer_accepts_the_full_request_contract(client: TestClient) -> None:
    response = client.post(
        "/answer",
        json={
            "query": "How is billing connected to the warehouse?",
            "dataset": "default",
            "node_sets": ["billing"],
            "freshness_required": False,
            "user_id": "u-1",
            "max_cost_cents": 5.0,
        },
    )
    assert response.status_code == 200
    assert response.json()["route"]["node_sets"] == ["billing"]


def test_telemetry_exposes_the_decision_trail(client: TestClient) -> None:
    """A route must be explainable from the response, not just from logs."""
    telemetry = client.post(
        "/answer", json={"query": "Which downstream systems are affected if ingest fails?"}
    ).json()["telemetry"]
    for field in ("route_signals", "attempted_modes", "escalation_chain", "degraded", "total_ms"):
        assert field in telemetry


def test_pipeline_failure_returns_502_not_a_traceback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(request: object) -> None:
        raise RuntimeError("internal detail that must not leak")

    monkeypatch.setattr(orchestrator, "answer", boom)
    response = client.post("/answer", json={"query": "anything"})
    assert response.status_code == 502
    assert "internal detail" not in response.text
    assert "RuntimeError" in response.json()["detail"]
