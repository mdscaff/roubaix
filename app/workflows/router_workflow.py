"""Temporal RouterWorkflow — durable query orchestration.

This workflow replaces the in-process QueryOrchestrator loop when
Temporal infrastructure is available. The FastAPI endpoint starts this
workflow and awaits its result.

Phase 1: scaffold with activities mapped to existing service classes.
Phase 2: replace activities with Nexus operations for cross-service fan-out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow

from app.domain.models import (
    AnswerResult,
    QueryRequest,
    RouteDecision,
    SearchMode,
)


@dataclass
class RouteInput:
    query: str
    normalized_query: str
    dataset: str
    freshness_required: bool


@dataclass
class RetrieveInput:
    query: str
    mode: str
    dataset: str
    node_sets: list[str]


# --- Activities (wrap existing service logic) ---


@activity.defn
async def normalize_query(query: str) -> str:
    """Normalize query for cache/similarity lookups."""
    from app.services.normalizer import QueryNormalizer

    return QueryNormalizer().normalize(query)


@activity.defn
async def route_query(input: RouteInput) -> dict:
    """Run deterministic router and return RouteDecision as dict."""
    from app.services.router import QueryRouter

    request = QueryRequest(
        query=input.query,
        normalized_query=input.normalized_query,
        dataset=input.dataset,
        freshness_required=input.freshness_required,
    )
    decision = QueryRouter().route(request)
    return decision.model_dump()


@activity.defn
async def retrieve_and_pack(input: RetrieveInput) -> dict:
    """Retrieve evidence from Cognee and pack it."""
    from app.integrations.cognee_client import CogneeClient
    from app.services.evidence import EvidencePacker

    client = CogneeClient()
    result = await client.search(
        query=input.query,
        mode=SearchMode(input.mode),
        dataset=input.dataset,
        node_sets=input.node_sets,
    )
    packed = EvidencePacker().pack(result)
    return packed.model_dump()


# --- Workflow ---


@workflow.defn
class RouterWorkflow:
    """Durable query pipeline.

    Phase 1: sequential activities (normalize → route → retrieve → synthesize).
    Phase 2: Nexus fan-out for parallel multi-mode retrieval.
    """

    @workflow.run
    async def run(self, request_dict: dict) -> dict:
        request = QueryRequest(**request_dict)

        # Step 1: Normalize
        normalized = await workflow.execute_activity(
            normalize_query,
            request.query,
            start_to_close_timeout=timedelta(seconds=5),
        )

        # Step 2: Route
        route_input = RouteInput(
            query=request.query,
            normalized_query=normalized,
            dataset=request.dataset or "default",
            freshness_required=request.freshness_required,
        )
        route_dict = await workflow.execute_activity(
            route_query,
            route_input,
            start_to_close_timeout=timedelta(seconds=10),
        )
        route = RouteDecision(**route_dict)

        # Step 3: Retrieve + Pack
        retrieve_input = RetrieveInput(
            query=request.query,
            mode=route.mode.value,
            dataset=request.dataset or "default",
            node_sets=route.node_sets,
        )
        packed_dict = await workflow.execute_activity(
            retrieve_and_pack,
            retrieve_input,
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Step 4: Synthesize (inline for now, Nexus op in Phase 2)
        answer_text = (
            f"Roubaix response using mode={route.mode}. "
            f"Query: {request.query}. "
            f"Evidence packed successfully."
        )

        result = AnswerResult(
            answer=answer_text,
            accepted=True,
            route=route,
            retrieval_mode=route.mode,
            retry_count=0,
            cache_hit=False,
            telemetry={"evidence_items": len(packed_dict.get("evidence_items", []))},
        )
        return result.model_dump()
