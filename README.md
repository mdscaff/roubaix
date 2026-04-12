# Roubaix

Cognee-centered graph retrieval experiment with DSPy/GEPA optimization and AdalFlow runtime control.

## Why this exists

Roubaix is a reference implementation scaffold for a graph-aware retrieval system that aims to:

- reduce inference cost by routing queries to the cheapest valid Cognee retrieval mode
- improve multi-hop answer quality with graph-aware evidence packing
- keep answers fresh without destroying prompt-cache efficiency
- produce a codebase that coding agents can navigate and extend safely

The architecture centers on three layers:

1. **Cognee** for graph + vector retrieval
2. **DSPy + GEPA** for offline optimization of routing, scoping, and evidence shaping
3. **AdalFlow** for runtime fallback, escalation, and freshness-aware control

## Current state

This repository is a **build-ready scaffold**, not a finished product. It includes:

- architecture and implementation docs
- project instructions for Codex and Claude Code
- a typed Python service skeleton
- interfaces and placeholders for the major components
- test scaffolding and acceptance criteria

## Repository map

- `docs/architecture.md` — technical architecture memo
- `docs/implementation-plan.md` — phased implementation plan
- `docs/evaluation-plan.md` — metrics, datasets, and acceptance gates
- `AGENTS.md` — Codex guidance
- `CLAUDE.md` — Claude Code guidance
- `.codex/config.toml` — Codex project config
- `app/` — Python application source
- `tests/` — starter tests
- `configs/` — prompt and policy config
- `scripts/` — local dev helpers

## Quickstart

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn app.api.main:app --reload
```

## Suggested first implementation steps

1. Implement `CogneeClient.search()` and `CogneeClient.ingest()`.
2. Implement `QueryRouter.route()` using a rule baseline before DSPy optimization.
3. Implement `EvidencePacker.pack()` for triplet and chunk modes.
4. Implement `RuntimeController.decide()` for bounded retries.
5. Add an eval harness and wire in DSPy + GEPA.

## Target runtime flow

```text
Query -> Route -> NodeSet scope -> Cognee retrieval -> Evidence pack -> Synthesis -> Runtime check -> Return or escalate
```

## Notes for engineers

- Do not treat the graph as prompt payload.
- Keep the cached prompt prefix stable and push dynamic evidence into the suffix.
- Prefer shallow, scoped retrieval before broad graph expansion.
- Instrument every decision so routing quality can be improved with evidence rather than opinion.
