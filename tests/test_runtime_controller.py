from app.domain.models import (
    DegradedKind,
    PackedEvidence,
    QueryRequest,
    RouteDecision,
    SearchMode,
)
from app.services.runtime_controller import (
    CheckErrorPolicy,
    ControlAction,
    RuntimeController,
    StopReason,
)


def _packed(
    mode: SearchMode = SearchMode.CHUNKS,
    items: list[str] | None = None,
    *,
    degraded: bool = False,
    degraded_kind: DegradedKind | None = None,
) -> PackedEvidence:
    items = items or []
    return PackedEvidence(
        mode=mode,
        summary="\n".join(items) or "No evidence items returned.",
        evidence_items=items,
        degraded=degraded,
        degraded_reason="live_search_failed: RuntimeError" if degraded else None,
        degraded_kind=degraded_kind,
    )


def _controller() -> RuntimeController:
    return RuntimeController(allow_stub_evidence=True)


# Fixtures use query-relevant evidence deliberately: the controller now checks
# what the evidence is ABOUT (set-level sufficiency), not just how much of it
# there is. Toy fixtures like items=["a","b"] against query "q" are correctly
# flagged off-topic — which is the exact failure the count heuristic missed.


def test_accepts_when_evidence_meets_budget() -> None:
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(
        QueryRequest(query="what port does billing expose"),
        route,
        _packed(items=["billing exposes port 8443", "the billing port is internal"]),
        retry_count=0,
    )
    assert decision.action is ControlAction.ACCEPT
    assert decision.next_route is None
    assert "sufficiency.sufficient" in decision.signals


def test_off_topic_evidence_is_insufficient_even_when_plentiful() -> None:
    """The check the volume heuristic could never make: twelve on-budget items
    about the wrong entity pass every count/token test."""
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(
        QueryRequest(query="what port does billing expose"),
        route,
        _packed(items=[f"the weather in Ghent on day {i} was mild" for i in range(12)]),
        retry_count=0,
    )
    assert decision.action is not ControlAction.ACCEPT
    assert "insufficient_set" in decision.reason


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
    """One tiny partially-relevant item against a budget of eight is a miss.

    The fixture covers 1 of 4 query terms — inside the UNCERTAIN band, so the
    volume floor (not the sufficiency verdict) is what fires. A fully-covering
    item would be accepted however small: sufficiency outranks thinness.
    """
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(
        QueryRequest(query="billing warehouse port timeout"),
        route,
        _packed(items=["billing mentioned"]),
        retry_count=0,
        widened=True,
    )
    assert decision.action is ControlAction.ESCALATE
    assert "thin_evidence" in decision.reason


def test_a_sufficient_set_is_an_answer_however_small() -> None:
    """Regression guard on the design choice: sufficiency outranks thinness."""
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(
        QueryRequest(query="what port does billing expose"),
        route,
        _packed(items=["billing exposes port 8443"]),
        retry_count=0,
    )
    assert decision.action is ControlAction.ACCEPT


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


def test_a_capability_gap_escalates_instead_of_failing_closed() -> None:
    """A backend declaring a mode unsupported is not an outage. Retrying that
    mode reproduces it forever; the ladder is the designed recovery. Measured
    on the turso profile: 3 of 4 CYPHER-routed held-out queries failed closed
    with retry_count=0 and an empty escalation chain before this."""
    controller = RuntimeController(allow_stub_evidence=False)
    route = RouteDecision(mode=SearchMode.CYPHER, rationale="test")
    decision = controller.decide(
        QueryRequest(query="how many nodes are in the billing subgraph"),
        route,
        _packed(
            mode=SearchMode.CYPHER,
            items=["Placeholder"],
            degraded=True,
            degraded_kind="capability",
        ),
        retry_count=0,
    )
    assert decision.action is ControlAction.ESCALATE
    assert decision.next_route is not None
    assert decision.next_route.mode is SearchMode.GRAPH_COMPLETION
    assert decision.reason.startswith("capability_gap_CYPHER:")


def test_a_substrate_failure_still_fails_closed() -> None:
    """The distinction has to cut both ways, or 'escalate on degraded' becomes
    three more calls against a substrate that is down."""
    controller = RuntimeController(allow_stub_evidence=False)
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    decision = controller.decide(
        QueryRequest(query="q"),
        route,
        _packed(items=["Placeholder"], degraded=True, degraded_kind="failure"),
        retry_count=0,
    )
    assert decision.action is ControlAction.FAIL_CLOSED
    assert decision.stop_reason is StopReason.DEGRADED_RETRIEVAL


