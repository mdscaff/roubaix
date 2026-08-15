import pytest

from app.domain.models import QueryRequest, RouteDecision, SearchMode
from app.services.router import QueryRouter


def _route(query: str, **kwargs: object) -> RouteDecision:
    return QueryRouter().route(QueryRequest(query=query, **kwargs))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What is the latest status of the rollout?", SearchMode.TEMPORAL),
        ("How are service A and service B related?", SearchMode.TRIPLET_COMPLETION),
        ("What services depend on the auth gateway?", SearchMode.TRIPLET_COMPLETION),
        (
            "Which downstream systems are affected if the warehouse ingest fails?",
            SearchMode.GRAPH_COMPLETION,
        ),
        ("Summarize the themes in our platform architecture.", SearchMode.GRAPH_SUMMARY_COMPLETION),
        ("How are the major subsystems organized?", SearchMode.GRAPH_SUMMARY_COMPLETION),
        ("Run a graph query to list all nodes with more than three edges.", SearchMode.CYPHER),
        ("What port does the billing service expose?", SearchMode.CHUNKS),
    ],
)
def test_routes_representative_queries(query: str, expected: SearchMode) -> None:
    assert _route(query).mode is expected


@pytest.mark.parametrize(
    "query",
    [
        "How do we handle concurrent writes?",  # "concurrent" contains "current"
        "What is in our knowledge base?",  # "knowledge" contains "edge"
        "Show the matching algorithm we use.",  # "matching" contains "match"
        "Explain the recentralization plan.",  # "recentralization" contains "recent"
    ],
)
def test_substring_lookalikes_do_not_buy_an_expensive_mode(query: str) -> None:
    """Regression: bare substring matching routed all four to a costly mode."""
    assert _route(query).mode is SearchMode.CHUNKS


@pytest.mark.parametrize(
    "query",
    [
        "Which services are not connected to billing?",
        "Show me services with no relationship to the warehouse.",
    ],
)
def test_negated_signals_do_not_fire(query: str) -> None:
    """ "Not connected to X" is not a relationship lookup."""
    assert _route(query).mode is not SearchMode.TRIPLET_COMPLETION


def test_caller_freshness_contract_overrides_scoring() -> None:
    decision = _route("What port does the billing service expose?", freshness_required=True)
    assert decision.mode is SearchMode.TEMPORAL
    assert "caller.freshness_required" in decision.signals


def test_decision_records_the_signals_that_produced_it() -> None:
    """Routing must be explainable from telemetry, not re-argued."""
    decision = _route("Which downstream systems are affected if ingest fails?")
    assert decision.signals
    assert all(s.startswith("multihop.") for s in decision.signals)
    assert decision.scores


def test_competing_signals_resolve_by_weight_not_rule_order() -> None:
    """Structural markers outweigh the relational ones in the same sentence."""
    decision = _route("Match all services connected to the billing node.")
    assert decision.mode is SearchMode.CYPHER
    assert "TRIPLET_COMPLETION" in decision.scores  # the loser is still recorded


def test_unmatched_query_is_the_cheap_default_and_not_reported_confident() -> None:
    decision = _route("Tell me about the system.")
    assert decision.mode is SearchMode.CHUNKS
    assert decision.confident is False


def test_clear_win_is_reported_confident() -> None:
    decision = _route("Summarize the themes in our platform architecture.")
    assert decision.confident is True


def test_node_sets_pass_through_from_the_request() -> None:
    decision = _route("What port does billing expose?", node_sets=["billing"])
    assert decision.node_sets == ["billing"]
