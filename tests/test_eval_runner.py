"""Tests for offline eval harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models import QueryRequest, SearchMode
from app.evals.baselines import Baseline, create_orchestrator, router_for_baseline
from app.evals.models import EvalQuery
from app.evals.report import compute_acceptance_gates, generate_report
from app.evals.runner import load_corpus, run_eval


@pytest.fixture
def mini_corpus(tmp_path: Path) -> Path:
    rows = [
        EvalQuery(
            query_id="fact-001",
            bucket="local_factual",
            query="What port does the billing service expose?",
            expected_mode=SearchMode.CHUNKS,
        ),
        EvalQuery(
            query_id="rel-001",
            bucket="relationship-heavy",
            query="How is billing connected to the data warehouse?",
            expected_mode=SearchMode.TRIPLET_COMPLETION,
        ),
    ]
    path = tmp_path / "queries.jsonl"
    path.write_text("\n".join(row.model_dump_json() for row in rows) + "\n", encoding="utf-8")
    return path


def test_forced_mode_router_baselines() -> None:
    chunks = router_for_baseline(Baseline.CHUNKS_ONLY)
    graph = router_for_baseline(Baseline.GRAPH_ONLY)
    request = QueryRequest(query="How is billing connected to the warehouse?")
    assert chunks.route(request).mode == SearchMode.CHUNKS
    assert graph.route(request).mode == SearchMode.GRAPH_COMPLETION


def test_load_corpus_skips_blank_lines(tmp_path: Path, mini_corpus: Path) -> None:
    content = mini_corpus.read_text(encoding="utf-8")
    mini_corpus.write_text("# comment\n\n" + content, encoding="utf-8")
    loaded = load_corpus(mini_corpus)
    assert len(loaded) == 2


@pytest.mark.asyncio
async def test_run_eval_writes_artifacts(tmp_path: Path, mini_corpus: Path) -> None:
    summary = await run_eval(
        corpus_path=mini_corpus,
        output_dir=tmp_path,
        baselines=[Baseline.ROUBAIX_RULES, Baseline.CHUNKS_ONLY],
        run_id="test-run",
    )
    run_dir = tmp_path / "test-run"
    assert (run_dir / "results.jsonl").is_file()
    assert (run_dir / "summary.json").is_file()
    assert summary.run_id == "test-run"
    assert len(summary.baseline_stats) == 2

    lines = (run_dir / "results.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4


@pytest.mark.asyncio
async def test_generate_report_from_run(tmp_path: Path, mini_corpus: Path) -> None:
    await run_eval(
        corpus_path=mini_corpus,
        output_dir=tmp_path,
        baselines=[Baseline.ROUBAIX_RULES],
        run_id="report-run",
    )
    run_dir = tmp_path / "report-run"
    report = generate_report(run_dir)
    assert "# Roubaix eval report" in report
    assert (run_dir / "report.md").is_file()


def test_acceptance_gates_use_baseline_comparison() -> None:
    from app.evals.models import BaselineStats, GateVerdict

    stats = [
        BaselineStats(
            baseline="roubaix_rules",
            query_count=10,
            accepted_rate=1.0,
            cache_hit_rate=0.0,
            median_total_ms=10,
            p95_total_ms=20,
            median_evidence_items=1.0,
            mode_distribution={"CHUNKS": 8, "TRIPLET_COMPLETION": 2},
            median_input_tokens=600.0,
            routing_accuracy=0.8,
            best_fixed_mode_accuracy=0.5,
            cost_is_measured=True,
        ),
        BaselineStats(
            baseline="graph_only",
            query_count=10,
            accepted_rate=1.0,
            cache_hit_rate=0.0,
            median_total_ms=30,
            p95_total_ms=40,
            median_evidence_items=1.0,
            mode_distribution={"GRAPH_COMPLETION": 10},
            median_input_tokens=1000.0,
        ),
        BaselineStats(
            baseline="full_context",
            query_count=10,
            accepted_rate=1.0,
            cache_hit_rate=0.0,
            median_total_ms=50,
            p95_total_ms=60,
            median_evidence_items=12.0,
            mode_distribution={"CHUNKS": 10},
            median_input_tokens=4000.0,
        ),
    ]
    gates = compute_acceptance_gates(stats)
    # 600 vs 1000 median input tokens = 40% reduction, clears the 25% target.
    assert gates["input_tokens_25pct_below_graph_only"] is GateVerdict.PASS
    assert gates["acceptance_not_worse_than_graph_only"] is GateVerdict.PASS
    assert gates["routing_beats_best_fixed_mode"] is GateVerdict.PASS
    assert gates["cheaper_than_full_context"] is GateVerdict.PASS


def test_token_gate_fails_when_reduction_is_insufficient() -> None:
    """The central cost gate must be losable."""
    from app.evals.models import BaselineStats, GateVerdict

    stats = [
        BaselineStats(
            baseline="roubaix_rules",
            query_count=10,
            accepted_rate=1.0,
            cache_hit_rate=0.0,
            median_total_ms=10,
            p95_total_ms=20,
            median_evidence_items=1.0,
            median_input_tokens=950.0,  # only 5% below graph_only
            cost_is_measured=True,
        ),
        BaselineStats(
            baseline="graph_only",
            query_count=10,
            accepted_rate=1.0,
            cache_hit_rate=0.0,
            median_total_ms=30,
            p95_total_ms=40,
            median_evidence_items=1.0,
            median_input_tokens=1000.0,
        ),
    ]
    assert (
        compute_acceptance_gates(stats)["input_tokens_25pct_below_graph_only"] is GateVerdict.FAIL
    )


def test_gates_report_unknown_when_the_comparison_baseline_is_absent() -> None:
    """An unmeasured claim must never read as a pass."""
    from app.evals.models import BaselineStats, GateVerdict

    stats = [
        BaselineStats(
            baseline="roubaix_rules",
            query_count=10,
            accepted_rate=1.0,
            cache_hit_rate=0.0,
            median_total_ms=10,
            p95_total_ms=20,
            median_evidence_items=1.0,
            median_input_tokens=600.0,
        )
    ]
    gates = compute_acceptance_gates(stats)
    assert gates["input_tokens_25pct_below_graph_only"] is GateVerdict.UNKNOWN
    assert gates["cheaper_than_full_context"] is GateVerdict.UNKNOWN
    # No provider-reported usage was seen, so cost figures are not measured.
    assert gates["cost_figures_are_measured"] is GateVerdict.UNKNOWN


def test_create_orchestrator_is_independent_per_baseline() -> None:
    orch_a = create_orchestrator(Baseline.CHUNKS_ONLY)
    orch_b = create_orchestrator(Baseline.ROUBAIX_RULES)
    assert orch_a.cache is not orch_b.cache


def test_dspy_baseline_is_not_run_by_default() -> None:
    """An eval run must not silently start calling (and paying for) an LLM."""
    from app.evals.baselines import DEFAULT_BASELINES

    assert Baseline.DSPY_ROUTER not in DEFAULT_BASELINES
    assert Baseline.FULL_CONTEXT in DEFAULT_BASELINES


def test_dspy_baseline_never_silently_becomes_the_deterministic_router() -> None:
    """A measurement that cannot be made must not become a different measurement.

    Everywhere else a DSPy failure degrades quietly to the baseline, because
    answering matters more than optimising. Here the opposite holds: an eval row
    labelled `dspy_router` that actually measured the deterministic router would
    report a comparison that never happened.
    """
    from app.evals import baselines as baselines_module

    original = baselines_module._dspy_router

    def unavailable() -> object:
        raise RuntimeError("Baseline 'dspy_router' requires the `opt` extra")

    baselines_module._dspy_router = unavailable  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="requires the `opt` extra"):
            router_for_baseline(Baseline.DSPY_ROUTER)
    finally:
        baselines_module._dspy_router = original  # type: ignore[assignment]
