# Changelog

All notable changes to Roubaix are documented here.

## [0.3.1] — 2026-08-15

Dependency refresh and selective adoption of agent-harness patterns.

### Fixed

- **Langfuse tracing was broken against the current major version.** The floor
  was `>=2.0.0` while the code called `Langfuse.start_span`, removed in v4.
  A fresh install resolved to v4 and would have raised `AttributeError` on the
  first traced query. Rewritten against the v4 OpenTelemetry-based API.
- **Freshness contract was abandoned by escalation.** The check keyed on the
  mode currently in play, so once the ladder moved past `TEMPORAL` to a broader
  mode, undated evidence was returned as if freshness had been validated. It now
  keys on "TEMPORAL was attempted and nothing is dated".
- **Blocking `Path.is_file()` inside an async endpoint**; **`subprocess.run`
  without `check`** in the demo builder (a failing ffmpeg passed silently);
  **implicit string concatenation inside list literals**, where a missing comma
  reads as concatenation.
- Migrated `(str, Enum)` classes to `StrEnum`, permanently removing the
  interpolation footgun where `f"{mode}"` rendered `SearchMode.CHUNKS`.

### Added

- **`StopReason`** — a closed vocabulary for why the control loop stopped. A
  budget trip is an outcome, not an error, so `limit_latency` sits in the same
  enum as `sufficient_evidence`. Closes the earlier finding that escalation
  reasons were free text and could not be aggregated.
- **`max_latency_ms` is enforced.** It had been declared on the request model
  and read nowhere. Documented as a soft cap checked at loop boundaries.
- **`CheckErrorPolicy`** — per-check behaviour when a control check itself
  raises. Defaults to DENY; PROCEED exists and is explicitly named fail-open.
- **OpenTelemetry GenAI attribute mapping** (`app/observability/gen_ai.py`),
  with Roubaix dimensions under `roubaix.*` and the semconv version pinned and
  reported. No claim of compliance — nothing in that spec is Stable.
- **Explicit ruff rule set.** The default selection changes between releases, so
  relying on it meant a dependency upgrade silently changed what CI enforces.
- `tiktoken` declared explicitly; it was only present transitively via cognee,
  so token-estimate accuracy depended on which extras were installed.
- [ADR-004](docs/adr/ADR-004-evaluate-strands-adopt-patterns-not-dependency.md).

### Changed

- All dependencies upgraded to current and floors raised to the tested versions.

### Rejected

- **Strands Agents SDK.** A genuine runtime controller, but every control
  primitive it has is denominated in units Roubaix does not have — turns, tool
  calls, conversation messages. Four of its ten hook events are tool events, and
  there is no extension point for a stage that is neither a model call nor a
  tool call, which is all four of Roubaix's interesting stages. Four of its
  patterns were reimplemented instead.

## [0.3.0] — 2026-08-15

Correctness and honesty pass following a full audit of the pipeline against
its stated design, plus a survey of comparable agent harnesses and graph-memory
systems. See `docs/roadmap.md` for what the research recommends next.

### Fixed — correctness

- **Fabricated evidence could be answered from and cached.** A Cognee failure
  was caught and replaced with placeholder evidence, synthesized into a fluent
  answer, marked `accepted: true`, and cached for an hour. Retrieval failures
  now carry a `degraded` flag; the controller fails closed on it by default and
  degraded answers are never cached.
- **Inverted relationships shared a cache key.** `normalize()` sorted tokens, so
  "does A depend on B" and "does B depend on A" produced the same key and one
  could be served the other's answer. Normalization is now order-preserving.
- **Router phrases could never match.** Multi-word patterns were matched against
  the sorted, stop-word-stripped bag, so "depends on" and "how are ... organized"
  were unmatchable. `GRAPH_COMPLETION` was unreachable entirely, capping the
  multi-hop bucket at zero.
- **Substring matching bought expensive modes.** "concurrent" matched "current"
  (→ TEMPORAL, 120s TTL), "knowledge" matched "edge" (→ CYPHER). Patterns now
  use word boundaries.
- **Freshness contract was bypassed by the cache and satisfiable without a
  timestamp.** `freshness_required` is now part of the cache key, and a
  freshness-required query must return evidence carrying a parseable date or the
  controller refuses.
- **A dead LLM provider was invisible.** Synthesis failures returned a template
  marked accepted. They now fail closed. A malformed JSON body (ValueError, not
  an httpx.HTTPError) previously escaped as an unhandled 500.
- **The evidence budget was discarded at packing time**, making the router's
  cost decision decorative.
- **The Cognee env bridge was a no-op in the API process** — cognee was imported
  before `configure_cognee()` ran.
- **`Timer` minted one metric key per distinct millisecond** — unbounded
  cardinality.

### Added

- **Scored router** with weighted signals, cost-rank tie-break, negation
  suppression, confidence margin, and per-decision signal/score telemetry.
- **Progressive escalation** — widen the same mode before climbing a terminating
  ladder; no mode is attempted twice.
- **Real cost accounting** — provider-reported usage when available, explicitly
  labelled estimates otherwise, per-model price table.
- **Caller cost ceiling** that trims the evidence pack rather than refusing.
- **Held-out routing corpus** (`evals/queries_heldout.jsonl`, n=26) and
  `scripts/eval_routing.py`, gated in CI. Measured: **85% held-out accuracy vs
  23% for the best single fixed mode**.
- **`full_context` eval baseline** — the comparison most likely to embarrass a
  graph system.
- **Losable acceptance gates** with PASS/FAIL/UNKNOWN verdicts and run validity
  warnings.
- **Timeouts** on retrieval and synthesis; pooled HTTP client.
- `docs/roadmap.md`, ADR-003.

### Removed

- **AdalFlow.** Evaluated and rejected — it is an auto-optimization library with
  no retry/escalation/fallback primitives, overlapping DSPy rather than the
  runtime controller, with no release since September 2025. See ADR-003.
- **Fabricated demo telemetry.** The demo page substituted invented timings and
  a route mode the router could not emit when the API was unreachable.

### Changed

- README leads with the one real measurement and an explicit "what is not
  measured" section.
- API declares `AnswerResult` as its response model and returns 502 rather than
  leaking a traceback.
- Cache key covers query, dataset, freshness, NodeSet scope, model, caller
  identity, and a policy version.

### Known gaps

- NodeSet scoping is caller-supplied, not derived.
- No external benchmark has been run; no live-retrieval cost figure exists.
- DSPy/GEPA are not wired. Prompt-prefix caching yields no discount at the
  current prefix length.

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
