# Roubaix roadmap

Derived from an audit of this repository plus a survey of comparable systems
(Cognee upstream, Zep/Graphiti, Mem0, LightRAG, HippoRAG 2, GraphRAG and
LazyGraphRAG, Letta, Databricks Omnigent, DSPy/GEPA, LangGraph, Temporal).

Each item states what to do, why the evidence supports it, and which component
it lands in. Items are ordered by impact × implementability. Nothing here is
described as done.

---

## Diagnostic lens: harness, loop, graph

A useful framing for deciding *where* a reliability problem lives, and the rule
that comes with it: pick the layer by diagnosing the failure mode, not by
defaulting to the most complex one.

- **Harness** — the environment around the model: retrieval surfaces, evidence
  injection, caching, timeouts, budgets, telemetry, failure policy. This is
  where Roubaix lives and where nearly all of its value is.
- **Loop** — repeated work → evidence → feedback → stop conditions. Roubaix's
  loop is deliberately small: one bounded pass with a terminating widen/escalate
  ladder and an explicit stop reason. It is not an autonomous agent loop and
  should not become one.
- **Graph** — explicit topology, branching, joins, controlled cycles. Roubaix
  is a fixed six-stage pipeline. Modelling it as a graph would add a
  serialization format and a scheduler to express something that is currently a
  readable `for` loop. Revisit only if approvals or multi-specialist flows
  appear.

Nearly every remaining item in this roadmap is a *harness* item. That is the
correct centre of gravity for a retrieval service, and it is worth saying
explicitly, because the pull toward graph ceremony is strong and mostly wrong
here. See [ADR-004](adr/ADR-004-evaluate-strands-adopt-patterns-not-dependency.md).

## The positioning problem, stated first

**Cognee now ships its own rule-based query router upstream**
(`cognee/api/v1/recall/query_router.py`): weighted regex scoring, a default
mode, a score threshold. Architecturally it is the same object as
`app/services/router.py`.

So "we route to the cheapest mode" is no longer a differentiator — it describes
a feature the substrate has. What Roubaix's router still does that the upstream
one does not is emit an **evidence budget, a NodeSet scope, and a freshness
contract** alongside the mode, and feed those into a controller that can widen,
escalate, or refuse. That is the defensible claim, and the pitch should say
that rather than "we pick the cheapest mode".

Three refinements from the upstream router are worth porting outright. One
(negation suppression) is done; two remain:

- **Override telemetry.** Upstream keeps `override_counts: dict[(routed, override), int]`
  — a misrouting confusion matrix built from production disagreements. Roubaix
  records `signals` and `scores` per decision but never aggregates them into
  "which mode do we most often get wrong, and in which direction". That
  aggregate is the input to every later routing improvement.
- **Confidence-driven escalation.** `RouteDecision.confident` exists and is
  measured (93% accuracy on confident routes vs 85% overall on the held-out
  corpus) but nothing consumes it. The controller should escalate more readily
  on an unconfident route.

---

## Tier 1 — highest impact

### 1. Derive NodeSet scope instead of accepting it

`CLAUDE.md` calls NodeSet scoping "a first-class cost lever". It is currently
caller-supplied and passed through: if the caller sends nothing, nothing is
scoped, and the cheapest available cost lever is inert on every organic query.

Start lexical, not neural. Published routing work finds TF-IDF beats sentence
embeddings for query-type classification — surface keyword patterns are strong
predictors — so an entity match against known NodeSet names is the right first
implementation. Cognee also supports `node_name_filter_operator="OR"|"AND"`,
which Roubaix never sets.

*Lands in:* `QueryRouter._decision`, `CogneeClient.search`.

### 2. Emit OpenTelemetry GenAI spans *(partially done)*

**Done:** attribute naming (`app/observability/gen_ai.py`) maps Roubaix
telemetry onto `gen_ai.usage.*` / `gen_ai.request.model`, with Roubaix
dimensions namespaced under `roubaix.*`. Langfuse v4 is OpenTelemetry-based, so
these land in real spans. The per-span client construction and the blocking
`flush()` on the request path are both gone.

**Remaining:** a first-class span tree (`invoke_workflow` → `plan` →
`execute_tool` → `chat`) with an OTLP exporter, so traces work without Langfuse.

Original notes follow.

`RouteDecision.signals`, `ControlDecision.reason`, and the cost telemetry are
already exactly the structured data a serious harness exports — and all of it
currently dies at the process boundary. The existing Langfuse hook wraps one
operation, constructs a client per span, and calls a blocking `flush()` inside
the async request path.

Map: `invoke_workflow roubaix.query` → `plan roubaix.router` → `execute_tool
cognee.search` → `chat {model}`. Emit `gen_ai.*` attributes for the standard
fields and put Roubaix-specific dimensions under `roubaix.*` — never invent
`gen_ai.*` keys. Emit policy/controller decisions as their own spans; a
guardrail decision belongs in the same waterfall as retrieval and synthesis.

