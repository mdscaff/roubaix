from app.domain.models import PackedEvidence, QueryRequest, RouteDecision, SearchMode
from app.services.runtime_controller import ControlAction, RuntimeController


def _packed(
    mode: SearchMode = SearchMode.CHUNKS,
    items: list[str] | None = None,
    *,
    degraded: bool = False,
) -> PackedEvidence:
    items = items or []
    return PackedEvidence(
        mode=mode,
        summary="\n".join(items) or "No evidence items returned.",
        evidence_items=items,
        degraded=degraded,
        degraded_reason="live_search_failed: RuntimeError" if degraded else None,
    )


def _controller() -> RuntimeController:
    return RuntimeController(allow_stub_evidence=True)


def test_accepts_when_evidence_meets_budget() -> None:
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(
        QueryRequest(query="q"), route, _packed(items=["a", "b"]), retry_count=0
    )
    assert decision.action is ControlAction.ACCEPT
    assert decision.next_route is None


def test_escalates_when_evidence_empty_and_retries_remain() -> None:
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    decision = _controller().decide(QueryRequest(query="q"), route, _packed(), retry_count=0)
    assert decision.action is ControlAction.ESCALATE
    assert decision.next_route is not None
    assert decision.next_route.mode is SearchMode.GRAPH_COMPLETION
    assert "empty_evidence" in decision.reason


def test_escalates_on_thin_evidence_relative_to_budget() -> None:
    """One item against a budget of eight is a retrieval miss, not concision."""
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(
        QueryRequest(query="q"), route, _packed(items=["a"]), retry_count=0
    )
    assert decision.action is ControlAction.ESCALATE
    assert "thin_evidence" in decision.reason


def test_fail_closed_after_max_retries() -> None:
    route = RouteDecision(mode=SearchMode.GRAPH_COMPLETION, rationale="test")
    decision = _controller().decide(
        QueryRequest(query="q"),
        route,
        _packed(SearchMode.GRAPH_COMPLETION),
        retry_count=2,
    )
    assert decision.action is ControlAction.FAIL_CLOSED
    assert decision.next_route is None
    assert "max_retries_exhausted" in decision.reason


def test_fail_closed_when_escalation_ladder_exhausted() -> None:
    route = RouteDecision(mode=SearchMode.GRAPH_SUMMARY_COMPLETION, rationale="test")
    decision = _controller().decide(
        QueryRequest(query="q"),
        route,
        _packed(SearchMode.GRAPH_SUMMARY_COMPLETION),
        retry_count=0,
    )
    assert decision.action is ControlAction.FAIL_CLOSED
    assert "ladder_exhausted" in decision.reason


def test_does_not_re_escalate_into_an_already_attempted_mode() -> None:
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    decision = _controller().decide(
        QueryRequest(query="q"),
        route,
        _packed(),
        retry_count=0,
        attempted_modes=frozenset({SearchMode.CHUNKS, SearchMode.GRAPH_COMPLETION}),
    )
    assert decision.action is ControlAction.FAIL_CLOSED


def test_fails_closed_on_degraded_evidence_by_default() -> None:
    """Stub/fallback evidence is fabricated; answering from it is worse than not."""
    controller = RuntimeController(allow_stub_evidence=False)
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    decision = controller.decide(
        QueryRequest(query="q"),
        route,
        _packed(items=["Placeholder chunk for query: q"], degraded=True),
        retry_count=0,
    )
    assert decision.action is ControlAction.FAIL_CLOSED
    assert decision.reason.startswith("degraded_retrieval:")


def test_escalates_to_temporal_when_freshness_contract_unmet() -> None:
    route = RouteDecision(
        mode=SearchMode.CHUNKS,
        rationale="test",
        requires_freshness_validation=True,
    )
    decision = _controller().decide(
        QueryRequest(query="q", freshness_required=True),
        route,
        _packed(items=["a", "b", "c"]),
        retry_count=0,
    )
    assert decision.action is ControlAction.ESCALATE
    assert decision.next_route is not None
    assert decision.next_route.mode is SearchMode.TEMPORAL
