from app.domain.models import QueryRequest, RouteDecision, SearchMode
from app.services.normalizer import QueryNormalizer


class QueryRouter:
    """Deterministic baseline router before DSPy optimization."""

    def __init__(self, normalizer: QueryNormalizer | None = None) -> None:
        self.normalizer = normalizer or QueryNormalizer()

    def route(self, request: QueryRequest) -> RouteDecision:
        # Use pre-normalized form if available, otherwise normalize inline.
        q = request.normalized_query or self.normalizer.normalize(request.query)
        if request.freshness_required or any(word in q for word in ["today", "latest", "current", "recent"]):
            return RouteDecision(
                mode=SearchMode.TEMPORAL,
                node_sets=[],
                evidence_budget=6,
                requires_freshness_validation=True,
                rationale="freshness-sensitive query",
            )
        if any(word in q for word in ["relationship", "connected", "relates", "depends on", "related"]):
            return RouteDecision(
                mode=SearchMode.TRIPLET_COMPLETION,
                node_sets=[],
                evidence_budget=8,
                rationale="relationship-heavy query",
            )
        if any(word in q for word in ["how are", "summarize", "themes", "overview"]):
            return RouteDecision(
                mode=SearchMode.GRAPH_SUMMARY_COMPLETION,
                node_sets=[],
                evidence_budget=10,
                rationale="broad explanatory query",
            )
        if any(word in q for word in ["cypher", "match", "node", "edge", "graph query"]):
            return RouteDecision(
                mode=SearchMode.CYPHER,
                node_sets=[],
                evidence_budget=5,
                rationale="structural graph query",
            )
        return RouteDecision(
            mode=SearchMode.CHUNKS,
            node_sets=[],
            evidence_budget=8,
            rationale="default low-cost retrieval",
        )
