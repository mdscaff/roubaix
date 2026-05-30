"""Normalize Cognee search payloads into Roubaix RetrievalEvidence."""

from __future__ import annotations

from typing import Any

from app.domain.models import RetrievalEvidence, SearchMode


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _collect_strings(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, dict):
        for key in ("text_result", "context_result", "search_result", "context", "text"):
            if key in raw:
                return _collect_strings(raw[key])
        return [_stringify(raw)] if raw else []
    if isinstance(raw, list):
        items: list[str] = []
        for entry in raw:
            items.extend(_collect_strings(entry))
        return [item for item in items if item]
    text = _stringify(raw)
    return [text] if text else []


def evidence_from_search_results(
    mode: SearchMode,
    raw_results: Any,
    *,
    dataset: str,
    node_sets: list[str] | None,
) -> RetrievalEvidence:
    """Convert Cognee ``search(..., only_context=True)`` output into typed evidence."""
    strings = _collect_strings(raw_results)
    provenance = [{"dataset": dataset, "node_sets": node_sets or [], "source": "cognee"}]

    if mode in {SearchMode.CHUNKS, SearchMode.RAG_COMPLETION}:
        return RetrievalEvidence(chunks=strings, provenance=provenance)

    if mode == SearchMode.TRIPLET_COMPLETION:
        triplets = [{"subject": "context", "predicate": "contains", "object": text} for text in strings]
        return RetrievalEvidence(triplets=triplets, provenance=provenance)

    if mode in {SearchMode.GRAPH_COMPLETION, SearchMode.GRAPH_SUMMARY_COMPLETION}:
        graph_paths = [{"context": text} for text in strings]
        return RetrievalEvidence(graph_paths=graph_paths, provenance=provenance)

    if mode in {SearchMode.CYPHER, SearchMode.NATURAL_LANGUAGE}:
        rows = [{"context": text} for text in strings]
        return RetrievalEvidence(rows=rows, provenance=provenance)

    if mode == SearchMode.TEMPORAL:
        return RetrievalEvidence(timestamps=strings, provenance=provenance)

    return RetrievalEvidence(chunks=strings, provenance=provenance)
