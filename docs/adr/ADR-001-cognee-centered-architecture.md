# ADR-001: Adopt a Cognee-centered retrieval architecture

## Status
Accepted

## Context
We need a graph-aware retrieval system that improves cost, quality, and freshness without defaulting every query to an expensive graph reasoning path.

## Decision
Use Cognee as the retrieval substrate and add:

- DSPy + GEPA for offline optimization
- AdalFlow for runtime control *(superseded: see [ADR-003](ADR-003-reject-adalflow-keep-explicit-controller.md) — AdalFlow was evaluated and rejected; runtime control is an explicit in-repo controller)*

## Consequences

### Positive
- multiple retrieval modes become first-class design levers
- optimization and runtime control are separated cleanly
- prompt cache strategy becomes clearer

### Negative
- more moving parts than a naive single-path pipeline
- requires instrumentation discipline
