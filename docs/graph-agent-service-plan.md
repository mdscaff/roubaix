# Graph Agent Service — product & A2A architecture

**Status:** Strategic plan (May 2026)  
**Foundation:** Roubaix (retrieval optimizer) + Cognee (graph/vector memory) + pgGraph (Postgres)  
**Go-to-market:** A2A-compatible agent listed in your public agent registry  
**Related:** [nlweb-integration-plan.md](./nlweb-integration-plan.md)

---

## 1. Should we do this?

**Yes.** Roubaix as an internal experiment is the right kernel; the **sellable product** is one layer up:

> **Ingest a customer’s web presence → build a queryable knowledge graph → expose it as an A2A agent other agents (and humans) can hire.**

That matches where the market is moving:

- **NLWeb** standardizes site-level Q&A (Schema.org + `/ask` + MCP).
- **A2A** standardizes **agent-to-agent** task delegation ([spec](https://a2a-protocol.org/latest/specification/)).
- **MCP** standardizes tool/context for a single agent session.

Your differentiation is not “another chatbot” — it is **graph-first memory with cost-aware retrieval**, packaged as a **discoverable, billable agent** in your registry.

Roubaix stays the **optimization engine** (routing, evidence, telemetry). The new service is the **Graph Site Agent** (working name).

---

## 2. Product definition

### What customers buy

| Tier | Capability |
|------|------------|
| **Discover** | Crawl/index their public site (or connect CMS/API) → knowledge graph in 24–48h |
| **Query** | A2A agent skills: `ask_site`, `find_related`, `summarize_section`, `check_freshness` |
| **Optimize** | Roubaix routing + eval reports (cost/latency/quality per query type) |
| **Operate** | Re-crawl schedules, graph diff alerts, registry listing + SLA |

### What you operate

- Per-tenant Cognee **dataset** (= one customer site or domain)
- pgGraph on Postgres (your pgGraph adapter is the infra moat)
- Agent Card at `/.well-known/agent.json` (A2A v1.0) per tenant or shared multi-tenant server
- Registration payload for **your public agent registry** (extends Agent Card with pricing, SLA, vertical tags)

### Honest scope v1

- Public web + Schema.org/RSS/sitemap first
- Authenticated CMS/API connectors second
- Not a general-purpose web scraper for paywalled content without customer credentials

---

## 3. Content acquisition (how we get site data)

Three paths, combinable:

### Path A — Polite public crawl (default)

```text
sitemap.xml / robots.txt
    → URL frontier (respect crawl-delay, allow/deny)
    → fetch HTML
    → extract: main text, JSON-LD Schema.org, Open Graph, canonical URL
    → normalize → cognee.add per page
```

**Tools to evaluate:** custom asyncio crawler (control), NLWeb AskAgent crawler (Schema.org focus), or `trafilatura` + `extruct` for markup extraction.

**NodeSet assignment:** `@type` from JSON-LD (`Product`, `Article`, `Organization`) + URL path prefix (`/docs`, `/pricing`).

### Path B — Structured feeds (higher quality, lower cost)

- RSS/Atom, product feeds, sitemap + lastmod
- Customer-uploaded JSON-LD export
- Shopify/WooCommerce/Contentful webhooks → incremental `cognee.add` (no full recrawl)

**Best for freshness** — aligns with NLWeb production guidance (live source of truth).

### Path C — Customer-authorized access

- API keys to CMS, Notion, Confluence, GitHub docs
- OAuth for private knowledge bases (enterprise tier)

**Legal/commercial:** crawl only with customer attestation they own the content; ToS for agent registry listing.

---

## 4. Cognee pipeline: content → ontology → knowledge graph

### Pipeline stages

```text
1. EXTRACT   page/feed → { url, title, text, schema_json?, fetched_at }
2. CHUNK     Cognee TextChunker (existing defaults)
3. ADD       cognee.add(content, dataset_name=tenant_id, node_set=[...])
4. COGNIFY   cognee.cognify(datasets=[tenant_id])  → entities, edges, embeddings
5. VALIDATE  graph stats, orphan rate, sample queries (eval harness)
6. PUBLISH   enable A2A skills for tenant; register in agent registry
```

### Ontology strategy

Cognee’s default `KnowledgeGraph` model works for v1. Upgrade path:

| Stage | Ontology |
|-------|----------|
| v1 | Cognee default cognify (automatic entity/relation extraction) |
| v2 | Schema.org-aligned Pydantic `graph_model` for e-commerce, publishers, SaaS docs |
| v3 | Customer-specific ontology upload (OWL/JSON Schema) + constrained cognify `custom_prompt` |

**Do not block launch on custom ontology** — Schema.org types from JSON-LD already give you typed NodeSets.

### pgGraph role

- Graph storage + traversal at scale on Postgres customers already understand
- Same cluster can hold NLWeb-style `documents` + pgvector if you also offer NLWeb-compatible `/ask` later
- Your cognee-community pgGraph adapter is a **credible enterprise story**

### Incremental updates

```text
crawl diff (new/changed URLs since last run)
    → cognee.add(..., incremental_loading=True)
    → cognify(incremental_loading=True)
    → temporal markers for "last verified" (Roubaix TEMPORAL mode)
```

---

## 5. Roubaix role in the service

Roubaix is **not** the customer-facing brand. It powers:

| Roubaix component | Service use |
|-----------------|-------------|
| `QueryRouter` | Pick Cognee mode per A2A skill / query shape |
| `CogneeClient` | Retrieval execution |
| `EvidencePacker` | Token-bounded context for answers |
| `AnswerSynthesizer` | Optional `generate` skill |
| `RuntimeController` | Fail closed; escalate on empty graph hits |
| Eval harness | SLA reporting, before/after graph rebuilds |
| Telemetry | Billable metrics: queries, tokens, modes used |

**Per-tenant config:** `dataset=customer_slug`, NodeSets from Schema.org types, `ROUBAIX_MAX_EVIDENCE_ITEMS` by pricing tier.

---

## 6. A2A agent design

Host at `https://graph-agent.{your-domain}/` (or per-tenant subdomain).

### Agent Card (`/.well-known/agent.json`)

```json
{
  "name": "Graph Site Agent",
  "description": "Builds and queries a knowledge graph from your website. Graph-first retrieval with cost-aware routing.",
  "version": "1.0.0",
  "url": "https://graph-agent.example.com/a2a",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["application/json", "text/plain"],
  "skills": [
    {
      "id": "ingest_site",
      "name": "Ingest website",
      "description": "Crawl or accept a sitemap URL and build/update the knowledge graph",
      "tags": ["crawl", "cognee", "onboarding"]
    },
    {
      "id": "ask_site",
      "name": "Ask site",
      "description": "Natural language Q&A over the customer's graph with routed retrieval",
      "tags": ["qa", "graph", "roubaix"]
    },
    {
      "id": "graph_stats",
      "name": "Graph statistics",
      "description": "Entity counts, coverage, last crawl, freshness summary",
      "tags": ["observability"]
    },
    {
      "id": "find_related",
      "name": "Find related entities",
      "description": "Relationship-heavy queries via triplet/graph completion modes",
      "tags": ["graph", "relationships"]
    }
  ],
  "securitySchemes": {
    "bearer": { "type": "http", "scheme": "bearer" }
  }
}
```

### Skill → implementation map

| A2A skill | Backend action |
|-----------|----------------|
| `ingest_site` | Start async task: crawl → add → cognify; return `task_id` |
| `ask_site` | Roubaix orchestrator `answer()` → structured JSON + telemetry |
| `graph_stats` | Cognee dataset metadata + last ingest job status |
| `find_related` | Force `TRIPLET_COMPLETION` or `GRAPH_COMPLETION` route |

Use A2A **Tasks** for long-running ingest (minutes–hours). Use **Messages** for `ask_site` (seconds).

### MCP compatibility (optional, same server)

Expose parallel MCP tools mirroring skills — NLWeb ecosystem and Cursor clients can call the same backend without A2A.

---

## 7. Agent registry integration (your other project)

A2A discovery supports ([agent discovery](https://a2a-protocol.org/v0.2.5/topics/agent-discovery/)):

1. **Well-known URL** — `/.well-known/agent.json` (required)
2. **Curated registry** — your public registry (commercial listing)
3. **Direct config** — enterprise customers hardcode Agent Card URL

### Registry record (proposed fields beyond Agent Card)

| Field | Purpose |
|-------|---------|
| `registry_id` | Stable listing ID |
| `pricing_tier` | free / pro / enterprise |
| `metering` | per-query, per-page-indexed, monthly graph slot |
| `verticals` | ecommerce, saas, publisher, local |
| `capabilities_extra` | `graph_first: true`, `pggraph: true`, `schema_org: true` |
| `demo_url` | Link to demo-reel video for this agent |
| `sla` | P95 latency, freshness window |

**Sales flow:** customer finds agent in registry → delegates `ingest_site` task → receives tenant API key → other agents call `ask_site` on their graph.

---

## 8. Recommended repo / service structure

Split for clarity and sales:

```text
roubaix/                    # OSS retrieval optimizer (keep credible, narrow)
cognee-community/pggraph/   # OSS graph adapter (you maintain)
graph-site-agent/           # NEW — commercial A2A service (or private repo)
├── agent/
│   ├── a2a_server.py       # A2A task/message handlers
│   ├── agent.json          # template Agent Card
│   └── skills/             # ingest, ask, stats
├── crawl/
│   ├── sitemap.py
│   ├── extract_schema.py   # JSON-LD, RSS
│   └── frontier.py
├── ingest/
│   ├── cognee_pipeline.py  # add → cognify → validate
│   └── tenant.py           # per-customer dataset isolation
├── routers/
│   └── roubaix_client.py   # depends on roubaix package
└── registry/
    └── publish.py          # push listing to your registry API

your-agent-registry/        # separate project — lists Graph Site Agent
```

**Dependency:** `graph-site-agent` imports `roubaix` as a library; does not fork it.

---

## 9. Commercial packaging

### Pricing axes (pick 2 for v1)

1. **Indexed pages** — crawl/cognify meter
2. **Queries/month** — A2A `ask_site` calls
3. **Graph slots** — number of domains
4. **Freshness SLA** — recrawl frequency

### Deliverables customers see

- Agent Card URL for their tenant
- Registry listing badge (“Graph-certified site”)
- Monthly eval PDF from Roubaix harness (quality + cost trend)
- Optional NLWeb `/ask` endpoint (upsell from [nlweb-integration-plan](./nlweb-integration-plan.md))

### What to open-source vs keep proprietary

| Open | Proprietary (service) |
|------|------------------------|
| Roubaix core | Multi-tenant orchestration |
| pgGraph adapter | Crawl + extract pipeline |
| demo-reel | Registry integration + billing |
| A2A skill schemas (docs) | Hosted graph storage + ops |

---

## 10. Risks

| Risk | Mitigation |
|------|------------|
| Crawl legal/ToS | Customer warrant; robots.txt respect; allowlist domains |
| Cognee cognify cost (LLM) | Tier limits; batch off-peak; incremental cognify |
| Weak sites (no Schema.org) | Text-only cognify still works; recommend Schema.org in onboarding |
| A2A spec churn | Pin protocol version; abstract skills behind internal API |
| Tenant isolation | Dataset-per-tenant; separate Postgres schemas; no cross-tenant NodeSets |
| “Just use NLWeb” | Position as graph upgrade + A2A native; NLWeb as optional export format |

---

## 11. Phased roadmap

### Phase G0 — Prove the loop (2 weeks)

- [ ] Crawl 1 public site (your choice) → `cognee.add` → `cognify` on pgGraph
- [ ] Roubaix `ask_site` queries via CLI (10 questions, relationship + factual)
- [ ] Draft Agent Card + manual registry JSON entry

### Phase G1 — A2A MVP (3–4 weeks)

- [ ] `graph-site-agent` repo with A2A server (`ingest_site`, `ask_site`)
- [ ] Async ingest task with status polling
- [ ] Per-tenant API keys
- [ ] List in your agent registry (staging)

### Phase G2 — Productize crawl (2–3 weeks)

- [ ] Sitemap + JSON-LD extractor; incremental recrawl
- [ ] `graph_stats` skill; freshness timestamps
- [ ] demo-reel video for registry listing

### Phase G3 — Revenue (ongoing)

- [ ] Metering + tiers
- [ ] Schema.org vertical playbooks (e-commerce first)
- [ ] NLWeb `/ask` export skill (optional)
- [ ] DSPy-optimized router per vertical (Roubaix Phase 3)

---

## 12. Decision summary

| Question | Recommendation |
|----------|----------------|
| Use Roubaix as foundation? | **Yes** — retrieval optimizer, not the whole product |
| Crawl vs API? | **Both** — crawl for launch; feeds/API for freshness |
| Cognee for ontology/graph? | **Yes** — `add` + `cognify`; Schema.org NodeSets first |
| A2A agent? | **Yes** — primary GTM for agent registry; skills above |
| NLWeb? | **Complementary** — optional export protocol, not core identity |
| New repo? | **Yes** — `graph-site-agent` service separate from Roubaix OSS |

**Next concrete step:** Phase G0 — crawl one real customer-shaped site, cognify to pgGraph, publish a stub Agent Card, and register it in your registry as a **beta listing**.
