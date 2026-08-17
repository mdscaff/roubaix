# Implementation plan

Derived from a verified deep-research pass over the 2024–2026 literature
(2026-08-15): 26 primary sources fetched, 110 claims extracted, 25 put through
3-vote adversarial verification, 22 confirmed, 3 refuted. Every measured number
below survived that process; the papers' own headline claims did not get a free
pass. The previous scaffold-era plan this file replaces was delivered through
its Phase 2 and is preserved in git history.

Two ground rules carried over from the rest of this repo:

- A finding from an unreviewed preprint is labelled as one, and a self-reported
  margin is quoted as self-reported.
- Three of the five researched problem areas produced **zero surviving
  claims**. They are recorded in [What the research could not establish](#what-the-research-could-not-establish)
  rather than filled in with plausible-sounding defaults.

---

## What the evidence validated before proposing anything new

Worth stating first, because it means three existing design decisions should
*not* be churned:

1. **Chunk-default with graph-as-escalation is the right shape.** GraphRAG-Bench
   (ICLR 2026, arXiv:2506.05690) was motivated by the repeated finding that
   GraphRAG underperforms vanilla RAG on real-world tasks; vanilla RAG scores
   83.2% context recall on simple discrete-fact questions, and graph methods
   only pay off on multi-hop/aggregation. Only 6 of 9 graph methods beat BM25
   at all, by 0.2–2 points. Mode selection matters more than "use a graph" —
   which is Roubaix's thesis, now with independent benchmark support.
2. **Token ceilings on the widen/escalate path are a correctness lever, not
   just a cost lever.** The same benchmark measured Global-GraphRAG's prompt
   growing ~5× (≈7,800 → ≈40,000 tokens) with difficulty, with the added tokens
   often redundant and *degrading* context relevance. The packer's hard token
   budget on escalated routes stays, and gets an explicit test.
3. **Fail-closed must be externally driven — never delegated to the synthesis
   model's own refusal behavior.** Sufficient Context (ICLR 2025,
   arXiv:2411.06037) shows frontier models (Gemini 1.5 Pro, GPT-4o, Claude 3.5)
   answer incorrectly rather than abstain on insufficient context;
   AbstentionBench (NeurIPS 2025 D&B, arXiv:2506.09038) corroborates across 20
   models and finds reasoning fine-tuning actively *degrades* abstention.
   Roubaix's controller-driven refusal is the right mechanism; "ask the model
   to say when it doesn't know" is not a fallback plan.

---

## Phase A — Set-level sufficiency gate in the runtime controller — **DELIVERED (Tiers 0–1)**

Shipped in `app/services/sufficiency.py` + controller wiring. Delivered scope:
Tier 0 (stemmed set-level lexical signals, always on) and Tier 1 (pluggable
scorer with a MiniCheck adapter behind the `verify` extra, refining only the
UNCERTAIN band, failure-latching). Tier 2 remains unbuilt, as decided below.
Both acceptance gates are encoded as losable tests against the held-out
queries and currently pass at 100% flagged / 0% false-INSUFFICIENT.

**The highest-value item.** The controller currently gates on evidence count
and token volume; one irrelevant chunk passes. The literature converged on two
design constraints:

- **Sufficiency is a set-level property** (SURE-RAG, arXiv:2605.03534; field
  consensus across ≥4 2025–2026 papers). Per-chunk relevance scoring
  structurally cannot detect a missing multi-hop link — a scorer cannot observe
  an absent bridge passage — and mean-pooling dilutes a decisive refutation.
  The gate must aggregate over the packed evidence *set*.
- **It does not need a frontier-LLM call.** SURE-RAG's calibrated encoder beats
  a GPT-4o judge at the three-way supports/refutes/insufficient task (0.9075 vs
  0.7284 Macro-F1 — in-domain-trained vs zero-shot, so an asymmetric
  comparison, and it reverses on HaluBench). MiniCheck-FT5 (EMNLP 2024,
  arXiv:2404.10774; 770M, Apache-2.0, pip-installable) reaches GPT-4-level
  fact-checking (74.7% vs 75.3% balanced accuracy on LLM-AggreFact) at ~400×
  lower cost.

### Design: a three-tier gate, cheapest first

```
Tier 0  (free, always on)      set-level lexical signals
Tier 1  (770M encoder, opt-in) MiniCheck per-sentence support scores, aggregated
Tier 2  (LLM autorater, opt-in) Sufficient-Context-style (query, evidence) call
```

