"""Regression gate on routing quality.

The held-out corpus (`evals/queries_heldout.jsonl`) was written without
reference to the router's patterns and has deliberately **not** been tuned
against. Its four known misses are documented in `docs/evaluation-plan.md`.
Tuning the rules until this file scores 100% would convert the only unbiased
measurement in the repo into a restatement of the rules, so the threshold here
is set below the current score to catch regressions without inviting overfit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.evals.routing_eval import evaluate_routing

REPO_ROOT = Path(__file__).resolve().parents[1]
TUNING = REPO_ROOT / "evals" / "queries.jsonl"
HELDOUT = REPO_ROOT / "evals" / "queries_heldout.jsonl"

# Current held-out accuracy is 85%. The gate sits below that so a genuine
# regression fails while normal rule maintenance does not require touching it.
MIN_HELDOUT_ACCURACY = 0.80


def test_router_is_perfect_on_the_corpus_it_was_tuned_against() -> None:
    """An upper bound, not a measurement — asserted only to catch breakage."""
    assert evaluate_routing(TUNING).accuracy == 1.0


def test_router_holds_up_on_unseen_queries() -> None:
    report = evaluate_routing(HELDOUT)
    assert report.accuracy >= MIN_HELDOUT_ACCURACY, (
        f"held-out routing accuracy {report.accuracy:.0%} fell below "
        f"{MIN_HELDOUT_ACCURACY:.0%}. Misses: "
        + "; ".join(f"{m.query_id}: {m.expected}->{m.actual}" for m in report.misses)
    )


def test_router_beats_the_best_single_fixed_mode_on_unseen_queries() -> None:
    """A router that cannot beat 'always pick the commonest mode' is decoration."""
    report = evaluate_routing(HELDOUT)
    assert report.lift_over_best_fixed > 0.2


def test_generalisation_gap_is_reported_not_hidden() -> None:
    """Tuning-set accuracy must exceed held-out accuracy, or the split is wrong."""
    tuning = evaluate_routing(TUNING)
    heldout = evaluate_routing(HELDOUT)
    assert tuning.accuracy >= heldout.accuracy


@pytest.mark.parametrize("corpus", [TUNING, HELDOUT])
def test_every_miss_carries_the_signals_that_caused_it(corpus: Path) -> None:
    """A miss you cannot explain is a miss you cannot fix."""
    report = evaluate_routing(corpus)
    for miss in report.misses:
        assert miss.expected != miss.actual
        assert isinstance(miss.signals, list)  # present even when empty


def test_confidence_flag_is_informative() -> None:
    """Confident routes should be more accurate than the overall population."""
    report = evaluate_routing(HELDOUT)
    assert report.confident_accuracy is not None
    assert report.confident_accuracy >= report.accuracy
