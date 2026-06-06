# ADR-002: Graph Site Agent product layer and Pipecat voice integration

## Status
Open

## Context

Roubaix (ADR-001) is a credible OSS retrieval optimizer: deterministic routing, evidence packing, Cognee integration, and eval instrumentation. It is not a sellable product on its own.

Market signals (May 2026):

- **A2A** standardizes agent-to-agent task delegation ([spec](https://a2a-protocol.org/latest/specification/)).
- **NLWeb** standardizes site-level Q&A (Schema.org + `/ask` + MCP) — complementary, not core identity.
- **Pipecat** ([pipecat-ai](https://github.com/pipecat-ai)) is the leading open-source real-time voice/multimodal agent framework. Maintainers recommend **LLM-triggered tool calls** for RAG, not a built-in retrieval service ([issue #1529](https://github.com/pipecat-ai/pipecat/issues/1529), [function calling](https://docs.pipecat.ai/pipecat/learn/function-calling)).

We built **graph-site-agent** (private repo) as the commercial wrapper: crawl public sites → Cognee graph per tenant → query via Roubaix, exposed as A2A skills and REST. Live crawl validation on **studiolens.ai** and **accelerare.com** succeeded; graph-backed answers are blocked until LLM API keys are configured in the service `.env`.

## Decision

Adopt a three-layer architecture and integration strategy:

| Layer | Repo | Visibility | Role |
|-------|------|------------|------|
| Retrieval kernel | `roubaix` | Public OSS | Routing, evidence, telemetry, eval harness |
| Site graph service | `graph-site-agent` | **Private** | Crawl, ingest, tenancy, A2A agent, REST API |
| Voice channel | Pipecat (external) | OSS example | STT → LLM tools → TTS; calls graph-site-agent |

**Product boundary:** Customers buy graph-site-agent (ingest + query + registry listing). Roubaix remains the optimization engine imported as a library — not forked.

**Pipecat integration (primary path):** Expose `ask_site` and `find_related` as Pipecat `@tool` / `FunctionSchema` handlers that call graph-site-agent REST (`POST /v1/ask`) or in-process `RoubaixBridge`. Use `cancel_on_interruption=True` for retrieval tools (voice waits for grounded answer). Do **not** add retrieval logic to Pipecat core; contribute a **`pipecat-examples`** demo instead ([issue #2570](https://github.com/pipecat-ai/pipecat/issues/2570)).

**Secondary paths (later):**

- MCP tools on graph-site-agent, consumed via Pipecat [`MCPClient`](https://docs.pipecat.ai/api-reference/server/utilities/mcp/mcp).
- Multi-agent handoff: greeter `LLMWorker` → site-knowledge specialist.
- Predictive retrieval (`pipecat-primd` pattern) with Roubaix cache + CHUNKS fast path.

**A2A skills (graph-site-agent v1):** `ingest_site`, `ask_site`, `find_related`, `graph_stats`.

## Consequences

### Positive

- Clear OSS vs commercial split; Roubaix stays narrow and credible.
- Pipecat alignment matches maintainer guidance (tool-call RAG).
- Voice demos (e.g. StudioLens product Q&A) become a distribution channel without coupling Pipecat to Cognee.
- Measurable integration point: Roubaix `telemetry.total_ms` per Pipecat tool call.

### Negative

- Two repos to operate; graph-site-agent dependency on editable/local `roubaix` path complicates deploy until published wheels or git deps are defined.
- Voice latency budget (&lt;800ms P95 for `ask_site`) may force CHUNKS-only routing initially; graph modes are cost/latency expensive for real-time speech.
- Private graph-site-agent limits community contribution unless examples live in public `pipecat-examples` or Roubaix docs.

### Risks

| Risk | Mitigation |
|------|------------|
| Empty API keys → stub retrieval | Document `.env` bridging; fail loudly in ingest CLI when cognify cannot run |
| Sitemap-less SPAs (studiolens.ai) | Seed-URL fallback (shipped); add same-origin link discovery in G2 |
| Pipecat spec churn | Thin adapter package; no fork |

## Next

1. **Unblock live graph** — set `OPENROUTER_API_KEY` / `OPENAI_API_KEY` in graph-site-agent `.env`; re-ingest studiolens.ai; verify non-stub `ask_site` answers.
2. **Pipecat P0 demo** — `pipecat-examples/graph-site-voice` (or sibling repo): `@tool ask_site` → graph-site-agent REST, tenant `studiolens.ai`, latency logging.
3. **graph-site-agent G1** — per-tenant API keys, durable ingest job store, registry publish stub.
4. **Pipecat P1** — MCP tool surface on graph-site-agent; Pipecat `MCPClient` demo.
5. **OSS contribution** — PR to [pipecat-examples](https://github.com/pipecat-ai/pipecat-examples) with instrumented example; link from Roubaix README.
6. **Link discovery** — homepage crawl for sitemap-less Next.js sites.

## References

- [docs/graph-agent-service-plan.md](../graph-agent-service-plan.md)
- [docs/nlweb-integration-plan.md](../nlweb-integration-plan.md)
- Private implementation: `graph-site-agent` (GitHub, private)
