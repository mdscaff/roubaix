from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.domain.models import RetrievalEvidence, RetrievalResult, SearchMode


class CogneeClient:
    """Thin wrapper around Cognee HTTP APIs or SDK calls.

    Replace the internals with the official SDK or API integration after validating
    the exact version-specific endpoints in use.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = base_url or settings.cognee_base_url
        self.api_key = api_key or settings.cognee_api_key

    async def search(
        self,
        query: str,
        mode: SearchMode,
        dataset: str,
        node_sets: list[str] | None = None,
    ) -> RetrievalResult:
        # TODO: wire to official Cognee SDK/API.
        # This placeholder returns predictable structure for local development.
        evidence = RetrievalEvidence(
            triplets=[{"subject": "A", "predicate": "related_to", "object": "B"}]
            if mode == SearchMode.TRIPLET_COMPLETION
            else [],
            chunks=[f"Placeholder chunk for query: {query}"]
            if mode in {SearchMode.CHUNKS, SearchMode.RAG_COMPLETION}
            else [],
            graph_paths=[{"path": ["A", "B", "C"]}]
            if mode in {SearchMode.GRAPH_COMPLETION, SearchMode.GRAPH_SUMMARY_COMPLETION}
            else [],
            rows=[{"key": "value"}] if mode in {SearchMode.CYPHER, SearchMode.NATURAL_LANGUAGE} else [],
            timestamps=["2026-04-12"] if mode == SearchMode.TEMPORAL else [],
            provenance=[{"dataset": dataset, "node_sets": node_sets or []}],
        )
        return RetrievalResult(mode=mode, evidence=evidence, retrieval_stats={"dataset": dataset})

    async def ingest(self, content: str, dataset: str, node_sets: list[str] | None = None) -> dict[str, Any]:
        # TODO: connect to `add` and `cognify` workflow.
        return {"status": "accepted", "dataset": dataset, "node_sets": node_sets or []}
