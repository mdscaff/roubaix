"""Temporal worker entry point for the router workflow.

Run with: python -m app.workers.router_worker

Requires a running Temporal server (e.g., `temporal server start-dev`).
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.workflows.router_workflow import (
    RouterWorkflow,
    normalize_query,
    retrieve_and_pack,
    route_query,
)


async def main() -> None:
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[RouterWorkflow],
        activities=[normalize_query, route_query, retrieve_and_pack],
    )

    # TODO Phase 2: register Nexus service handlers
    # worker.register_nexus_service_handler(EvidenceRetrievalServiceHandler())
    # worker.register_nexus_service_handler(SynthesisServiceHandler())
    # worker.register_nexus_service_handler(QualityServiceHandler())

    print(f"Router worker listening on task queue: {settings.temporal_task_queue}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
