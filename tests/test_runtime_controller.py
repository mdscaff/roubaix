from app.domain.models import PackedEvidence, QueryRequest, RouteDecision, SearchMode
from app.services.runtime_controller import RuntimeController


def _empty_packed(mode: SearchMode = SearchMode.CHUNKS) -> PackedEvidence:
    return PackedEvidence(mode=mode, summary="No evidence items returned.", evidence_items=[])


def test_accepts_when_evidence_present() -> None:
    controller = RuntimeController()
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    packed = PackedEvidence(mode=SearchMode.CHUNKS, summary="chunk", evidence_items=["a"])
    accepted, next_route = controller.decide(QueryRequest(query="q"), route, packed, retry_count=0)
    assert accepted is True
    assert next_route is None


def test_escalates_when_evidence_empty_and_retries_remain() -> None:
    controller = RuntimeController()
    route = RouteDecision(mode=SearchMode.CHUNKS, rationale="test")
    accepted, next_route = controller.decide(
        QueryRequest(query="q"),
        route,
        _empty_packed(),
        retry_count=0,
    )
    assert accepted is False
    assert next_route is not None
    assert next_route.mode == SearchMode.GRAPH_COMPLETION


def test_fail_closed_after_max_retries() -> None:
    controller = RuntimeController()
    route = RouteDecision(mode=SearchMode.GRAPH_COMPLETION, rationale="test")
    accepted, next_route = controller.decide(
        QueryRequest(query="q"),
        route,
        _empty_packed(SearchMode.GRAPH_COMPLETION),
        retry_count=2,
    )
    assert accepted is False
    assert next_route is None