- **Tier 0** — pure Python, no new dependency. Signals computed over the packed
  set as a whole: query-term coverage (fraction of `QueryNormalizer.keywords()`
  present anywhere in the set — set-level by construction), evidence-set
  internal agreement (pairwise fingerprint overlap), and provenance diversity.
  Verdict `sufficient / uncertain / insufficient` with thresholds in config.
- **Tier 1** — new optional extra `verify` (`minicheck`). Runs only on Tier-0
  `uncertain`. Per-item support probability for the *query as claim proxy*,
  aggregated min/mean into the set verdict. The [0,1] output is **not
  demonstrated to be calibrated** (no ECE analysis in the paper) — thresholds
  are tuned empirically on the eval corpus, and the config comment says so.
  CPU inference of a 770M T5 is seconds-scale; the config gates it to
  escalated/unconfident routes by default so the hot path stays fast.
- **Tier 2** — one LLM call with the Sufficient Context autorater prompt shape
  (93% accuracy on their 115-instance human-labeled set — a small sample,
  quoted as such). Off by default; the option exists for deployments that
  already pay for synthesis and want the 2–10% selective-accuracy gain the
  paper measured from combining sufficiency with confidence.

### Wiring

- New `app/services/sufficiency.py`: `SufficiencyVerdict` (StrEnum:
  `SUFFICIENT / UNCERTAIN / INSUFFICIENT / REFUTED`) + `SufficiencyGate` with
  the tier ladder. Pure function of `(QueryRequest, PackedEvidence)`.
- `RuntimeController.decide()` consumes the verdict **between** the degraded
  check and the thin-evidence check: `INSUFFICIENT` → widen (first time) /
  escalate; `REFUTED` → fail closed with new `StopReason.EVIDENCE_CONFLICT`;
  `UNCERTAIN` at the retry limit → accept with
  `StopReason.THIN_EVIDENCE_ACCEPTED` and the verdict in telemetry.
- The existing count/token thin-evidence heuristic remains as the floor — the
  gate augments it, and `CheckErrorPolicy` already covers a gate that throws.

**Effort:** M (Tier 0 + wiring + tests), S more for Tier 1.
**Acceptance gates (losable):** on the held-out corpus with stub evidence
deliberately mismatched to queries, the gate must flag ≥80% as
non-sufficient; on well-matched evidence, false-`INSUFFICIENT` ≤10%. A new
drift probe asserts the tiers actually run when configured (an unprobed tier
reports UNKNOWN, never PASS).
**Risks:** SURE-RAG is a ~3-month-old unreviewed preprint — we borrow its
*set-level* framing (independently corroborated), not its model. All
benchmarks are open-domain QA; transfer to Cognee-style graph evidence is
unmeasured, which is exactly what the acceptance gates check.

---

## Phase B — Evidentiality-aware packing with a reflection-widen loop — **DELIVERED**

Shipped: the packer orders deduplicated items by stemmed query-term overlap
(the same matching as the gate, so packing and gating agree) before the
budgets bite; `best_dropped_evidentiality` is the budget-pressure observable;
`ROUBAIX_EVIDENTIALITY_ORDERING=false` restores rank order.

ECoRAG (ACL 2025 Findings, arXiv:2506.05167) validated the pattern Roubaix's
controller already half-implements: compress evidence to what is *evidential*
(necessary and non-interfering for the answer), then run a cheap reflection —
their 770M evaluator emits a single token per iteration — to judge sufficiency
of the *compressed* set, widening until sufficient or a token limit. Honest
read of their numbers: gains over strong compressors are sub-1.5 EM points;
**the real win is token reduction at equal-or-better accuracy**, which is
Roubaix's headline metric.

Adaptation (their trained compressor needs LLM-mined labels we don't have):

1. Packer orders items by a query-aware evidentiality proxy (Tier-0 signals
   from Phase A, per-item) instead of raw retrieval order, before the token
   budget bites — so the budget cuts the *least* evidential items, not the
   last-retrieved ones. Retrieval rank becomes a tie-break rather than the
   order.
2. The widen path re-packs with the gate's verdict attached, closing the
   ECoRAG loop with machinery that already exists (`WIDEN` is latched
   once, so the loop is bounded by construction).
3. `dropped_over_budget` telemetry gains the evidentiality score of the best
   dropped item — the observable for "the budget is cutting things the gate
   wanted".

**Effort:** S–M. **Acceptance gate:** median packed tokens on the eval corpus
does not increase, and the Phase-A false-`INSUFFICIENT` rate does not worsen
with reordering on. **Risk:** a weak proxy could reorder worse than retrieval
rank — the gate above is the tripwire, and rank-order remains one config flag
away.

---

## Phase C — NodeSet derivation by entity anchoring

The roadmap's #1 item, now with a validated mechanism. SubgraphRAG (ICLR 2025,
arXiv:2410.20724) shows query-to-subgraph scoping needs **no LLM in the
retrieval loop**: anchor on linked topic entities to prune the space, then
score triples with a lightweight MLP + directional distance encoding —
outperforming GNN/LLM/heuristic retrievers (WebQSP triple recall ~0.883 vs RoG
0.713) at near-cosine-baseline speed.

