from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import settings
from app.domain.models import RetrievalEvidence, RetrievalResult, SearchMode
from app.integrations.cognee_mapping import to_cognee_search_type
from app.integrations.cognee_results import evidence_from_search_results
from app.integrations.cognee_setup import get_cognee_status

logger = logging.getLogger(__name__)


async def embed_triplets(dataset: str) -> dict[str, Any]:
    """Embed *dataset*'s triples so TRIPLET_COMPLETION can retrieve them.

    ``cognify`` does not do this: measured on cognee 1.4.2, the triplet_text
    collection stays empty and TRIPLET_COMPLETION raises ``NoDataError`` until
    this memify pipeline runs. Every ingestion path needs it, so it lives here
    rather than in one script — a corpus ingested without it has a retrieval
    mode that is permanently dead, and nothing else reports that.

    Never raises: a failure costs one retrieval mode, not the ingestion.
    """
    try:
        from cognee.memify_pipelines.create_triplet_embeddings import (  # type: ignore[import-not-found]
            create_triplet_embeddings,
        )
        from cognee.modules.users.methods import (  # type: ignore[import-not-found]
            get_default_user,
        )

        await create_triplet_embeddings(user=await get_default_user(), dataset=dataset)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - one mode degrades; ingestion stands
        logger.warning(
            "triplet_embeddings_failed",
            extra={"dataset": dataset, "error": str(exc), "error_type": type(exc).__name__},
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


class CogneeClient:
    """Thin wrapper around Cognee SDK search and ingest.

    Falls back to deterministic placeholder evidence when the SDK is unavailable
    (for example in CI without the ``opt`` extra).
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
        evidence_budget: int | None = None,
    ) -> RetrievalResult:
        top_k = evidence_budget or settings.max_evidence_items
        if self._use_live_search():
            try:
                return await asyncio.wait_for(
                    self._live_search(
                        query=query,
                        mode=mode,
                        dataset=dataset,
                        node_sets=node_sets,
                        top_k=top_k,
                    ),
                    timeout=settings.retrieval_timeout_s,
                )
            except TimeoutError:
                logger.warning(
                    "cognee_search_timeout",
                    extra={
                        "mode": mode.value,
                        "dataset": dataset,
                        "timeout_s": settings.retrieval_timeout_s,
                    },
                )
                return self._placeholder_search(
                    query,
                    mode,
                    dataset,
                    node_sets,
                    reason=f"retrieval_timeout_after_{settings.retrieval_timeout_s}s",
                )
            except Exception as exc:  # noqa: BLE001 - substrate boundary; failure is flagged degraded
                logger.warning(
                    "cognee_search_failed",
                    extra={"mode": mode.value, "dataset": dataset, "error": str(exc)},
                )
                return self._placeholder_search(
                    query,
                    mode,
                    dataset,
                    node_sets,
                    reason=f"live_search_failed: {type(exc).__name__}",
                )
        return self._placeholder_search(
            query, mode, dataset, node_sets, reason="cognee_not_configured"
        )

    async def ingest(
        self, content: str, dataset: str, node_sets: list[str] | None = None
    ) -> dict[str, Any]:
        if self._use_live_search():
            try:
                return await self._live_ingest(content, dataset, node_sets)
            except Exception as exc:  # noqa: BLE001 - substrate boundary; failure is flagged degraded
                logger.warning(
                    "cognee_ingest_failed",
                    extra={"dataset": dataset, "error": str(exc)},
                )
        return {
            "status": "accepted",
            "dataset": dataset,
            "node_sets": node_sets or [],
            "stub": True,
        }

    def _use_live_search(self) -> bool:
        status = get_cognee_status()
        return bool(status.get("configured"))

    async def _live_search(
        self,
        *,
        query: str,
        mode: SearchMode,
        dataset: str,
        node_sets: list[str] | None,
        top_k: int,
    ) -> RetrievalResult:
        import cognee  # type: ignore[import-not-found]

        query_type = to_cognee_search_type(mode)
        kwargs: dict[str, Any] = {
            "query_type": query_type,
            "datasets": [dataset],
            "top_k": top_k,
            "only_context": True,
        }
        if node_sets:
            kwargs["node_name"] = node_sets
            # Explicit, not defaulted: OR means "any of these NodeSets", which
            # is the correct semantic for entity-derived scope (a query naming
            # two entities wants evidence about either, not the intersection).
            kwargs["node_name_filter_operator"] = "OR"

        raw_results = await cognee.search(query, **kwargs)
        evidence = evidence_from_search_results(
            mode,
            raw_results,
            dataset=dataset,
            node_sets=node_sets,
        )
        return RetrievalResult(
            mode=mode,
            evidence=evidence,
            retrieval_stats={
                "dataset": dataset,
                "top_k": top_k,
                "node_sets": node_sets or [],
                "live": True,
            },
        )

    async def _live_ingest(
        self, content: str, dataset: str, node_sets: list[str] | None
    ) -> dict[str, Any]:
        import cognee  # type: ignore[import-not-found]

        add_kwargs: dict[str, Any] = {"dataset_name": dataset}
        if node_sets:
            add_kwargs["node_set"] = node_sets
        await cognee.add(content, **add_kwargs)
        await cognee.cognify(datasets=[dataset])
        return {
            "status": "accepted",
            "dataset": dataset,
            "node_sets": node_sets or [],
            "live": True,
            "triplet_embeddings": await embed_triplets(dataset),
        }

    @staticmethod
    def _placeholder_search(
        query: str,
        mode: SearchMode,
        dataset: str,
        node_sets: list[str] | None,
        *,
        reason: str = "cognee_not_configured",
    ) -> RetrievalResult:
        """Deterministic stub evidence for CI and unconfigured environments.

        This content is fabricated. It is flagged ``degraded`` all the way to
        the runtime controller, which refuses to synthesize a confident answer
        from it unless ``ROUBAIX_ALLOW_STUB_EVIDENCE`` is set. Silently
        answering from stub data was the single worst failure mode here: a
        transient Cognee outage produced fluent, cached, entirely invented
        answers that were indistinguishable from grounded ones.
        """
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
            rows=[{"key": "value"}]
            if mode in {SearchMode.CYPHER, SearchMode.NATURAL_LANGUAGE}
            else [],
            timestamps=["2026-04-12"] if mode == SearchMode.TEMPORAL else [],
            provenance=[{"dataset": dataset, "node_sets": node_sets or [], "stub": True}],
        )
        return RetrievalResult(
            mode=mode,
            evidence=evidence,
            retrieval_stats={"dataset": dataset, "stub": True, "reason": reason},
            degraded=True,
            degraded_reason=reason,
        )
