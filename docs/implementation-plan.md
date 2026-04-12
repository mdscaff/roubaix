# Implementation Plan

## Phase 0: project scaffolding

- set up repo structure
- establish typed domain models
- add API skeleton
- add agent instruction files for Codex and Claude Code

## Phase 1: deterministic baseline

- implement rule-based query routing
- implement NodeSet selection baseline
- implement Cognee client wrapper
- implement basic evidence packer for chunk and triplet modes
- implement basic synthesis orchestration
- add metrics for route, evidence size, retries, and latency

### Exit criteria

- end-to-end request path works for at least two search modes
- tests cover routing and evidence packing
- telemetry emitted for the critical decision points

## Phase 2: runtime control

- implement progressive escalation policy
- add confidence and freshness checks
- bound retries by token and latency budgets

### Exit criteria

- runtime controller can retry with a different mode or expanded scope
- failures are observable, not silent

## Phase 3: optimization loop

- add DSPy program definitions
- add eval dataset and metric functions
- compile baseline optimization with GEPA
- compare optimized router/packer against rule baseline

### Exit criteria

- offline eval reports cost/quality deltas
- optimized configuration can be promoted behind a flag

## Phase 4: advanced retrieval

- add temporal retrieval support
- add ontology-aware retrieval for stable domains
- add more sophisticated evidence compression

## Phase 5: hardening

- tighten error handling
- add regression suite
- document operational runbooks
