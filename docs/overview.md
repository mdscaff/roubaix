# Roubaix — A Reference Architecture for Cost-Aware Graph Retrieval

## What Roubaix Is

Roubaix is a **graph-retrieval orchestration system** built on Cognee that turns an LLM-era retrieval stack into a **cost- and latency-aware pipeline**. Instead of throwing every query at the most expensive retrieval mode and flooding the prompt with context, Roubaix routes each query to the cheapest retrieval strategy that can plausibly answer it, packs evidence compactly, and escalates progressively only when needed.

It is explicitly a **technical experiment**, not a demo — designed for skeptical, detail-oriented audiences who care about verifiable implementation choices over rhetoric.

---

## The Core Thesis

> **Use the graph to retrieve and compress evidence, not to flood the prompt.**

Most RAG systems have one retrieval path and stuff everything into context. Roubaix rejects that. It treats retrieval as a **routing problem with a cost gradient**: chunks are cheap, triplets are medium, graph summaries are expensive, temporal validation is expensive-but-sometimes-required. The router picks; the runtime controller escalates only if the cheap path fails.

---

## The Core Technologies and Why Each Is Here

### 1. Cognee — the retrieval substrate
**Why:** Cognee provides a unified SearchMode abstraction (CHUNKS, TRIPLET_COMPLETION, GRAPH_COMPLETION, GRAPH_SUMMARY_COMPLETION, CYPHER, NATURAL_LANGUAGE, TEMPORAL) over graph + vector memory. Roubaix doesn't want to build its own retrieval layer — it wants to **exploit the fact that different retrieval modes have radically different costs** and route intelligently between them. ADR-001 locks this in: Cognee owns retrieval.

### 2. DSPy + GEPA — offline optimization (stubbed, not yet wired)
**Why:** The deterministic keyword router is a baseline. Once evaluation data exists, DSPy programs will replace hand-tuned heuristics for route classification, NodeSet selection, and evidence budget. GEPA compiles those programs. Critically, this is **offline** work — it does not add runtime dependencies.

### 3. AdalFlow — runtime control (stubbed)
**Why:** Progressive escalation, confidence checks, freshness validation. Starts as a local `RuntimeController` baseline so the system works end-to-end before adding sophistication.

### 4. HyperSpace AGI architect-v1 patterns — the tiered resolution pipeline
**Why:** HyperSpace's four-layer resolution (Normalize → Content Store → Similarity → Full Resolution) is the single most transferable idea in the LLM-agent space. Roubaix adopts:
- `QueryNormalizer` — canonical form via lowercase, stop-word removal, keyword sort, SHA-256 content addressing
- `ContentAddressedCache` — OrderedDict-based LRU with configurable TTL, plus a **shorter TTL for freshness-sensitive routes** (the `requires_freshness_validation` flag on RouteDecision drives this automatically)
- MinHash similarity index (Phase 2), TaskDag decomposition (Phase 4)

### 5. Temporal.io Nexus — durable multi-agent coordination
**Why:** Python's `asyncio.gather` has no durability. If a process crashes mid-retrieval, in-flight work is lost. Temporal Nexus provides:
- **Durable execution** — workflow state persists in event history
- **Cross-service contracts** — EvidenceRetrievalService, SynthesisService, QualityService as independent Nexus services with clean I/O boundaries
- **Built-in reliability** — automatic retries, circuit breaking after 5 failures, rate limiting, multi-cursor outbound queue so a slow service doesn't block others
- **Observability** — single event history shows the full query flow across every agent
- **Independent scaling** — more workers for CHUNKS, fewer for CYPHER, isolated failure domains

Nexus is **how the agents talk without coupling**.

### 6. FastAPI + Pydantic — typed contracts and a thin HTTP shell
**Why:** Every interface is a Pydantic model (`QueryRequest`, `RouteDecision`, `PackedEvidence`, `AnswerResult`, plus the Nexus I/O types). Pydantic enforces contracts at runtime; FastAPI auto-generates OpenAPI. The HTTP layer will eventually become a thin client that starts a Temporal workflow and awaits its result.

### 7. structlog + in-memory metrics — instrumentation first
**Why:** Every decision point emits a counter. You cannot optimize what you do not measure. The evaluation plan defines seven query buckets and four metric categories — metrics emission is the foundation for comparing DSPy-optimized configs against the deterministic baseline.

---

## The Core Components and What Each Does

| Component | Responsibility | Why It Exists as Its Own Module |
|---|---|---|
| `QueryNormalizer` | Canonical query form + SHA-256 content keys | Foundation for all cache layers — normalization *before* routing is the single biggest cache-hit lever |
| `ContentAddressedCache` | LRU + TTL keyed by content hash | Short-circuits identical queries; freshness-aware TTL means time-sensitive queries don't serve stale answers |
| `QueryRouter` | Deterministic keyword-based SearchMode selection | Replaceable baseline — DSPy-optimized router slots in later without changing callers |
| `CogneeClient` | Cognee I/O boundary | Single place that talks to the retrieval substrate; stubbed for development, wired in Phase 1 |
| `EvidencePacker` | Mode-specific evidence compression | Compression, not accumulation — the anti-RAG-stuffing lever |
| `RuntimeController` | Accept-or-escalate policy with bounded retries | The "progressive escalation" principle made concrete |
| `QueryOrchestrator` | Tiered pipeline coordinator | Now: normalize → cache check → route → retrieve → pack → decide → synthesize → cache |
| `RouterWorkflow` (Temporal) | Durable workflow wrapping the orchestrator | Phase 2: replaces asyncio with Temporal for crash-safe execution |
| `EvidenceRetrievalService` (Nexus) | Per-mode retrieval operations | Each SearchMode gets its own operation → independent scaling, isolated failures, mode-specific retry policies |
| `SynthesisService` (Nexus) | LLM answer generation | Separated from retrieval so it scales and times-out independently |
| `QualityService` (Nexus) | Per-route outcome tracking | HyperSpace-inspired quality-based auto-eviction: routes below 60% success get deprioritized |