def test_a_capability_gap_at_the_end_of_the_ladder_fails_closed() -> None:
    """No next mode means nothing to escalate to, so the fail-closed guarantee
    still terminates the loop."""
    controller = RuntimeController(allow_stub_evidence=False)
    route = RouteDecision(mode=SearchMode.GRAPH_SUMMARY_COMPLETION, rationale="test")
    decision = controller.decide(
        QueryRequest(query="q"),
        route,
        _packed(
            mode=SearchMode.GRAPH_SUMMARY_COMPLETION,
            items=["Placeholder"],
            degraded=True,
            degraded_kind="capability",
        ),
        retry_count=0,
    )
    assert decision.action is ControlAction.FAIL_CLOSED


def test_a_capability_gap_never_re_escalates_into_an_attempted_mode() -> None:
    """Escalating into a mode already tried would loop until MAX_ATTEMPTS."""
    controller = RuntimeController(allow_stub_evidence=False)
    route = RouteDecision(mode=SearchMode.CYPHER, rationale="test")
    decision = controller.decide(
        QueryRequest(query="q"),
        route,
        _packed(mode=SearchMode.CYPHER, items=["P"], degraded=True, degraded_kind="capability"),
        retry_count=1,
        attempted_modes=frozenset({SearchMode.GRAPH_COMPLETION}),
    )
    assert decision.action is ControlAction.FAIL_CLOSED


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
        QueryRequest(query="rollout stage", freshness_required=True), route, packed, retry_count=0
    )
    assert decision.action is ControlAction.ACCEPT


def test_freshness_contract_survives_escalation_away_from_temporal() -> None:
    """Regression: escalating past TEMPORAL silently dropped the contract.

    Once the ladder moves to a broader non-temporal mode, the evidence is no
    longer dated — but `requires_freshness_validation` is still set, and the
    answer would have been returned as if the freshness requirement were met.
    """
    route = RouteDecision(
        mode=SearchMode.GRAPH_SUMMARY_COMPLETION,
        rationale="escalated from TEMPORAL",
        requires_freshness_validation=True,
        evidence_budget=10,
    )
    packed = _packed(SearchMode.GRAPH_SUMMARY_COMPLETION, items=["a broad undated summary"] * 1)
    packed.token_estimate = 500  # substantial, so not thin
    decision = RuntimeController(allow_stub_evidence=True, strict_freshness=True).decide(
        QueryRequest(query="what is the latest status", freshness_required=True),
        route,
        packed,
        retry_count=1,
        attempted_modes=frozenset({SearchMode.TEMPORAL, SearchMode.GRAPH_SUMMARY_COMPLETION}),
    )
    assert decision.action is ControlAction.FAIL_CLOSED
    assert decision.stop_reason is StopReason.FRESHNESS_UNVERIFIABLE


def test_latency_ceiling_is_a_stop_reason_not_an_error() -> None:
    """A caller ceiling is an expected outcome, so it lives in the same enum."""
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test", evidence_budget=8)
    decision = _controller().decide(
        QueryRequest(query="q", max_latency_ms=100),
        route,
        _packed(items=["a", "b", "c"]),
        retry_count=0,
        elapsed_ms=250,
    )
    assert decision.stop_reason is StopReason.LIMIT_LATENCY
    assert decision.action is ControlAction.ACCEPT  # usable evidence is still returned


def test_latency_ceiling_with_no_evidence_fails_closed() -> None:
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    decision = _controller().decide(
        QueryRequest(query="q", max_latency_ms=100), route, _packed(), retry_count=0, elapsed_ms=250
    )
    assert decision.action is ControlAction.FAIL_CLOSED
    assert decision.stop_reason is StopReason.LIMIT_LATENCY


def test_a_raising_check_fails_closed_by_default() -> None:
    """A control check that cannot run has not passed."""
    controller = RuntimeController(allow_stub_evidence=True)
    broken = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    object.__setattr__(broken, "evidence_budget", "not-a-number")  # forces a TypeError
    decision = controller.decide(QueryRequest(query="q"), broken, _packed(), retry_count=0)
    assert decision.action is ControlAction.FAIL_CLOSED
    assert decision.stop_reason is StopReason.CHECK_ERROR


def test_fail_open_policy_is_available_but_must_be_asked_for() -> None:
    controller = RuntimeController(
        allow_stub_evidence=True, on_check_error=CheckErrorPolicy.PROCEED
    )
    broken = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    object.__setattr__(broken, "evidence_budget", "not-a-number")
    decision = controller.decide(QueryRequest(query="q"), broken, _packed(), retry_count=0)
    assert decision.action is ControlAction.ACCEPT
    assert "fail_open" in decision.signals
