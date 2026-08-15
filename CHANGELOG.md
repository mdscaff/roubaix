# Changelog

All notable changes to Roubaix are documented here.

## [0.4.0] — 2026-08-15

A correctness and honesty pass over the whole pipeline, following a full audit
against the stated design and a survey of comparable systems. Supersedes the
unreleased 0.3.0 and 0.3.1 work, which is folded in here.

The short version: six defects were found where the system reported success it
had not established, the eval harness could not fail, and three named
architecture components did not exist. All of that is now either fixed, deleted,
or labelled.

### Fixed — correctness

Each was reproduced before being fixed, and each has a regression test.

- **Fabricated evidence was answered from and cached.** A Cognee failure was
  caught and replaced with placeholder evidence, synthesized into a fluent
  answer marked `accepted: true`, and cached for an hour. A transient outage
  produced invented answers indistinguishable from grounded ones. Retrieval
  failures now carry a `degraded` flag, the controller fails closed on it, and
  degraded answers are never cached.
- **Inverted relationships collided on one cache key.** `normalize()` sorted
  tokens, so "does A depend on B" and "does B depend on A" produced the same
  key and one could be served the other's answer. Normalization is now
  order-preserving; the order-insensitive form moved to `fingerprint()`,
  documented as unsafe for keys.
- **Router phrases could never match.** Multi-word patterns were matched against
  the sorted, stop-word-stripped bag, making "depends on" and
  "how are … organized" unmatchable. `GRAPH_COMPLETION` was unreachable
  entirely, capping the multi-hop bucket at zero.
- **Substring matching bought expensive modes.** "concurrent" matched "current"
  (→ TEMPORAL, 120s TTL); "knowledge" matched "edge" (→ CYPHER).
- **The freshness contract was bypassed by the cache**, and satisfiable by any
  non-empty result. It was then *abandoned by escalation* — once the ladder
  moved past TEMPORAL, undated evidence was returned as if freshness had been
  validated. The check now keys on "TEMPORAL was attempted and nothing is
  dated".
- **A dead LLM provider was invisible.** Synthesis failures returned a template
  marked accepted. A malformed JSON body (`ValueError`, not `httpx.HTTPError`)
  escaped as an unhandled 500.
- **Langfuse tracing was broken against its current major version.** The floor
  was `>=2.0.0` while the code called `Langfuse.start_span`, removed in v4.
- The evidence budget was discarded at packing time, making routing decorative;
  the Cognee env bridge was a no-op in the API process (cognee was imported
  before `configure_cognee()` ran); `Timer` minted one metric key per distinct
  millisecond; a blocking `Path.is_file()` sat in an async endpoint; the demo
  page substituted invented telemetry when the API was unreachable, and that
  fabricated payload is what got filmed into the demo video.

### Added

- **Scored router** — weighted signals, word-boundary patterns, cost-rank
  tie-break, negation suppression, confidence margin, and per-decision
  signal/score telemetry so a route can be replayed rather than re-argued.
- **Progressive escalation** — widen the same mode before climbing a
  terminating ladder; no mode is attempted twice.
- **`StopReason`** — a closed vocabulary for why the loop stopped. A budget trip
  is an outcome, not an error, so `limit_latency` sits in the same enum as
  `sufficient_evidence`.
- **Caller ceilings enforced.** `max_cost_cents` trims the evidence pack rather
  than refusing; `max_latency_ms` stops the loop. Both documented as soft caps
  checked at loop boundaries.
- **`CheckErrorPolicy`** — per-check behaviour when a control check itself
  raises. Defaults to DENY; PROCEED exists and is named fail-open.
- **Real cost accounting** — provider-reported usage when available, explicitly
  labelled estimates otherwise, per-model price table.
- **DSPy learned router stage**, running only on the band the deterministic
  router flags unconfident — 42% of held-out traffic, 73% accurate, holding 3 of
  4 misses, against 93% for the confident band. 58% of queries never reach an
  LM. Any failure degrades to the deterministic decision.
- **GEPA optimizer** with a cost-aware feedback metric (`0.7 * correct + 0.3 *
  cost_efficiency`, because a correctness-only metric converges on the most
  expensive mode) that returns text rather than a float, since GEPA passes it to
  the reflection prompt verbatim.
- **Held-out routing corpus** (n=26) and `scripts/eval_routing.py`, gated in CI.
- **Losable acceptance gates** with PASS/FAIL/**UNKNOWN** verdicts, a
  `full_context` baseline, and validity warnings printed above the results.
- **OpenTelemetry GenAI attribute mapping**, with Roubaix dimensions namespaced
  under `roubaix.*` and the semconv version pinned and reported.
- Retrieval and synthesis timeouts; a pooled HTTP client; API response model and
  a 502 boundary handler; explicit ruff rule set; `tiktoken` declared.
- ADRs [003](docs/adr/ADR-003-reject-adalflow-keep-explicit-controller.md),
  [004](docs/adr/ADR-004-evaluate-strands-adopt-patterns-not-dependency.md),
  [005](docs/adr/ADR-005-dspy-learned-stage-over-the-ambiguous-band.md).

### Removed

- **AdalFlow.** Named as the runtime-control layer before being evaluated. It is
  an auto-optimization library with no retry/escalation/fallback primitives,
  overlapping DSPy rather than the controller, with no release since Sept 2025.
- **Strands Agents SDK** was evaluated and its dependency refused — its control
  primitives are denominated in turns, tool calls, and conversation messages,
  none of which Roubaix has. Four of its patterns were reimplemented instead.
- Fabricated demo telemetry; the `not_implemented` placeholders.

### Changed

- All dependencies upgraded to current (August 2026) with floors raised to the
  tested versions. `(str, Enum)` classes migrated to `StrEnum`, removing the
  interpolation footgun where `f"{mode}"` rendered `SearchMode.CHUNKS`.
- Docs now match the code. The README leads with the one real measurement and an
  explicit "what is not measured" section.
- Corrected a claim this repo had repeated: GEPA optimizes instruction *text*,
  not numeric weights, so the rule weights are hand-tuned and measured.

### Measured

| Corpus | n | Routing accuracy | Best single fixed mode |
|---|---:|---:|---:|
| held-out (never tuned against) | 26 | **85%** | 23% |
| tuning (rules written against it) | 20 | 100% — an upper bound, not a measurement | 30% |

Tests 41 → 140 (121 with the DSPy suite skipped, which is how CI runs).

### Not established

No run against live Cognee with a live LLM has been recorded, so there is no
cost, latency, or answer-quality figure to quote — and none is claimed. No GEPA
compile has been run; whether it beats the scored rule engine is open, and a
negative result would be a legitimate finding.

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
