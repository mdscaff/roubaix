"""Pydantic models for eval corpus rows and run summaries."""

from __future__ import annotations

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


class BaselineStats(BaseModel):
    baseline: str
    query_count: int
    accepted_rate: float
    cache_hit_rate: float
    median_total_ms: float
    p95_total_ms: float
    median_evidence_items: float
    mode_distribution: dict[str, int] = Field(default_factory=dict)
    routing_accuracy: float | None = None


class EvalSummary(BaseModel):
    run_id: str
    corpus_path: str
    baselines: list[str]
    started_at: str
    finished_at: str
    baseline_stats: list[BaselineStats] = Field(default_factory=list)
    acceptance_gates: dict[str, bool] = Field(default_factory=dict)
