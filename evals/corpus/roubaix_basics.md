# Roubaix baseline corpus

Roubaix is a Cognee-centered graph retrieval experiment. It routes each user query to a
deterministic search mode before calling Cognee retrieval.

## Routing

Simple factual questions should prefer CHUNKS mode for low latency. Relationship questions
should prefer TRIPLET_COMPLETION or GRAPH_COMPLETION. Freshness-sensitive questions should set
requires_freshness_validation and use shorter cache TTL.

## Evidence packing

The evidence packer compresses triplets, chunks, graph paths, and temporal markers into a
token-bounded summary. The default evidence budget is eight items and the global cap is
twelve items via ROUBAIX_MAX_EVIDENCE_ITEMS.

## Runtime control

When retrieval returns no evidence, the runtime controller escalates once to an alternate
mode. After ROUBAIX_MAX_RETRIES attempts, Roubaix fails closed instead of synthesizing from
empty context.

## pgGraph

For local development, Roubaix can register the community pgGraph adapter when
GRAPH_DATABASE_PROVIDER=pggraph and Postgres is available on port 5433.
