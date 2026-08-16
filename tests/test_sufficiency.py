"""Tests for the set-level sufficiency gate.

The plan's acceptance gates (docs/implementation-plan.md, Phase A) are encoded
here directly: on evidence deliberately mismatched to the query the gate must
flag at least 80% as non-sufficient, and on well-matched evidence the
false-INSUFFICIENT rate must be at most 10%. Both run against the held-out
corpus queries so the numbers are not tuned on the same text as the gate's
thresholds — and both are losable.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import PackedEvidence, QueryRequest, SearchMode
from app.services.sufficiency import (
    SufficiencyGate,
    SufficiencyVerdict,
    build_gate,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HELDOUT = REPO_ROOT / "evals" / "queries_heldout.jsonl"


def _packed(items: list[str]) -> PackedEvidence:
    return PackedEvidence(
        mode=SearchMode.CHUNKS,
        summary="\n".join(items),
        evidence_items=items,
        provenance=[{"dataset": "default", "i": i} for i in range(len(items))],
    )


def _heldout_queries() -> list[str]:
    rows = [
        json.loads(line)
        for line in HELDOUT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [row["query"] for row in rows]


def _matched_evidence(query: str) -> list[str]:
    """Evidence that restates the query's content terms — the well-matched case."""
    return [
        f"Regarding this: {query.rstrip('?').lower()} — the recorded answer is X.",
        "See also X.",
    ]


# --- verdict basics ----------------------------------------------------------


def test_empty_set_is_insufficient() -> None:
    result = SufficiencyGate().check(
        QueryRequest(query="what port does billing expose"), _packed([])
    )
    assert result.verdict is SufficiencyVerdict.INSUFFICIENT
    assert "sufficiency.empty_set" in result.signals


def test_full_coverage_is_sufficient() -> None:
    result = SufficiencyGate().check(
        QueryRequest(query="what port does billing expose"),
        _packed(["billing exposes port 8443 to internal callers"]),
    )
    assert result.verdict is SufficiencyVerdict.SUFFICIENT
    assert result.coverage == 1.0
    assert result.tier == 0


def test_off_topic_set_is_insufficient_regardless_of_volume() -> None:
    """The case the count heuristic structurally cannot see."""
    result = SufficiencyGate().check(
        QueryRequest(query="what port does billing expose"),
        _packed([f"the weather in Ghent on day {i} was mild" for i in range(12)]),
    )
    assert result.verdict is SufficiencyVerdict.INSUFFICIENT
    assert "sufficiency.off_topic_set" in result.signals


def test_coverage_is_set_level_not_per_item() -> None:
    """A multi-hop answer split across a bridge must not be penalized.

    Neither item alone covers the query; the union does. This is the exact
    shape a per-item scorer gets wrong — it cannot observe that the two items
    together complete the chain.
    """
    gate = SufficiencyGate()
    query = QueryRequest(query="does billing depend on the warehouse")
    both = gate.check(
        query,
        _packed(["billing depends on the ingest API", "the ingest API writes to the warehouse"]),
    )
    assert both.coverage == 1.0
    assert both.verdict is SufficiencyVerdict.SUFFICIENT
    # Either half alone is partial — the set is what is sufficient.
    first_alone = gate.check(query, _packed(["billing depends on the ingest API"]))
    assert first_alone.coverage < 1.0


def test_morphological_variants_count_as_coverage() -> None:
    """ "expose" must match "exposes" — token-exact matching without stemming
    systematically under-covers natural phrasing."""
    result = SufficiencyGate().check(
        QueryRequest(query="what port does billing expose"),
        _packed(["billing exposes port 8443"]),
    )
    assert result.coverage == 1.0


def test_partial_coverage_lands_in_the_uncertain_band() -> None:
    result = SufficiencyGate().check(
        QueryRequest(query="billing warehouse port timeout"),
        _packed(["billing was mentioned once in passing"]),
    )
    assert result.verdict is SufficiencyVerdict.UNCERTAIN


def test_no_content_terms_is_uncertainty_not_a_verdict() -> None:
    result = SufficiencyGate().check(QueryRequest(query="what is it"), _packed(["some text"]))
    assert result.verdict is SufficiencyVerdict.UNCERTAIN
    assert "sufficiency.no_query_terms" in result.signals


