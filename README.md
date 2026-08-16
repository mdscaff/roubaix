# Roubaix

Cognee-centered graph retrieval with cost-aware routing, progressive escalation, and a fail-closed runtime controller.

## Why this exists

Roubaix routes every query to the **cheapest valid** Cognee retrieval mode before paying for graph depth. The goal is measurable outcomes: lower cost per answer, better multi-hop quality, freshness when it matters, and telemetry that survives scrutiny.

The design constraint that shapes everything else: **use the graph to retrieve and compress evidence, not to flood the prompt.**

## What is actually measured

Routing is the one part of the pipeline that can be measured honestly without a live graph or an LLM, so it is the one part with numbers. Each is reported with the corpus it came from and the reference points that make it readable.

| Metric | Value | How to read it |
|---|---|---|
| Routing accuracy, **held-out** corpus (n=26) | **85%** | The honest number. These queries were written without reference to the router's patterns and have not been tuned against. |
| Routing accuracy, tuning corpus (n=20) | 100% | An upper bound, not a measurement. The rules were written against this corpus. |
| Best single fixed mode, held-out | 23% | What "always pick the commonest mode" scores. The router's lift is **+62 points**. |
| Accuracy when the router reports confidence | 93% | 58% of traffic. |
| Accuracy when the router reports **low** confidence | 73% | 42% of traffic, holding 3 of the 4 misses. This is the band the DSPy stage serves. |

Reproduce with `uv run python scripts/eval_routing.py`. It needs no Cognee instance, no LLM, and no network — routing is a pure function of the query, which is why it is the one metric gated in CI.

The four held-out misses are listed in [docs/evaluation-plan.md](docs/evaluation-plan.md) and are deliberately **not** fixed. Tuning the rules until the held-out corpus scores 100% would turn the only unbiased measurement in this repo into a restatement of the rules.

### What is not measured

Stated plainly, because the gap between these two lists is where this kind of project usually oversells:

- **Cost per answer.** The plumbing is real — provider-reported token usage when available, explicitly labelled estimates otherwise, a per-model price table, cost in every trace. But no run against live Cognee with a live LLM has been recorded here, so there is no cost figure to quote.
- **Answer quality.** There is no ground-truth answer in the corpus and no LLM judge. `accepted_rate` measures "the controller did not refuse", which is not a quality metric.
- **Latency.** Eval runs against stub retrieval report ~0ms. Those numbers describe the stub.
- **Prompt-prefix cache savings.** The prefix is structured for it but is ~120 tokens, under every provider's minimum cacheable prefix. The discount is currently zero. See the note in `app/services/synthesizer.py`.
- **Multi-hop quality vs. a vector baseline.** Not yet run against MuSiQue / 2WikiMultiHopQA / HotpotQA. See the benchmark plan in [docs/evaluation-plan.md](docs/evaluation-plan.md).

Eval reports print these as **validity warnings above the results**, and gates report `UNKNOWN` rather than `PASS` when a claim could not be established.

## Runtime flow

```text
Query → Normalize → Cache check → Route → Retrieve → Pack evidence
      → Runtime control ─┬─ accept   → Synthesize → Cache → Return
                         ├─ widen    → same mode, larger budget
                         ├─ escalate → next mode up a terminating ladder
                         └─ fail closed (explicit non-answer, never cached)
```

## Design decisions worth arguing with

**Routing is a scored rule engine, not an if/elif ladder.** Competing signals are common — "match all services *connected to* the billing node" is both structural and relational. A ladder resolves that by author ordering; a score resolves it by weight, ties break toward the cheaper mode, and every decision records the named signals that produced it, so a route can be replayed from telemetry rather than re-argued.

**Fail-closed is real, and it fires on six conditions**: fabricated evidence (a retrieval failure must never be synthesized into a fluent answer), a freshness contract that produced no dated evidence, an exhausted escalation ladder, an exhausted retry budget, a failed LLM call, and a control check that itself raised — because a check that cannot run has not passed. Each carries a `stop_reason` from a closed vocabulary.

**Freshness means a timestamp exists.** Temporal retrieval degrades silently to unfiltered search when it cannot extract an interval from the query — evidence comes back, so a count-based gate marks the contract met. Roubaix requires a parseable date in the packed evidence and refuses otherwise.

**Escalation widens before it climbs.** Depth is a cheaper dial than algorithm, so the controller retries the same mode with a larger evidence budget before buying a more expensive one.

