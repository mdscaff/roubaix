"""Nexus service definition for evidence retrieval.

Each search mode is exposed as an independent Nexus operation so callers
can fan out parallel retrievals via the RouterWorkflow.
"""

from __future__ import annotations

from dataclasses import dataclass

import nexusrpc
import nexusrpc.handler
from temporalio import nexus

from app.domain.models import PackedEvidence, SearchRequest


@nexusrpc.service
class EvidenceRetrievalService:
    """Contract: callers invoke per-mode search operations."""

    search_documents: nexusrpc.Operation[SearchRequest, PackedEvidence]
    search_temporal: nexusrpc.Operation[SearchRequest, PackedEvidence]
    search_graph: nexusrpc.Operation[SearchRequest, PackedEvidence]


@nexusrpc.handler.service_handler(service=EvidenceRetrievalService)
class EvidenceRetrievalServiceHandler:
    """Handler implementations — each delegates to a workflow.

    TODO: Wire to real EvidenceRetrievalWorkflow once Temporal infra is live.
    """

    @nexus.workflow_run_operation
    async def search_documents(
        self,
        ctx: nexus.WorkflowRunOperationContext,
        input: SearchRequest,
    ) -> nexus.WorkflowHandle[PackedEvidence]:
        # Phase 2: start EvidenceRetrievalWorkflow for CHUNKS/RAG_COMPLETION
        raise NotImplementedError("Wire to EvidenceRetrievalWorkflow in Phase 2")

    @nexus.workflow_run_operation
    async def search_temporal(
        self,
        ctx: nexus.WorkflowRunOperationContext,
        input: SearchRequest,
    ) -> nexus.WorkflowHandle[PackedEvidence]:
        # Phase 2: start TemporalRetrievalWorkflow for TEMPORAL mode
        raise NotImplementedError("Wire to TemporalRetrievalWorkflow in Phase 2")

    @nexus.workflow_run_operation
    async def search_graph(
        self,
        ctx: nexus.WorkflowRunOperationContext,
        input: SearchRequest,
    ) -> nexus.WorkflowHandle[PackedEvidence]:
        # Phase 2: start GraphRetrievalWorkflow for GRAPH_*/TRIPLET_*/CYPHER
        raise NotImplementedError("Wire to GraphRetrievalWorkflow in Phase 2")
