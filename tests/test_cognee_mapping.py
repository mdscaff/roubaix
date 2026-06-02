from app.domain.models import SearchMode
from app.integrations.cognee_mapping import SEARCH_MODE_TO_COGNEE, to_cognee_search_type
from app.integrations.cognee_results import evidence_from_search_results


def test_search_mode_mapping_names() -> None:
    assert SEARCH_MODE_TO_COGNEE[SearchMode.GRAPH_SUMMARY_COMPLETION] == "GRAPH_SUMMARY_COMPLETION"


def test_evidence_from_string_context() -> None:
    evidence = evidence_from_search_results(
        SearchMode.CHUNKS,
        "chunk one",
        dataset="default",
        node_sets=["ns_a"],
    )
    assert evidence.chunks == ["chunk one"]
    assert evidence.provenance[0]["dataset"] == "default"


def test_evidence_from_list_context() -> None:
    evidence = evidence_from_search_results(
        SearchMode.GRAPH_COMPLETION,
        ["path context"],
        dataset="default",
        node_sets=None,
    )
    assert evidence.graph_paths == [{"context": "path context"}]


def test_to_cognee_search_type_when_installed() -> None:
    try:
        from cognee import SearchType
    except ImportError:
        return
    assert to_cognee_search_type(SearchMode.CHUNKS) == SearchType.CHUNKS
