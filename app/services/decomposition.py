"""Schema-constrained sub-question decomposition for multi-hop escalations.

Phase D of docs/implementation-plan.md. The verified mechanism (Youtu-GraphRAG,
ICLR 2026 — mechanism confirmed against their repo; their headline margins are
self-reported and not claimed here): decompose a complex query into parallel
sub-queries **constrained by the graph's declared schema**, so decomposition
cannot invent sub-questions the graph could never answer.

Scoped exactly as the plan requires:

- Runs **only** when the controller escalates *into* GRAPH_COMPLETION — the
  point where the benchmark evidence says multi-hop structure pays. Never on
  the cheap path, and not when the router chose GRAPH_COMPLETION directly with
  confidence (that query already routed cleanly; decomposition is a recovery
  tool for queries that defeated a cheaper mode).
- One LLM call per decomposition, hard-capped sub-query fan-out.
- Degrades to single-query retrieval on any failure — missing `opt` extra,
  no LM configured, provider error, or a decomposition that returns nothing
  usable. An enhancement, never a validation.
- Sub-results that come back degraded (stub/fallback) are **dropped, not
  merged**: fabricated evidence must never be blended into a live evidence
  set, where it would be indistinguishable after packing. All-degraded means
  the merged result is degraded, and the controller fails closed as usual.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.core.config import settings
from app.domain.models import RetrievalEvidence, RetrievalResult, SearchMode

logger = logging.getLogger(__name__)

# Fan-out bounds. Two sub-queries is the minimum that pays for the LLM call;
# four is the ceiling so a runaway decomposition cannot multiply retrieval cost.
MIN_SUBQUERIES = 2
MAX_SUBQUERIES = 4


class SubQueryDecomposer(Protocol):
    """Returns schema-aligned sub-queries for *query*, or [] when decomposition
    is not possible or not worthwhile. Must not raise for control flow."""

    def decompose(self, query: str, schema: list[str]) -> list[str]: ...


class DspyDecomposer:
    """DSPy-backed decomposer (``opt`` extra). Lazily built, failure-latching.

    The signature receives the graph's declared NodeSet names as the schema
    constraint: sub-queries must be answerable within those entity groupings.
    An empty schema still works — the constraint is then only "self-contained
    and answerable against a knowledge graph".
    """

    def __init__(self) -> None:
        self._program: object | None = None
        self._failed = False

    def decompose(self, query: str, schema: list[str]) -> list[str]:
        if self._failed:
            return []
        try:
            program = self._build()
            prediction = program(  # type: ignore[operator]
                query=query,
                schema=", ".join(schema) if schema else "unspecified",
            )
            subqueries = [
                s.strip()
                for s in str(prediction.subqueries).split("\n")
                if s.strip() and s.strip() != query
            ]
            return subqueries[:MAX_SUBQUERIES]
        except Exception as exc:  # noqa: BLE001 - enhancement, never a validation
            self._failed = True
            logger.warning(
                "decomposition_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return []

    def _build(self) -> object:
        if self._program is None:
            import dspy

            class DecomposeMultiHop(dspy.Signature):
                """Split a multi-hop question into 2-4 independent sub-questions.

                Each sub-question must be self-contained, answerable from a
                knowledge graph scoped to the listed entity groups, and
                collectively the sub-questions must cover every hop of the
                original question. Do not invent entities that are not implied
                by the question. Output one sub-question per line, nothing else.
                """

                query: str = dspy.InputField(desc="The multi-hop question.")
                schema: str = dspy.InputField(
                    desc="Entity groups the graph declares, comma-separated."
                )
                subqueries: str = dspy.OutputField(desc="One sub-question per line.")

            self._program = dspy.ChainOfThought(DecomposeMultiHop)
        return self._program


def build_decomposer() -> SubQueryDecomposer | None:
    """Return the configured decomposer, or None when disabled or unavailable."""
    if not settings.decomposition_enabled:
        return None
    try:
        import dspy  # noqa: F401

        return DspyDecomposer()
    except ImportError:
        logger.info(
            "decomposition_unavailable",
            extra={"hint": "install the `opt` extra: uv sync --extra opt"},
        )
        return None


def merge_results(query: str, results: list[RetrievalResult]) -> RetrievalResult:
    """Merge parallel sub-query results into one GRAPH_COMPLETION result.

    Degraded sub-results are dropped before merging — fabricated evidence must
    never be blended into a live set. If everything was degraded, the merged
    result is degraded and carries the first reason, so the controller's
    fail-closed path sees it exactly as it would a single degraded retrieval.
    The packer downstream deduplicates items that several sub-queries returned.
    """
    live = [r for r in results if not r.degraded]
    dropped = len(results) - len(live)
    if not live:
        first_reason = results[0].degraded_reason if results else "no_subquery_results"
        return RetrievalResult(
            mode=SearchMode.GRAPH_COMPLETION,
            evidence=RetrievalEvidence(),
            retrieval_stats={"decomposed": True, "subqueries": len(results)},
            degraded=True,
            degraded_reason=f"all_subqueries_degraded:{first_reason}",
        )

    merged = RetrievalEvidence()
    for result in live:
        merged.triplets.extend(result.evidence.triplets)
        merged.chunks.extend(result.evidence.chunks)
        merged.graph_paths.extend(result.evidence.graph_paths)
        merged.rows.extend(result.evidence.rows)
        merged.timestamps.extend(result.evidence.timestamps)
        merged.provenance.extend(result.evidence.provenance)

    return RetrievalResult(
        mode=SearchMode.GRAPH_COMPLETION,
        evidence=merged,
        retrieval_stats={
            "decomposed": True,
            "subqueries": len(results),
            "subqueries_live": len(live),
            "subqueries_dropped_degraded": dropped,
            "original_query": query,
        },
    )
