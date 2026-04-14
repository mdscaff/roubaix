"""Query orchestrator with tiered resolution pipeline.

Inspired by HyperSpace AGI architect-v1 four-layer resolution:
  1. Normalize query (canonical form for cache keys)
  2. Check content-addressed cache (exact match)
  3. Full routing + retrieval (fallback)
  4. Cache result for future queries

Layer 3 (MinHash similarity) will be added in Phase 2.
"""

from app.domain.models import AnswerResult, QueryRequest, RouteDecision
from app.integrations.cognee_client import CogneeClient
from app.observability.metrics import metrics
from app.services.cache import ContentAddressedCache
from app.services.evidence import EvidencePacker
from app.services.normalizer import QueryNormalizer
from app.services.router import QueryRouter
from app.services.runtime_controller import RuntimeController


class QueryOrchestrator:
    def __init__(
        self,
        router: QueryRouter,
        cognee_client: CogneeClient,
        evidence_packer: EvidencePacker,
        runtime_controller: RuntimeController,
        normalizer: QueryNormalizer | None = None,
        cache: ContentAddressedCache | None = None,
    ) -> None:
        self.router = router
        self.cognee_client = cognee_client
        self.evidence_packer = evidence_packer
        self.runtime_controller = runtime_controller
        self.normalizer = normalizer or QueryNormalizer()
        self.cache = cache or ContentAddressedCache()

    async def answer(self, request: QueryRequest) -> AnswerResult:
        # --- Layer 1: Normalize ---
        normalized = self.normalizer.normalize(request.query)
        dataset = request.dataset or "default"
        request.normalized_query = normalized

        # --- Layer 2: Cache check ---
        cache_key = self.normalizer.content_key(normalized, dataset)
        request.content_key = cache_key
        cached = self.cache.get(cache_key)
        if cached is not None:
            metrics.increment("cache:hit")
            hit = cached.model_copy(update={"cache_hit": True})
            return hit

        metrics.increment("cache:miss")

        # --- Layer 3: Full resolution ---
        retry_count = 0
        route = self.router.route(request)
        while True:
            result = await self.cognee_client.search(
                query=request.query,
                mode=route.mode,
                dataset=dataset,
                node_sets=route.node_sets,
            )
            packed = self.evidence_packer.pack(result)
            accepted, next_route = self.runtime_controller.decide(request, route, packed, retry_count)
            metrics.increment(f"route:{route.mode}")
            if accepted:
                answer_text = self._synthesize(request, route, packed)
                answer_result = AnswerResult(
                    answer=answer_text,
                    accepted=True,
                    route=route,
                    retrieval_mode=route.mode,
                    retry_count=retry_count,
                    cache_hit=False,
                    telemetry={"evidence_items": len(packed.evidence_items)},
                )
                # Cache the result. Use shorter TTL for freshness-sensitive routes.
                self.cache.put(
                    cache_key,
                    answer_result,
                    freshness_sensitive=route.requires_freshness_validation,
                )
                return answer_result
            retry_count += 1
            route = next_route or route

    def _synthesize(self, request: QueryRequest, route: RouteDecision, packed: object) -> str:
        # TODO: implement LLM call with cache-aware prompt (static prefix + dynamic suffix)
        return (
            f"Roubaix response using mode={route.mode}. "
            f"Query: {request.query}. "
            f"Evidence packed successfully."
        )
