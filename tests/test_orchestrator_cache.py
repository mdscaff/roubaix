"""Tests for the tiered resolution pipeline in QueryOrchestrator."""

from __future__ import annotations

import pytest

from app.domain.models import QueryRequest, RetrievalEvidence, RetrievalResult, SearchMode
from app.integrations.cognee_client import CogneeClient
from app.services.cache import ContentAddressedCache
from app.services.evidence import EvidencePacker
from app.services.normalizer import QueryNormalizer
from app.services.orchestrator import QueryOrchestrator
from app.services.router import QueryRouter
from app.services.runtime_controller import RuntimeController


class FakeCogneeClient(CogneeClient):
    """Returns non-degraded evidence so cache behaviour can be tested.

    The real client's stub path is flagged ``degraded``, and degraded answers
    are deliberately never cached — so cache tests must not run against it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def search(
        self,
        query: str,
        mode: SearchMode,
        dataset: str,
        node_sets: list[str] | None = None,
        evidence_budget: int | None = None,
    ) -> RetrievalResult:
        self.calls += 1
        return RetrievalResult(
            mode=mode,
            evidence=RetrievalEvidence(
                # Chunks echo the query so the set-level sufficiency gate sees
                # full coverage — these tests are about the cache, not the gate.
                chunks=[f"{query} — answer detail {i} for {dataset}" for i in range(4)],
                triplets=[
                    {"subject": "A", "predicate": "rel", "object": f"B{i}"} for i in range(4)
                ],
                graph_paths=[{"path": ["A", "B", f"C{i}"]} for i in range(4)],
                rows=[{"k": f"v{i}"} for i in range(4)],
                timestamps=[f"2026-0{i + 1}-01" for i in range(4)],
            ),
            retrieval_stats={"dataset": dataset},
        )


def _make_orchestrator(
    cache: ContentAddressedCache | None = None,
    client: CogneeClient | None = None,
) -> QueryOrchestrator:
    normalizer = QueryNormalizer()
    return QueryOrchestrator(
        router=QueryRouter(normalizer=normalizer),
        cognee_client=client or FakeCogneeClient(),
        evidence_packer=EvidencePacker(),
        runtime_controller=RuntimeController(),
        normalizer=normalizer,
        cache=cache or ContentAddressedCache(),
    )


@pytest.mark.asyncio
async def test_first_call_is_cache_miss() -> None:
    orch = _make_orchestrator()
    result = await orch.answer(QueryRequest(query="What is the latest status?"))
    assert result.cache_hit is False
    assert result.accepted is True


@pytest.mark.asyncio
async def test_second_identical_call_is_cache_hit() -> None:
    orch = _make_orchestrator()
    r1 = await orch.answer(QueryRequest(query="What is the latest status?"))
    r2 = await orch.answer(QueryRequest(query="What is the latest status?"))
    assert r1.cache_hit is False
    assert r2.cache_hit is True


@pytest.mark.asyncio
async def test_casing_and_whitespace_variants_hit_same_cache() -> None:
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="What is the latest status?"))
    r2 = await orch.answer(QueryRequest(query="  what IS THE latest status  "))
    assert r2.cache_hit is True


@pytest.mark.asyncio
async def test_different_datasets_are_separate_cache_entries() -> None:
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="test query", dataset="ds_a"))
    r2 = await orch.answer(QueryRequest(query="test query", dataset="ds_b"))
    assert r2.cache_hit is False


@pytest.mark.asyncio
async def test_inverted_relationship_queries_do_not_share_a_cache_entry() -> None:
    """Regression: token-sorting normalization served one answer for both."""
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="Does billing depend on the warehouse?"))
    r2 = await orch.answer(QueryRequest(query="Does the warehouse depend on billing?"))
    assert r2.cache_hit is False


@pytest.mark.asyncio
async def test_freshness_request_does_not_reuse_a_non_fresh_cache_entry() -> None:
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="rollout state"))
    r2 = await orch.answer(QueryRequest(query="rollout state", freshness_required=True))
    assert r2.cache_hit is False


@pytest.mark.asyncio
async def test_node_set_scope_is_part_of_the_cache_key() -> None:
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="scoped query", node_sets=["billing"]))
    r2 = await orch.answer(QueryRequest(query="scoped query", node_sets=["auth"]))
    assert r2.cache_hit is False


@pytest.mark.asyncio
async def test_degraded_answers_are_never_cached() -> None:
    """A transient retrieval outage must not poison the cache."""
    orch = _make_orchestrator(client=CogneeClient())  # stub path → degraded
    r1 = await orch.answer(QueryRequest(query="What port does billing expose?"))
    r2 = await orch.answer(QueryRequest(query="What port does billing expose?"))
    assert r1.telemetry["degraded"] is True
    assert r2.cache_hit is False


@pytest.mark.asyncio
async def test_cache_hit_reports_zero_incurred_cost_and_keeps_origin_cost() -> None:
    orch = _make_orchestrator()
    r1 = await orch.answer(QueryRequest(query="What is the latest status?"))
    r2 = await orch.answer(QueryRequest(query="What is the latest status?"))
    assert r2.telemetry["estimated_cost_usd"] == 0.0
    assert r2.telemetry["origin_estimated_cost_usd"] == r1.telemetry["estimated_cost_usd"]


# --- the paraphrase second-chance lookup --------------------------------------


@pytest.mark.asyncio
async def test_paraphrase_variant_serves_from_cache_without_retrieval() -> None:
    """ "…depend on the warehouse" and "…depend on warehouse" are one question;
    exact keying made them two retrievals. The second-chance key forgives
    function words and morphology — and nothing else."""
    client = FakeCogneeClient()
    orch = _make_orchestrator(client=client)
    r1 = await orch.answer(QueryRequest(query="Does billing depend on the warehouse?"))
    assert r1.accepted is True
    calls_paid = client.calls
    r2 = await orch.answer(QueryRequest(query="does billing depend on warehouse"))
    assert r2.cache_hit is True
    assert r2.telemetry["cache_hit_kind"] == "paraphrase"
    assert client.calls == calls_paid  # not one more retrieval was paid for
    assert r2.telemetry["input_tokens"] == 0


@pytest.mark.asyncio
async def test_exact_hits_still_report_exact() -> None:
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="Does billing depend on the warehouse?"))
    r2 = await orch.answer(QueryRequest(query="Does billing depend on the warehouse?"))
    assert r2.cache_hit is True
    assert r2.telemetry["cache_hit_kind"] == "exact"


@pytest.mark.asyncio
async def test_negated_variant_never_hits_the_paraphrase_entry() -> None:
    """A collision here would serve a wrong answer at cache speed."""
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="Is billing connected to the warehouse?"))
    r2 = await orch.answer(QueryRequest(query="Is billing not connected to the warehouse?"))
    assert r2.cache_hit is False


@pytest.mark.asyncio
async def test_freshness_required_requests_never_consult_the_paraphrase_key() -> None:
    """The interlock's read side: a freshness contract must reach retrieval
    even when a stale-but-valid paraphrase entry exists."""
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="the rollout state of billing"))
    r2 = await orch.answer(QueryRequest(query="rollout state of billing", freshness_required=True))
    assert r2.cache_hit is False


@pytest.mark.asyncio
async def test_temporal_routes_never_write_a_paraphrase_entry() -> None:
    """The interlock's write side: the paraphrase form forgives auxiliary
    words, and with them tense — so a temporally-validated answer must not be
    reachable through it, even for callers who did not declare freshness."""
    orch = _make_orchestrator()
    r1 = await orch.answer(QueryRequest(query="What deployments happened in the past week?"))
    assert r1.route.requires_freshness_validation is True
    r2 = await orch.answer(QueryRequest(query="what deployments happened in past week"))
    assert r2.cache_hit is False


@pytest.mark.asyncio
async def test_paraphrase_entries_do_not_cross_callers() -> None:
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="Does billing depend on the warehouse?", user_id="u1"))
    r2 = await orch.answer(QueryRequest(query="does billing depend on warehouse", user_id="u2"))
    assert r2.cache_hit is False
