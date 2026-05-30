"""Map Roubaix SearchMode values onto Cognee SearchType."""

from __future__ import annotations

from app.domain.models import SearchMode

try:
    from cognee import SearchType
except ImportError:  # pragma: no cover - exercised when cognee optional extra is absent
    SearchType = None  # type: ignore[misc, assignment]


SEARCH_MODE_TO_COGNEE: dict[SearchMode, str] = {
    SearchMode.CHUNKS: "CHUNKS",
    SearchMode.RAG_COMPLETION: "RAG_COMPLETION",
    SearchMode.TRIPLET_COMPLETION: "TRIPLET_COMPLETION",
    SearchMode.GRAPH_COMPLETION: "GRAPH_COMPLETION",
    SearchMode.GRAPH_SUMMARY_COMPLETION: "GRAPH_SUMMARY_COMPLETION",
    SearchMode.CYPHER: "CYPHER",
    SearchMode.NATURAL_LANGUAGE: "NATURAL_LANGUAGE",
    SearchMode.TEMPORAL: "TEMPORAL",
}


def to_cognee_search_type(mode: SearchMode) -> object:
    """Return the Cognee SearchType enum member for a Roubaix mode."""
    if SearchType is None:
        raise ImportError("cognee is not installed")
    name = SEARCH_MODE_TO_COGNEE[mode]
    return SearchType[name]
