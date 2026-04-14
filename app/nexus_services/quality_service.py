"""Nexus service definition for quality tracking.

Records routing outcomes and provides per-mode success rates
for quality-based auto-eviction (inspired by HyperSpace's self-curating
plans that evict below 60% success after 5 samples).
"""

from __future__ import annotations

import nexusrpc
import nexusrpc.handler

from app.domain.models import OutcomeRecord, RouteStats, SearchMode


@nexusrpc.service
class QualityService:
    """Contract: record outcomes and query historical success rates."""

    record_outcome: nexusrpc.Operation[OutcomeRecord, None]
    get_route_stats: nexusrpc.Operation[SearchMode, RouteStats]


@nexusrpc.handler.service_handler(service=QualityService)
class QualityServiceHandler:
    """Sync handler — lightweight in-memory stats.

    TODO: Phase 2 — persist via Temporal workflow state for durability.
    """

    @nexusrpc.handler.sync_operation
    async def record_outcome(
        self,
        ctx: nexusrpc.handler.SyncOperationContext,
        input: OutcomeRecord,
    ) -> None:
        # Phase 2: update windowed outcome tracker
        pass

    @nexusrpc.handler.sync_operation
    async def get_route_stats(
        self,
        ctx: nexusrpc.handler.SyncOperationContext,
        input: SearchMode,
    ) -> RouteStats:
        # Phase 2: return windowed stats for the given mode
        return RouteStats(mode=input)
