"""Tests for the DSPy learned-router stage.

Everything here runs without an LM, an API key, or a network call. The learned
stage is gated by the deterministic router's confidence flag, and the metric is
pure — so both are testable directly. An optimizer component that can only be
exercised by spending money is the untestable abstraction CLAUDE.md warns
against.

Skipped entirely when the `opt` extra is absent, which is how CI runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("dspy", reason="requires the `opt` extra")

import dspy

from app.domain.models import QueryRequest, SearchMode
from app.integrations.dspy_program import (
    MAX_BUDGET,
    MIN_BUDGET,
    DspyRouter,
    _clamp_budget,
    build_dspy_router,
)
from app.integrations.gepa_optimizer import router_metric, score_and_feedback
from app.services.router import QueryRouter


class FakeProgram:
    """Stands in for a compiled RouterProgram. Records what it was asked."""

    def __init__(self, mode: str = "TRIPLET_COMPLETION", budget: int = 8) -> None:
        self.mode = mode
        self.budget = budget
        self.calls: list[str] = []

    def __call__(self, query: str, freshness_required: bool = False) -> dspy.Prediction:
        self.calls.append(query)
        return dspy.Prediction(
            mode=self.mode,
            evidence_budget=self.budget,
            rationale="fake rationale",
            used_llm=True,
        )


class RaisingProgram:
    def __call__(self, **kwargs: object) -> object:
        raise RuntimeError("provider is down")


# --- gating: which queries are allowed to cost money -------------------------


def test_confident_routes_never_reach_the_learned_stage() -> None:
    """58% of held-out traffic is confident and 93% accurate. It stays free."""
    program = FakeProgram()
    router = DspyRouter(program=program)  # type: ignore[arg-type]
    decision = router.route(QueryRequest(query="Summarize the themes in our architecture."))
    assert decision.mode is SearchMode.GRAPH_SUMMARY_COMPLETION
    assert program.calls == []
    assert router.llm_calls == 0


def test_unconfident_routes_do_reach_the_learned_stage() -> None:
    program = FakeProgram(mode="TRIPLET_COMPLETION")
    router = DspyRouter(program=program)  # type: ignore[arg-type]
    decision = router.route(QueryRequest(query="Which team owns the notification service?"))
    assert program.calls, "an unconfident route should consult the learned stage"
    assert decision.mode is SearchMode.TRIPLET_COMPLETION
    assert router.llm_calls == 1


def test_explicit_freshness_contract_never_reaches_the_learned_stage() -> None:
    """A caller assertion is a contract, not a classification problem."""
    program = FakeProgram()
    router = DspyRouter(program=program)  # type: ignore[arg-type]
    decision = router.route(QueryRequest(query="anything at all", freshness_required=True))
    assert decision.mode is SearchMode.TEMPORAL
    assert program.calls == []


def test_learned_decision_records_what_the_baseline_would_have_said() -> None:
    """A trace must show which stage decided, and what it overrode."""
    router = DspyRouter(program=FakeProgram(mode="GRAPH_COMPLETION"))  # type: ignore[arg-type]
    decision = router.route(QueryRequest(query="Which team owns the notification service?"))
    assert "dspy.ambiguous_band" in decision.signals
    assert any(s.startswith("dspy.baseline_was_") for s in decision.signals)
    assert decision.confident is False


# --- failure policy: an enhancement must never become a validation -----------


def test_a_raising_program_falls_back_to_the_deterministic_decision() -> None:
    router = DspyRouter(program=RaisingProgram())  # type: ignore[arg-type]
    baseline = QueryRouter().route(QueryRequest(query="Which team owns the notification service?"))
    decision = router.route(QueryRequest(query="Which team owns the notification service?"))
    assert decision.mode is baseline.mode
    assert router.fallbacks == 1
    assert router.llm_calls == 0


def test_an_invalid_mode_from_the_program_falls_back() -> None:
    router = DspyRouter(program=FakeProgram(mode="NOT_A_REAL_MODE"))  # type: ignore[arg-type]
    decision = router.route(QueryRequest(query="Which team owns the notification service?"))
    assert decision.mode in set(SearchMode)
    assert router.fallbacks == 1


def test_build_dspy_router_with_a_missing_artifact_returns_the_baseline() -> None:
    router = build_dspy_router(artifact=Path("/nonexistent/router_gepa.json"))
    assert type(router) is QueryRouter  # not a DspyRouter


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1, MIN_BUDGET), (99, MAX_BUDGET), (8, 8), ("nonsense", 8), (None, 8)],
)
def test_budget_is_clamped_so_the_optimizer_cannot_ask_for_everything(
    raw: object, expected: int
) -> None:
    assert _clamp_budget(raw) == expected


# --- the metric: pure, and the part that decides what gets optimized ---------


def test_correct_choice_scores_above_any_incorrect_one() -> None:
    correct, _ = score_and_feedback(SearchMode.CHUNKS, SearchMode.CHUNKS, query="q", rationale="r")
    wrong, _ = score_and_feedback(
        SearchMode.CHUNKS, SearchMode.GRAPH_COMPLETION, query="q", rationale="r"
    )
    assert correct > wrong


def test_metric_is_cost_aware_not_only_correctness() -> None:
    """Without this, the optimizer converges on the most expensive mode.

    The expensive mode is never *wrong*, only wasteful, and a correctness-only
    score cannot see waste.
    """
    cheap, _ = score_and_feedback(SearchMode.CHUNKS, SearchMode.CHUNKS, query="q", rationale="r")
    expensive, _ = score_and_feedback(
        SearchMode.TEMPORAL, SearchMode.TEMPORAL, query="q", rationale="r"
    )
    assert cheap > expensive, "two correct answers must not score equally regardless of cost"


def test_over_escalation_is_named_as_the_expensive_failure() -> None:
    _, feedback = score_and_feedback(
        SearchMode.CHUNKS, SearchMode.GRAPH_SUMMARY_COMPLETION, query="q", rationale="r"
    )
    assert "OVER-ESCALATED" in feedback
    assert "CHUNKS" in feedback and "GRAPH_SUMMARY_COMPLETION" in feedback


def test_under_escalation_is_named_distinctly() -> None:
    _, feedback = score_and_feedback(
        SearchMode.GRAPH_COMPLETION, SearchMode.CHUNKS, query="q", rationale="r"
    )
    assert "UNDER-ESCALATED" in feedback


def test_feedback_is_text_the_reflection_step_can_act_on() -> None:
    """GEPA passes this string to the reflection prompt verbatim."""
    _, feedback = score_and_feedback(
        SearchMode.CHUNKS,
        SearchMode.CYPHER,
        query="what port does billing expose",
        rationale="looked structural",
    )
    assert "what port does billing expose" in feedback
    assert "looked structural" in feedback
    assert len(feedback) > 80  # a caption, not a label


def test_metric_rejects_an_invalid_mode_with_the_valid_set() -> None:
    gold = dspy.Example(query="q", expected_mode="CHUNKS")
    pred = dspy.Prediction(mode="TOTALLY_INVALID", rationale="r")
    result = router_metric(gold, pred)
    assert result.score == 0.0
    assert "not a valid mode" in result.feedback
    assert "CHUNKS" in result.feedback


def test_metric_returns_a_score_and_feedback_pair() -> None:
    gold = dspy.Example(query="q", expected_mode="TRIPLET_COMPLETION")
    pred = dspy.Prediction(mode="TRIPLET_COMPLETION", rationale="one hop")
    result = router_metric(gold, pred)
    assert 0.0 < result.score <= 1.0
    assert isinstance(result.feedback, str) and result.feedback
