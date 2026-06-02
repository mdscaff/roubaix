# Roubaix

Cognee-centered graph retrieval experiment with DSPy/GEPA optimization and AdalFlow runtime control.

## Why this exists

Roubaix routes every query to the **cheapest valid** Cognee retrieval mode before paying for graph depth. The goal is measurable outcomes: lower cost per answer, better multi-hop quality, freshness when it matters, and telemetry leadership can audit.

Three planned layers:

1. **Cognee** — graph + vector retrieval (live SDK path with CI-safe stub fallback)
2. **DSPy + GEPA** — offline optimization of routing, scoping, and evidence shaping *(roadmap)*
3. **AdalFlow** — runtime fallback, escalation, and freshness-aware control *(roadmap)*

## What's new in v0.2.0

See [CHANGELOG.md](CHANGELOG.md) for full release notes. Highlights:

| Area | Improvement |
|------|-------------|
| **Phase 1 baseline** | Live `cognee.search(..., only_context=True)` with stub fallback; OpenRouter synthesis; fail-closed runtime escalation |
| **Eval harness** | JSONL runner, baselines (`chunks_only`, `graph_only`, `roubaix_rules`), optional Langfuse tracing |
| **Cognee bootstrap** | Startup env bridging (OpenRouter/OpenAI → Cognee `LLM_*` / `EMBEDDING_*`); optional pgGraph adapter |
| **Telemetry** | Per-answer `retrieval_ms`, `synthesis_ms`, token estimates, cache hit, escalation reason |
| **CI** | GitHub Actions — ruff, mypy, pytest on Python 3.11/3.12; `uv.lock` for reproducible installs |
| **Local stack** | Postgres docker compose for Cognee + pgGraph; `scripts/seed_cognee.py` baseline corpus |
| **Demo video** | Seek-synced narrated MP4 pipeline (zoom, scroll, live API beat) — see [demo-reel](https://github.com/mdscaff/demo-reel) |

**41 tests passing.** Still an experiment — DSPy/GEPA optimization and AdalFlow orchestration are not wired to `/answer` yet.

## Current state

| Component | Status |
|-----------|--------|
| Rule-based routing + cache | Working |
| Cognee retrieval | Live SDK when configured; stub in CI |
| Evidence packing | Working (chunk, triplet, graph modes) |
| LLM synthesis | OpenRouter (fallback template without API key) |
| Runtime controller | Fail-closed after `ROUBAIX_MAX_RETRIES` |
| Eval harness | JSONL runner + report scripts |
| Temporal / Nexus | Scaffold only |
| DSPy / GEPA / AdalFlow | Placeholders |

## Repository map

- `docs/architecture.md` — technical architecture memo
- `docs/implementation-plan.md` — phased plan (Phase 1 largely complete)
- `docs/evaluation-plan.md` — metrics, datasets, acceptance gates
- `docs/demo-video-generator-prompt.md` — agent prompt for narrated demos
- `app/evals/` — eval runner, baselines, report
- `app/integrations/cognee_setup.py` — Cognee/pgGraph startup bridging
- `evals/queries.jsonl` — starter eval queries
- `docker/docker-compose.yml` — Postgres for local Cognee/pgGraph
- `scripts/run_eval.py`, `scripts/seed_cognee.py`, `scripts/build_demo_video.py`

## Quickstart

```bash
cp .env.example .env
# Set OPENROUTER_API_KEY (synthesis) and OPENAI_API_KEY (embeddings) as needed

uv sync --extra dev
uv run uvicorn app.api.main:app --reload
```

Open the browser demo at [http://localhost:8000/demo](http://localhost:8000/demo).

### Optional: Cognee + pgGraph locally

```bash
docker compose -f docker/docker-compose.yml up -d
# Set GRAPH_DATABASE_PROVIDER=pggraph and DB_* vars in .env (see .env.example)

uv sync --extra opt --extra pggraph
uv run --extra opt --extra pggraph python scripts/seed_cognee.py
```

### Run evals

```bash
uv run python scripts/run_eval.py --baseline roubaix_rules
uv run python scripts/report_eval.py evals/runs/<run-id>
```

### Build narrated demo video

```bash
uv sync --extra demo
python -m playwright install chromium
uv run --extra demo python scripts/build_demo_video.py --tts edge   # or elevenlabs
# → dist/roubaix_demo.mp4
```

For CI-ready release videos, see the standalone [demo-reel](https://github.com/mdscaff/demo-reel) toolkit.

## Target runtime flow

```text
Query → Normalize → Cache check → Route → Cognee retrieval → Evidence pack
      → Runtime check → Synthesize → Cache store → Return (or escalate / fail closed)
```

## Notes for engineers

- Do not treat the graph as prompt payload.
- Keep the cached prompt prefix stable; push dynamic evidence into the suffix.
- Prefer shallow, scoped retrieval before broad graph expansion.
- Instrument every decision so routing quality improves with evidence, not opinion.
- Read `AGENTS.md` and `CLAUDE.md` before making architectural changes.

## License

See repository defaults; experiment / internal use unless otherwise specified.
