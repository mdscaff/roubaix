"""Baseline router configurations for comparative eval runs."""

from __future__ import annotations

from enum import StrEnum

from app.domain.models import SearchMode
from app.integrations.cognee_client import CogneeClient
from app.services.cache import ContentAddressedCache
from app.services.evidence import EvidencePacker
from app.services.normalizer import QueryNormalizer
from app.services.orchestrator import QueryOrchestrator
from app.services.router import ForcedModeRouter, QueryRouter
from app.services.runtime_controller import RuntimeController


class Baseline(StrEnum):
    CHUNKS_ONLY = "chunks_only"
    GRAPH_ONLY = "graph_only"
    ROUBAIX_RULES = "roubaix_rules"
    # "Retrieve broadly and let the model sort it out." This is the baseline
    # most likely to embarrass a graph system, and leaving it out is how a
    # retrieval architecture avoids being compared against the thing it must
    # beat to justify its complexity.
    FULL_CONTEXT = "full_context"


BASELINE_MODES: dict[Baseline, SearchMode | None] = {
    Baseline.CHUNKS_ONLY: SearchMode.CHUNKS,
    Baseline.GRAPH_ONLY: SearchMode.GRAPH_COMPLETION,
    Baseline.ROUBAIX_RULES: None,
    Baseline.FULL_CONTEXT: SearchMode.CHUNKS,
}

# Effectively unbounded packing for the full-context baseline.
_FULL_CONTEXT_TOKENS = 1_000_000


class UnboundedPacker(EvidencePacker):
    """Packs everything retrieved, ignoring evidence and token budgets."""

    def pack(self, result, *, evidence_budget=None, token_budget=None):  # type: ignore[no-untyped-def, override]
        return super().pack(
            result,
            evidence_budget=_FULL_CONTEXT_TOKENS,
            token_budget=_FULL_CONTEXT_TOKENS,
        )


def router_for_baseline(baseline: Baseline) -> QueryRouter:
    forced = BASELINE_MODES[baseline]
    if forced is None:
        return QueryRouter()
    if baseline is Baseline.FULL_CONTEXT:
        return ForcedModeRouter(forced, evidence_budget=_FULL_CONTEXT_TOKENS)
    return ForcedModeRouter(forced)


def create_orchestrator(
    baseline: Baseline,
    *,
    cache: ContentAddressedCache | None = None,
) -> QueryOrchestrator:
    normalizer = QueryNormalizer()
    packer = UnboundedPacker() if baseline is Baseline.FULL_CONTEXT else EvidencePacker()
    return QueryOrchestrator(
        router=router_for_baseline(baseline),
        cognee_client=CogneeClient(),
        evidence_packer=packer,
        runtime_controller=RuntimeController(),
        normalizer=normalizer,
        cache=cache or ContentAddressedCache(),
    )
