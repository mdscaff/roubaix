# Evaluation Plan

## Evaluation goals

Quantify whether Roubaix improves:

- answer quality
- cost per successful answer
- latency
- freshness accuracy
- unnecessary escalation rate

## Query buckets

- local factual lookup
- relationship-heavy lookup
- multi-hop reasoning
- broad summary/explanation
- structural/graph query
- time-sensitive query
- ambiguous or under-specified query

## Metrics

### Business metrics

- cost per successful answer
- p50 / p95 latency
- freshness correctness
- successful-answer rate

### System metrics

- search mode distribution
- NodeSet scope size
- evidence items selected
- input tokens to synthesis model
- retries per query
- escalation reason

### Quality metrics

- correctness
- groundedness
- citation/provenance sufficiency
- temporal correctness

## Baselines

1. chunk-only RAG baseline
2. single graph-completion baseline
3. Roubaix rule-based routing baseline
4. full-context baseline — retrieve broadly and let the model sort it out; the
   comparison most likely to embarrass a graph system
5. Roubaix DSPy/GEPA-optimized configuration — **excluded from the default set**,
   because it requires an optional dependency and calls a paid API. Request it
   explicitly with `--baselines dspy_router`. It raises rather than falling back
   if DSPy is unavailable: a row labelled `dspy_router` that measured the
   deterministic router would report a comparison that never happened.

## Acceptance gates

- at least 25% reduction in median input tokens versus single-path graph baseline
- no degradation in overall correctness
- measurable improvement on temporal queries when temporal mode is enabled
- reduced unnecessary use of expensive graph modes

---

## Corpora

| File | n | Purpose |
|---|---:|---|
| `evals/queries.jsonl` | 20 | **Tuning set.** The router's rules were written against it. Accuracy here is an upper bound, not a measurement. |
| `evals/queries_heldout.jsonl` | 26 | **Held-out set.** Written without reference to the router's patterns and never tuned against. This is the number to quote. |

Both are small. Per-bucket rates rest on 2–4 queries each; treat differences as
directional, not significant. Growing the held-out set is the cheapest
credibility improvement available — but it only stays held out if the rules are
never adjusted to fix its misses.

## Measured results

Routing, `scripts/eval_routing.py`, no Cognee instance and no LLM required:

| Corpus | n | Accuracy | Best fixed mode | Lift | Unconfident share |
|---|---:|---:|---:|---:|---:|
| tuning | 20 | 100% | 30% | +70 pts | 35% |
| held-out | 26 | **85%** | 23% | **+62 pts** | 42% |

Accuracy split by the router's own confidence flag — the measurement that
decided where a learned stage belongs (ADR-005):

| Band | Share of held-out traffic | Accuracy | Misses |
|---|---:|---:|---:|
| confident | 58% | 93% | 1 of 4 |
| unconfident | 42% | 73% | 3 of 4 |

75% of the errors sit in 42% of the traffic, and the router identifies which 42%
before spending anything. That is the band the DSPy stage serves.

Held-out accuracy by bucket: ambiguous 100%, broad_summary 100%, local_factual
100%, multi-hop 100%, structural_graph 75%, time_sensitive 75%,
relationship-heavy 50%.

CI gates held-out accuracy at ≥80% with a required positive lift over the best
fixed mode (`.github/workflows/ci.yml`).

### Known misses — deliberately not fixed

These four failures are the honest cost of keeping the held-out set unbiased.
Tuning the rules until this corpus scores 100% would convert the only unbiased
measurement in the repo into a restatement of the rules.

| Query | Expected | Routed | Diagnosis |
|---|---|---|---|
| "Which team owns the notification service?" | `TRIPLET_COMPLETION` | `CHUNKS` | `relation.owner` fires but at weight 1.5, under the 2.0 threshold. A single ownership signal does not currently buy graph retrieval. |
| "Which upstream services call the identity provider?" | `TRIPLET_COMPLETION` | `GRAPH_COMPLETION` | Genuinely ambiguous. "Upstream" is a multi-hop cue; the question is one hop. Over-escalation — the expensive direction. |
| "Find all edges of type owns between teams and services." | `CYPHER` | `TRIPLET_COMPLETION` | `relation.between` + `relation.owner` (3.0) outweigh `structural.topology` (2.0). Structural intent lost to relational vocabulary. |
| "What has changed since the last release?" | `TEMPORAL` | `CHUNKS` | `temporal.change` fires at 1.0, under threshold. "Since the last release" is a relative window with no absolute date token. |

Three of the four are threshold or weight effects rather than missing patterns,
which is the argument for the learned second stage in `docs/roadmap.md` §5:
the ambiguous band is exactly where these land.

## Gate semantics

Gates report `PASS` / `FAIL` / `UNKNOWN`. **`UNKNOWN` is not a pass** — it means
the run could not establish the claim either way, usually because a comparison
baseline was not run or because no provider-reported token usage was seen.

Current gates, all of them losable comparisons:

- `input_tokens_25pct_below_graph_only` — the central cost thesis
- `acceptance_not_worse_than_graph_only` — a cost saving that cost quality is not a win
- `routing_beats_best_fixed_mode` — a router that cannot beat "always pick the commonest label" has earned nothing
- `cheaper_than_full_context` — against the `full_context` baseline
- `cost_figures_are_measured` — `UNKNOWN` whenever cost is estimated rather than reported

Note on the 25% target: published work puts a competent lexical router at
roughly 28% token savings. Clearing this gate shows the router is not broken; it
is not on its own evidence that the router is good. Report the oracle gap
alongside it.

## Validity warnings

Eval reports print these above the results rather than in a footnote:

- retrieval was degraded (stub/fallback) on N% of queries — latency, evidence
  counts, and quality describe the stub, not Cognee
- no LLM provider configured — answers are local templates, quality unmeasured
- no provider-reported usage seen — all cost figures are heuristic estimates
- corpus is small — differences are directional

## Benchmark plan

No external benchmark has been run yet. Minimum credible set before any quality
claim is published:

| Benchmark | Why |
|---|---|
| MuSiQue | Composed 2–4 hop questions; the set where graph structure demonstrably pays |
| 2WikiMultiHopQA | Text plus structure; largest published graph gains |
| HotpotQA | **Negative control.** Graph gains nearly vanish here in published results. Not beating chunks is the expected outcome, not a failure. |
| LongMemEval | Knowledge-update and abstention categories map directly onto the freshness policy and the fail-closed path |
| Full-context baseline | Always. The comparison most likely to embarrass a graph system. |

Report quality, input tokens, p50/p95 latency, mode distribution, and escalation
rate together. Reporting quality without cost, or cost without quality, is how
the unfalsifiable claims in this space get made.
