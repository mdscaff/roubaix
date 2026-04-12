# AGENTS.md

Project instructions for Codex.

## Objective

Implement Roubaix as a measurable, production-credible experiment for Cognee-centered graph retrieval with DSPy/GEPA optimization and AdalFlow runtime control.

## Non-negotiable rules

1. Verify implementation assumptions against official docs before changing dependency-specific code.
2. Favor small, testable commits over broad rewrites.
3. Preserve typed interfaces unless there is a strong architectural reason to change them.
4. Keep business value visible: cost, latency, answer quality, and freshness.
5. Never collapse all retrieval into one expensive graph path.
6. Keep static prompt policy separate from dynamic evidence.

## Primary docs to consult first

- `docs/architecture.md`
- `docs/implementation-plan.md`
- `docs/evaluation-plan.md`

## Working style

- Start by reading the relevant interface and adjacent tests.
- Implement the narrowest change that satisfies the next milestone.
- Add or update tests with each behavior change.
- Prefer explicit TODO comments with owner/next-step context over vague placeholders.

## Architecture boundaries

- `app/integrations/cognee_client.py` owns Cognee I/O only.
- `app/services/router.py` owns query-to-mode routing decisions.
- `app/services/evidence.py` owns evidence compression.
- `app/services/runtime_controller.py` owns retry/escalation policy.
- `app/integrations/dspy_program.py` and `app/integrations/gepa_optimizer.py` own optimization logic.
- `app/integrations/adalflow_controller.py` owns AdalFlow-specific orchestration.

## Guardrails

- Do not make framework-specific assumptions without leaving a citation in code comments or docs.
- Do not silently add background jobs, queues, or databases unless reflected in docs.
- Do not introduce magical heuristics without metrics.

## Definition of done for a feature

- compiles
- tests pass
- metrics emitted for the new decision point
- docs updated if behavior changed
