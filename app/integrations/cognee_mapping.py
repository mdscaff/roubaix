"""Map Roubaix SearchMode values onto Cognee SearchType."""

from __future__ import annotations

from app.domain.models import SearchMode

# Cognee is imported lazily, NOT at module scope. Cognee reads its LLM_* and
# EMBEDDING_* configuration from the environment on import, and app.api.main
# imports this module (via CogneeClient) before it calls configure_cognee().
# A module-scope `from cognee import SearchType` therefore initialised Cognee
# with unbridged env, after which the bridge applied to nothing — silently
# disabling the whole env-bridging feature in the API process.


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
    """Return the Cognee SearchType enum member for a Roubaix mode.

    Imports cognee at call time so that ``configure_cognee()`` has already
    bridged the environment by the time cognee initialises.
    """
    try:
        from cognee import SearchType
    except ImportError as exc:  # pragma: no cover - optional extra absent
        raise ImportError("cognee is not installed") from exc
    return SearchType[SEARCH_MODE_TO_COGNEE[mode]]
