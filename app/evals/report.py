"""Eval report generation and acceptance gate checks.

Two principles govern this module:

1. **A gate that was not measured reports UNKNOWN, never PASS.** The previous
   gates could not fail — one compared a mode the router could not emit (0)
   against a forced baseline (20), another compared a stub's fixed evidence
   count against itself. Green ticks that are true by construction are worse
   than no gates.
2. **Quality and cost are always reported together.** Reporting either alone is
   how every unfalsifiable claim in this space gets made.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from pathlib import Path

from app.evals.models import BaselineStats, EvalSummary, GateVerdict
from app.observability.eval_trace import EvalTrace

# Required token reduction versus the single-path graph baseline. Note that a
# competent router is expected to save roughly this much; published work puts a
# lexical router at ~28% savings. Clearing this bar means the router is not
# broken — it is not on its own evidence of routing skill. That is why the gate
# is paired with an acceptance-rate gate and an oracle gap.
TOKEN_REDUCTION_TARGET = 0.25


def load_traces(run_dir: Path) -> list[EvalTrace]:
    results_path = run_dir / "results.jsonl"
    traces: list[EvalTrace] = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            traces.append(EvalTrace.model_validate_json(line))
    return traces


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    On a corpus of 20, p95 is the maximum. Small-n percentiles are reported for
    shape, not for capacity planning.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def _best_fixed_mode_accuracy(labeled: list[EvalTrace]) -> float | None:
    """Accuracy of the best possible single-mode router on this corpus.

    This is the number a router has to beat to have done anything at all. If
    70% of a corpus is labelled CHUNKS, a router that always answers CHUNKS
    scores 70% and has learned nothing.
    """
    if not labeled:
        return None
    counts = Counter(t.expected_mode for t in labeled if t.expected_mode is not None)
    return max(counts.values()) / len(labeled) if counts else None


def summarize_baseline(traces: list[EvalTrace]) -> list[BaselineStats]:
    stats: list[BaselineStats] = []
    for baseline in sorted({trace.baseline for trace in traces}):
        rows = [trace for trace in traces if trace.baseline == baseline]
        n = max(len(rows), 1)

        total_ms = [float(t.total_ms) for t in rows]
        evidence = [float(t.evidence_items) for t in rows]
        input_tokens = [float(t.input_tokens or 0) for t in rows]

        costs = [t.estimated_cost_usd for t in rows if t.estimated_cost_usd is not None]
        total_cost = sum(costs) if costs else None
        accepted_count = sum(1 for t in rows if t.accepted)

        labeled = [t for t in rows if t.expected_mode is not None]
        routing_accuracy = None
        by_bucket: dict[str, float] = {}
        if labeled:
            routing_accuracy = sum(
                1 for t in labeled if t.route_mode == t.expected_mode
            ) / len(labeled)
            per_bucket: dict[str, list[bool]] = defaultdict(list)
            for t in labeled:
                per_bucket[t.bucket].append(t.route_mode == t.expected_mode)
            by_bucket = {b: sum(v) / len(v) for b, v in sorted(per_bucket.items())}

        best_fixed = _best_fixed_mode_accuracy(labeled)

        stats.append(
            BaselineStats(
                baseline=baseline,
                query_count=len(rows),
                accepted_rate=accepted_count / n,
                cache_hit_rate=sum(1 for t in rows if t.cache_hit) / n,
                median_total_ms=statistics.median(total_ms) if total_ms else 0.0,
                p95_total_ms=_percentile(total_ms, 95),
                median_evidence_items=statistics.median(evidence) if evidence else 0.0,
                mode_distribution=dict(Counter(t.route_mode.value for t in rows)),
                median_input_tokens=statistics.median(input_tokens) if input_tokens else 0.0,
                total_input_tokens=int(sum(input_tokens)),
                total_output_tokens=int(sum(t.output_tokens or 0 for t in rows)),
                total_estimated_cost_usd=total_cost,
                cost_per_accepted_answer_usd=(
                    total_cost / accepted_count if total_cost is not None and accepted_count else None
                ),
                escalation_rate=sum(1 for t in rows if t.retry_count > 0) / n,
                fail_closed_rate=sum(1 for t in rows if not t.accepted) / n,
                routing_accuracy=routing_accuracy,
                best_fixed_mode_accuracy=best_fixed,
                oracle_gap=(1.0 - routing_accuracy) if routing_accuracy is not None else None,
                routing_accuracy_by_bucket=by_bucket,
                degraded_retrieval_rate=sum(1 for t in rows if t.extra.get("degraded")) / n,
                unsynthesized_rate=sum(1 for t in rows if t.extra.get("unsynthesized")) / n,
                cost_is_measured=any(t.extra.get("cost_is_estimate") is False for t in rows),
            )
        )
    return stats


def collect_validity_warnings(stats: list[BaselineStats]) -> list[str]:
    """Conditions that make a run's numbers non-representative.

    These are printed above the results, not buried in a footnote. A reader who
    takes a latency number from a stub run and puts it in a deck is the failure
    mode this exists to prevent.
    """
    warnings: list[str] = []
    if any(s.degraded_retrieval_rate > 0 for s in stats):
        worst = max(s.degraded_retrieval_rate for s in stats)
        warnings.append(
            f"Retrieval was degraded (stub/fallback) on up to {worst:.0%} of queries. "
            f"Latency, evidence counts, and quality from this run describe the stub, "
            f"not Cognee. Do not quote them."
        )
    if any(s.unsynthesized_rate > 0 for s in stats):
        warnings.append(
            "No LLM provider was configured for at least some queries; answers are "
            "local templates. Answer quality is not measured by this run."
        )
    if not any(s.cost_is_measured for s in stats):
        warnings.append(
            "No provider-reported token usage was seen. All cost figures are local "
            "estimates from a chars-per-token heuristic, not measurements."
        )
    if all(s.query_count < 30 for s in stats):
        warnings.append(
            f"Corpus is small (n={max(s.query_count for s in stats)} per baseline). "
            f"Per-bucket rates rest on a handful of queries each; treat differences "
            f"as directional, not significant."
        )
    return warnings


def compute_acceptance_gates(stats: list[BaselineStats]) -> dict[str, GateVerdict]:
    """Evaluate the gates from docs/evaluation-plan.md.

    Every gate is a comparison Roubaix could lose.
    """
    by_name = {item.baseline: item for item in stats}
    gates: dict[str, GateVerdict] = {}

    rules = by_name.get("roubaix_rules")
    graph = by_name.get("graph_only")
    full = by_name.get("full_context")

    def verdict(condition: bool | None) -> GateVerdict:
        if condition is None:
            return GateVerdict.UNKNOWN
        return GateVerdict.PASS if condition else GateVerdict.FAIL

    # The central cost claim: fewer input tokens than paying for graph depth
    # on every query.
    if rules and graph and graph.median_input_tokens > 0:
        reduction = 1.0 - (rules.median_input_tokens / graph.median_input_tokens)
        gates["input_tokens_25pct_below_graph_only"] = verdict(
            reduction >= TOKEN_REDUCTION_TARGET
        )
    else:
        gates["input_tokens_25pct_below_graph_only"] = GateVerdict.UNKNOWN

    # Cost saving is only a win if quality did not pay for it. Without an
    # answer-correctness signal, acceptance rate is the weakest possible proxy
    # — so this gate is necessary but nowhere near sufficient.
    gates["acceptance_not_worse_than_graph_only"] = (
        verdict(rules.accepted_rate >= graph.accepted_rate) if rules and graph else GateVerdict.UNKNOWN
    )

    # A router that cannot beat the best single fixed mode has not earned its
    # complexity.
    if rules and rules.routing_accuracy is not None and rules.best_fixed_mode_accuracy is not None:
        gates["routing_beats_best_fixed_mode"] = verdict(
            rules.routing_accuracy > rules.best_fixed_mode_accuracy
        )
    else:
        gates["routing_beats_best_fixed_mode"] = GateVerdict.UNKNOWN

    # The baseline most likely to embarrass a graph system: send everything.
    if rules and full and full.median_input_tokens > 0:
        gates["cheaper_than_full_context"] = verdict(
            rules.median_input_tokens < full.median_input_tokens
        )
    else:
        gates["cheaper_than_full_context"] = GateVerdict.UNKNOWN

    # Cost gates are meaningless on estimated tokens; say so rather than
    # passing them.
    if rules and not rules.cost_is_measured:
        gates["cost_figures_are_measured"] = GateVerdict.UNKNOWN

    return gates


def generate_report(run_dir: Path) -> str:
    """Return markdown report for an eval run directory."""
    summary_path = run_dir / "summary.json"
    if summary_path.is_file():
        summary = EvalSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
        stats, gates = summary.baseline_stats, summary.acceptance_gates
        run_id, corpus_path = summary.run_id, summary.corpus_path
        warnings = summary.validity_warnings
    else:
        traces = load_traces(run_dir)
        stats = summarize_baseline(traces)
        gates = compute_acceptance_gates(stats)
        run_id, corpus_path = run_dir.name, "unknown"
        warnings = collect_validity_warnings(stats)

    lines = [f"# Roubaix eval report — `{run_id}`", "", f"Corpus: `{corpus_path}`", ""]

    if warnings:
        lines.extend(["## Run validity", ""])
        lines.extend(f"- **{w}**" for w in warnings)
        lines.append("")

    lines.extend(
        [
            "## Baseline summary",
            "",
            "| Baseline | Queries | Accepted | Median ms | p95 ms | Median in-tok | Cost/accepted | Escalated | Routing acc |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in stats:
        routing = "—" if item.routing_accuracy is None else f"{item.routing_accuracy:.0%}"
        cost = (
            "—"
            if item.cost_per_accepted_answer_usd is None
            else f"${item.cost_per_accepted_answer_usd:.6f}"
        )
        lines.append(
            f"| {item.baseline} | {item.query_count} | {item.accepted_rate:.0%} | "
            f"{item.median_total_ms:.0f} | {item.p95_total_ms:.0f} | "
            f"{item.median_input_tokens:.0f} | {cost} | "
            f"{item.escalation_rate:.0%} | {routing} |"
        )

    lines.extend(["", "## Routing quality in context", ""])
    for item in stats:
        if item.routing_accuracy is None:
            continue
        best_fixed = (
            "—"
            if item.best_fixed_mode_accuracy is None
            else f"{item.best_fixed_mode_accuracy:.0%}"
        )
        lines.append(
            f"- **{item.baseline}**: {item.routing_accuracy:.0%} accuracy "
            f"(best single fixed mode on this corpus: {best_fixed}; "
            f"gap to oracle: {item.oracle_gap:.0%})"
            if item.oracle_gap is not None
            else f"- **{item.baseline}**: {item.routing_accuracy:.0%}"
        )
        for bucket, acc in item.routing_accuracy_by_bucket.items():
            lines.append(f"  - `{bucket}`: {acc:.0%}")
    lines.append("")

    lines.extend(["## Mode distribution", ""])
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
        for gate, result in sorted(gates.items()):
            value = result.value if hasattr(result, "value") else str(result)
            lines.append(f"- **{gate}**: {value}")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `results.jsonl` is the source of truth for this run.",
            "- `UNKNOWN` means the run could not establish the claim either way. "
            "It is not a pass.",
            "- Routing accuracy is meaningful only against a corpus the router was "
            "not tuned on. Use `evals/queries_heldout.jsonl` for that number and "
            "label which corpus produced it.",
            "- A 25% token reduction is roughly what any competent router achieves; "
            "clearing that gate shows the router is not broken, not that it is good.",
            "",
        ]
    )
    report = "\n".join(lines)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    return report
