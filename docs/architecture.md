# Roubaix Architecture

## Business value first

Roubaix exists to improve four measurable outcomes:

- lower cost per successful answer
- lower latency per successful answer
- higher quality on relationship-heavy and multi-hop queries
- higher freshness accuracy on time-sensitive queries

The central design choice is simple: **use the graph to retrieve and compress evidence, not to flood the prompt**.

## Architectural thesis

Roubaix uses:

- **Cognee** as the retrieval substrate for graph + vector memory
- **A deterministic scored router and an explicit runtime controller** for mode
  selection, progressive escalation, and fail-closed refusal. Both are plain
  Python state machines with no framework dependency — see
  [ADR-003](adr/ADR-003-reject-adalflow-keep-explicit-controller.md) for the
  alternatives evaluated and rejected.
- **DSPy + GEPA** *(planned, not wired)* to optimize routing in the ambiguous
  band only, leaving the cheap deterministic path free of an LLM call. See
  [roadmap.md](roadmap.md).

Note on positioning: Cognee now ships its own weighted-regex query router
upstream. Mode selection alone is therefore not a differentiator. What Roubaix
adds is the **contract attached to the mode** — an evidence budget, a NodeSet
scope, and a freshness requirement — and a controller that can act on it.

## Core components

### 1. Query router

Maps an incoming query to:

- a Cognee search mode
- a NodeSet scope
- an evidence budget
- a freshness policy

### 2. Cognee retrieval executor

Runs the chosen retrieval strategy and returns structured evidence.

### 3. Evidence packer

Compresses the returned material into the smallest answer-supporting payload.

### 4. Synthesizer

Builds the final LLM call with a cacheable prefix and dynamic suffix.

### 5. Runtime controller

The only component permitted to decide that no answer should be produced. It
returns a structured decision — action, machine-readable reason, and the signals
behind it — so "why did this query cost three retrievals" is answerable from a
trace.

Four actions:

- **accept** — evidence is sufficient
- **widen** — retry the same mode with a larger evidence budget. Depth is a
  cheaper dial than algorithm, so this rung comes before any mode change. Latched
  to fire at most once per query.
- **escalate** — move up a terminating ladder to a broader mode. A mode is never
  attempted twice, which is what makes "bounded retries" a guarantee rather than
  a small retry counter.
- **fail closed** — return an explicit non-answer, never cached

Fail-closed fires on: degraded (fabricated) evidence, a freshness contract with
no dated evidence, an exhausted ladder, and a failed LLM call.

## Search-mode strategy

The initial baseline should route roughly as follows:

- exact structural question -> `CYPHER` or `NATURAL_LANGUAGE`
- relationship-heavy question -> `TRIPLET_COMPLETION`
- broad explanatory question -> `GRAPH_SUMMARY_COMPLETION`
- general graph-aware QA -> `GRAPH_COMPLETION`
- local factual lookup -> `CHUNKS` or `RAG_COMPLETION`
- time-sensitive question -> `TEMPORAL`

## Cache boundary

### Cacheable prefix

- system instructions
- answer schema
- retrieval policy summary
- ontology summary
- examples

### Dynamic suffix

- user query
- selected NodeSets
- retrieved triplets/chunks/paths
- time-sensitive evidence
- controller notes

## Failure semantics

Two rules, applied consistently:

- **Validation failures are loud.** A retrieval outage, an unverifiable freshness
  claim, or a dead LLM provider produces an explicit refusal with a reason. None
  of these are cached.
- **Enhancement failures degrade quietly to the baseline.** An optional
  optimizer, a tracing exporter, or a cost estimate that cannot be produced must
  never take down a request.

The distinction matters because the worst bugs found in this codebase were all
the same shape: a validation failure that degraded quietly. A Cognee outage
returned fabricated placeholder evidence which was synthesized into a fluent
answer and cached for an hour; a dead LLM provider returned a template marked
`accepted: true`. Both were invisible in the response and in eval results.

## Why this should outperform a monolithic graph system

1. It avoids paying for the most expensive path on every query.
2. It scopes the graph with NodeSets. *(Currently caller-supplied only — the
   lever exists but is not yet derived. See roadmap §1.)*
3. It treats freshness as a retrieval concern, and refuses rather than guessing
   when freshness cannot be verified against a timestamp.
4. It measures every routing and escalation decision.

Point 2 is stated as an intent rather than a result on purpose. The measured
claim today is routing accuracy (85% held-out, +62 points over the best fixed
mode); the cost claim is not yet measured against live retrieval. See
[evaluation-plan.md](evaluation-plan.md).

## Diagram summary

```text
Query
  -> Router
  -> NodeSet scope
  -> Cognee search mode
  -> Evidence pack
  -> Synthesis
  -> Runtime controller
  -> Return or escalate
```
