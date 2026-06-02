"""Structured per-query traces for offline eval runs.

EvalTrace is the canonical record written by scripts/run_eval.py. Langfuse and
other observability tools may mirror these fields but should not replace them.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import SearchMode

eval_run_context: ContextVar[EvalRunContext | None] = ContextVar("eval_run_context", default=None)


class EvalRunContext(BaseModel):
    """Active eval run metadata, set by the runner for each query."""

    run_id: str
    baseline: str
    query_id: str
    bucket: str
    dataset: str = "default"


class EvalTrace(BaseModel):
    """One query execution under a specific baseline configuration."""

    run_id: str
    baseline: str
    query_id: str
    bucket: str
    query: str
    dataset: str

    route_mode: SearchMode
    route_rationale: str
    node_sets: list[str] = Field(default_factory=list)
    evidence_budget: int = 8
    requires_freshness_validation: bool = False
    expected_mode: SearchMode | None = None

    evidence_items: int = 0
    retrieval_ms: int = 0
    synthesis_ms: int = 0
    total_ms: int = 0

    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None

    retry_count: int = 0
    escalation_reason: str | None = None
    cache_hit: bool = False
    accepted: bool = False

    pggraph_extension: bool | None = None
    answer: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
    recorded_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    extra: dict[str, Any] = Field(default_factory=dict)


def get_eval_run_context() -> EvalRunContext | None:
    return eval_run_context.get()
