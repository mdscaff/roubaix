# NLWeb × Roubaix integration plan

**Status:** Research / planning (May 2026)  
**NLWeb reference:** [github.com/nlweb-ai/NLWeb](https://github.com/nlweb-ai/NLWeb) (~6.2k stars, MIT, Microsoft-affiliated)  
**Roubaix role:** Graph-first retrieval optimizer behind NLWeb’s protocol surface  
**pgGraph role:** Postgres graph substrate shared with NLWeb’s existing Postgres/pgvector path

---

## 1. Executive summary

[NLWeb](https://github.com/nlweb-ai/NLWeb) is a protocol + reference implementation for **conversational website interfaces** that speak Schema.org JSON and expose `/ask` + `/mcp` for humans and agents. Today its retrieval path is **vector-first**: crawl Schema.org / JSON-LD → embed → store in Qdrant, Azure AI Search, **Postgres/pgvector**, etc. → similarity search → optional LLM summarize/generate.

**Roubaix** is the opposite emphasis: **graph-first, cost-aware retrieval** over Cognee (chunks, triplets, graph completion, temporal) with evidence packing and measurable routing telemetry.

The opportunity:

1. **Websites that already have Schema.org** can adopt NLWeb quickly — Roubaix makes their retrieval **relationship-aware and cheaper**, not just semantically similar.
2. **Websites without NLWeb** become a services business: implement crawl + NLWeb endpoint + **Roubaix graph layer** on Postgres/pgGraph.
3. **pgGraph** (Cognee community adapter you are building) lands on the same Postgres ecosystem NLWeb already documents — one infra story for vector + graph.

Positioning: *NLWeb is the HTTP/MCP/HTML layer; Roubaix is the graph retrieval brain.*

---

## 2. What NLWeb is (relevant subset)

### Protocol surface

| Endpoint | Purpose |
|----------|---------|
| `POST /ask` | Natural language query → JSON (Schema.org-shaped results) |
| `POST /mcp` | Same args; MCP-compatible response + `list_tools`, `call_tool`, etc. |

**Core parameters:** `query` (required), `site`, `prev` / `decontextualized_query`, `mode` (`list` \| `summarize` \| `generate`), `streaming`, `query_id`.

**Response shape (list mode):** `query_id` + array of hits: `url`, `name`, `site`, `score`, `description`, `schema_object`.

Spec: [nlweb.ai](https://github.com/nlweb-ai/NLWeb) docs + [REST API doc](https://github.com/nlweb-ai/NLWeb/blob/main/docs/nlweb-rest-api.md).

### Reference implementation modules

| Module | Role |
|--------|------|
| **AskAgent** | Query agent, UI, ingestion, vector retrieval |
| **AgentFinder** | Discover NLWeb agents on the web |
| **DataFinder** | NL → SQL for CRM/enterprise (Schema.org ontology maps) |
| **ModelRouter** | Pick LLM by cost/quality |
| **NLWebScorer** | Neural reranking |

### Ingestion pipeline

1. Crawl sitemap / pages with **Schema.org JSON-LD** (or convert RSS → JSON-LD).
2. Embed content; upload to vector store (`schema_json`, `url`, `name`, `site`, `embedding`).
3. Serve via `/ask` with modes that add LLM summarization or RAG-style generation.

NLWeb explicitly recommends production deployments **connect to live data** rather than stale copies — aligns with Roubaix freshness / temporal validation.

### Retrieval extensibility

NLWeb documents a **`VectorDBClientInterface`** (`search`, `search_all_sites`, `search_by_url`, `upload_documents`, `delete_documents_by_site`, …) registered in `config_retrieval.yaml` and `retriever.py`. **Postgres + pgvector is already supported** ([setup-postgres.md](https://github.com/nlweb-ai/NLWeb/blob/main/docs/setup-postgres.md)).

**Gap NLWeb does not solve today:** multi-hop relationships, typed graph traversal, mode-specific cost control. That is Roubaix’s lane.

---

## 3. Architectural fit (graph-first on NLWeb)

```text
┌─────────────────────────────────────────────────────────────────┐
│  Website (Schema.org JSON-LD, RSS, product feeds, live DB)      │
└────────────────────────────┬────────────────────────────────────┘
                             │ crawl / sync
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Ingestion (dual write)                                         │
│  ├─ NLWeb path: documents table + pgvector (baseline / compat)  │
│  └─ Roubaix path: cognee.add → cognify → pgGraph + vectors      │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┴───────────────────┐
         ▼                                       ▼
┌─────────────────┐                   ┌─────────────────────────┐
│ NLWeb /ask      │                   │ Roubaix orchestrator    │
│ list|summarize  │  ── optional ──▶  │ route → Cognee mode     │
│ generate        │     delegation      │ pack evidence → synth   │
└────────┬────────┘                   └───────────┬─────────────┘
         │                                        │
         └────────────────┬───────────────────────┘
                          ▼
              Schema.org JSON response + MCP tools
                          │
                          ▼
                   Human UI / AI agents
```

### Complementary strengths

| NLWeb | Roubaix |
|-------|---------|
| Schema.org response contract | Cognee `SearchMode` routing |
| MCP / A2A agent surface | NodeSet scoping + evidence budget |
| Mixed-mode control flow (many small LLM calls) | Fail-closed runtime + escalation |
| Crawler + WordPress plugin | Eval harness + telemetry per route |
| Vector similarity default | Graph/triplet/temporal when needed |

**Design rule:** NLWeb keeps **protocol, UI widgets, and control-flow prompts**; Roubaix owns **retrieval mode selection and graph evidence** unless NLWeb’s `list` mode is explicitly vector-only (fast path).

---

## 4. Integration strategies (ranked)

### Strategy A — **Roubaix retrieval provider for NLWeb** (recommended v1)

Implement a NLWeb `VectorDBClientInterface` backend named `cognee` or `roubaix` that:

1. On `search(query, site, …)` → calls Roubaix internal API (not raw vectors only).
2. Roubaix router picks mode from query shape (product lookup → `CHUNKS`; “how does X relate to Y” → `TRIPLET_COMPLETION` / `GRAPH_COMPLETION`).
3. Maps `RetrievalResult` → NLWeb hit list (`url`, `name`, `score`, `schema_object` from provenance).

**Pros:** Drop-in for NLWeb deployments; one config switch (`preferred_endpoint: roubaix`).  
**Cons:** Upstream contribution to NLWeb repo or fork; interface is vector-shaped (may need extension for graph-native scores).

**Files to add (NLWeb side):** `retrieval/roubaix_client.py`, `config_retrieval.yaml` endpoint, `docs/setup-roubaix.md`.  
**Files to add (Roubaix side):** `app/integrations/nlweb_mapper.py`, `POST /internal/retrieve` (typed, no full synthesis).

### Strategy B — **Roubaix `/ask` adapter** (fastest Roubaix-only demo)

Expose NLWeb-compatible `/ask` on Roubaix FastAPI:

- Parse NLWeb query params.
- Run orchestrator in **list mode** (retrieval only, skip long synthesis) or map `mode=generate` to full `AnswerResult`.
- Emit NLWeb JSON schema from `PackedEvidence` + Cognee provenance.

**Pros:** No NLWeb fork; full control; good for **services business** (“we host your NLWeb endpoint”).  
**Cons:** Does not use NLWeb crawler/UI out of the box; you reimplement or embed AskAgent UI.

### Strategy C — **Sidecar composition** (production services default)

Run NLWeb AskAgent + Roubaix side by side:

- NLWeb handles crawl, MCP, mixed-mode dialogs, WordPress plugin.
- Roubaix configured as **secondary retrieval endpoint** invoked when `mode=generate` or when NLWebScorer confidence is low.
- Shared Postgres: NLWeb `documents` table + Cognee pgGraph schema (separate schemas, same cluster).

**Pros:** Best for “implement NLWeb for customers”; minimal fork.  
**Cons:** Two processes to operate; need clear latency budgets.

### Strategy D — **AgentFinder + Roubaix as discoverable graph agent**

Register Roubaix-powered sites in AgentFinder with capability metadata: `graph_retrieval: true`, `schema_org: true`. Agents route relationship queries to Roubaix-backed endpoints.

**Pros:** Ecosystem play; aligns with “web of agents.”  
**Cons:** Depends on AgentFinder adoption; later phase.

---

## 5. Data model mapping: Schema.org → Cognee → NLWeb

### Ingest

| NLWeb field | Cognee / Roubaix use |
|-------------|----------------------|
| `schema_json` (JSON-LD) | `cognee.add(json, dataset_name=site, node_set=[type@Product, …])` |
| `url` | Document ID + provenance |
| `site` | Cognee dataset / NodeSet root |
| `name` | Chunk metadata + graph entity label |
| `embedding` | Cognee embedding pipeline (1536-dim `text-embedding-3-small` matches NLWeb Postgres docs) |

**Action:** `scripts/ingest_schema_org.py` — read NLWeb `documents` rows or crawler output → `cognee.add` + `cognify`.

### Retrieve → NLWeb response

| Roubaix field | NLWeb hit field |
|---------------|-----------------|
| `evidence.chunks[i]` | `description` or `schema_object.description` |
| `provenance[].dataset` | `site` |
| `provenance[].url` | `url` |
| graph path / triplet | `schema_object` nested graph or `ItemList` |
| router mode + telemetry | `score` (derive from evidence count, retrieval_ms, mode confidence) |

Use Schema.org **`ItemList`** / **`ListItem`** for multi-hit responses (NLWeb is moving to richer structures per REST doc).

---

## 6. Postgres + pgGraph: unified infra story

NLWeb already documents Postgres + pgvector ([setup-postgres.md](https://github.com/nlweb-ai/NLWeb/blob/main/docs/setup-postgres.md)). Roubaix + Cognee pgGraph uses Postgres as **graph** backend.

**Recommended deployment:**

```text
Postgres 16 + pgvector + pgGraph (Cognee community adapter)
├── schema nlweb: documents (id, url, name, schema_json, site, embedding)
└── schema cognee: graph + vector tables (managed by Cognee/pgGraph)
```

**Sync options:**

1. **Event-driven:** NLWeb crawler upload → webhook → Roubaix ingest job (near real-time graph).
2. **Batch:** Nightly cognify for sites with low churn.
3. **Live DB:** For customers with product DB, skip duplicate crawl; Cognee reads via connector (matches NLWeb production guidance).

This is the **technical wedge for services sales**: “One Postgres bill; vector search + knowledge graph.”

---

## 7. Roubaix implementation phases

### Phase NLWeb-0 — Spike (1 week)

- [ ] Clone NLWeb AskAgent locally; run hello-world with Postgres provider.
- [ ] Ingest `evals/corpus` or sample Schema.org Product JSON into Cognee + pgGraph.
- [ ] Manual comparison: NLWeb `list` vs Roubaix `CHUNKS` vs `TRIPLET_COMPLETION` on 10 queries.
- [ ] Document latency/cost/quality in `evals/nlweb-baseline.jsonl`.

**Exit:** One page scorecard proving graph modes win on relationship queries.

### Phase NLWeb-1 — Schema.org ingest bridge (1–2 weeks)

- [ ] `app/integrations/schema_org_ingest.py` — JSON-LD → `cognee.add` / `cognify`.
- [ ] Map `@type` → NodeSet candidates (`Product`, `Recipe`, `LocalBusiness`, …).
- [ ] `scripts/ingest_nlweb_documents.py` — import from NLWeb Postgres `documents` table.

**Exit:** Single site dataset cognified from Schema.org export.

### Phase NLWeb-2 — NLWeb response mapper (1 week)

- [ ] `app/integrations/nlweb_mapper.py` — `RetrievalResult` → NLWeb hit list.
- [ ] `POST /ask` on Roubaix (thin handler) or `POST /internal/nlweb/retrieve`.
- [ ] Support `mode=list` only first; wire `query_id`, `site`, `prev` passthrough.

**Exit:** NLWeb REST client can call Roubaix and parse response without code changes.

### Phase NLWeb-3 — NLWeb retrieval provider OR sidecar (2 weeks)

Pick Strategy A or C:

- **A:** PR to NLWeb (or `roubaix-nlweb-bridge` package) implementing `VectorDBClientInterface`.
- **C:** `config_retrieval.yaml` custom HTTP retriever + deployment helm chart.

**Exit:** AskAgent `preferred_endpoint` points at Roubaix-backed search.

### Phase NLWeb-4 — MCP parity (1 week)

- [ ] Expose Roubaix `/mcp` shim delegating to same retrieval core.
- [ ] Tool: `ask(query, site, mode)` returning Schema.org JSON.
- [ ] Register in AgentFinder testbed (optional).

**Exit:** Claude/Cursor can call site via MCP with graph-backed hits.

### Phase NLWeb-5 — Eval + optimization (ongoing)

- [ ] Extend `app/evals/baselines.py`: `nlweb_vector_only` vs `roubaix_graph_first`.
- [ ] Metrics: cost/query, P95 latency, MRR on relationship query set.
- [ ] DSPy optimize router for Schema.org site types (Phase 3 of main plan).

**Exit:** Published comparison report for sales material.

---

## 8. Business model: “NLWeb implementation + graph retrieval”

### Open source (trust + adoption)

| Repo | Role |
|------|------|
| **Roubaix** | Graph-first retrieval optimizer (Apache/MIT aligned with NLWeb) |
| **pgGraph adapter** (cognee-community) | Postgres graph engine |
| **demo-reel** | Release/demo videos for customer sites |
| **`roubaix-nlweb-bridge`** (proposed) | Optional thin integration package |

### Commercial services (revenue)

1. **NLWeb launch package** (fixed fee)  
   - Schema.org audit, crawler setup, `/ask` + `/mcp`, WordPress plugin or headless widget.  
   - *Without graph:* commodity NLWeb install.  
   - *With Roubaix:* **graph-first retrieval** as upsell.

2. **Managed graph retrieval** (monthly)  
   - Hosted Postgres/pgGraph + Cognee ops, freshness monitors, eval dashboards.  
   - SLAs on relationship-query accuracy (measurable via Roubaix eval harness).

3. **Vertical playbooks**  
   - E-commerce (Product, Offer): cross-sell queries → `TRIPLET_COMPLETION`.  
   - Local / travel (Place, TouristAttraction): geo + relationship graph.  
   - Publishers (Article, NewsArticle): temporal mode for “latest”.

### Differentiated pitch

> “NLWeb gives your site a voice. Roubaix gives it a **memory structure** — so ‘What goes with this product?’ and ‘Which locations share this supplier?’ work, not just keyword similarity. Same Postgres. Same MCP. Graph-first.”

### What not to claim

- Roubaix is not a replacement for NLWeb’s mixed-mode dialog control (clarifying questions, site-specific flows).
- Full NLWeb parity is not required day one — **`list` mode + MCP `ask`** is enough for agent interoperability.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| NLWeb API / repo churn (recent fork org `nlweb-ai`) | Pin versions; bridge package; contract tests on `/ask` JSON |
| `VectorDBClientInterface` is vector-centric | Extend with `search_graph()` optional method or HTTP sidecar |
| Dual ingest drift (NLWeb vs Cognee) | Single source: crawl once → fan-out write; or graph-only with NLWeb-compatible export |
| Latency stack (NLWeb LLM calls + Roubaix retrieval) | Route simple queries to `CHUNKS` only; cache by `query_id` + content key |
| Microsoft trademark / ecosystem | NLWeb is MIT; contribute upstream; avoid MS trademark in product name |
| pgGraph maturity | Keep NLWeb pgvector path as fallback; feature-flag graph modes |

---

## 10. Recommended next actions

1. **This week:** Phase NLWeb-0 spike — run NLWeb Postgres hello-world + Roubaix seed corpus side by side.
2. **Design doc PR:** Add `app/integrations/nlweb_mapper.py` interface sketch + sample `/ask` response JSON.
3. **New repo (optional):** `roubaix-nlweb-bridge` for NLWeb provider plugin (keeps Roubaix core clean per AGENTS.md boundaries).
4. **Customer template:** “Schema.org site → NLWeb + Roubaix on one Postgres” deployment diagram for sales.
5. **Upstream:** Open NLWeb discussion issue: *“Graph retrieval provider via Cognee/pgGraph”* — gauge maintainer interest before large PR.

---

## 11. References

- NLWeb repo: https://github.com/nlweb-ai/NLWeb  
- NLWeb REST API: https://github.com/nlweb-ai/NLWeb/blob/main/docs/nlweb-rest-api.md  
- NLWeb Postgres setup: https://github.com/nlweb-ai/NLWeb/blob/main/docs/setup-postgres.md  
- NLWeb providers checklist: https://github.com/nlweb-ai/NLWeb/blob/main/docs/nlweb-providers.md  
- Schema.org + NLWeb overview: https://www.schemaapp.com/schema-markup/nlweb-consuming-schema-markup-for-ai-applications/  
- Roubaix architecture: [architecture.md](./architecture.md)  
- Cognee pgGraph adapter: https://github.com/topoteretes/cognee-community/tree/main/packages/graph/pggraph  
