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

import asyncio
from time import perf_counter
from typing import Any, cast

from app.core.config import settings
from app.core.tokens import cost_for, estimate_tokens, max_input_tokens_for_budget
from app.domain.models import AnswerResult, PackedEvidence, QueryRequest, RouteDecision, SearchMode
from app.integrations.cognee_client import CogneeClient
from app.integrations.langfuse_tracing import (
    record_attributes,
    record_usage,
    trace_synthesis_span,
)
from app.observability.eval_trace import get_eval_run_context
from app.observability.gen_ai import gen_ai_attributes
from app.observability.metrics import metrics
from app.services.cache import ContentAddressedCache
from app.services.decomposition import SubQueryDecomposer, build_decomposer, merge_results
from app.services.evidence import EvidencePacker
from app.services.graph_answerer import GraphAnswerer
from app.services.memgraph import InMemoryGraph, build_graph
from app.services.normalizer import QueryNormalizer
from app.services.router import QueryRouter
from app.services.runtime_controller import ControlAction, RuntimeController, StopReason
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
        decomposer: SubQueryDecomposer | None = None,
        graph: InMemoryGraph | None = None,
    ) -> None:
        self.router = router
        self.cognee_client = cognee_client
        self.evidence_packer = evidence_packer
        self.runtime_controller = runtime_controller
        self.synthesizer = synthesizer or AnswerSynthesizer()
        self.normalizer = normalizer or QueryNormalizer()
        self.cache = cache or ContentAddressedCache()
        self.decomposer = decomposer if decomposer is not None else build_decomposer()
        self.graph = graph if graph is not None else build_graph()
        self.graph_answerer = GraphAnswerer(self.graph) if self.graph is not None else None

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

        # --- Tier 0: the resident in-memory graph ---
        # The fastest answer Roubaix can give: a traversal of a graph already
        # in process memory. Zero tokens, zero cost, microseconds. Answers only
        # when the structural pattern matches AND every entity resolves to a
        # resident node; anything else falls through — the fast path's failure
        # mode is "fell through", never "guessed". Not cached: it IS the fast
        # tier, and caching it would only shadow graph updates.
        if self.graph_answerer is not None:
            tier0 = self.graph_answerer.try_answer(normalized)
            if tier0 is not None:
                metrics.increment("tier:memgraph")
                total_ms = int((perf_counter() - answer_start) * 1000)
                mode = (
                    SearchMode.GRAPH_COMPLETION
                    if tier0.pattern in ("path", "no_path")
                    else SearchMode.TRIPLET_COMPLETION
                )
                return AnswerResult(
                    answer=tier0.answer,
                    accepted=True,
                    route=RouteDecision(
                        mode=mode,
                        node_sets=list(request.node_sets),
                        evidence_budget=len(tier0.edges),
                        rationale=f"answered from resident graph ({tier0.pattern})",
                        signals=["tier.memgraph", *tier0.signals],
                    ),
                    retrieval_mode=mode,
                    retry_count=0,
                    cache_hit=False,
                    telemetry={
                        "tier": "memgraph",
                        "evidence_items": len(tier0.edges),
                        "evidence": [e.as_text() for e in tier0.edges],
                        "retrieval_ms": 0,
                        "synthesis_ms": 0,
                        "total_ms": total_ms,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "estimated_cost_usd": 0.0,
                        "cost_is_estimate": False,
                        "graph_nodes": self.graph.node_count if self.graph else 0,
                        "graph_edges": self.graph.edge_count if self.graph else 0,
                    },
                )
        metrics.increment("tier:pipeline")

        # --- Layer 3: Route, retrieve, control ---
        retry_count = 0
        route = self.router.route(request)
        retrieval_ms = 0
        attempted: set[SearchMode] = set()
        escalations: list[str] = []
        widened = False
        decomposed = False
        subquery_count = 0
        packed: PackedEvidence | None = None

        for _ in range(MAX_ATTEMPTS):
            attempted.add(route.mode)
            metrics.increment(f"route:{route.mode.value}")

            retrieval_start = perf_counter()
            # Phase D: a query that defeated a cheaper mode and is now
            # escalating into GRAPH_COMPLETION gets one shot at schema-
            # constrained decomposition — parallel sub-queries over the same
            # scope, merged before packing. Escalation-only by design: a query
            # the router sent to GRAPH_COMPLETION directly routed cleanly and
            # does not pay for a decomposition call.
            subqueries: list[str] = []
            if (
                self.decomposer is not None
                and not decomposed
                and retry_count > 0
                and route.mode is SearchMode.GRAPH_COMPLETION
            ):
                schema = self._schema_for_decomposition()
                subqueries = self.decomposer.decompose(request.query, schema)

            if len(subqueries) >= 2:
                decomposed = True
                subquery_count = len(subqueries)
                metrics.increment("runtime:decomposed")
                sub_results = await asyncio.gather(
                    *(
                        self.cognee_client.search(
                            query=subquery,
                            mode=SearchMode.GRAPH_COMPLETION,
                            dataset=dataset,
                            node_sets=route.node_sets,
                            evidence_budget=route.evidence_budget,
                        )
                        for subquery in subqueries
                    )
                )
                result = merge_results(request.query, list(sub_results))
            else:
                result = await self.cognee_client.search(
                    query=request.query,
                    mode=route.mode,
                    dataset=dataset,
                    node_sets=route.node_sets,
                    evidence_budget=route.evidence_budget,
                )
            retrieval_ms += int((perf_counter() - retrieval_start) * 1000)

            packed = self.evidence_packer.pack(
                result,
                evidence_budget=route.evidence_budget,
                query_keywords=self.normalizer.keywords(request.query),
            )
            decision = self.runtime_controller.decide(
                request,
                route,
                packed,
                retry_count,
                frozenset(attempted),
                widened,
                elapsed_ms=int((perf_counter() - answer_start) * 1000),
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
                    stop_reason=decision.stop_reason,
                    escalations=escalations,
                    decomposed=decomposed,
                    subquery_count=subquery_count,
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
                stop_reason=StopReason.ATTEMPT_CEILING,
                escalations=escalations,
                decomposed=decomposed,
                subquery_count=subquery_count,
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
            metadata={
                "route_mode": route.mode.value,
                "route_signals": route.signals,
                "route_confident": route.confident,
                "evidence_items": len(packed.evidence_items),
                "evidence_tokens": packed.token_estimate,
                "retry_count": retry_count,
            },
            model=self.synthesizer.model,
        ) as span:
            synthesis = await self.synthesizer.synthesize(request, route, packed)
            cost = cost_for(
                model=self.synthesizer.model,
                input_tokens=synthesis.input_tokens_estimate,
                output_tokens=estimate_tokens(synthesis.answer),
                estimated=synthesis.usage_measured is False,
            )
            record_usage(
                span,
                input_tokens=cost.input_tokens,
                output_tokens=cost.output_tokens,
                usd=cost.usd,
            )
            record_attributes(
                span,
                gen_ai_attributes(
                    cost.as_telemetry(),
                    model=self.synthesizer.model,
                    route_mode=route.mode.value,
                ),
            )
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
                stop_reason=StopReason.SYNTHESIS_FAILED,
                escalations=escalations,
                decomposed=decomposed,
                subquery_count=subquery_count,
                retrieval_ms=retrieval_ms,
                answer_start=answer_start,
                packed=packed,
            )

        total_ms = int((perf_counter() - answer_start) * 1000)

        answer_result = AnswerResult(
            answer=synthesis.answer,
            accepted=True,
            route=route,
            retrieval_mode=route.mode,
            retry_count=retry_count,
            cache_hit=False,
            telemetry={
                "tier": "pipeline",
                "evidence_items": len(packed.evidence_items),
                "evidence_tokens": packed.token_estimate,
                "evidence_dropped_duplicates": packed.dropped_duplicates,
                "evidence_dropped_over_budget": packed.dropped_over_budget,
                "best_dropped_evidentiality": packed.best_dropped_evidentiality,
                "evidence_hashes": packed.evidence_hashes,
                "retrieval_ms": retrieval_ms,
                "synthesis_ms": synthesis_ms,
                "total_ms": total_ms,
                "escalation_reason": escalations[-1] if escalations else None,
                "stop_reason": (decision.stop_reason.value if decision.stop_reason else None),
                "escalation_chain": escalations,
                "attempted_modes": sorted(m.value for m in attempted),
                "route_signals": route.signals,
                "route_confident": route.confident,
                "widened": widened,
                "decomposed": decomposed,
                "subquery_count": subquery_count,
                "temporal_grounded": packed.temporal_grounded,
                "unsynthesized": synthesis.unsynthesized,
                "degraded": packed.degraded,
                "degraded_reason": packed.degraded_reason,
                "budget_downgrade": budget_downgrade,
                **cost.as_telemetry(),
            },
        )

        # The slow path teaches the fast path: accepted, non-degraded triplet
        # evidence is promoted into the resident graph, so the next structural
        # query in this neighborhood answers in Tier 0 — the contract that a
        # query which could not be fast makes its successors fast. Degraded
        # evidence is never promoted: fabricated edges would make the fastest
        # tier the least trustworthy one.
        if (
            self.graph is not None
            and not packed.degraded
            and route.mode is SearchMode.TRIPLET_COMPLETION
        ):
            promoted = self.graph.promote(
                packed.evidence_items, provenance=f"{dataset}:{route.mode.value}"
            )
            if promoted:
                metrics.increment("memgraph:promoted", promoted)
                answer_result.telemetry["promoted_edges"] = promoted

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

    def _schema_for_decomposition(self) -> list[str]:
        """The graph's declared NodeSet names, from the router's index.

        The schema constraint is what keeps decomposition from inventing
        sub-questions the graph could never answer; with no index configured
        the decomposer falls back to its unconstrained-but-bounded prompt.
        """
        index = getattr(self.router, "node_set_index", None)
        if index is None:
            return []
        return sorted({nodeset for nodeset, _, _ in index._aliases})

    def _fail_closed(
        self,
        *,
        route: RouteDecision,
        retry_count: int,
        reason: str,
        escalations: list[str],
        stop_reason: StopReason | None = None,
        decomposed: bool = False,
        subquery_count: int = 0,
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
            "stop_reason": stop_reason.value if stop_reason else None,
            "escalation_chain": escalations,
            "decomposed": decomposed,
            "subquery_count": subquery_count,
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
