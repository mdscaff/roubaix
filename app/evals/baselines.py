"""Baseline router configurations for comparative eval runs."""

from __future__ import annotations

from enum import Enum

from app.domain.models import SearchMode
from app.integrations.cognee_client import CogneeClient
from app.services.cache import ContentAddressedCache
from app.services.evidence import EvidencePacker
from app.services.normalizer import QueryNormalizer
from app.services.orchestrator import QueryOrchestrator
from app.services.router import ForcedModeRouter, QueryRouter
from app.services.runtime_controller import RuntimeController


class Baseline(str, Enum):
    CHUNKS_ONLY = "chunks_only"
    GRAPH_ONLY = "graph_only"
    ROUBAIX_RULES = "roubaix_rules"


BASELINE_MODES: dict[Baseline, SearchMode | None] = {
    Baseline.CHUNKS_ONLY: SearchMode.CHUNKS,
    Baseline.GRAPH_ONLY: SearchMode.GRAPH_COMPLETION,
    Baseline.ROUBAIX_RULES: None,
}


def router_for_baseline(baseline: Baseline) -> QueryRouter:
    forced = BASELINE_MODES[baseline]
    if forced is None:
        return QueryRouter()
    return ForcedModeRouter(forced)


def create_orchestrator(
    baseline: Baseline,
    *,
    cache: ContentAddressedCache | None = None,
) -> QueryOrchestrator:
    normalizer = QueryNormalizer()
    return QueryOrchestrator(
        router=router_for_baseline(baseline),
        cognee_client=CogneeClient(),
        evidence_packer=EvidencePacker(),
        runtime_controller=RuntimeController(),
        normalizer=normalizer,
        cache=cache or ContentAddressedCache(),
    )
