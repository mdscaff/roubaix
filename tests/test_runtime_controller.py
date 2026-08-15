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


def test_widens_the_same_mode_before_paying_for_a_more_expensive_one() -> None:
    """Depth is the cheaper dial; jumping mode first skips it."""
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(QueryRequest(query="q"), route, _packed(), retry_count=0)
    assert decision.action is ControlAction.WIDEN
    assert decision.next_route is not None
    assert decision.next_route.mode is SearchMode.CHUNKS  # same mode
    assert decision.next_route.evidence_budget > route.evidence_budget


def test_escalates_when_evidence_empty_and_widening_already_tried() -> None:
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    decision = _controller().decide(
        QueryRequest(query="q"), route, _packed(), retry_count=0, widened=True
    )
    assert decision.action is ControlAction.ESCALATE
    assert decision.next_route is not None
    assert decision.next_route.mode is SearchMode.GRAPH_COMPLETION
    assert "empty_evidence" in decision.reason


def test_escalates_on_thin_evidence_relative_to_budget() -> None:
    """One item against a budget of eight is a retrieval miss, not concision."""
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(
        QueryRequest(query="q"), route, _packed(items=["a"]), retry_count=0, widened=True
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
        widened=True,
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
        widened=True,
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


def test_fails_closed_when_freshness_evidence_carries_no_date() -> None:
    """Temporal retrieval degrades silently to unfiltered search.

    Evidence comes back, so a count-based gate reports the freshness contract
    satisfied. Nothing in that evidence can be checked against a point in time,
    so the only honest outcome is a refusal.
    """
    route = RouteDecision(
        mode=SearchMode.TEMPORAL,
        rationale="test",
        requires_freshness_validation=True,
        evidence_budget=6,
    )
    decision = RuntimeController(allow_stub_evidence=True, strict_freshness=True).decide(
        QueryRequest(query="q", freshness_required=True),
        route,
        _packed(SearchMode.TEMPORAL, items=["the rollout reached stage three", "no date here"]),
        retry_count=0,
    )
    assert decision.action is ControlAction.FAIL_CLOSED
    assert decision.reason == "freshness_unverifiable_no_dated_evidence"


def test_accepts_freshness_when_evidence_is_dated() -> None:
    route = RouteDecision(
        mode=SearchMode.TEMPORAL,
        rationale="test",
        requires_freshness_validation=True,
        evidence_budget=6,
    )
    packed = _packed(SearchMode.TEMPORAL, items=["2026-04-12 rollout reached stage three", "b"])
    packed.temporal_grounded = True
    packed.token_estimate = 500
    decision = RuntimeController(allow_stub_evidence=True, strict_freshness=True).decide(
        QueryRequest(query="q", freshness_required=True), route, packed, retry_count=0
    )
    assert decision.action is ControlAction.ACCEPT
