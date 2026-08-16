"""Regression gate on NodeSet scoping precision (Phase C1 acceptance gate).

The gate from docs/implementation-plan.md: derived NodeSets ≥70% precision
against hand labels, and a caller-supplied scope is never narrowed. Current
precision is 92%; the threshold sits at the plan's 70% so genuine regressions
fail while corpus maintenance does not require touching it.

The corpus deliberately contains rows the matcher cannot get right
(sc-trap-003/004 misfire, sc-recall-001/002 miss), so neither metric can be
driven to 100% without changing the mechanism — that is what keeps this a
measurement rather than a restatement.
"""

from __future__ import annotations

from pathlib import Path

from app.evals.scoping_eval import evaluate_scoping

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "evals" / "queries_scoping.jsonl"
INDEX = REPO_ROOT / "evals" / "nodesets_eval.json"

MIN_PRECISION = 0.70  # the plan's acceptance gate, verbatim


def test_scoping_precision_meets_the_acceptance_gate() -> None:
    report = evaluate_scoping(CORPUS, INDEX)
    assert report.precision >= MIN_PRECISION, (
        f"scoping precision {report.precision:.0%} fell below {MIN_PRECISION:.0%}. Misfires: "
        + "; ".join(f"{m.query_id}: derived {m.derived}" for m in report.misfires)
    )


def test_caller_scope_is_never_narrowed() -> None:
    """The invariant half of the gate — a hard property, not a threshold."""
    assert evaluate_scoping(CORPUS, INDEX).caller_scope_preserved is True


def test_known_weaknesses_stay_documented_not_fixed_by_labels() -> None:
    """The trap and recall rows must keep failing until the *mechanism* improves.

    If a future edit makes these pass by relabelling them (rather than by C2's
    learned scorer or a better matcher), the corpus has been tuned into a
    restatement — this test makes that visible in review.
    """
    report = evaluate_scoping(CORPUS, INDEX)
    misfire_ids = {m.query_id for m in report.misfires}
    miss_ids = {m.query_id for m in report.misses}
    assert {"sc-trap-003", "sc-trap-004"} <= misfire_ids
    assert {"sc-recall-001", "sc-recall-002"} <= miss_ids
