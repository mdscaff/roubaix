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
- **DSPy + GEPA** to optimize query routing, NodeSet scoping, evidence packing, and synthesis prompts
- **AdalFlow** to handle runtime fallback, escalation, and freshness-aware control

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

Evaluates first-pass quality and decides whether to accept, retry, expand scope, switch mode, or validate freshness.

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

## Why this should outperform an inferior monolithic graph system

1. It avoids paying for the most expensive path on every query.
2. It scopes the graph aggressively with NodeSets.
3. It treats freshness as a retrieval concern, not a prompt-regeneration concern.
4. It measures every routing and escalation decision.

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
