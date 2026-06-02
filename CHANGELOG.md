# Changelog

All notable changes to Roubaix are documented here.

## [0.2.0] — 2026-05-30

First integrated baseline after the initial scaffold. Merged via [PR #1](https://github.com/mdscaff/roubaix/pull/1).

### Added

- **Phase 1 retrieval path** — `CogneeClient.search()` calls the Cognee SDK with `only_context=True`; falls back to deterministic stub when Cognee is not installed (CI-safe).
- **Search mode mapping** — Roubaix `SearchMode` → Cognee `SearchType` (`cognee_mapping.py`, `cognee_results.py`).
- **OpenRouter synthesis** — `AnswerSynthesizer` with cache-friendly static system prompt + dynamic evidence suffix; template fallback when no API key.
- **Fail-closed runtime** — `RuntimeController` rejects empty evidence after `ROUBAIX_MAX_RETRIES` instead of synthesizing from nothing.
- **Eval harness** — JSONL runner (`app/evals/`), baselines (`chunks_only`, `graph_only`, `roubaix_rules`), starter queries (`evals/queries.jsonl`), report scripts.
- **Cognee startup bridging** — `configure_cognee()` maps OpenRouter/OpenAI settings to Cognee env vars; optional pgGraph adapter registration.
- **Orchestrator telemetry** — `retrieval_ms`, `synthesis_ms`, `total_ms`, token estimates, `escalation_reason` on every answer.
- **Content-addressed cache** — tiered resolution pipeline with normalized query keys (unchanged from scaffold, now instrumented).
- **Local Postgres stack** — `docker/docker-compose.yml` for Cognee + pgGraph development.
- **Seed script** — `scripts/seed_cognee.py` + `evals/corpus/roubaix_basics.md`.
- **CI** — GitHub Actions workflow (ruff, mypy, pytest) on Python 3.11 and 3.12.
- **Reproducible deps** — `uv.lock`, updated dependency floors, fixed `temporal` extra (`nexus-rpc`).
- **Narrated demo pipeline** — seek-synced 24fps capture with zoom/scroll/highlight animation (`static/demo.html` record mode, `scripts/build_demo_video.py`).
- **Demo docs** — `docs/demo-video-generator-prompt.md`; extracted toolkit published as [demo-reel](https://github.com/mdscaff/demo-reel).

### Changed

- `ForcedModeRouter` for eval baselines that pin a single search mode.
- `/healthz` includes Cognee configuration status.
- Demo page adds Phase 1 section and animated record-mode timeline for video generation.
- Narration script updated for Phase 1 capabilities.

### Fixed

- Nexus sync-operation type hints for mypy 2.x (`StartOperationContext`).
- Runtime controller no longer accepts empty evidence after max retries.
- Playwright demo recording replaced static WebM capture with seek-based frame pipeline (no frozen video).

### Known gaps (honest)

- NodeSet scoping in router still returns `[]`; `evidence_budget` passed to retrieval but not fully enforced end-to-end.
- DSPy, GEPA, and AdalFlow are not connected to the live `/answer` path.
- `ROUBAIX_ENABLE_TEMPORAL` is defined but unused.
- Eval corpus is minimal; expand once live pgGraph retrieval is seeded.

---

## [0.1.0] — initial scaffold

- Typed domain models, API skeleton, rule-based router placeholder
- Browser demo page, Temporal Nexus scaffold
- Content-addressed cache and tiered resolution pipeline
- Architecture and implementation docs
