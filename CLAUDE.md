# CLAUDE.md

Persistent instructions for Claude Code.

## Project intent

Roubaix is a technical experiment, not a demo. The audience is skeptical and detail-oriented. Favor verifiable implementation choices, narrow claims, and instrumentation over rhetoric.

## Priorities

1. Measurable business value first
2. Clear technical boundaries
3. Cache-aware architecture
4. Scoped graph retrieval
5. Runtime safety and fallback behavior

## Expected workflow

- Read `README.md` and `docs/architecture.md` first.
- When implementing, inspect adjacent files before editing.
- Prefer making a baseline version work before adding DSPy/GEPA or AdalFlow sophistication.
- Leave concise implementation notes where future optimization will matter.

## Coding conventions

- Python 3.11+
- Type hints everywhere
- Pydantic models for API contracts
- Small service classes with explicit dependencies
- No hidden globals beyond config loading
- Avoid giant utility files

## Project heuristics

- Routing should begin with a deterministic baseline.
- Evidence packing should minimize tokens, not maximize context.
- Runtime control should escalate progressively.
- Freshness-sensitive questions should trigger temporal validation.
- NodeSet scoping should be treated as a first-class cost lever.

## When uncertain

Prefer a conservative, observable implementation with a TODO over an untestable abstraction.
