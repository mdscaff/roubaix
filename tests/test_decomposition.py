"""Tests for schema-constrained sub-question decomposition (Phase D).

All fakes, no LM: the gating (escalation-only), the merge semantics (degraded
sub-results never blend into a live set), and the degradation path are the
contract; the DSPy program is one pluggable implementation of it.
"""

from __future__ import annotations

import pytest

from app.domain.models import (
    QueryRequest,
    RetrievalEvidence,
    RetrievalResult,
    SearchMode,
)
from app.integrations.cognee_client import CogneeClient
from app.services.cache import ContentAddressedCache
from app.services.decomposition import merge_results
from app.services.evidence import EvidencePacker
from app.services.normalizer import QueryNormalizer
from app.services.orchestrator import QueryOrchestrator
from app.services.router import QueryRouter
from app.services.runtime_controller import RuntimeController


class RecordingDecomposer:
    def __init__(self, subqueries: list[str]) -> None:
        self.subqueries = subqueries
        self.calls: list[tuple[str, list[str]]] = []

    def decompose(self, query: str, schema: list[str]) -> list[str]:
        self.calls.append((query, schema))
        return self.subqueries


class EmptyThenRichClient(CogneeClient):
    """First mode returns nothing (forcing escalation); GRAPH_COMPLETION is rich.

    Search calls are recorded so tests can assert which queries actually hit
    the substrate.
    """

    def __init__(self) -> None:
        super().__init__()
        self.searches: list[tuple[str, SearchMode]] = []

    async def search(
        self,
        query: str,
        mode: SearchMode,
        dataset: str,
        node_sets: list[str] | None = None,
        evidence_budget: int | None = None,
    ) -> RetrievalResult:
        self.searches.append((query, mode))
        if mode is not SearchMode.GRAPH_COMPLETION:
            return RetrievalResult(
                mode=mode, evidence=RetrievalEvidence(), retrieval_stats={}
            )
        return RetrievalResult(
            mode=mode,
            evidence=RetrievalEvidence(
                # Distinct per hop (dedup would collapse identical dicts), and
                # carrying the sub-query text — which is how decomposed
                # retrieval jointly covers the original question.
                graph_paths=[{"path": [query, f"hop {i}", "answer"]} for i in range(4)]
            ),
            retrieval_stats={},
        )


def _orchestrator(
    client: CogneeClient, decomposer: RecordingDecomposer | None
) -> QueryOrchestrator:
    normalizer = QueryNormalizer()
    return QueryOrchestrator(
        router=QueryRouter(normalizer=normalizer),
        cognee_client=client,
        evidence_packer=EvidencePacker(),
        runtime_controller=RuntimeController(),
        normalizer=normalizer,
        cache=ContentAddressedCache(),
        decomposer=decomposer,
    )


# --- gating ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decomposition_fires_only_on_escalation_into_graph_completion() -> None:
    client = EmptyThenRichClient()
    # Sub-queries that JOINTLY cover the original question's terms — the exact
    # property decomposition exists to provide, and what the set-level
    # sufficiency gate downstream checks for.
    decomposer = RecordingDecomposer(
        ["how does checkout record a purchase", "which system reaches the ledger"]
    )
    orch = _orchestrator(client, decomposer)
    # A factual-looking query (no multi-hop signal words, so it routes CHUNKS)
    # that finds nothing gets widened, then escalated into GRAPH_COMPLETION.
    result = await orch.answer(
        QueryRequest(query="how does a checkout purchase reach the ledger")
    )
    assert decomposer.calls, "escalation into GRAPH_COMPLETION should decompose"
    assert result.telemetry["decomposed"] is True
    assert result.telemetry["subquery_count"] == 2
    graph_queries = [q for q, m in client.searches if m is SearchMode.GRAPH_COMPLETION]
    assert "how does checkout record a purchase" in graph_queries
    assert "which system reaches the ledger" in graph_queries


@pytest.mark.asyncio
async def test_directly_routed_graph_completion_does_not_decompose() -> None:
    """A query the router sent to GRAPH_COMPLETION routed cleanly; decomposition
    is a recovery tool for queries that defeated a cheaper mode."""
    client = EmptyThenRichClient()
    decomposer = RecordingDecomposer(["a", "b"])
    orch = _orchestrator(client, decomposer)
    result = await orch.answer(
        QueryRequest(query="Which downstream systems are affected if ingest fails?")
    )
    assert decomposer.calls == []
    assert result.telemetry["decomposed"] is False


@pytest.mark.asyncio
async def test_fewer_than_two_subqueries_degrades_to_single_query() -> None:
    """One sub-query cannot pay for the decomposition call — single-query path."""
    client = EmptyThenRichClient()
    decomposer = RecordingDecomposer(["only one"])
    orch = _orchestrator(client, decomposer)
    result = await orch.answer(QueryRequest(query="how does a checkout purchase reach the ledger"))
    assert decomposer.calls, "the escalation should still consult the decomposer"
    assert result.telemetry["decomposed"] is False
    assert "only one" not in [q for q, _ in client.searches]


@pytest.mark.asyncio
async def test_no_decomposer_means_the_pipeline_is_unchanged() -> None:
    client = EmptyThenRichClient()
    orch = _orchestrator(client, None)
    result = await orch.answer(QueryRequest(query="how does a checkout purchase reach the ledger"))
    assert result.telemetry["decomposed"] is False
    assert result.accepted is True


# --- merge semantics ---------------------------------------------------------


def _live(paths: int) -> RetrievalResult:
    return RetrievalResult(
        mode=SearchMode.GRAPH_COMPLETION,
        evidence=RetrievalEvidence(graph_paths=[{"path": ["a", f"b{i}"]} for i in range(paths)]),
        retrieval_stats={},
    )


def _degraded() -> RetrievalResult:
    return RetrievalResult(
        mode=SearchMode.GRAPH_COMPLETION,
        evidence=RetrievalEvidence(graph_paths=[{"path": ["FAKE"]}]),
        retrieval_stats={},
        degraded=True,
        degraded_reason="retrieval_timeout_after_20.0s",
    )


def test_merge_concatenates_live_subresults() -> None:
    merged = merge_results("q", [_live(2), _live(3)])
    assert len(merged.evidence.graph_paths) == 5
    assert merged.degraded is False
    assert merged.retrieval_stats["subqueries_live"] == 2


def test_degraded_subresults_are_dropped_not_blended() -> None:
    """Fabricated evidence must never blend into a live set — after packing the
    two would be indistinguishable."""
    merged = merge_results("q", [_live(2), _degraded()])
    assert len(merged.evidence.graph_paths) == 2
    assert all("FAKE" not in str(p) for p in merged.evidence.graph_paths)
    assert merged.degraded is False
    assert merged.retrieval_stats["subqueries_dropped_degraded"] == 1


def test_all_degraded_subresults_yield_a_degraded_merge() -> None:
    """The controller's fail-closed path must see this exactly as it would a
    single degraded retrieval."""
    merged = merge_results("q", [_degraded(), _degraded()])
    assert merged.degraded is True
    assert merged.degraded_reason is not None
    assert merged.degraded_reason.startswith("all_subqueries_degraded:")
    assert merged.evidence.graph_paths == []