**Cost ceilings shrink the request, they don't refuse it.** A caller's `max_cost_cents` trims the evidence pack to fit rather than failing — the caller asked for a cheaper answer, not no answer. A `max_latency_ms` ceiling stops the loop and returns whatever usable evidence exists. Both are soft caps checked at loop boundaries, so an in-flight retrieval can overshoot; that is stated rather than implied.

**Stopping is a vocabulary, not a log line.** Every outcome carries a `stop_reason` from a closed enum — `sufficient_evidence`, `limit_latency`, `freshness_unverifiable`, `degraded_retrieval`, `ladder_exhausted`, and so on. A budget trip is an outcome, not an error, so it lives in the same enum as a normal accept. That makes "how often do we stop on the latency ceiling" a query over telemetry rather than a grep over strings.

**The learned router earns its place or does not run.** DSPy is not a replacement for the deterministic router — it is a second stage gated on the router's own confidence flag, so 58% of queries never reach an LM. Any failure (no `opt` extra, no LM, provider error, invalid output) falls back to the deterministic decision. The eval baseline is the one place this inverts: it raises rather than falling back, because a row labelled `dspy_router` that silently measured the deterministic router would report a comparison that never happened.

**Sufficiency is judged at the set level.** Twelve on-budget items about the wrong entity pass every count and token check — so the controller now asks what the packed evidence set is *about*: stemmed query-term coverage over the union of the set (a multi-hop answer split across a bridge is not penalized), with an optional 770M verifier refining the uncertain band. Off-topic sets are refused or escalated; a fully-covering set is an answer, however small.

**Evidence reduction is disclosed.** Withheld items leave a marker naming how to retrieve them, so the synthesizer can distinguish "there was no more evidence" from "there was more and it was dropped".

## Current state

| Component | Status |
|---|---|
| Scored router + cost-rank tie-break + negation handling | Working, gated in CI |
| Content-addressed cache (LRU + TTL) | Working; key covers query, dataset, freshness, scope, model, policy version |
| Cognee retrieval | Live SDK when configured; flagged-degraded stub otherwise |
| Evidence packing (dedup, token budget, disclosure) | Working |
| Runtime controller (widen → escalate → fail closed) | Working |
| Set-level sufficiency gate (Tier 0 lexical; Tier 1 MiniCheck via `verify` extra) | Working; acceptance gates encoded as losable tests |
| Evidentiality-ordered packing + budget-pressure observable | Working; one flag back to rank order |
| NodeSet derivation by entity anchoring (`ROUBAIX_NODESET_INDEX_PATH`) | Working; caller scope always wins |
| Sub-question decomposition on GRAPH_COMPLETION escalations | Working; recall-vs-cost gate awaits the live stack |
| Live stack bootstrap (`scripts/live_stack.py`) | Preflight + embedded profile + seeded smoke test; needs an LLM key to go live |
| Cost accounting (measured vs estimated) | Working; no live run recorded |
| Caller ceilings (`max_cost_cents`, `max_latency_ms`) | Working; cost trims the pack, latency stops the loop |
| Stop-reason vocabulary + OTel `gen_ai.*` attributes | Working |
| Eval harness (5 baselines, losable gates, validity warnings) | Working; 4 run by default, `dspy_router` on request |
| LLM synthesis | OpenRouter; failures fail closed |
| NodeSet scoping (learned triple scorer, C2) | Deferred until live telemetry can mine training labels |
| Temporal / Nexus | Scaffold; behind the main orchestrator (see ADR-002) |
| DSPy / GEPA learned router | **Wired** — runs only on the unconfident band (42% of traffic, holding 75% of errors); degrades to deterministic on any failure. No compile run recorded yet. See [ADR-005](docs/adr/ADR-005-dspy-learned-stage-over-the-ambiguous-band.md) |
| AdalFlow | **Rejected** — see [ADR-003](docs/adr/ADR-003-reject-adalflow-keep-explicit-controller.md) |
| Strands Agents SDK | **Patterns adopted, dependency refused** — see [ADR-004](docs/adr/ADR-004-evaluate-strands-adopt-patterns-not-dependency.md) |

**182 tests passing** with the optional `opt` extra installed; 121 passing and the DSPy suite skipped without it, which is how CI runs. All dependencies current as of August 2026.

## Quickstart

```bash
cp .env.example .env
# Set OPENROUTER_API_KEY (synthesis) and OPENAI_API_KEY (embeddings) as needed

uv sync --extra dev
uv run uvicorn app.api.main:app --reload
```

