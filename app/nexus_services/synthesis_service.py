"""Nexus service definition for LLM synthesis.

Separated from retrieval so synthesis workers can scale independently
and use different timeout/retry policies.
"""

from __future__ import annotations

import nexusrpc
import nexusrpc.handler
from temporalio import nexus

from app.domain.models import SynthesisRequest, SynthesisResult


@nexusrpc.service
class SynthesisService:
    """Contract: caller sends packed evidence, receives synthesized answer."""

    synthesize: nexusrpc.Operation[SynthesisRequest, SynthesisResult]


@nexusrpc.handler.service_handler(service=SynthesisService)
class SynthesisServiceHandler:
    """Handler — delegates to a SynthesisWorkflow.

    TODO: Wire to real SynthesisWorkflow with cache-aware prompt building.
    """

    @nexus.workflow_run_operation
    async def synthesize(
        self,
        ctx: nexus.WorkflowRunOperationContext,
        input: SynthesisRequest,
    ) -> nexus.WorkflowHandle[SynthesisResult]:
        # Phase 2: start SynthesisWorkflow
        raise NotImplementedError("Wire to SynthesisWorkflow in Phase 2")
