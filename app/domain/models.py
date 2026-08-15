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
    node_sets: list[str] = Field(
        default_factory=list,
        description="Caller-supplied NodeSet scope. Narrowing the graph is the "
        "cheapest available cost lever, so it is part of the request contract.",
    )
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
    signals: list[str] = Field(
        default_factory=list,
        description="Named patterns that fired for this decision. Recorded so a "
        "route can be explained and replayed rather than argued about.",
    )
    scores: dict[str, float] = Field(
        default_factory=dict,
        description="Per-mode score from the rule engine, for tie-break auditing.",
    )


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
    degraded: bool = Field(
        default=False,
        description="True when this evidence did NOT come from the live retrieval "
        "substrate (SDK unavailable, search failed, stub fallback). Degraded "
        "evidence must never be synthesized into a confident answer or cached.",
    )
    degraded_reason: str | None = None


class PackedEvidence(BaseModel):
    mode: SearchMode
    summary: str
    evidence_items: list[str] = Field(default_factory=list)
    evidence_hashes: list[str] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None
    token_estimate: int = 0
    dropped_duplicates: int = 0
    dropped_over_budget: int = 0


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
    usage_measured: bool = Field(
        default=False,
        description="True when input_tokens_estimate came from the provider's "
        "reported usage rather than a local heuristic. Cost reporting must not "
        "present an estimate as a measurement.",
    )


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
