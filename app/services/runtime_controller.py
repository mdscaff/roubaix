"""Runtime control: accept, escalate, or fail closed.

The controller is the only component allowed to decide that an answer should
not be produced. It returns a structured decision rather than a bare boolean so
the reason survives into telemetry — "why did this query cost three retrievals"
is the question the harness has to be able to answer from a trace.

Escalation is progressive and bounded: each step moves to a broader (more
expensive) mode, and the ladder terminates. It never escalates the same mode
twice, which is what turns "bounded retries" into a real guarantee rather than
a retry counter that happens to be small.
"""

from __future__ import annotations

import logging
import math
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.config import settings
from app.domain.models import PackedEvidence, QueryRequest, RouteDecision, SearchMode

logger = logging.getLogger(__name__)


class ControlAction(StrEnum):
    ACCEPT = "accept"
    # Widen the *same* mode (larger evidence budget) before paying for a more
    # expensive one. Depth is a cheaper dial than algorithm, and jumping mode
    # first skips the cheapest thing that could possibly work.
    WIDEN = "widen"
    ESCALATE = "escalate"
    FAIL_CLOSED = "fail_closed"


class StopReason(StrEnum):
    """Closed vocabulary for why the control loop stopped.

    A budget trip is an *outcome*, not an error, so it lives in the same enum as
    a normal accept rather than being raised. That is what makes "how often did
    we stop on the latency ceiling" a query over telemetry instead of a grep
    over log strings. `reason` remains free text for the human-readable detail;
    this is the field to aggregate on.
    """

    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    THIN_EVIDENCE_ACCEPTED = "thin_evidence_accepted"
    EMPTY_EVIDENCE = "empty_evidence"
    DEGRADED_RETRIEVAL = "degraded_retrieval"
    FRESHNESS_UNVERIFIABLE = "freshness_unverifiable"
    LADDER_EXHAUSTED = "ladder_exhausted"
    MAX_RETRIES = "max_retries"
    ATTEMPT_CEILING = "attempt_ceiling"
    SYNTHESIS_FAILED = "synthesis_failed"
    # Caller-supplied ceilings. Reaching one is a normal, expected outcome.
    LIMIT_LATENCY = "limit_latency"
    LIMIT_COST = "limit_cost"
    # A control check itself raised. Recorded distinctly so a broken check is
    # never mistaken for a genuine evidence problem.
    CHECK_ERROR = "check_error"


class CheckErrorPolicy(StrEnum):
    """What to do when a control check itself raises.

    Default is DENY — for a system whose thesis is fail-closed, a check that
    cannot run has not passed. PROCEED is fail-open and is named as such: it
    means a broken check silently stops enforcing its policy.
    """

    THROW = "throw"
    DENY = "deny"
    PROCEED = "proceed"


class ControlDecision(BaseModel):
    action: ControlAction
    reason: str
    stop_reason: StopReason | None = None
    next_route: RouteDecision | None = None
    signals: list[str] = Field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.action is ControlAction.ACCEPT


# Progressive escalation: each mode's broader successor. Terminal modes map to
# None, which means "nothing cheaper-or-broader left to try — fail closed".
ESCALATION_LADDER: dict[SearchMode, SearchMode | None] = {
    SearchMode.CHUNKS: SearchMode.GRAPH_COMPLETION,
    SearchMode.RAG_COMPLETION: SearchMode.GRAPH_COMPLETION,
    SearchMode.TRIPLET_COMPLETION: SearchMode.GRAPH_COMPLETION,
    SearchMode.CYPHER: SearchMode.GRAPH_COMPLETION,
    SearchMode.NATURAL_LANGUAGE: SearchMode.GRAPH_COMPLETION,
    SearchMode.TEMPORAL: SearchMode.GRAPH_COMPLETION,
    SearchMode.GRAPH_COMPLETION: SearchMode.GRAPH_SUMMARY_COMPLETION,
    SearchMode.GRAPH_SUMMARY_COMPLETION: None,
}

# Fraction of the requested evidence budget below which evidence is considered
# thin. Item count alone is not sufficient grounds to escalate: one substantial
# chunk is often the whole answer to a factual lookup, and escalating it to
# GRAPH_COMPLETION buys nothing and costs a retrieval. So thinness requires
# *both* a low item count and a low token volume — a lone five-word triplet is
# a retrieval miss, a lone 200-token chunk is an answer.
MIN_EVIDENCE_RATIO = 0.25
MIN_EVIDENCE_TOKENS_RATIO = 0.1


