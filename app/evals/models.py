"""Pydantic models for eval corpus rows and run summaries."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.domain.models import SearchMode


class EvalQuery(BaseModel):
    query_id: str
    bucket: str
    query: str
    dataset: str = "default"
    freshness_required: bool = False
    expected_mode: SearchMode | None = None
    notes: str | None = None


class GateVerdict(str, Enum):
    """A gate that was not measured must never read as a pass.

    UNKNOWN is a distinct outcome from FAIL: it means the run could not
    establish the fact either way, usually because the comparison baseline was
    not run or because a required signal (real cost, live retrieval) was absent.
    Collapsing UNKNOWN into PASS is how an unmeasured claim becomes a green tick.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class BaselineStats(BaseModel):
    baseline: str
    query_count: int
    accepted_rate: float
    cache_hit_rate: float
    median_total_ms: float
    p95_total_ms: float
    median_evidence_items: float
    mode_distribution: dict[str, int] = Field(default_factory=dict)

    # Cost — the headline claim, and previously null on every row.
    median_input_tokens: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_estimated_cost_usd: float | None = None
    cost_per_accepted_answer_usd: float | None = None

    # Control-loop behaviour.
    escalation_rate: float = 0.0
    fail_closed_rate: float = 0.0

    # Routing quality, with the reference points that make the number mean
    # something. Accuracy alone is unreadable without knowing what a trivial
    # always-pick-the-commonest-mode router would have scored.
    routing_accuracy: float | None = None
    best_fixed_mode_accuracy: float | None = None
    oracle_gap: float | None = None
    routing_accuracy_by_bucket: dict[str, float] = Field(default_factory=dict)

    # Run validity. A run against stub retrieval or with no LLM configured
    # produces numbers that are not production-representative, and the report
    # has to say so rather than presenting them as measurements.
    degraded_retrieval_rate: float = 0.0
    unsynthesized_rate: float = 0.0
    cost_is_measured: bool = False


class EvalSummary(BaseModel):
    run_id: str
    corpus_path: str
    baselines: list[str]
    started_at: str
    finished_at: str
    baseline_stats: list[BaselineStats] = Field(default_factory=list)
    acceptance_gates: dict[str, GateVerdict] = Field(default_factory=dict)
    validity_warnings: list[str] = Field(default_factory=list)
