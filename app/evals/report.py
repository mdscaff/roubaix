"""Eval report generation and acceptance gate checks."""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path

from app.domain.models import SearchMode
from app.evals.models import BaselineStats, EvalSummary
from app.observability.eval_trace import EvalTrace


def load_traces(run_dir: Path) -> list[EvalTrace]:
    results_path = run_dir / "results.jsonl"
    traces: list[EvalTrace] = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            traces.append(EvalTrace.model_validate_json(line))
    return traces


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def summarize_baseline(traces: list[EvalTrace]) -> list[BaselineStats]:
    stats: list[BaselineStats] = []
    baselines = sorted({trace.baseline for trace in traces})
    for baseline in baselines:
        rows = [trace for trace in traces if trace.baseline == baseline]
        total_ms = [float(trace.total_ms) for trace in rows]
        evidence = [float(trace.evidence_items) for trace in rows]
        mode_distribution = Counter(trace.route_mode.value for trace in rows)
        labeled = [trace for trace in rows if trace.expected_mode is not None]
        routing_accuracy = None
        if labeled:
            correct = sum(1 for trace in labeled if trace.route_mode == trace.expected_mode)
            routing_accuracy = correct / len(labeled)

        stats.append(
            BaselineStats(
                baseline=baseline,
                query_count=len(rows),
                accepted_rate=sum(1 for trace in rows if trace.accepted) / max(len(rows), 1),
                cache_hit_rate=sum(1 for trace in rows if trace.cache_hit) / max(len(rows), 1),
                median_total_ms=statistics.median(total_ms) if total_ms else 0.0,
                p95_total_ms=_percentile(total_ms, 95),
                median_evidence_items=statistics.median(evidence) if evidence else 0.0,
                mode_distribution=dict(mode_distribution),
                routing_accuracy=routing_accuracy,
            )
        )
    return stats


def compute_acceptance_gates(stats: list[BaselineStats]) -> dict[str, bool]:
    """Evaluate gates from docs/evaluation-plan.md where data allows."""
    by_name = {item.baseline: item for item in stats}
    gates: dict[str, bool] = {}

    rules = by_name.get("roubaix_rules")
    graph = by_name.get("graph_only")
    if rules and graph:
        gates["roubaix_median_evidence_lte_graph"] = (
            rules.median_evidence_items <= graph.median_evidence_items
        )
        graph_expensive_modes = graph.mode_distribution.get(SearchMode.GRAPH_COMPLETION.value, 0)
        rules_expensive_modes = rules.mode_distribution.get(SearchMode.GRAPH_COMPLETION.value, 0)
        gates["roubaix_uses_graph_mode_less_than_graph_only"] = (
            rules_expensive_modes < graph_expensive_modes
        )

    if rules and rules.routing_accuracy is not None:
        gates["roubaix_routing_accuracy_gte_70pct"] = rules.routing_accuracy >= 0.7

    return gates


def generate_report(run_dir: Path) -> str:
    """Return markdown report for an eval run directory."""
    summary_path = run_dir / "summary.json"
    if summary_path.is_file():
        summary = EvalSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
        stats = summary.baseline_stats
        gates = summary.acceptance_gates
        run_id = summary.run_id
        corpus_path = summary.corpus_path
    else:
        traces = load_traces(run_dir)
        stats = summarize_baseline(traces)
        gates = compute_acceptance_gates(stats)
        run_id = run_dir.name
        corpus_path = "unknown"

    lines = [
        f"# Roubaix eval report — `{run_id}`",
        "",
        f"Corpus: `{corpus_path}`",
        "",
        "## Baseline summary",
        "",
        "| Baseline | Queries | Accepted | Cache hit | Median ms | p95 ms | Median evidence | Routing acc |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for item in stats:
        routing = "—" if item.routing_accuracy is None else f"{item.routing_accuracy:.0%}"
        lines.append(
            f"| {item.baseline} | {item.query_count} | {item.accepted_rate:.0%} | "
            f"{item.cache_hit_rate:.0%} | {item.median_total_ms:.0f} | {item.p95_total_ms:.0f} | "
            f"{item.median_evidence_items:.1f} | {routing} |"
        )

    lines.extend(["", "## Mode distribution", ""])
    for item in stats:
        lines.append(f"### {item.baseline}")
        if not item.mode_distribution:
            lines.append("- (no data)")
        else:
            for mode, count in sorted(item.mode_distribution.items()):
                lines.append(f"- `{mode}`: {count}")
        lines.append("")

    lines.extend(["## Acceptance gates", ""])
    if not gates:
        lines.append("- No gates evaluated for this run.")
    else:
        for gate, passed in sorted(gates.items()):
            status = "PASS" if passed else "FAIL"
            lines.append(f"- **{gate}**: {status}")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `results.jsonl` is the source of truth for this run.",
            "- Langfuse traces (if enabled) mirror synthesis spans for debugging only.",
            "- Token and cost gates activate once real synthesis telemetry is wired.",
            "",
        ]
    )
    report = "\n".join(lines)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    return report
