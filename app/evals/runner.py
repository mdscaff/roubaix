"""Offline eval runner — executes corpus queries across baselines."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable

from app.domain.models import AnswerResult, QueryRequest
from app.evals.baselines import Baseline, create_orchestrator
from app.evals.models import EvalQuery, EvalSummary
from app.evals.report import compute_acceptance_gates, summarize_baseline
from app.observability.eval_trace import EvalRunContext, EvalTrace, eval_run_context


def load_corpus(path: Path) -> list[EvalQuery]:
    queries: list[EvalQuery] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        queries.append(EvalQuery.model_validate_json(line))
    return queries


def _trace_from_answer(
    *,
    ctx: EvalRunContext,
    eval_query: EvalQuery,
    result: AnswerResult,
    total_ms: int,
) -> EvalTrace:
    telemetry = result.telemetry
    return EvalTrace(
        run_id=ctx.run_id,
        baseline=ctx.baseline,
        query_id=ctx.query_id,
        bucket=ctx.bucket,
        query=eval_query.query,
        dataset=ctx.dataset,
        route_mode=result.route.mode,
        route_rationale=result.route.rationale,
        node_sets=result.route.node_sets,
        evidence_budget=result.route.evidence_budget,
        requires_freshness_validation=result.route.requires_freshness_validation,
        expected_mode=eval_query.expected_mode,
        evidence_items=telemetry.get("evidence_items", 0),
        retrieval_ms=telemetry.get("retrieval_ms", 0),
        synthesis_ms=telemetry.get("synthesis_ms", 0),
        total_ms=telemetry.get("total_ms", total_ms),
        input_tokens=telemetry.get("input_tokens"),
        output_tokens=telemetry.get("output_tokens"),
        estimated_cost_usd=telemetry.get("estimated_cost_usd"),
        retry_count=result.retry_count,
        escalation_reason=telemetry.get("escalation_reason"),
        cache_hit=result.cache_hit,
        accepted=result.accepted,
        pggraph_extension=telemetry.get("pggraph_extension"),
        answer=result.answer,
    )


async def run_eval(
    *,
    corpus_path: Path,
    output_dir: Path,
    baselines: Iterable[Baseline] | None = None,
    run_id: str | None = None,
) -> EvalSummary:
    """Run all corpus queries for each baseline and write JSONL artifacts."""
    started_at = datetime.now(UTC)
    run_id = run_id or started_at.strftime("run-%Y%m%dT%H%M%SZ")
    output_dir = output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus = load_corpus(corpus_path)
    selected = list(baselines or Baseline)
    traces: list[EvalTrace] = []

    for baseline in selected:
        orchestrator = create_orchestrator(baseline)
        for eval_query in corpus:
            ctx = EvalRunContext(
                run_id=run_id,
                baseline=baseline.value,
                query_id=eval_query.query_id,
                bucket=eval_query.bucket,
                dataset=eval_query.dataset,
            )
            token = eval_run_context.set(ctx)
            total_start = perf_counter()
            try:
                result = await orchestrator.answer(
                    QueryRequest(
                        query=eval_query.query,
                        dataset=eval_query.dataset,
                        freshness_required=eval_query.freshness_required,
                    )
                )
            finally:
                eval_run_context.reset(token)

            total_ms = int((perf_counter() - total_start) * 1000)
            traces.append(
                _trace_from_answer(
                    ctx=ctx,
                    eval_query=eval_query,
                    result=result,
                    total_ms=total_ms,
                )
            )

    results_path = output_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as handle:
        for trace in traces:
            handle.write(trace.model_dump_json())
            handle.write("\n")

    baseline_stats = summarize_baseline(traces)
    finished_at = datetime.now(UTC)
    summary = EvalSummary(
        run_id=run_id,
        corpus_path=str(corpus_path),
        baselines=[b.value for b in selected],
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        baseline_stats=baseline_stats,
        acceptance_gates=compute_acceptance_gates(baseline_stats),
    )
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary.model_dump(), indent=2), encoding="utf-8")
    return summary
