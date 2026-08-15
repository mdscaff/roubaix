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

## Phase A — Set-level sufficiency gate in the runtime controller

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

## Phase B — Evidentiality-aware packing with a reflection-widen loop

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

1. **C1 — lexical entity anchoring (now).** Maintain a NodeSet name/alias index
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

## Phase D — Schema-constrained sub-question decomposition (escalation-only)

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

1. **Learned routing / cascades.** No verified evidence that a learned router
   (GEPA-compiled or Adaptive-RAG-style) beats a strong rule baseline like the
   85% deterministic router, and no verified GEPA compile results in the wild
   for classification-shaped tasks. This *raises* the evidentiary bar for
   ADR-005's compile run: the plan remains compile-then-judge on the held-out
   corpus, and a negative result is now the literature-consistent outcome, not
   a surprise. Status changes when our own compile run lands numbers.
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
