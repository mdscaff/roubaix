from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.core.logging import configure_logging
from app.domain.models import QueryRequest
from app.integrations.cognee_client import CogneeClient
from app.services.evidence import EvidencePacker
from app.services.orchestrator import QueryOrchestrator
from app.services.router import QueryRouter
from app.services.runtime_controller import RuntimeController

configure_logging()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STATIC = _REPO_ROOT / "static"

app = FastAPI(title="Roubaix API", version="0.1.0")

orchestrator = QueryOrchestrator(
    router=QueryRouter(),
    cognee_client=CogneeClient(),
    evidence_packer=EvidencePacker(),
    runtime_controller=RuntimeController(),
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/demo")
async def demo() -> FileResponse:
    """CEO-friendly browser demo (problems, outcomes, live POST /answer)."""
    path = _STATIC / "demo.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Missing demo page: {path}")
    return FileResponse(path, media_type="text/html")


@app.post("/answer")
async def answer(request: QueryRequest) -> dict:
    result = await orchestrator.answer(request)
    return result.model_dump()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=False)
