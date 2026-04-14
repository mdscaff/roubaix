from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SearchMode(str, Enum):
    CHUNKS = "CHUNKS"
    RAG_COMPLETION = "RAG_COMPLETION"
    TRIPLET_COMPLETION = "TRIPLET_COMPLETION"
    GRAPH_COMPLETION = "GRAPH_COMPLETION"
    GRAPH_SUMMARY_COMPLETION = "GRAPH_SUMMARY_COMPLETION"
    CYPHER = "CYPHER"
    NATURAL_LANGUAGE = "NATURAL_LANGUAGE"
    TEMPORAL = "TEMPORAL"


class QueryRequest(BaseModel):
    query: str
    user_id: str | None = None
    dataset: str | None = None
    freshness_required: bool = False
    max_latency_ms: int | None = None
    max_cost_cents: float | None = None
    normalized_query: str | None = None
    content_key: str | None = None


class RouteDecision(BaseModel):
    mode: SearchMode
    node_sets: list[str] = Field(default_factory=list)
    evidence_budget: int = 8
    requires_freshness_validation: bool = False
    rationale: str


class RetrievalEvidence(BaseModel):
    triplets: list[dict[str, Any]] = Field(default_factory=list)
    chunks: list[str] = Field(default_factory=list)
    graph_paths: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    timestamps: list[str] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    mode: SearchMode
    evidence: RetrievalEvidence
    retrieval_stats: dict[str, Any] = Field(default_factory=dict)


class PackedEvidence(BaseModel):
    mode: SearchMode
    summary: str
    evidence_items: list[str] = Field(default_factory=list)
    evidence_hashes: list[str] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)


class AnswerResult(BaseModel):
    answer: str
    accepted: bool
    route: RouteDecision
    retrieval_mode: SearchMode
    retry_count: int = 0
    cache_hit: bool = False
    telemetry: dict[str, Any] = Field(default_factory=dict)


# --- Nexus I/O types (used by Temporal Nexus services) ---


class SearchRequest(BaseModel):
    """Input for EvidenceRetrievalService Nexus operations."""

    query: str
    normalized_query: str
    mode: SearchMode
    dataset: str
    node_sets: list[str] = Field(default_factory=list)
    evidence_budget: int = 8
    requires_freshness_validation: bool = False


class SynthesisRequest(BaseModel):
    """Input for SynthesisService Nexus operations."""

    query: str
    mode: SearchMode
    packed_evidence: PackedEvidence
    route_rationale: str


class SynthesisResult(BaseModel):
    """Output from SynthesisService."""

    answer: str
    input_tokens_estimate: int = 0


class OutcomeRecord(BaseModel):
    """Tracks a routing outcome for quality-based auto-eviction."""

    normalized_query: str
    mode: SearchMode
    accepted: bool
    evidence_count: int
    retry_count: int


class RouteStats(BaseModel):
    """Per-mode success statistics returned by QualityService."""

    mode: SearchMode
    total: int = 0
    successes: int = 0
    success_rate: float = 0.0
