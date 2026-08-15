from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.logging import configure_logging
from app.domain.models import AnswerResult, QueryRequest
from app.integrations import langfuse_tracing
from app.integrations.cognee_client import CogneeClient
from app.integrations.cognee_setup import configure_cognee, get_cognee_status
from app.services.cache import ContentAddressedCache
from app.services.evidence import EvidencePacker
from app.services.normalizer import QueryNormalizer
from app.services.orchestrator import QueryOrchestrator
from app.services.router import QueryRouter
from app.services.runtime_controller import RuntimeController

configure_logging()
# Must run before any Cognee import: Cognee reads its LLM_*/EMBEDDING_* config
# from the environment at import time. `app.integrations.cognee_mapping` imports
# cognee lazily for exactly this reason.
configure_cognee()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STATIC = _REPO_ROOT / "static"

_normalizer = QueryNormalizer()
_cache = ContentAddressedCache(
    max_size=settings.cache_max_size,
    default_ttl_s=settings.cache_default_ttl_s,
    freshness_ttl_s=settings.cache_freshness_ttl_s,
)

orchestrator = QueryOrchestrator(
    router=QueryRouter(normalizer=_normalizer),
    cognee_client=CogneeClient(),
    evidence_packer=EvidencePacker(),
    runtime_controller=RuntimeController(),
    normalizer=_normalizer,
    cache=_cache,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Release the pooled synthesis connection and flush buffered traces at
    # shutdown. Flushing per span would block the event loop on every query.
    await orchestrator.synthesizer.aclose()
    langfuse_tracing.flush()


app = FastAPI(title="Roubaix API", version="0.3.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {"status": "ok", "cognee": get_cognee_status()}


@app.get("/demo")
async def demo() -> FileResponse:
    """CEO-friendly browser demo (problems, outcomes, live POST /answer)."""
    path = _STATIC / "demo.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Missing demo page: {path}")
    return FileResponse(path, media_type="text/html")


@app.post("/answer", response_model=AnswerResult)
async def answer(request: QueryRequest) -> AnswerResult:
    """Answer a query, or return an explicit non-answer.

    A refusal is a 200 with ``accepted: false`` and an ``escalation_reason``,
    not an error status: the request was handled correctly and the caller needs
    the reason. Errors here are genuine faults.
    """
    try:
        return await orchestrator.answer(request)
    except Exception as exc:  # noqa: BLE001 - boundary handler
        # Never leak an internal traceback to the caller, and never let one
        # surface as an unhandled 500 with no log line.
        raise HTTPException(
            status_code=502,
            detail=f"Query pipeline failed: {type(exc).__name__}",
        ) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api.main:app", host=settings.api_host, port=settings.api_port, reload=False)