class RuntimeController:
    """Bounded, progressive fallback policy."""

    def __init__(
        self,
        allow_stub_evidence: bool | None = None,
        strict_freshness: bool | None = None,
        on_check_error: CheckErrorPolicy = CheckErrorPolicy.DENY,
    ) -> None:
        self._allow_stub = (
            settings.allow_stub_evidence if allow_stub_evidence is None else allow_stub_evidence
        )
        self._strict_freshness = (
            settings.strict_freshness if strict_freshness is None else strict_freshness
        )
        self._on_check_error = on_check_error

    def decide(
        self,
        request: QueryRequest,
        route: RouteDecision,
        packed: PackedEvidence,
        retry_count: int,
        attempted_modes: frozenset[SearchMode] = frozenset(),
        widened: bool = False,
        elapsed_ms: int = 0,
    ) -> ControlDecision:
        """Decide, applying the configured policy if the decision itself raises.

        A control check that cannot run has not passed. The default is therefore
        DENY, not PROCEED — PROCEED is fail-open, which for this system means a
        broken check silently stops enforcing the policy it exists to enforce.
        """
        try:
            return self._decide(
                request, route, packed, retry_count, attempted_modes, widened, elapsed_ms
            )
        except Exception as exc:
            if self._on_check_error is CheckErrorPolicy.THROW:
                raise
            logger.exception("control_check_failed", extra={"policy": self._on_check_error.value})
            if self._on_check_error is CheckErrorPolicy.PROCEED:
                # Fail-open, and named as such at the call site.
                return ControlDecision(
                    action=ControlAction.ACCEPT,
                    reason=f"check_error_fail_open:{type(exc).__name__}",
                    stop_reason=StopReason.CHECK_ERROR,
                    signals=["check_error", "fail_open"],
                )
            return ControlDecision(
                action=ControlAction.FAIL_CLOSED,
                reason=f"check_error_fail_closed:{type(exc).__name__}",
                stop_reason=StopReason.CHECK_ERROR,
                signals=["check_error"],
            )

    def _decide(
        self,
        request: QueryRequest,
        route: RouteDecision,
        packed: PackedEvidence,
        retry_count: int,
        attempted_modes: frozenset[SearchMode] = frozenset(),
        widened: bool = False,
        elapsed_ms: int = 0,
    ) -> ControlDecision:
        # 1. Degraded evidence is fabricated evidence. Escalating cannot fix a
        #    substrate that is down, and answering from it is worse than not
        #    answering, so this fails closed immediately.
        if packed.degraded and not self._allow_stub:
            return ControlDecision(
                action=ControlAction.FAIL_CLOSED,
                reason=f"degraded_retrieval:{packed.degraded_reason or 'unknown'}",
                stop_reason=StopReason.DEGRADED_RETRIEVAL,
                signals=["degraded"],
            )

        # 2. Freshness contract. Two distinct failures, and conflating them was
        #    letting stale answers through a gate that reported success.
        if route.requires_freshness_validation:
            # 2a. Wrong mode entirely — escalate to TEMPORAL and try again.
            if route.mode is not SearchMode.TEMPORAL and SearchMode.TEMPORAL not in attempted_modes:
                return self._escalate_to(
                    SearchMode.TEMPORAL,
                    route,
                    reason=f"freshness_required_unvalidated_in_{route.mode.value}",
                    signals=["freshness_unmet"],
                )
            # 2b. TEMPORAL has been tried, and the evidence still carries no
            #     date. Two ways to get here, and both must refuse:
            #
            #     - Temporal retrieval degraded silently to unfiltered search
            #       (it does this when it cannot extract an interval from the
            #       query), so evidence came back and the contract *looks* met.
            #     - Escalation moved on to a broader non-temporal mode, which
            #       abandons the freshness contract while still answering.
            #
            #     The second case is the same bug as the first wearing the
            #     escalation ladder as a disguise: the check must key on
            #     "TEMPORAL was attempted and nothing is dated", not on the
            #     mode we happen to be in right now.
            if (
                self._strict_freshness
                and packed.evidence_items
                and not packed.temporal_grounded
                and (route.mode is SearchMode.TEMPORAL or SearchMode.TEMPORAL in attempted_modes)
            ):
                return ControlDecision(
                    action=ControlAction.FAIL_CLOSED,
                    reason="freshness_unverifiable_no_dated_evidence",
                    stop_reason=StopReason.FRESHNESS_UNVERIFIABLE,
                    signals=["freshness_ungrounded"],
                )

        # 3. Caller latency ceiling. Spending another retrieval when the budget
        #    is already gone cannot help: the answer would arrive too late to be
        #    wanted. Take what we have if it is usable, refuse if it is not.
        #
        #    Soft cap, deliberately: the check happens at loop boundaries, so a
        #    single slow retrieval already in flight can overshoot by up to
        #    `retrieval_timeout_s`. Stated here rather than implied.
        if request.max_latency_ms is not None and elapsed_ms >= request.max_latency_ms:
            has_evidence = bool(packed.evidence_items)
            return ControlDecision(
                action=ControlAction.ACCEPT if has_evidence else ControlAction.FAIL_CLOSED,
                reason=f"latency_budget_spent_{elapsed_ms}ms_of_{request.max_latency_ms}ms",
                stop_reason=StopReason.LIMIT_LATENCY,
                signals=["limit_latency"],
            )

        # 4. Empty or thin evidence relative to the budget the router asked for.
        threshold = max(1, math.ceil(route.evidence_budget * MIN_EVIDENCE_RATIO))
        token_floor = int(settings.evidence_token_budget * MIN_EVIDENCE_TOKENS_RATIO)
        count = len(packed.evidence_items)
        is_thin = count < threshold and packed.token_estimate < token_floor
        if count == 0 or is_thin:
            shortfall = "empty_evidence" if count == 0 else f"thin_evidence_{count}_of_{threshold}"
            if retry_count >= settings.max_retries:
                return ControlDecision(
                    action=ControlAction.FAIL_CLOSED if count == 0 else ControlAction.ACCEPT,
                    reason=(
                        f"max_retries_exhausted_after_{shortfall}"
                        if count == 0
                        else f"accepted_thin_evidence_at_retry_limit:{shortfall}"
                    ),
                    stop_reason=(
                        StopReason.MAX_RETRIES if count == 0 else StopReason.THIN_EVIDENCE_ACCEPTED
                    ),
                    signals=[shortfall, "retry_limit"],
                )
            # Cheapest rung first: widen the current mode's budget before
            # buying a more expensive mode. Only once per mode, so this cannot
            # become a widening loop.
            if route.evidence_budget < settings.max_evidence_items and not widened:
                return ControlDecision(
                    action=ControlAction.WIDEN,
                    reason=f"{shortfall}_widen_{route.mode.value}",
                    stop_reason=None,  # not a stop: the loop continues
                    signals=[shortfall, "widen"],
                    next_route=route.model_copy(
                        update={
                            "evidence_budget": settings.max_evidence_items,
                            "rationale": f"widened {route.mode.value} budget after {shortfall}",
                        }
                    ),
                )

            nxt = ESCALATION_LADDER.get(route.mode)
            if nxt is None or nxt in attempted_modes:
                return ControlDecision(
                    action=ControlAction.FAIL_CLOSED if count == 0 else ControlAction.ACCEPT,
                    reason=(
                        f"escalation_ladder_exhausted_after_{shortfall}"
                        if count == 0
                        else f"accepted_thin_evidence_ladder_exhausted:{shortfall}"
                    ),
                    stop_reason=(
                        StopReason.LADDER_EXHAUSTED
                        if count == 0
                        else StopReason.THIN_EVIDENCE_ACCEPTED
                    ),
                    signals=[shortfall, "ladder_exhausted"],
                )
            return self._escalate_to(
                nxt,
                route,
                reason=f"{shortfall}_escalation_from_{route.mode.value}",
                signals=[shortfall],
            )

        return ControlDecision(
            action=ControlAction.ACCEPT,
            reason="sufficient_evidence",
            stop_reason=StopReason.SUFFICIENT_EVIDENCE,
        )

    @staticmethod
    def _escalate_to(
        mode: SearchMode,
        route: RouteDecision,
        *,
        reason: str,
        signals: list[str],
    ) -> ControlDecision:
        return ControlDecision(
            action=ControlAction.ESCALATE,
            reason=reason,
            signals=signals,
            next_route=RouteDecision(
                mode=mode,
                node_sets=route.node_sets,
                evidence_budget=max(route.evidence_budget, 8),
                requires_freshness_validation=route.requires_freshness_validation,
                rationale=f"escalated from {route.mode.value}: {reason}",
                signals=[f"escalation.{s}" for s in signals],
                scores=route.scores,
            ),
        )