Open the browser demo at [http://localhost:8000/demo](http://localhost:8000/demo).

Without a live Cognee instance, retrieval returns stub evidence flagged `degraded`, and the controller **fails closed** rather than answering from it. Set `ROUBAIX_ALLOW_STUB_EVIDENCE=true` to exercise the full pipeline anyway — that is what CI does.

### Optional: Cognee + pgGraph locally

```bash
docker compose -f docker/docker-compose.yml up -d
# Set GRAPH_DATABASE_PROVIDER=pggraph and DB_* vars in .env

uv sync --extra opt --extra pggraph
uv run --extra opt --extra pggraph python scripts/seed_cognee.py
```

Note: the Postgres graph backend is a development convenience. Cognee documents it as a demo feature and directs production graph workloads to a graph-native backend, so numbers produced on it are not production-representative.

### Stand up live Cognee

```bash
uv sync --extra opt
uv run python scripts/live_stack.py --preflight-only   # names anything missing

# With OPENROUTER_API_KEY / OPENAI_API_KEY set:
uv run python scripts/live_stack.py                    # seed + smoke, stamped report
```

The preflight boundary is measured, not assumed, and it probes **egress as
well as keys**: in a sandboxed environment the network allowlist is usually
the binding constraint (a working key cannot reach a blocked provider). Three
routes to live, in order of readiness, are laid out in
[docs/implementation-plan.md](docs/implementation-plan.md) ("Going live"):
this environment plus one egress-allowlist entry, a Docker machine via the
checked-in compose file, or Cognee Cloud — the last is not yet a working path
(no cloud transport exists in the SDK or this repo), and the preflight says so
rather than letting a set key masquerade as one. Reports produced with mock
embeddings are stamped `quality_meaningful: false` and must not be quoted.

### Run evals

```bash
# Routing only. No Cognee, no LLM, no network. This is the CI gate.
uv run python scripts/eval_routing.py

# Full pipeline. Needs live Cognee and a live LLM to mean anything;
# without them the report says so in its validity warnings.
uv run python scripts/run_eval.py --report
```

### Optional: compile the learned router

```bash
uv sync --extra opt

# Report the plan and the budget without calling an LM.
uv run --extra opt python scripts/optimize_router.py --dry-run

# Compile (costs money — see the budget note in gepa_optimizer.optimize).
uv run --extra opt python scripts/optimize_router.py \
    --task-model openai/gpt-4.1-mini --reflection-model openai/gpt-5

# Judge the artifact on the held-out corpus.
uv run --extra opt python scripts/eval_routing.py \
    --dspy-artifact artifacts/router_gepa.json
```

Compiling on the held-out corpus is refused: training on the evaluation set
would destroy the only unbiased measurement here.

## Repository map

- `CHANGELOG.md` — release notes
- `docs/architecture.md` — technical architecture memo
- `docs/roadmap.md` — what to build next, with the evidence behind each item
- `docs/implementation-plan.md` — the build plan derived from a verified research pass, including what the research could *not* establish
- `docs/evaluation-plan.md` — metrics, corpora, known misses, benchmark plan
- `docs/adr/` — architecture decisions, including what was rejected and why
- `app/services/` — router, evidence packer, runtime controller, cache, orchestrator
- `app/evals/` — eval runner, baselines, routing eval, report
- `app/integrations/dspy_program.py` — learned router stage (optional `opt` extra)
- `scripts/optimize_router.py` — GEPA compile; `--dry-run` reports the plan without calling an LM
- `evals/queries.jsonl`, `evals/queries_heldout.jsonl` — tuning and held-out corpora

## Notes for engineers

- Do not treat the graph as prompt payload.
- Keep the cached prompt prefix stable; push dynamic evidence into the suffix.
- Prefer shallow, scoped retrieval before broad graph expansion.
- Instrument every decision so routing quality improves with evidence, not opinion.
- Never report an estimate as a measurement, and never let an unmeasured gate read as a pass.
- Read `AGENTS.md` and `CLAUDE.md` before making architectural changes.

## Reading order for a skeptic

1. **[CHANGELOG.md](CHANGELOG.md)** — what changed and, more usefully, what was found broken.
2. **"What is not measured"** above — the claims deliberately not made.
3. **[docs/evaluation-plan.md](docs/evaluation-plan.md)** — the corpora, the four known held-out misses left unfixed on purpose, and what `UNKNOWN` means in a gate.
4. **[docs/adr/](docs/adr/)** — the decisions, including three things evaluated and not adopted.

## License

See repository defaults; experiment / internal use unless otherwise specified.
