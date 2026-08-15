# ADR-005: DSPy as a learned second stage over the ambiguous band

- **Status:** Accepted (implemented; no compile run recorded)
- **Date:** 2026-08-15
- **Related:** [ADR-003](ADR-003-reject-adalflow-keep-explicit-controller.md),
  [ADR-004](ADR-004-evaluate-strands-adopt-patterns-not-dependency.md)

## Context

DSPy + GEPA has been named in this repository's architecture since the first
commit, and was never wired: `dspy_program.py` was an 18-line docstring plus an
unused dataclass, and `gepa_optimizer.py` returned `{"status": "not_implemented"}`.

Meanwhile the deterministic router got good. Measured on the held-out corpus
(26 queries written without reference to the router's patterns, never tuned
against): **85% accuracy against 23% for the best single fixed mode.**

So the question is not "should DSPy replace the router" — it is "where, if
anywhere, does a learned stage pay for itself".

## The measurement that decided it

Errors are not evenly distributed across the router's own confidence signal:

| Band | Share of traffic | Accuracy | Misses |
|---|---:|---:|---:|
| Router reports confident | 58% | 93% | 1 of 4 |
| Router reports **unconfident** | 42% | **73%** | **3 of 4** |

`RouteDecision.confident` is true when the winning mode beat its runner-up by
`CONFIDENCE_MARGIN`; false when nothing cleared `MIN_SCORE` or the win was
narrow. That flag already existed and nothing consumed it.

**75% of the errors live in 42% of the traffic, and the router can identify
which 42% before spending anything.**

## Decision

Run the learned stage **only on the unconfident band**.

- Confident decisions are returned unchanged and never reach an LM.
- An explicit `freshness_required` contract never reaches an LM either — that is
  a caller assertion, not a classification problem.
- Everything else consults a GEPA-compiled DSPy program.

`DspyRouter` implements the same `route()` contract as `QueryRouter`, so the
orchestrator and the eval baselines cannot tell which one they hold.

### Failure policy: enhancement, not validation

Per `docs/architecture.md`, validation failures are loud and enhancement
failures degrade quietly. This is an enhancement. Missing `opt` extra, missing
artifact, unconfigured LM, provider error, or an invalid mode string in the
output all fall back to the deterministic decision, incrementing a `fallbacks`
counter. Verified: with DSPy installed and no LM configured, an unconfident
query returns the deterministic route and the service keeps working.

**The one place this inverts is the eval harness.** `Baseline.DSPY_ROUTER`
raises if DSPy is unavailable rather than falling back, because an eval row
labelled `dspy_router` that silently measured the deterministic router would
report a comparison that never happened. Same principle as `UNKNOWN` never
reading as `PASS`: a measurement that cannot be made must not quietly become a
different measurement. It is also excluded from the default baseline set, so an
eval run never starts calling a paid API on its own.

### GEPA optimizes instructions, not weights

Correcting an assumption this repo previously held, including in a comment in
`router.py`: GEPA performs reflective *text* evolution, proposing instruction
mutations from observed failures along a Pareto frontier. It has no numeric
search space. What it tunes is the natural-language policy in the signature
docstring. The rule weights remain hand-tuned and measured. Numeric weight
tuning, if ever wanted, is Optuna over the existing eval harness — a different
and much cheaper project.

### The metric is the real design

Two properties, both tested without an LM:

1. **Cost-aware.** `0.7 * correct + 0.3 * cost_efficiency`. A correctness-only
   metric converges on `GRAPH_SUMMARY_COMPLETION` for everything: the most
   expensive mode is never *wrong*, only wasteful, and correctness cannot see
   waste. A test asserts that two correct answers do not score equally when one
   is more expensive.
2. **Returns text, not a float.** GEPA passes `feedback` to the reflection
   prompt verbatim. The feedback names the failure in Roubaix's vocabulary and
   distinguishes over-escalation (expensive, invisible downstream) from
   under-escalation (cheap, visible as a retry).

The evidence budget the program emits is clamped to 4–12, because an optimizer
rewarded partly on correctness would otherwise learn to ask for everything.

## Consequences

**Positive.** The expensive path is bounded by a signal the system already
computes, and its cost is directly observable (`llm_calls`, `fallbacks`). The
deterministic router remains the whole system when DSPy is absent — which is how
CI runs. Every component is testable without an API key: the metric is pure, and
the gating is exercised with a fake program.

**Negative.** The band is defined by the deterministic router's confidence, so a
recalibration of `CONFIDENCE_MARGIN` silently changes how much traffic reaches
the LM. That coupling is deliberate but should be watched; `unconfident_share`
is reported by `scripts/eval_routing.py` for exactly this reason.

**Not yet established.** No compile run has been recorded. Whether GEPA actually
beats the scored rule engine on the held-out corpus is an open question, and a
negative result is a legitimate and publishable finding given how well the
deterministic router already does. Judge a compiled artifact with:

```bash
uv run --extra opt python scripts/eval_routing.py --dspy-artifact artifacts/router_gepa.json
```

Compiling on the held-out corpus is refused by `scripts/optimize_router.py` —
training on the evaluation set would destroy the only unbiased measurement here.
