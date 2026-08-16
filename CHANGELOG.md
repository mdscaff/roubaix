# Changelog

All notable changes to Roubaix are documented here.

## [0.9.0] — 2026-08-16

Every remaining phase that can run without live egress, run. What still
needs the live stack (Phase D's recall-vs-cost gate, C2 label mining, the
GEPA compile) stays honestly blocked and documented, not simulated.

### Added

- **Phase C1 acceptance gate, RUN: 92% precision / 92% recall** (gate ≥70%
  precision) on a new scoping-labelled extension of the held-out corpus
  (`evals/queries_scoping.jsonl`, 34 rows + `evals/nodesets_eval.json`),
  measured through the real `QueryRouter.route` wiring and gated in CI
  (`scripts/eval_scoping.py --min-precision 0.70`). Precision is gated
  because a false scope hides evidence; recall is reported, not gated — it
  is Phase C2's territory. The corpus pins known failure modes (verb/name
  collisions misfire, lexical gaps miss) and a test fails if relabelling
  ever "fixes" them without a mechanism change. Caveat stated in the module:
  labels and index share an author — judgment-anchored, not blind.
- **Resident-graph persistence** (`save_snapshot`/`load_snapshot`,
  `ROUBAIX_MEMGRAPH_SNAPSHOT_PATH`): the graph is snapshotted atomically
  (tmp-then-rename) at shutdown and restored at startup. Without it,
  everything Tier 0 learned via promotion died with the process — the
  learning contract silently reset at every restart. Restored edges flow
  through `add_edge` (canonicalization, dedup, junk rejection, LRU bound),
  and snapshot order preserves LRU recency across the restart. Failures
  log-and-skip in both directions; persistence is an enhancement.
- **Paraphrase second-chance cache lookup**: a new cache key over the
  paraphrase-collapsed form (stemmed keywords, stop words dropped, **order
  preserved**), consulted only after an exact miss. "Does billing depend on
  the warehouse?" now serves from a cached "does billing depend on
  warehouse" — zero retrieval, zero tokens. Pre-committed interlocks from
  the research phase hold on both sides: freshness-required requests never
  consult the namespace, freshness-validated answers (caller-declared *or*
  router-derived temporal) are never written to it, and dataset / NodeSet
  scope / model / caller / policy version all remain in the key, so a hit
  can never cross a tenant, scope, or model boundary. Inversion survives by
  order; negation survives because "not" is not a stop word. The collision
  contract is encoded as a losable must-collide / must-not-collide table in
  the tests. Telemetry reports `cache_hit_kind: exact|paraphrase`.

### Changed

- `_stem` moved to its canonical home in `app/services/normalizer.py`
  (re-exported from `sufficiency` for existing importers) — the cache's
  paraphrase key needs it and the normalizer must not import upward.

### Still blocked on live egress + an LLM key (unchanged, deliberate)

Phase D's recall-vs-cost gate, the full-pipeline eval, C2's label mining,
and the GEPA compile-then-judge run. `scripts/live_stack.py` preflight names
the binding constraint per check.

## [0.8.0] — 2026-08-16

Tier 0 no longer starts cold, and the tier model is demonstrable end to end
against a real server.

### Added

- **Warm-load from Cognee at startup** (`warm_load_from_cognee` in
  `app/services/memgraph.py`, called from the FastAPI lifespan). Reads the
  whole store through the adapter-agnostic `get_graph_data()` interface
  (turso/pgGraph/neo4j alike), maps node ids to their `name` property, and
  feeds every edge through `add_edge` — so canonicalization, dedup, junk
  rejection, and the LRU bound apply to warm-loaded edges exactly as to
  promoted ones, with provenance `cognee:warm_load`. An enhancement, never a
  validation: cognee missing, unconfigured, or empty logs a skip and returns
  0, and the graph still learns from promotion at runtime. Covered by unit
  tests with an injected fake engine.
- **`/healthz` reports the resident graph** (`enabled`, `nodes`, `edges`):
  Tier 0's residency is operational state, so it is observable.