Two honesty constraints: nothing in the GenAI semantic conventions is marked
Stable, so pin a version and say "emits conventions vX (Development
stability)" rather than claiming compliance. And `gen_ai.usage.cache_read.input_tokens`
is the field that would finally make the prompt-cache claim falsifiable.

*Lands in:* new `app/observability/otel.py`, `orchestrator.py`, `synthesizer.py`,
`cognee_client.py`.

### 3. A retrieval-quality gate in the controller

The controller's only correctness proxy is evidence count and token volume. One
*irrelevant* chunk passes. Corrective-RAG's evaluator (correct / ambiguous /
incorrect) and Self-RAG's "is the generation supported by the passage" gate are
the established shapes here.

Do it cheaply first: max lexical overlap between query and packed items, and
provenance count. Only escalate to an LLM check when the cheap signals are
inconclusive. Map correct→ACCEPT, ambiguous→widen, incorrect→escalate.

*Lands in:* `runtime_controller.py`, using `evidence_hashes` and `token_estimate`
the packer already emits.

### 4. Capability drift probes in the eval harness

Borrowed from Omnigent's harness bench, which is the best idea in that codebase:
assert that *declared* capability matches *observed* behavior, and emit a
distinct `DRIFT` verdict when they disagree. If the router declares it can serve
a query class, or the client declares it honours a NodeSet scope, a probe should
confirm it empirically rather than trusting the flag.

Two rules that make it work: probes are component-agnostic (never name a
specific retriever — per-component facts live in a self-declared profile), and
an unprobed dimension reports `UNKNOWN`, never `PASS`. The `GateVerdict` enum
already models the second half.

*Lands in:* `app/evals/`.

---

## Tier 2 — clear value, more work

### 5. Wire DSPy + GEPA at the ambiguous band only

Keep the deterministic scored router as the cheap first stage; hand it a learned
second stage for the queries where nothing clears `MIN_SCORE` or the win is not
confident. On the held-out corpus that band is 42% of queries and contains most
of the errors — and the cheap path never pays for an LLM call.

**Correction to an earlier assumption in this repo:** GEPA reflects on
instruction *text* via LLM-proposed mutations over a Pareto frontier. It is not
a numeric hyperparameter optimizer, so handing it `Signal.weight` values is a
category error. What it should tune is the natural-language policy the fallback
module reasons with. Numeric weight tuning, if wanted, is Optuna over the
existing eval harness — a different and much cheaper project.

Two implementation notes that matter: GEPA's feedback metric should return
*text*, not a float — the string reaches the reflection prompt verbatim, so it
should name the failure in Roubaix's own vocabulary ("over-escalated to
GRAPH_SUMMARY_COMPLETION when TRIPLET_COMPLETION had sufficient evidence"). And
the metric must be cost-aware, or the optimizer will converge on the most
expensive mode for everything.

Gate it in `evals/`, not in production. If GEPA does not beat the scored rule
engine on the held-out corpus, that is a legitimate and publishable finding.

*Lands in:* `app/integrations/dspy_program.py`, `gepa_optimizer.py`, `evals/`.

### 6. Replace ordinal `COST_RANK` with measured cost

`COST_RANK` is integers 0–6 over modes whose real latency spans orders of
magnitude in the published literature and whose token costs span far more. The
orchestrator already records `retrieval_ms`, `evidence_tokens`, `input_tokens`,
and `estimated_cost_usd` per answer. Build a rolling per-mode empirical cost
table from those and let the router tie-break on measured cost.

*Lands in:* `app/core/tokens.py`, `app/observability/metrics.py`, `router.COST_RANK`.

### 7. Adopt the unused Cognee search modes

Roubaix models 7 of the ~18 modes upstream exposes. Three are directly useful:

- `CHUNKS_LEXICAL` — a new cost-rank floor below `CHUNKS`.
- `GRAPH_COMPLETION_CONTEXT_EXTENSION` — progressive neighbourhood widening; the
  natural lateral rung between `GRAPH_COMPLETION` and the summary mode.
- `GRAPH_COMPLETION_DECOMPOSITION` — sub-question decomposition, aimed at the
  genuinely multi-hop queries where graph structure demonstrably pays.

*Lands in:* `SearchMode`, `cognee_mapping.py`, `ESCALATION_LADDER`.

### 8. Supersession filtering at pack time

Graphiti's bi-temporal model (valid time vs transaction time, with contradicting
edges *invalidated* rather than deleted) is the strongest temporal primitive in
this space. Cognee is uni-temporal, so it cannot be had directly — but the
valuable half can be approximated: attach `(valid_from, valid_to, ingested_at)`
at ingest, then have the packer drop items superseded by a newer item asserting
the same subject-predicate. Edge invalidation as a pack-time filter is cheap,
observable, and makes freshness correctness measurable.

*Lands in:* `EvidencePacker`, alongside the existing dedup pass.