---

## Why This Is a Great Reference Architecture

### 1. The cost gradient is explicit
Most RAG systems have one retrieval path. Roubaix models retrieval as a cost hierarchy and routes accordingly. CHUNKS is cheap; GRAPH_SUMMARY_COMPLETION is expensive; TEMPORAL is expensive and freshness-gated. The router's job is to pick the cheapest plausible answer.

### 2. The cache strategy mirrors the real cost distribution
```
Normalize (~0ms) → Content Store (~2ms) → Similarity (~15ms) → Full Retrieval (~3-8s)
```
This is not theoretical — it matches how LLM-era systems actually behave. Each layer is a short-circuit for the next. Most queries never reach the expensive layer.

### 3. Static vs. dynamic separation is enforced at the architectural level
- **Static (cacheable prefix):** system instructions, answer schema, retrieval policy, ontology summary, examples
- **Dynamic (suffix):** user query, NodeSets, retrieved triplets, timestamps, controller notes

This split is not a comment in the code — it's the reason the module boundaries exist. It directly reduces input tokens because the prefix hits the LLM provider's prompt cache.

### 4. Optimization and runtime are properly decoupled
DSPy/GEPA compile *offline* — they produce better programs that slot into the same interfaces. AdalFlow handles runtime escalation. This means you can ship a deterministic baseline today and swap in optimized versions without re-architecting. `configs/router-policy.md` is a human-readable policy document that mirrors the code — the baseline is reviewable by non-engineers.

### 5. Durability is a first-class concern, not an afterthought
The Temporal Nexus integration means that when a retrieval crashes mid-flight, Temporal resumes it. When a synthesis LLM call times out, the circuit breaker trips before cascading. When you need to scale one service independently of another, you add workers. This is production-grade resilience wired in from the scaffold stage.

### 6. Every layer is independently testable and replaceable
- `QueryNormalizer` — 8 unit tests, no dependencies
- `ContentAddressedCache` — 7 unit tests covering LRU, TTL, freshness
- `QueryRouter` — deterministic, 2 tests; replaceable with DSPy without changing the orchestrator
- `CogneeClient` — stubbed, swappable with real SDK
- Nexus services — contract-first, mockable in tests

**23 tests, all passing, zero flakiness.**

### 7. It's instrumented from day one
Every routing decision emits a counter. Every cache hit/miss. The evaluation plan defines exactly which metrics matter (cost per successful answer, p50/p95 latency, freshness correctness, successful-answer rate, search-mode distribution, evidence items, input tokens, retries). When Phase 3 arrives and DSPy is compiled against evaluation data, the leaderboard comparison is already wired.

### 8. The architecture accepts graceful degradation
Every advanced dependency (DSPy, AdalFlow, Cognee SDK, Temporal, MinHash) is an **optional extras group** in `pyproject.toml`. The baseline works without them. This is rare in agent systems, which typically couple tightly to their frameworks.

---

## What's Special About It

Most "agent" systems do one of two things:
- **Framework cargo-culting:** "We use LangGraph + LlamaIndex + Pinecone + Redis" without articulating *why* each one is there or what it displaces.
- **Framework minimalism:** "We wrote it all ourselves in 200 lines of Python" without acknowledging the durability, observability, and scaling problems that frameworks actually solve.

Roubaix does neither. Every dependency has a **named job and a named displacement target**:
- Cognee **displaces** building your own retrieval layer (ADR-001 justifies this explicitly)
- Temporal Nexus **displaces** `asyncio.gather` + ad-hoc retry logic + custom circuit breakers
- DSPy/GEPA **displace** hand-tuned prompts
- HyperSpace's tiered resolution pipeline **displaces** a flat retrieval path with no memory
- The content-addressed cache **displaces** duplicate Cognee calls for identical queries

It is also **phased honestly**. The implementation plan has five phases, not one mega-commit. The `.md` documents in `docs/` are load-bearing — `architecture.md` explains the thesis, `evaluation-plan.md` defines acceptance gates (25% reduction in median input tokens with no correctness degradation), ADR-001 locks in the Cognee decision.

And critically: it **refuses to fork what it doesn't need**. HyperSpace's P2P GossipSub, CRDT sync, distributed training, and WASM sandboxing are all listed in the plan's "What NOT to Adopt" section with specific reasons. The discipline to not adopt things is as important as the discipline to adopt the right ones.

---

## Bottom Line

Roubaix is what a cost-aware, cache-aware, durable, observable, testable LLM retrieval system looks like when each architectural choice is justified, measured, and replaceable. It is the architecture you would point a new engineer at to show them how to build a production agent system without either (a) reinventing Temporal or (b) drowning in framework soup.
