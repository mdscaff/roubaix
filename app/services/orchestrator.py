from app.domain.models import AnswerResult, QueryRequest, RouteDecision
from app.integrations.cognee_client import CogneeClient
from app.observability.metrics import metrics
from app.services.evidence import EvidencePacker
from app.services.router import QueryRouter
from app.services.runtime_controller import RuntimeController


class QueryOrchestrator:
    def __init__(
        self,
        router: QueryRouter,
        cognee_client: CogneeClient,
        evidence_packer: EvidencePacker,
        runtime_controller: RuntimeController,
    ) -> None:
        self.router = router
        self.cognee_client = cognee_client
        self.evidence_packer = evidence_packer
        self.runtime_controller = runtime_controller

    async def answer(self, request: QueryRequest) -> AnswerResult:
        retry_count = 0
        route = self.router.route(request)
        while True:
            result = await self.cognee_client.search(
                query=request.query,
                mode=route.mode,
                dataset=request.dataset or "default",
                node_sets=route.node_sets,
            )
            packed = self.evidence_packer.pack(result)
            accepted, next_route = self.runtime_controller.decide(request, route, packed, retry_count)
            metrics.increment(f"route:{route.mode}")
            if accepted:
                answer = self._synthesize(request, route, packed)
                return AnswerResult(
                    answer=answer,
                    accepted=True,
                    route=route,
                    retrieval_mode=route.mode,
                    retry_count=retry_count,
                    telemetry={"evidence_items": len(packed.evidence_items)},
                )
            retry_count += 1
            route = next_route or route

    def _synthesize(self, request: QueryRequest, route: RouteDecision, packed: object) -> str:
        return (
            f"Roubaix response using mode={route.mode}. "
            f"Query: {request.query}. "
            f"Evidence packed successfully."
        )