### 9. Write-side memory loop, off the request path

`_live_ingest` runs `add` + `cognify` synchronously inside the ingest request —
the worst of both cost models. Cognee's `improve(run_in_background=True)` folds
answer feedback into `feedback_weight` on the nodes and edges that produced an
answer, and `search(feedback_influence=...)` then biases retrieval toward what
worked. Letta's sleep-time-compute pattern is the same idea: expensive memory
reorganisation runs asynchronously so p95 is unaffected. This is the only
mechanism in the stack that makes retrieval quality improve with traffic.

*Lands in:* `CogneeClient`, plus a feedback field on `AnswerResult`.

---

## Tier 3 — queued, with a constraint written down first

### 10. Semantic cache — design rule before implementation

`ContentAddressedCache` is exact-match; `QueryNormalizer.fingerprint` exists for
near-duplicate detection but nothing consumes it. Before implementing:

> **No semantic (non-exact) cache hit for a query whose route sets
> `requires_freshness_validation`, and no semantic hit across differing
> `node_sets` or `user_id`. Scope-filter before similarity search, never after.**

The documented production failures here are silent factual errors and
cross-tenant leaks, not cache misses: queries differing only in time period,
polarity, or analytical intent routinely exceed 0.95 cosine similarity. Roubaix
already tags freshness-sensitive routes, which means it has the exact signal
needed to make this safe — the rule just has to be written before the code.

MinHash over the normalized token bag is also the better primitive than
embedding cosine: it is a lexical near-duplicate detector and is much less prone
to semantic drift.

### 11. Model cascade for synthesis

`max_latency_ms` is still declared and unread. Roubaix routes over retrieval
modes (the expensive axis for graph RAG) but uses one model for synthesis. The
free difficulty proxy is already in the data: a CHUNKS route with clean evidence
is a small-model job; a summary route after two escalations is not.

### 12. Resolve the two-orchestrator divergence

`RouterWorkflow` has drifted behind `QueryOrchestrator`: no controller, no
cache, no synthesis, and it drops the evidence budget. A durable workflow that
silently skips the fail-closed path is worse than no workflow. Since
`RuntimeController.decide()` and `QueryRouter.route()` are pure, they can be
called inline inside the workflow body — only retrieval and synthesis need to be
activities. Either bring it to parity or mark it experimental.

Durability is genuinely not needed for a sub-second read path. The honest
Temporal use case in this repo is **ingestion** — `cognify` is long-running and
non-idempotent. See ADR-002.

---

## Benchmarks to run before quoting any quality number

See `docs/evaluation-plan.md` for the full plan. The minimum credible set:

- **MuSiQue** and **2WikiMultiHopQA** — where graph structure demonstrably pays.
- **HotpotQA** — as a *negative control*. Published results show graph gains
  nearly vanish here. If Roubaix's graph modes do not beat chunks on HotpotQA,
  that is the expected result, not a failure.
- **LongMemEval** — its knowledge-update and abstention categories map directly
  onto the freshness policy and the fail-closed path. Abstention is currently
  Roubaix's best safety feature and is entirely untested against a benchmark
  that scores it.
- **A full-context baseline**, always. It is the comparison most likely to
  embarrass a graph system, and in at least one vendor's own published table it
  beat the memory system being marketed.

Report quality, input tokens, p50/p95 latency, mode distribution, and escalation
rate **together**. Reporting quality without cost, or cost without quality, is
precisely how the unfalsifiable claims in this space get made.

---

## Traps to stay out of

These are failure patterns observed in comparable systems, recorded so Roubaix
does not repeat them.

1. **LoCoMo-style leaderboard claims.** The benchmark most agent-memory vendors
   report on has documented ground-truth errors, and one vendor's headline score
   fell from 84% to 58% under re-evaluation with the adversarial category
   correctly excluded and the baseline prompt held constant. Both leading
   vendors publicly attack the benchmark they lose on and defend the one they
   win on. Cross-vendor numbers in this space are not comparable.
2. **Quoting a managed-platform number for open-source code.** At least one
   vendor's headline figures reflect their hosted product, not the package you
   install.
3. **"GraphRAG beats vector RAG" as a general claim.** It is false as stated;
   independent benchmarks find graph pipelines frequently underperform vanilla
   RAG on real-world tasks. Graph wins on genuine multi-hop and sense-making.
4. **Hiding graph construction cost.** Query-side latency claims routinely
   exclude ingestion, where the LLM extraction bill actually lands. Roubaix's
   cost telemetry currently measures synthesis only, so "cost per successful
   answer" is understated by whatever cognify cost.
5. **Treating a ~25% token reduction as evidence of routing skill.** That is
   roughly what a competent lexical router achieves. Clearing the bar shows the
   router is not broken. Report the oracle gap alongside it.
6. **Benchmarking on the pgGraph dev stack.** Documented upstream as a demo
   feature. Numbers from it are not production-representative.