def test_signals_carry_the_evidence_for_the_verdict() -> None:
    """A verdict that cannot be explained from telemetry cannot be tuned."""
    result = SufficiencyGate().check(
        QueryRequest(query="what port does billing expose"),
        _packed(["billing exposes port 8443"]),
    )
    rendered = result.as_signals()
    assert "sufficiency.sufficient" in rendered
    assert any(s.startswith("sufficiency.tier") for s in rendered)
    assert any(s.startswith("sufficiency.coverage_") for s in rendered)


# --- tier 1 ------------------------------------------------------------------


class FakeScorer:
    def __init__(self, probs: list[float]) -> None:
        self.probs = probs
        self.calls = 0

    def score(self, query: str, items: list[str]) -> list[float]:
        self.calls += 1
        return self.probs[: len(items)]


class RaisingScorer:
    def score(self, query: str, items: list[str]) -> list[float]:
        raise RuntimeError("model not downloaded")


def test_tier1_runs_only_on_the_uncertain_band() -> None:
    scorer = FakeScorer([0.9, 0.9])
    gate = SufficiencyGate(support_scorer=scorer)
    # Clear-cut sufficient: tier 1 must not be consulted.
    gate.check(
        QueryRequest(query="what port does billing expose"),
        _packed(["billing exposes port 8443"]),
    )
    assert scorer.calls == 0
    # Uncertain band (1 of 4 terms covered): tier 1 refines.
    result = gate.check(
        QueryRequest(query="billing warehouse port timeout"),
        _packed(["billing was mentioned once", "unrelated filler text"]),
    )
    assert scorer.calls == 1
    assert result.tier == 1
    assert result.verdict is SufficiencyVerdict.SUFFICIENT


def test_tier1_low_support_downgrades_to_insufficient() -> None:
    gate = SufficiencyGate(support_scorer=FakeScorer([0.1, 0.1]))
    result = gate.check(
        QueryRequest(query="billing warehouse port timeout"),
        _packed(["billing was mentioned once", "unrelated filler text"]),
    )
    assert result.verdict is SufficiencyVerdict.INSUFFICIENT
    assert result.tier == 1


def test_a_raising_scorer_latches_off_and_leaves_uncertain() -> None:
    """The gate is an enhancement: a broken tier-1 must not change verdicts,
    and must not be retried on every request."""
    scorer = RaisingScorer()
    gate = SufficiencyGate(support_scorer=scorer)
    r1 = gate.check(
        QueryRequest(query="billing warehouse port timeout"),
        _packed(["billing was mentioned once", "unrelated filler text"]),
    )
    assert r1.verdict is SufficiencyVerdict.UNCERTAIN
    assert gate._scorer_failed is True


def test_build_gate_without_verify_extra_is_tier0_only() -> None:
    gate = build_gate()
    assert gate._scorer is None  # tier1 disabled by default in config


# --- the plan's acceptance gates, encoded and losable ------------------------


def test_acceptance_gate_mismatched_evidence_is_flagged() -> None:
    """Plan Phase A: >=80% of deliberately mismatched evidence sets must be
    flagged non-sufficient across the held-out queries."""
    gate = SufficiencyGate()
    queries = _heldout_queries()
    decoy = [
        "the cafeteria menu on Tuesday featured lentil soup",
        "annual leave requests are handled by the HR portal",
        "the office plants are watered every Friday",
    ]
    flagged = sum(
        1
        for q in queries
        if gate.check(QueryRequest(query=q), _packed(decoy)).verdict
        is not SufficiencyVerdict.SUFFICIENT
    )
    rate = flagged / len(queries)
    assert rate >= 0.80, f"only {rate:.0%} of mismatched sets were flagged"


def test_acceptance_gate_matched_evidence_is_not_rejected() -> None:
    """Plan Phase A: false-INSUFFICIENT on well-matched evidence <=10%."""
    gate = SufficiencyGate()
    queries = _heldout_queries()
    false_insufficient = sum(
        1
        for q in queries
        if gate.check(QueryRequest(query=q), _packed(_matched_evidence(q))).verdict
        is SufficiencyVerdict.INSUFFICIENT
    )
    rate = false_insufficient / len(queries)
    assert rate <= 0.10, f"{rate:.0%} of matched sets were wrongly rejected"
