"""Query orchestrator with tiered resolution pipeline.

Four-layer resolution:
  1. Normalize query (canonical, order-preserving form for cache keys)
  2. Check content-addressed cache (exact match)
  3. Route → retrieve → pack → runtime control, escalating progressively
  4. Cache the result, unless it is degraded or unaccepted

Layer 3 similarity matching (near-duplicate queries) is deferred; see
``QueryNormalizer.fingerprint`` for the intended signal.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, cast

from app.core.config import settings
from app.core.tokens import cost_for, estimate_tokens, max_input_tokens_for_budget
from app.domain.models import AnswerResult, PackedEvidence, QueryRequest, RouteDecision, SearchMode
from app.integrations.cognee_client import CogneeClient
from app.integrations.langfuse_tracing import trace_synthesis_span
from app.observability.eval_trace import get_eval_run_context
from app.observability.metrics import metrics
from app.services.cache import ContentAddressedCache
from app.services.evidence import EvidencePacker
from app.services.normalizer import QueryNormalizer
from app.services.router import QueryRouter
from app.services.runtime_controller import ControlAction, RuntimeController
from app.services.synthesizer import AnswerSynthesizer

# Hard ceiling on retrieval attempts, independent of the retry budget. Protects
# against a controller policy change accidentally creating an unbounded loop.
MAX_ATTEMPTS = 6


class QueryOrchestrator:
    def __init__(
        self,
        router: QueryRouter,
        cognee_client: CogneeClient,
        evidence_packer: EvidencePacker,
        runtime_controller: RuntimeController,
        synthesizer: AnswerSynthesizer | None = None,
        normalizer: QueryNormalizer | None = None,
        cache: ContentAddressedCache | None = None,
    ) -> None:
        self.router = router
        self.cognee_client = cognee_client
        self.evidence_packer = evidence_packer
        self.runtime_controller = runtime_controller
        self.synthesizer = synthesizer or AnswerSynthesizer()
        self.normalizer = normalizer or QueryNormalizer()
        self.cache = cache or ContentAddressedCache()

    async def answer(self, request: QueryRequest) -> AnswerResult:
        answer_start = perf_counter()
        eval_ctx = get_eval_run_context()

        # --- Layer 1: Normalize ---
        normalized = self.normalizer.normalize(request.query)
        dataset = request.dataset or settings.default_dataset
        request.normalized_query = normalized

        # --- Layer 2: Cache check ---
        # The key covers every input that can change the answer. A key over the
        # query text alone will happily serve a non-fresh answer to a request
        # that demanded freshness, or a differently-scoped answer to a
        # differently-scoped request.
        cache_key = self.normalizer.content_key(
            normalized,
            dataset,
            freshness_required=request.freshness_required,
            node_sets=request.node_sets,
            model=self.synthesizer.model,
            user_id=request.user_id,
        )
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
                            # A cache hit costs nothing at synthesis time; the
                            # original cost stays under `origin_*` so eval runs
                            # can separate cost-avoided from cost-incurred.
                            "origin_input_tokens": cached.telemetry.get("input_tokens"),
                            "origin_output_tokens": cached.telemetry.get("output_tokens"),
                            "origin_estimated_cost_usd": cached.telemetry.get("estimated_cost_usd"),
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "estimated_cost_usd": 0.0,
                        },
                    }
                ),
            )

        metrics.increment("cache:miss")

        # --- Layer 3: Route, retrieve, control ---
        retry_count = 0
        route = self.router.route(request)
        retrieval_ms = 0
        attempted: set[SearchMode] = set()
        escalations: list[str] = []
        widened = False
        packed: PackedEvidence | None = None

        for _ in range(MAX_ATTEMPTS):
            attempted.add(route.mode)
            metrics.increment(f"route:{route.mode.value}")

            retrieval_start = perf_counter()
            result = await self.cognee_client.search(
                query=request.query,
                mode=route.mode,
                dataset=dataset,
                node_sets=route.node_sets,
                evidence_budget=route.evidence_budget,
            )
            retrieval_ms += int((perf_counter() - retrieval_start) * 1000)

            packed = self.evidence_packer.pack(result, evidence_budget=route.evidence_budget)
            decision = self.runtime_controller.decide(
                request, route, packed, retry_count, frozenset(attempted), widened
            )

            if decision.action is ControlAction.ACCEPT:
                break

            # Widening retries the same mode with a larger budget. It does not
            # consume the retry allowance reserved for mode escalation, but it
            # is latched so it can happen at most once per query.
            if decision.action is ControlAction.WIDEN and decision.next_route is not None:
                metrics.increment("runtime:widen")
                escalations.append(decision.reason)
                widened = True
                route = decision.next_route
                continue

            if decision.action is ControlAction.FAIL_CLOSED or decision.next_route is None:
                metrics.increment("runtime:fail_closed")
                return self._fail_closed(
                    route=route,
                    retry_count=retry_count,
                    reason=decision.reason,
                    escalations=escalations,
                    retrieval_ms=retrieval_ms,
                    answer_start=answer_start,
                    packed=packed,
                )

            metrics.increment("runtime:escalate")
            escalations.append(decision.reason)
            retry_count += 1
            route = decision.next_route
        else:
            # Attempt ceiling hit without an accept — treat as fail closed
            # rather than answering from whatever the last attempt returned.
            metrics.increment("runtime:attempt_ceiling")
            return self._fail_closed(
                route=route,
                retry_count=retry_count,
                reason="attempt_ceiling_reached",
                escalations=escalations,
                retrieval_ms=retrieval_ms,
                answer_start=answer_start,
                packed=packed,
            )

        assert packed is not None  # loop always assigns before break

        # --- Budget gate: downgrade, don't kill ---
        # A caller cost ceiling shrinks the evidence pack to fit rather than
        # refusing the request. Refusing would be the wrong response to "answer
        # this cheaply" — the caller asked for a cheaper answer, not no answer.
        budget_downgrade: str | None = None
        if request.max_cost_cents is not None:
            affordable = max_input_tokens_for_budget(
                self.synthesizer.model, request.max_cost_cents / 100.0
            )
            if affordable is not None and packed.token_estimate > affordable:
                original_tokens = packed.token_estimate
                packed = self.evidence_packer.pack(
                    result,
                    evidence_budget=route.evidence_budget,
                    token_budget=max(affordable, 1),
                )
                budget_downgrade = (
                    f"evidence_trimmed_{original_tokens}_to_{packed.token_estimate}_tokens"
                    f"_for_{request.max_cost_cents}c_budget"
                )
                metrics.increment("runtime:budget_downgrade")

        # --- Synthesis ---
        synthesis_start = perf_counter()
        with trace_synthesis_span(
            name="roubaix.synthesize",
            run_id=eval_ctx.run_id if eval_ctx else None,
            baseline=eval_ctx.baseline if eval_ctx else None,
            query_id=eval_ctx.query_id if eval_ctx else None,
            metadata={"route_mode": route.mode.value},
        ):
            synthesis = await self.synthesizer.synthesize(request, route, packed)
        synthesis_ms = int((perf_counter() - synthesis_start) * 1000)

        # A failed LLM call yields a placeholder, not an answer. Accepting it
        # would make a provider outage invisible in both the API response and
        # the eval results.
        if synthesis.failed:
            metrics.increment("runtime:synthesis_failed")
            return self._fail_closed(
                route=route,
                retry_count=retry_count,
                reason=f"synthesis_failed:{synthesis.failure_reason}",
                escalations=escalations,
                retrieval_ms=retrieval_ms,
                answer_start=answer_start,
                packed=packed,
            )

        total_ms = int((perf_counter() - answer_start) * 1000)

        cost = cost_for(
            model=self.synthesizer.model,
            input_tokens=synthesis.input_tokens_estimate,
            output_tokens=estimate_tokens(synthesis.answer),
            estimated=synthesis.usage_measured is False,
        )

        answer_result = AnswerResult(
            answer=synthesis.answer,
            accepted=True,
            route=route,
            retrieval_mode=route.mode,
            retry_count=retry_count,
            cache_hit=False,
            telemetry={
                "evidence_items": len(packed.evidence_items),
                "evidence_tokens": packed.token_estimate,
                "evidence_dropped_duplicates": packed.dropped_duplicates,
                "evidence_dropped_over_budget": packed.dropped_over_budget,
                "evidence_hashes": packed.evidence_hashes,
                "retrieval_ms": retrieval_ms,
                "synthesis_ms": synthesis_ms,
                "total_ms": total_ms,
                "escalation_reason": escalations[-1] if escalations else None,
                "escalation_chain": escalations,
                "attempted_modes": sorted(m.value for m in attempted),
                "route_signals": route.signals,
                "route_confident": route.confident,
                "widened": widened,
                "temporal_grounded": packed.temporal_grounded,
                "unsynthesized": synthesis.unsynthesized,
                "degraded": packed.degraded,
                "degraded_reason": packed.degraded_reason,
                "budget_downgrade": budget_downgrade,
                **cost.as_telemetry(),
            },
        )

        # Degraded answers are never cached: caching them would outlive the
        # outage that produced them. Answers produced with no LLM configured
        # *are* cached — that is a stable deployment state rather than a
        # transient failure, and the model is part of the cache key, so
        # configuring a provider does not serve the old template.
        if not packed.degraded:
            self.cache.put(
                cache_key,
                answer_result,
                freshness_sensitive=route.requires_freshness_validation,
            )
        return answer_result

    def _fail_closed(
        self,
        *,
        route: RouteDecision,
        retry_count: int,
        reason: str,
        escalations: list[str],
        retrieval_ms: int,
        answer_start: float,
        packed: PackedEvidence | None,
    ) -> AnswerResult:
        """Return an explicit non-answer. Never cached."""
        total_ms = int((perf_counter() - answer_start) * 1000)
        telemetry: dict[str, Any] = {
            "evidence_items": len(packed.evidence_items) if packed else 0,
            "retrieval_ms": retrieval_ms,
            "synthesis_ms": 0,
            "total_ms": total_ms,
            "escalation_reason": reason,
            "escalation_chain": escalations,
            "route_signals": route.signals,
            "degraded": packed.degraded if packed else False,
            "degraded_reason": packed.degraded_reason if packed else None,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
        }
        return AnswerResult(
            answer="Insufficient evidence to answer confidently.",
            accepted=False,
            route=route,
            retrieval_mode=route.mode,
            retry_count=retry_count,
            cache_hit=False,
            telemetry=telemetry,
        )
