# Evaluation Plan

## Evaluation goals

Quantify whether Roubaix improves:

- answer quality
- cost per successful answer
- latency
- freshness accuracy
- unnecessary escalation rate

## Query buckets

- local factual lookup
- relationship-heavy lookup
- multi-hop reasoning
- broad summary/explanation
- structural/graph query
- time-sensitive query
- ambiguous or under-specified query

## Metrics

### Business metrics

- cost per successful answer
- p50 / p95 latency
- freshness correctness
- successful-answer rate

### System metrics

- search mode distribution
- NodeSet scope size
- evidence items selected
- input tokens to synthesis model
- retries per query
- escalation reason

### Quality metrics

- correctness
- groundedness
- citation/provenance sufficiency
- temporal correctness

## Baselines

1. chunk-only RAG baseline
2. single graph-completion baseline
3. Roubaix rule-based routing baseline
4. Roubaix DSPy/GEPA-optimized configuration

## Acceptance gates

- at least 25% reduction in median input tokens versus single-path graph baseline
- no degradation in overall correctness
- measurable improvement on temporal queries when temporal mode is enabled
- reduced unnecessary use of expensive graph modes
