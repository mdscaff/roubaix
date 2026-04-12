from app.domain.models import PackedEvidence, QueryRequest, RetrievalResult, RouteDecision, SearchMode


class RuntimeController:
    """Bounded fallback policy. Replace with AdalFlow orchestration later."""

    def decide(
        self,
        request: QueryRequest,
        route: RouteDecision,
        packed: PackedEvidence,
        retry_count: int,
    ) -> tuple[bool, RouteDecision | None]:
        if packed.evidence_items:
            return True, None
        if retry_count >= 2:
            return True, None
        fallback_mode = SearchMode.GRAPH_COMPLETION if route.mode == SearchMode.CHUNKS else SearchMode.CHUNKS
        return False, RouteDecision(
            mode=fallback_mode,
            node_sets=route.node_sets,
            evidence_budget=max(route.evidence_budget, 8),
            requires_freshness_validation=route.requires_freshness_validation,
            rationale=f"fallback from {route.mode}",
        )