- **`scripts/demo_e2e.py`** — an end-to-end demo that boots the real app
  under uvicorn (subprocess, not TestClient) in the production fail-closed
  posture and drives POST /answer through every tier boundary: warm-load +
  seed at startup, Tier-0 edge/path/no-path/neighbor answers with measured
  per-request latency, a p50/p95/p99 sweep, an honest fail-closed
  fall-through (no live retrieval → explicit non-answer with a reason), and
  the promotion learning loop (retrieval simulated, and labeled as such —
  everything else is the real pipeline). Each section asserts the behavior
  it claims; the script exits non-zero if any claim regressed, so the demo
  is also a smoke test.
- `configs/memgraph_seed.example.json`: a 12-edge commerce topology for
  seeding demos and local runs via `ROUBAIX_MEMGRAPH_SEED_PATH`.

### Changed

- Fail-closed telemetry now carries `tier: "pipeline"` like every other
  exit, so tier accounting has no untagged path.

## [0.7.0] — 2026-08-16

The problem statement, restated and rebuilt around: **fast and cheapest is
the product.** Sub-second whenever possible, fewest tokens when an LLM must be
paid, and a query that could not be fast must make its successors fast.

### Added

- **Tier 0: a resident in-memory graph** (`app/services/memgraph.py` +
  `graph_answerer.py`), checked after the cache and before routing.
  Structural queries — does A depend on B, what depends on X, how is A
  connected to B — answer by direct traversal: zero tokens, zero cost, and
  the zero is measured, not estimated. **Measured: p50 14µs per edge query;
  p50 0.19ms / p99 0.35ms full orchestrator round trip** on a 2,005-node
  graph, in-process.
- **The learning contract, implemented.** Accepted, non-degraded triplet
  evidence from the slow path is promoted into the resident graph
  (canonicalized, deduplicated, LRU-bounded with consistent eviction), so a
  differently-phrased structural follow-up — which exact-match caching can
  never serve — answers in Tier 0. Covered by an end-to-end test:
  first query pays for retrieval, second answers at tier=memgraph with
  input_tokens=0.
- Tier telemetry (`tier: memgraph|pipeline`), graph size gauges, promotion
  counts; `POLICY_VERSION` → 4 so pre-Tier-0 cached answers are not served
  under the new policy.

### Rules that keep the fastest tier trustworthy

Falls through, never guesses (pattern must match AND every entity must
resolve exactly — no fuzzy resolution); known-entities-with-no-path is an
honest finding while an empty neighborhood falls through; degraded evidence
is never promoted; node keys are canonicalized at insert so emergent
extraction's Sensor/sensor/MEMSSensor duplication collapses before entering
the graph — a lesson taken directly from the prior CXR ontology effort, whose
hand-authored OWL was never actually wired in and whose induction plan
existed largely to undo entity duplication.

## [0.6.1] — 2026-08-16

### Changed

- **Preflight now probes egress, not just keys.** A second measurement round
  established that in a sandboxed environment the network allowlist is the
  binding constraint: the Docker daemon starts, but every container registry's
  blob CDN is blocked (Docker Hub, ECR Public, ollama), as are
  huggingface.co, the LLM providers, and cognee.ai — while pypi and GitHub
  release downloads are open (and carry no usable LLM weights).
  `scripts/live_stack.py` reports which constraint binds and names the fix,
  including "add the provider to the environment's egress allowlist" when the
  key is present but the provider is unreachable.
- **Cognee Cloud is explicitly not-yet-a-path.** The installed SDK ships no
  cloud transport and `CogneeClient` implements none; if `COGNEE_API_KEY` is
  set, the preflight says the key cannot be used yet instead of letting it
  masquerade as a working configuration. The plan's "Going live" section
  records the three routes and what each needs.

## [0.6.0] — 2026-08-16

Phase D delivered; live-Cognee standup reduced to a single missing key.

### Added

- **Sub-question decomposition on GRAPH_COMPLETION escalations**
  (`app/services/decomposition.py`). Escalation-only by design: a query the
  router sent to GRAPH_COMPLETION directly routed cleanly and does not pay for
  a decomposition call. Sub-queries are schema-constrained by the NodeSet
  index, capped at 4, retrieved in parallel, and merged before packing.
  Degraded sub-results are dropped, never blended — fabricated evidence would
  be indistinguishable from live evidence after packing — and an all-degraded
  merge fails closed exactly as a single degraded retrieval. The plan's
  recall-vs-cost acceptance gate has not been run (needs the live stack) and
  no number is claimed.
