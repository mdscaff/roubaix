# Router policy

This file is intended to become the stable, cacheable policy summary used by the synthesis layer.

## High-level rules

- prefer the cheapest search mode that can plausibly answer the question
- use triplets for relationship-heavy questions
- use graph summaries for broad explanatory prompts
- use temporal retrieval when freshness matters
- escalate progressively, not immediately
