"""DSPy router: a learned second stage over the deterministic first stage.

Requires the ``opt`` extra (`uv sync --extra opt`). Nothing in the core service
imports this module at import time — see :func:`build_dspy_router`, which
imports it lazily and degrades to the deterministic router when DSPy is absent.

## Why a second stage rather than a replacement

The deterministic scored router in ``app/services/router.py`` is measured at
**85% on the held-out corpus against 23% for the best single fixed mode**.
Replacing it with an LLM would be paying for a call on every query to improve a
number that is already good, and giving up a decision that is currently free,
deterministic, and explainable.

The errors are not evenly distributed. Measured on the held-out corpus:

| Band | Share of traffic | Accuracy | Misses |
|---|---|---|---|
| Router reports confident | 58% | 93% | 1 of 4 |
| Router reports **unconfident** | 42% | **73%** | **3 of 4** |

So the learned stage runs only when the deterministic stage says it is not
confident — nothing cleared ``MIN_SCORE``, or the winner did not beat its
runner-up by ``CONFIDENCE_MARGIN``. That targets 75% of the errors while
leaving 58% of queries on the free path.

## Why GEPA tunes the instruction, not the weights

An earlier version of this repo assumed an optimizer would tune
``Signal.weight`` values. That is a category error: GEPA performs reflective
*text* evolution over instructions, proposing mutations from observed failures
along a Pareto frontier. It has no notion of a numeric search space. What it
optimizes here is the natural-language policy in :class:`SelectRetrievalMode`'s
docstring. Numeric weight tuning, if ever wanted, is Optuna over the existing
eval harness — a different and much cheaper project.

## Failure policy

This is an *enhancement*, not a validation (see ``docs/architecture.md``). Any
failure — DSPy missing, no LM configured, provider error, an invalid mode string
in the output — degrades silently to the deterministic decision. A router that
raises because its optional optimizer is unavailable is worse than one that is
merely unoptimized.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import dspy

from app.domain.models import QueryRequest, RouteDecision, SearchMode
from app.services.router import DEFAULT_RULE, RULES, QueryRouter

logger = logging.getLogger(__name__)

ModeName = Literal[
    "CHUNKS",
    "RAG_COMPLETION",
    "TRIPLET_COMPLETION",
    "GRAPH_COMPLETION",
    "GRAPH_SUMMARY_COMPLETION",
    "CYPHER",
    "NATURAL_LANGUAGE",
    "TEMPORAL",
]

# Evidence budget bounds for the learned stage. The optimizer may not invent an
# unbounded budget: the budget is a cost lever, and an optimizer rewarded partly
# on correctness would otherwise learn to ask for everything.
MIN_BUDGET = 4
MAX_BUDGET = 12


class SelectRetrievalMode(dspy.Signature):
    """Pick the cheapest retrieval mode that can fully answer the query.

    Modes, cheapest first: CHUNKS (local text lookup), RAG_COMPLETION,
    TRIPLET_COMPLETION (one-hop relationships between named entities),
    GRAPH_COMPLETION (multi-hop, impact analysis, dependency chains),
    GRAPH_SUMMARY_COMPLETION (broad explanation over many entities), CYPHER
    (structural queries about graph topology itself — nodes, edges, degree),
    NATURAL_LANGUAGE, TEMPORAL (anything whose answer changes with time).

    Prefer a cheaper mode unless the query demonstrably needs graph traversal,
    structural querying, or freshness. Over-selecting an expensive mode is a
    failure even when the resulting answer is correct.

    You are being consulted only for queries the deterministic rule engine could
    not classify confidently, so the rule scores you are given are weak or
    conflicting by construction. Treat them as one signal, not as the answer.
    """

    query: str = dspy.InputField(desc="The user's query, lowercased and depunctuated.")
    rule_scores: str = dspy.InputField(
        desc="Per-mode scores from the deterministic rule engine. Empty if nothing fired."
    )
    fired_signals: str = dspy.InputField(
        desc="Named patterns that matched, e.g. 'multihop.impact'. Empty if none."
    )

    mode: ModeName = dspy.OutputField(desc="The chosen retrieval mode.")
    evidence_budget: int = dspy.OutputField(desc=f"Items to retrieve, {MIN_BUDGET}-{MAX_BUDGET}.")
    rationale: str = dspy.OutputField(desc="One sentence naming the deciding evidence.")


class RouterProgram(dspy.Module):
    """Deterministic first stage, learned fallback for the unconfident band."""

    def __init__(self, baseline: QueryRouter | None = None) -> None:
        super().__init__()
        self.baseline = baseline or QueryRouter()
        self.select = dspy.ChainOfThought(SelectRetrievalMode)

    def forward(self, query: str, freshness_required: bool = False) -> dspy.Prediction:
        request = QueryRequest(query=query, freshness_required=freshness_required)
        decision = self.baseline.route(request)

        # The cheap path: a confident deterministic decision is returned as-is
        # and never reaches an LM.
        if decision.confident:
            return dspy.Prediction(
                mode=decision.mode.value,
                evidence_budget=decision.evidence_budget,
                rationale=decision.rationale,
                used_llm=False,
            )

        prediction = self.select(
            query=request.normalized_query or self.baseline.normalizer.normalize(query),
            rule_scores=_format_scores(decision.scores),
            fired_signals=", ".join(decision.signals) or "none",
        )
        return dspy.Prediction(
            mode=prediction.mode,
            evidence_budget=_clamp_budget(prediction.evidence_budget),
            rationale=prediction.rationale,
            used_llm=True,
        )


def _format_scores(scores: dict[str, float]) -> str:
    if not scores:
        return "none"
    return ", ".join(f"{mode}={score:g}" for mode, score in sorted(scores.items()))


def _clamp_budget(value: Any) -> int:
    try:
        budget = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RULE.evidence_budget
    return max(MIN_BUDGET, min(MAX_BUDGET, budget))


class DspyRouter(QueryRouter):
    """QueryRouter drop-in that consults a compiled program on unconfident routes.

    Implements the same ``route()`` contract, so the orchestrator and the eval
    baselines are unaware of which router they hold.
    """

    def __init__(
        self,
        program: RouterProgram | None = None,
        baseline: QueryRouter | None = None,
    ) -> None:
        super().__init__(normalizer=(baseline or QueryRouter()).normalizer, rules=RULES)
        self.baseline = baseline or QueryRouter()
        self.program = program or RouterProgram(baseline=self.baseline)
        self.llm_calls = 0
        self.fallbacks = 0

    def route(self, request: QueryRequest) -> RouteDecision:
        decision = self.baseline.route(request)

        # An explicit caller freshness contract is not a classification problem,
        # and the confident band is already 93% accurate. Neither reaches an LM.
        if decision.confident or request.freshness_required:
            return decision

        try:
            prediction = self.program(
                query=request.query,
                freshness_required=request.freshness_required,
            )
            if not getattr(prediction, "used_llm", False):
                return decision
            mode = SearchMode(prediction.mode)
        except Exception as exc:  # noqa: BLE001 - enhancement, never a validation
            self.fallbacks += 1
            logger.warning(
                "dspy_router_fallback",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return decision

        self.llm_calls += 1
        return RouteDecision(
            mode=mode,
            # The baseline decision's scope, not the raw request scope: the
            # deterministic stage may have derived NodeSets by entity anchoring,
            # and the learned stage changes the MODE, not the scope.
            node_sets=list(decision.node_sets),
            evidence_budget=_clamp_budget(prediction.evidence_budget),
            requires_freshness_validation=(
                mode is SearchMode.TEMPORAL or request.freshness_required
            ),
            rationale=str(prediction.rationale),
            # The provenance of the decision is recorded, so a trace shows which
            # stage decided and what the deterministic stage would have said.
            signals=[
                *decision.signals,
                "dspy.ambiguous_band",
                f"dspy.baseline_was_{decision.mode.value}",
            ],
            scores=decision.scores,
            confident=False,
        )


def load_compiled(artifact: Path, baseline: QueryRouter | None = None) -> RouterProgram:
    """Load a GEPA-compiled program from *artifact* (plain-text JSON)."""
    program = RouterProgram(baseline=baseline)
    program.load(path=str(artifact))
    return program


def build_dspy_router(
    artifact: Path | None = None,
    baseline: QueryRouter | None = None,
) -> QueryRouter:
    """Return a :class:`DspyRouter`, or the deterministic router if unavailable.

    The only supported entry point for the rest of the service. It never raises:
    a missing ``opt`` extra, a missing artifact, or an unconfigured LM all yield
    the deterministic router, which is a fully functional system.
    """
    fallback = baseline or QueryRouter()
    try:
        if artifact is not None and Path(artifact).is_file():
            return DspyRouter(program=load_compiled(Path(artifact), fallback), baseline=fallback)
        if artifact is not None:
            logger.warning("dspy_artifact_missing", extra={"artifact": str(artifact)})
            return fallback
        return DspyRouter(baseline=fallback)
    except Exception as exc:  # noqa: BLE001 - optional dependency path
        logger.warning(
            "dspy_router_unavailable",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        return fallback