Staged to match what Roubaix actually has:

1. **C1 — lexical entity anchoring — DELIVERED, gate RUN (2026-08-16)**
   (`app/services/scoping.py`: alias index from `ROUBAIX_NODESET_INDEX_PATH`,
   stemmed token-exact matching, caller scope always wins,
   `node_name_filter_operator="OR"` set explicitly, POLICY_VERSION bumped).
   **Acceptance gate result: 92% precision / 92% recall (gate: ≥70%
   precision), caller-scope invariant holds** — on the scoping-labelled
   extension of the held-out corpus (`evals/queries_scoping.jsonl`, 34 rows,
   `evals/nodesets_eval.json`), measured through `QueryRouter.route` with the
   index injected, gated in CI via `scripts/eval_scoping.py --min-precision
   0.70`. Caveat stated in the eval module: labels and index share an author,
   so this is judgment-anchored, not blind. The corpus pins the matcher's
   known failure modes so they cannot be papered over: verb/name collisions
   (`sc-trap-003/004`) count against precision, lexical gaps
   (`sc-recall-001/002`) against recall, and a test fails if relabelling ever
   "fixes" them without a mechanism change — recall is Phase C2's territory.
   Original notes: Maintain a NodeSet name/alias index
   from Cognee dataset metadata; match query keywords against it (the earlier
   research pass already found lexical beats dense embeddings for exactly this
   kind of scoping); emit matched NodeSets on `RouteDecision.node_sets` with a
   `scope.entity_match` signal. Caller-supplied scope still wins. Set Cognee's
   `node_name_filter_operator` explicitly instead of relying on its default.
2. **C2 — learned triple scorer (deferred, gated).** SubgraphRAG's MLP needs
   weak-supervision labels (shortest paths from topic to answer entities) that
   a Cognee graph lacks out of the box, and its benchmarks used *gold* entity
   annotations. Deferred until live-retrieval telemetry can mine those labels;
   the deferral condition is written here so it isn't silently forgotten.

**Effort:** C1 is M. **Acceptance gate:** on a scoping-labelled extension of
the held-out corpus, entity-derived NodeSets ≥70% precision against hand
labels; never *narrower* than a caller-supplied scope. **Refuted-claim note:**
verification killed the claim (from arXiv 2506.02404) that entity-anchored
retrieval is *insufficient* for reasoning chains — the anchoring approach
stands, but C1's output on multi-hop routes feeds Phase D rather than being
trusted alone.

---

## Phase D — Schema-constrained sub-question decomposition (escalation-only) — **DELIVERED; gate HALF-RUN, NOT PASSED**

Shipped in `app/services/decomposition.py` + orchestrator wiring: fires only
when the controller escalates into GRAPH_COMPLETION, fan-out capped at 4,
schema drawn from the NodeSet index, degraded sub-results dropped rather than
blended, all-degraded merges degraded.

**Status after the first live run (2026-08-17).** Until that run the mechanism
was delivered but *unreachable*: nothing bound a DSPy LM outside the GEPA
compile script, so every `decompose()` raised "No LM is loaded", latched
`_failed`, and degraded to single-query retrieval — silently, because that is
the designed fallback. Measured: **0/46 queries decomposed**. With the LM bound
(`dspy_program.inference_lm`), 15/46 fire.

The acceptance gate is **not passed**, and only half of it could be run:

- **Cost half — clears.** 1.06× input tokens on the n=15 population where
  decomposition fires (gate allows ≤1.5×).
- **Recall half — cannot be run at all.** There are no gold evidence labels in
  this repository, and the only observable proxy is saturated: `evidence_items`
  is exactly 1 for all 15 queries in *both* arms. A comparison that risked
  nothing is not a gate, so no adoption claim is made.

Two things for whoever writes the labels. First, the gate names the multi-hop
bucket, but the escalation ladder is `GRAPH_COMPLETION →
GRAPH_SUMMARY_COMPLETION` and every multi-hop query routes *directly* into
GRAPH_COMPLETION — it can only escalate out, never in. Decomposition is
unreachable on that bucket by construction; it fires on relationship-heavy and
ambiguous queries instead, so the gate's population needs restating. Second,
the seeded corpus (1KB, 20 nodes) is too small for fan-out to surface anything
a single query misses; the substrate has to grow with the labels.

