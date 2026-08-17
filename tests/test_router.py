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
        (
            "Run a graph query to list all nodes with more than three edges.",
            SearchMode.NATURAL_LANGUAGE,
        ),
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
    assert decision.mode is SearchMode.NATURAL_LANGUAGE
    assert "TRIPLET_COMPLETION" in decision.scores  # the loser is still recorded


def test_structural_questions_go_to_the_nl_path_not_the_cypher_one() -> None:
    """CYPHER takes a Cypher string. Sending it English is why all four
    held-out structural questions raised CypherSearchError on a backend that
    supports Cypher (measured 2026-08-17)."""
    for query in (
        "Cypher: list services with no outgoing edges.",
        "How many nodes are in the billing subgraph?",
    ):
        assert _route(query).mode is SearchMode.NATURAL_LANGUAGE, query


def test_the_owns_edges_query_stays_a_known_miss() -> None:
    """`ho-struct-004` routes to TRIPLET_COMPLETION on relation.between /
    relation.owner and is deliberately NOT fixed: adding a signal to capture it
    would tune the rules against the one unbiased corpus. Pinned so the miss
    stays visible and cannot be quietly relabelled away instead of mechanised."""
    decision = _route("Find all edges of type owns between teams and services.")
    assert decision.mode is SearchMode.TRIPLET_COMPLETION


def test_a_caller_who_writes_cypher_gets_cypher() -> None:
    """Detected on the RAW query: normalization lowercases and strips
    punctuation, which destroys the evidence that separates a Cypher string
    from prose about matching services."""
    decision = _route("MATCH (n:Service) RETURN n.name")
    assert decision.mode is SearchMode.CYPHER
    assert "caller.cypher_syntax" in decision.signals
    assert decision.confident is True


def test_naming_cypher_in_prose_is_not_writing_cypher() -> None:
    """The narrow half of the same contract: a question that mentions the
    language must not be mistaken for the language."""
    assert _route("Can you write a cypher query for this?").mode is not SearchMode.CYPHER


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
