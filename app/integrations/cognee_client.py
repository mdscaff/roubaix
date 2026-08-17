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
        self.base_url = base_url or settings.cognee_remote_url
        self.api_key = api_key or settings.cognee_api_key
        self._remote_client: Any | None = None

    def _use_remote(self) -> bool:
        """Remote mode wins over the embedded SDK when a service URL is set.

        Deliberately ordered that way: someone who configured a service URL
        meant to use that service, and silently running an in-process SDK
        against a different store instead is the kind of divergence that only
        shows up as "why is my data missing".
        """
        return bool(self.base_url)

    async def _remote(self) -> Any:
        """Connect (once) to the configured Cognee service.

        ``cognee.serve`` returns a CloudClient speaking the same HTTP API for a
        self-hosted instance and a Cognee Cloud tenant, so the sidecar and the
        hosted deployments differ only in URL and key.
        """
        if self._remote_client is None:
            import cognee  # type: ignore[import-not-found]

            self._remote_client = await cognee.serve(url=self.base_url, api_key=self.api_key)
        return self._remote_client

    async def aclose(self) -> None:
        """Release the remote session. No-op in embedded mode."""
        if self._remote_client is not None:
            await self._remote_client.close()
            self._remote_client = None

    async def search(
        self,
        query: str,
        mode: SearchMode,
        dataset: str,
        node_sets: list[str] | None = None,
        evidence_budget: int | None = None,
    ) -> RetrievalResult:
        top_k = evidence_budget or settings.max_evidence_items
        remote = self._use_remote()
        if remote or self._use_live_search():
            search = self._remote_search if remote else self._live_search
            try:
                return await asyncio.wait_for(
                    search(
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
                    reason=(
                        f"{'remote' if remote else 'live'}_search_failed: {type(exc).__name__}"
                    ),
                )
        return self._placeholder_search(
            query, mode, dataset, node_sets, reason="cognee_not_configured"
        )

    async def ingest(
        self, content: str, dataset: str, node_sets: list[str] | None = None
    ) -> dict[str, Any]:
        remote = self._use_remote()
        if remote or self._use_live_search():
            try:
                if remote:
                    return await self._remote_ingest(content, dataset, node_sets)
                return await self._live_ingest(content, dataset, node_sets)
            except Exception as exc:  # noqa: BLE001 - substrate boundary; failure is flagged degraded
                logger.warning(
                    "cognee_ingest_failed",
                    extra={"dataset": dataset, "remote": remote, "error": str(exc)},
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

    async def _remote_search(
        self,
        *,
        query: str,
        mode: SearchMode,
        dataset: str,
        node_sets: list[str] | None,
        top_k: int,
    ) -> RetrievalResult:
        """Search a remote Cognee service. Same contract as ``_live_search``.

        One capability gap, deliberately not hidden: the remote search endpoint
        forwards ``node_name`` but has no parameter for
        ``node_name_filter_operator``, so a multi-NodeSet scope falls to the
        server's default operator instead of the OR this repo sets explicitly
        in embedded mode. Scope still narrows, but "any of these NodeSets" is
        not guaranteed, so the stats record what could not be sent rather than
        implying parity.
        """
        client = await self._remote()

        kwargs: dict[str, Any] = {
            "search_type": to_cognee_search_type(mode),
            "datasets": [dataset],
            "top_k": top_k,
            "only_context": True,
        }
        if node_sets:
            kwargs["node_name"] = node_sets

        raw_results = await client.search(query, **kwargs)
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
                "remote": True,
                "service_url": self.base_url,
                "node_name_filter_operator_sent": False,
            },
        )

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

    async def _remote_ingest(
        self, content: str, dataset: str, node_sets: list[str] | None
    ) -> dict[str, Any]:
        """Ingest into a remote Cognee service.

        No ``embed_triplets`` call here, and that is not an omission: the
        memify pipeline runs *inside* the instance that owns the store, so
        invoking the local one would build triplet embeddings in an unrelated
        in-process database. Whether TRIPLET_COMPLETION works against a remote
        service is therefore that service's responsibility — flagged in the
        result so a dead retrieval mode is traceable to the right side.
        """
        client = await self._remote()

        add_kwargs: dict[str, Any] = {"dataset_name": dataset}
        if node_sets:
            add_kwargs["node_set"] = node_sets
        await client.add(content, **add_kwargs)
        await client.cognify(datasets=[dataset])
        return {
            "status": "accepted",
            "dataset": dataset,
            "node_sets": node_sets or [],
            "live": True,
            "remote": True,
            "triplet_embeddings": "owned_by_remote_service",
        }

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