- **`scripts/live_stack.py`** — one-command live standup with a preflight
  whose failure modes were measured, not assumed: `add` works fully offline
  (given `s3fs`, the embedded turso graph adapter, and skipping the connection
  test); `cognify`/`search` require exactly one thing, a working LLM key.
  Docker-less embedded profile (SQLite + LanceDB + turso), corpus seeding,
  per-mode smoke through Roubaix's own client, stamped reports with
  `quality_meaningful: false` whenever mock embeddings are in play. Exit 0
  live-ready, exit 2 prerequisites missing; nothing half-starts.
- `s3fs` declared in the `opt` extra — cognee's ingestion imports it even for
  local files and does not declare it itself.

### Observed while testing

The Phase A sufficiency gate rejected an early Phase D test fixture whose fake
sub-results did not cover the original query — the gates compose, and the
fixture had to provide exactly what decomposition exists to provide.
Fail-closed telemetry now carries `decomposed`/`subquery_count` so both exits
tell the truth.

### Still open

Phase C2 (learned triple scorer) and every live-stack gate: Phase D
recall-vs-cost, full-pipeline eval, scoping precision. All unlock with an LLM
key via `scripts/live_stack.py`.

## [0.5.0] — 2026-08-15

Phases A, B, and C1 of the research-backed implementation plan, built the same
day the plan landed. Every mechanism here traces to a claim that survived
3-vote adversarial verification; the plan records what did not.

### Added

- **Set-level sufficiency gate** (`app/services/sufficiency.py`), wired into
  the runtime controller between the latency ceiling and the volume floor.
  Sufficiency is judged over the packed evidence *set* — a per-item scorer
  cannot observe a missing bridge item — using stemmed query-term coverage,
  supporting-item ratio, and provenance diversity. Twelve on-budget items
  about the wrong entity now escalate or refuse instead of passing every
  count check. Tier 1 (optional `verify` extra) refines the uncertain band
  with a MiniCheck 770M adapter; its threshold is documented as an empirical
  dial because the model's [0,1] output is not demonstrably calibrated. The
  plan's acceptance gates are encoded as losable tests: ≥80% of mismatched
  evidence flagged (currently 100%), ≤10% false-INSUFFICIENT on matched
  evidence (currently 0%).
- **Evidentiality-ordered packing.** The token budget now cuts the least
  evidential items instead of the last-retrieved ones, with retrieval rank as
  the tie-break and one flag back to rank order.
  `best_dropped_evidentiality` telemetry is the observable for a budget that
  is cutting things the gate wanted.
- **NodeSet scope derivation by entity anchoring** (`app/services/scoping.py`):
  a lexical alias index (`ROUBAIX_NODESET_INDEX_PATH`) derives scope when the
  caller sent none; a caller-supplied scope is never widened or second-guessed.
  Matching is stemmed token-exact — the "port"-in-"support" substring bug
  class, now avoided in three places. The Cognee call sets
  `node_name_filter_operator="OR"` explicitly. POLICY_VERSION → 3.
- New `StopReason.EVIDENCE_CONFLICT`, reserved: the controller handles the
  REFUTED verdict, and the module states plainly that no current tier can
  emit it.

### Changed

- Controller and cache test fixtures are query-relevant now, because the
  controller checks what evidence is *about*, not just how much there is.
- The DSPy learned stage reuses the baseline's derived scope — it changes the
  mode, not the scope.

### Fixed

- Alias matching credited a nondeterministic alias (set iteration order); a
  flaky test caught it. Deterministic now, with a determinism test —
  unreplayable telemetry is not telemetry.

### Not yet run

- The scoping-precision acceptance gate (needs a scoping-labelled corpus
  extension), Tier-2 autorater (deliberately unbuilt), Phase D decomposition
  and C2 learned scorer (need live Cognee).

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
