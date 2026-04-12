from app.domain.models import QueryRequest, SearchMode
from app.services.router import QueryRouter


def test_router_prefers_temporal_for_recent_queries() -> None:
    router = QueryRouter()
    decision = router.route(QueryRequest(query="What is the latest status of the rollout?"))
    assert decision.mode == SearchMode.TEMPORAL


def test_router_prefers_triplets_for_relationship_queries() -> None:
    router = QueryRouter()
    decision = router.route(QueryRequest(query="How are service A and service B related?"))
    assert decision.mode == SearchMode.TRIPLET_COMPLETION