Original design notes follow.

Youtu-GraphRAG (ICLR 2026, arXiv:2508.19855; MIT-licensed repo) decomposes
complex queries into parallel sub-queries **constrained by the graph's declared
schema** — one LLM call, aligned to known entity/relation/attribute types, so
decomposition can't invent unanswerable sub-questions. Mechanism verified
against the repo; the headline margins (16.62% accuracy, 33.6% token
reduction) are self-reported and unreplicated, so we adopt the mechanism and
claim nothing until our own eval says so.

Scoped tightly: runs **only** when the controller escalates into
`GRAPH_COMPLETION` (per Finding 10, that is where multi-hop structure pays) —
never on the cheap path. Sub-queries retrieve in parallel against the
C1-derived NodeSets; results merge through the existing packer dedup.
`StopReason` and telemetry record `decomposed: true` and the sub-query count.

**Effort:** M–L, needs live Cognee to be meaningful. **Acceptance gate:** on
the multi-hop bucket under live retrieval, decomposition must beat
single-query GRAPH_COMPLETION on evidence recall at ≤1.5× token cost — a
losable comparison, run before any adoption claim.

---

## Phase E — conditional / deferred

- **TARG-style skip-retrieval gate** (arXiv:2511.09803, Nov 2025 preprint):
  logit-derived uncertainty from a short no-context draft decides whether to
  retrieve at all — 70–90% retrieval reduction at matched EM/F1 on open-domain
  QA, with **no trained evaluator**. Deferred because it needs logprobs from
  the serving stack (self-hosted only; most hosted APIs don't expose them) and
  Roubaix's grounding-first posture means answering *without* retrieval is a
  policy change, not just an optimization. Recorded so the option isn't lost:
  condition to revisit = self-hosted synthesis model.
- **Bespoke-MiniCheck-7B** as a Tier-1 upgrade (77.4% on LLM-AggreFact): a
  late-2024 self-reported leaderboard position, likely superseded, and its
  license requires commercial terms. Re-evaluate against 2026 verifiers
  (e.g., Paladin-mini) if Tier 1 proves load-bearing.

---

## What the research could not establish

Three of the five researched areas produced zero claims that survived
adversarial verification. These stay open, with what would change their status:

1. **Learned routing / cascades.** ~~No verified evidence that a learned router
   beats a strong rule baseline.~~ **Status changed 2026-08-17: our own compile
   run landed numbers.** GEPA-compiled on the tuning corpus, judged on the
   held-out one: **96% (25/26) vs the deterministic baseline's 85% (22/26)**,
   learned stage consulted on all 26 with 0 fallbacks, stable across 4 repeats.
   The single miss is `ho-rel-004` (expected TRIPLET_COMPLETION, routed
   GRAPH_COMPLETION).

   Read narrowly, for three reasons. 85% → 96% is **3 queries on n=26** — well
   inside what a differently-drawn corpus could move. The external literature
   still offers no verified GEPA results for classification-shaped tasks, so
   this is a local result, not a confirmation of one. And the first judge run
   reported exactly 85% because the compiled program fell back on 18/18 queries
   for want of a bound LM — a compile-then-judge that never judged the compiled
   program, and a reminder that this pipeline can report the baseline while
   appearing to test the candidate. A larger held-out corpus is what would turn
   this into a claim.
2. **LLM-as-judge reliability for answer correctness.** Survived only
   indirectly (SURE-RAG's GPT-4o judge underperforming an in-domain encoder on
   one task; the abstention results above). The eval plan's answer-quality
   metric therefore stays unbuilt rather than built on an unvalidated judge —
   `accepted_rate` keeps its documented "not a quality metric" label.
3. **Semantic-cache safety and prefix-cache economics.** No production
   precision thresholds, poisoning incidents, or measured prefix-cache
   economics survived verification. The pre-committed interlock rule (no
   semantic hit on freshness-required routes or across scope/tenant) stays as
   the design constraint, and the MinHash layer stays queued behind it.

Also refuted outright, and not to be relied on: both claims from arXiv
2510.18633 on bandit-style sub-query exploration.

---

## Live stack standup — measured boundary (2026-08-16)

`scripts/live_stack.py` is the one-command standup. Probing the installed SDK
(cognee 1.4.2) in an offline environment established exactly where "live"
begins, and the script's preflight encodes it:

| Step | Works offline? | Notes |
|---|---|---|
| `add` | **Yes** | given `s3fs` (now in the `opt` extra — cognee imports it even for local files), the embedded `turso` graph adapter (the default ladybug adapter downloads its JSON extension at first use, which fails behind restricted egress), and `COGNEE_SKIP_CONNECTION_TEST=true` |
| `cognify` | **No** | entity extraction has no mock; without a working LLM it spins in litellm retries until timeout |
| `search` | **No** | requires cognified data |
| embeddings | mockable | `MOCK_EMBEDDING=true` runs the plumbing with fake vectors; every report from that mode is stamped `quality_meaningful: false` |

Two corrections from the first run that actually went live (2026-08-17):

- **`cognify` does not populate the triplet_text collection.** TRIPLET_COMPLETION
  returns `NoDataError` until the `create_triplet_embeddings` memify pipeline
  runs, so `all_modes_live` could never be true. The standup now runs it after
  cognify.
- **OpenRouter serves `/v1/embeddings`**, verified with real vectors, so an
  OpenRouter-only setup gets real embeddings and never needs `MOCK_EMBEDDING`.
  The bridge in `cognee_setup.py` sets `LLM_ENDPOINT` but not
  `EMBEDDING_ENDPOINT`, so that setup must set `EMBEDDING_ENDPOINT` and
  `EMBEDDING_API_KEY` explicitly — otherwise the OpenRouter key is sent to
  `api.openai.com` and embeddings 401. The preflight's embedding check also
  only recognises `OPENAI_API_KEY`/`EMBEDDING_API_KEY`, so it reports "no
  embedding provider" for an OpenRouter-only environment that in fact has one.

### Going live: three routes, probed 2026-08-16

A second probing round (Docker daemon, container registries, ollama, GitHub
release assets, cognee.ai) sharpened the boundary: **the binding constraint in
a sandboxed environment is the egress allowlist, not keys and not Docker.**
Measured here: the Docker daemon starts fine, but every registry's blob CDN
(Docker Hub, ECR Public, ollama's registry) is blocked, as are huggingface.co,
api.openai.com, openrouter.ai, and cognee.ai; pypi and github.com release
downloads are open, but no reachable source ships usable LLM weights. The
preflight in `scripts/live_stack.py` now probes egress as well as keys, so it
reports *which* constraint binds.

1. **This environment (or any sandbox), one allowlist change.** Add
   `openrouter.ai` (or `api.openai.com`) to the environment's egress
   allowlist — for Claude Code web environments the owner sets this in the
   environment's network settings — set the key in a gitignored `.env`, and
   `uv run --extra opt python scripts/live_stack.py` stands the stack up with
   the embedded profile. Adding `api.openai.com` also gives real embeddings.

   **This was done on 2026-08-17, and the "everything below the line then
   unlocks" claim it used to carry was wrong.** Egress plus a key unlocked the
   standup and the GEPA compile-then-judge. It did *not* unlock Phase D's
   recall-vs-cost gate or C2's label mining: both need an evidence/answer
   labelled corpus, and `evals/*.jsonl` carry only `expected_mode`. What blocks
   those two is labels, not infrastructure — and no amount of egress will
   change that.
2. **A developer machine with Docker.** `docker compose -f
   docker/docker-compose.yml up` (Postgres + pgGraph) plus the same keys; or
   a local Ollama for a keyless-but-real LLM. Same one-command standup.
3. **Cognee Cloud (cognee.ai) with an API key.** Honestly the least ready:
   the installed SDK ships **no cloud transport**, `CogneeClient` implements
   none, and cognee.ai is egress-blocked from this environment, so its API
   shape cannot even be read from here. The `COGNEE_API_KEY` /
   `COGNEE_BASE_URL` settings exist but are documented as unused. Building
   the REST client is a real task that needs reachable API docs — recorded
   here so a set key is never mistaken for a working path, and the preflight
   says exactly that if it finds one.

An API key is a secret: it belongs in the gitignored `.env` or the
environment's secret store, never in a committed file or a chat transcript if
avoidable — and a key that has been shared around before is a good candidate
for rotation before use.

## Sequencing and dependencies

```
A (sufficiency gate, Tier 0)  ──►  B (evidentiality packing)   [shares signals]
A Tier 1 (MiniCheck)          ──►  optional, behind `verify` extra
C1 (entity NodeSets)          ──►  D (decomposition targets C1 scopes)
D, C2                         ──►  require live Cognee + telemetry labels
E                             ──►  conditional on serving-stack changes
```

A and C1 are independent and can land in either order; both are pure-Python
against existing tests. B follows A. D and C2 are the first items that
*require* the live stack, which makes standing up live Cognee + a seeded
corpus the enabling task for everything below the line — and the same
prerequisite the benchmark plan in `docs/evaluation-plan.md` is already
waiting on.
