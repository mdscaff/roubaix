"""Query orchestrator with tiered resolution pipeline.

Inspired by HyperSpace AGI architect-v1 four-layer resolution:
  1. Normalize query (canonical form for cache keys)
  2. Check content-addressed cache (exact match)
  3. Full routing + retrieval (fallback)
  4. Cache result for future queries

Layer 3 (MinHash similarity) will be added in Phase 2.
"""

from time import perf_counter
from typing import cast

from app.domain.models import AnswerResult, PackedEvidence, QueryRequest, RouteDecision
from app.integrations.cognee_client import CogneeClient
from app.integrations.langfuse_tracing import trace_synthesis_span
from app.observability.eval_trace import get_eval_run_context
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
        answer_start = perf_counter()
        eval_ctx = get_eval_run_context()

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
            total_ms = int((perf_counter() - answer_start) * 1000)
            return cast(
                AnswerResult,
                cached.model_copy(
                    update={
                        "cache_hit": True,
                        "telemetry": {
                            **cached.telemetry,
                            "cache_hit": True,
                            "total_ms": total_ms,
                            "retrieval_ms": 0,
                            "synthesis_ms": 0,
                        },
                    }
                ),
            )

        metrics.increment("cache:miss")

        # --- Layer 3: Full resolution ---
        retry_count = 0
        route = self.router.route(request)
        retrieval_ms = 0
        escalation_reason: str | None = None
        while True:
            retrieval_start = perf_counter()
            result = await self.cognee_client.search(
                query=request.query,
                mode=route.mode,
                dataset=dataset,
                node_sets=route.node_sets,
            )
            retrieval_ms += int((perf_counter() - retrieval_start) * 1000)
            packed = self.evidence_packer.pack(result)
            accepted, next_route = self.runtime_controller.decide(request, route, packed, retry_count)
            metrics.increment(f"route:{route.mode}")
            if accepted:
                synthesis_start = perf_counter()
                with trace_synthesis_span(
                    name="roubaix.synthesize",
                    run_id=eval_ctx.run_id if eval_ctx else None,
                    baseline=eval_ctx.baseline if eval_ctx else None,
                    query_id=eval_ctx.query_id if eval_ctx else None,
                    metadata={"route_mode": route.mode.value},
                ):
                    answer_text = self._synthesize(request, route, packed)
                synthesis_ms = int((perf_counter() - synthesis_start) * 1000)
                total_ms = int((perf_counter() - answer_start) * 1000)
                answer_result = AnswerResult(
                    answer=answer_text,
                    accepted=True,
                    route=route,
                    retrieval_mode=route.mode,
                    retry_count=retry_count,
                    cache_hit=False,
                    telemetry={
                        "evidence_items": len(packed.evidence_items),
                        "retrieval_ms": retrieval_ms,
                        "synthesis_ms": synthesis_ms,
                        "total_ms": total_ms,
                        "escalation_reason": escalation_reason,
                        "input_tokens": self._estimate_input_tokens(request, route, packed),
                        "output_tokens": self._estimate_output_tokens(answer_text),
                    },
                )
                # Cache the result. Use shorter TTL for freshness-sensitive routes.
                self.cache.put(
                    cache_key,
                    answer_result,
                    freshness_sensitive=route.requires_freshness_validation,
                )
                return answer_result
            escalation_reason = f"empty_evidence_escalation_from_{route.mode.value}"
            retry_count += 1
            route = next_route or route

    def _estimate_input_tokens(
        self,
        request: QueryRequest,
        route: RouteDecision,
        packed: PackedEvidence,
    ) -> int:
        # Placeholder until real synthesis reports provider token usage.
        material = " ".join([request.query, route.rationale, packed.summary])
        return max(1, len(material) // 4)

    def _estimate_output_tokens(self, answer: str) -> int:
        return max(1, len(answer) // 4)

    def _synthesize(
        self,
        request: QueryRequest,
        route: RouteDecision,
        packed: PackedEvidence,
    ) -> str:
        # TODO: implement LLM call with cache-aware prompt (static prefix + dynamic suffix)
        return (
            f"Roubaix response using mode={route.mode}. "
            f"Query: {request.query}. "
            f"Evidence packed successfully."
        )
