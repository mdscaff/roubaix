# Roubaix

Cognee-centered graph retrieval with cost-aware routing, progressive escalation, and a fail-closed runtime controller.

## Why this exists

Roubaix routes every query to the **cheapest valid** Cognee retrieval mode before paying for graph depth. The goal is measurable outcomes: lower cost per answer, better multi-hop quality, freshness when it matters, and telemetry that survives scrutiny.

The design constraint that shapes everything else: **use the graph to retrieve and compress evidence, not to flood the prompt.**

## What is actually measured

One number here is a real measurement. It is reported with the reference points that make it readable, and with the corpus it came from.

| Metric | Value | How to read it |
|---|---|---|
| Routing accuracy, **held-out** corpus (n=26) | **85%** | The honest number. These queries were written without reference to the router's patterns and have not been tuned against. |
| Routing accuracy, tuning corpus (n=20) | 100% | An upper bound, not a measurement. The rules were written against this corpus. |
| Best single fixed mode, held-out | 23% | What "always pick the commonest mode" scores. The router's lift is **+62 points**. |
| Accuracy when the router reports confidence | 93% | The confidence flag carries signal, so it is usable as an escalation input. |

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

**Fail-closed is real, and it fires on four conditions**: fabricated evidence (a retrieval failure must never be synthesized into a fluent answer), a freshness contract that produced no dated evidence, an exhausted escalation ladder, and a failed LLM call. Each carries a machine-readable reason.

**Freshness means a timestamp exists.** Temporal retrieval degrades silently to unfiltered search when it cannot extract an interval from the query — evidence comes back, so a count-based gate marks the contract met. Roubaix requires a parseable date in the packed evidence and refuses otherwise.

**Escalation widens before it climbs.** Depth is a cheaper dial than algorithm, so the controller retries the same mode with a larger evidence budget before buying a more expensive one.

**Cost ceilings shrink the request, they don't refuse it.** A caller's `max_cost_cents` trims the evidence pack to fit rather than failing — the caller asked for a cheaper answer, not no answer.

**Evidence reduction is disclosed.** Withheld items leave a marker naming how to retrieve them, so the synthesizer can distinguish "there was no more evidence" from "there was more and it was dropped".

## Current state

| Component | Status |
|---|---|
| Scored router + cost-rank tie-break + negation handling | Working, gated in CI |
| Content-addressed cache (LRU + TTL) | Working; key covers query, dataset, freshness, scope, model, policy version |
| Cognee retrieval | Live SDK when configured; flagged-degraded stub otherwise |
| Evidence packing (dedup, token budget, disclosure) | Working |
| Runtime controller (widen → escalate → fail closed) | Working |
| Cost accounting (measured vs estimated) | Working; no live run recorded |
| Eval harness (4 baselines, losable gates, validity warnings) | Working |
| LLM synthesis | OpenRouter; failures fail closed |
| NodeSet scoping | Caller-supplied and honoured; **not derived** — see roadmap |
| Temporal / Nexus | Scaffold; behind the main orchestrator (see ADR-002) |
| DSPy / GEPA | Not wired. Design in [docs/roadmap.md](docs/roadmap.md) |
| AdalFlow | **Rejected** — see [ADR-003](docs/adr/ADR-003-reject-adalflow-keep-explicit-controller.md) |

**103 tests passing.**

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

### Run evals

```bash
uv run python scripts/eval_routing.py              # routing only, no dependencies
uv run python scripts/run_eval.py --report        # full pipeline, needs live Cognee to mean anything
```

## Repository map

- `docs/architecture.md` — technical architecture memo
- `docs/roadmap.md` — what to build next, with the evidence behind each item
- `docs/evaluation-plan.md` — metrics, corpora, known misses, benchmark plan
- `docs/adr/` — architecture decisions, including what was rejected and why
- `app/services/` — router, evidence packer, runtime controller, cache, orchestrator
- `app/evals/` — eval runner, baselines, routing eval, report
- `evals/queries.jsonl`, `evals/queries_heldout.jsonl` — tuning and held-out corpora

## Notes for engineers

- Do not treat the graph as prompt payload.
- Keep the cached prompt prefix stable; push dynamic evidence into the suffix.
- Prefer shallow, scoped retrieval before broad graph expansion.
- Instrument every decision so routing quality improves with evidence, not opinion.
- Never report an estimate as a measurement, and never let an unmeasured gate read as a pass.
- Read `AGENTS.md` and `CLAUDE.md` before making architectural changes.

## License

See repository defaults; experiment / internal use unless otherwise specified.
