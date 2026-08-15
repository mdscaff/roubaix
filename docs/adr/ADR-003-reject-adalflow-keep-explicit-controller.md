# ADR-003: Reject AdalFlow; keep an explicit runtime controller

- **Status:** Accepted
- **Date:** 2026-08-15
- **Supersedes:** the "AdalFlow — runtime fallback, escalation, and freshness-aware
  control" line in `docs/architecture.md` and the `app/integrations/adalflow_controller.py`
  placeholder (both removed).

## Context

Roubaix's original three-layer thesis named AdalFlow as the runtime control
layer: fallback, escalation, and freshness-aware control. That was recorded as
architecture before the library was evaluated. `adalflow_controller.py` sat in
the tree for months returning `{"status": "not_implemented"}`, and `adalflow`
was carried as a declared dependency in the `opt` extra without a single import.

We evaluated it properly before building the runtime controller out.

## Findings

1. **It is not the right category of tool.** AdalFlow is a PyTorch-analogue
   auto-optimization library — `Generator`, `Component`, `Parameter`,
   `AdalComponent`, `Trainer`, plus textual-gradient and few-shot bootstrap
   optimization. It has an agent `Runner` with `call`/`acall`/`astream`, but no
   documented primitives for retry, escalation, or fallback policy. Its job
   overlaps DSPy's, not `runtime_controller.py`'s.

2. **Adopting it would mean running two prompt-optimization stacks.** Roubaix
   has already committed to DSPy + GEPA for offline optimization. AdalFlow would
   be a second, less-supported optimizer covering the same ground.

3. **Release cadence.** The last published release is 1.1.3 (September 2025).
   The repository has commits well into 2026, so it is not abandoned, but
   nothing installable has shipped in close to a year. Our declared floor of
   `>=1.1.0` resolves to that 2025 build.

## Decision

Drop AdalFlow. Keep `app/services/runtime_controller.py` as an explicit state
machine: a `ControlAction` enum, a terminating `ESCALATION_LADDER`, an
`attempted_modes` set, and a structured `ControlDecision` carrying the reason.

We also considered and declined:

- **LangGraph** — genuinely capable, but it is a large commitment for a bounded
  request/response loop. Its resume semantics re-execute the interrupted node,
  so every LLM call in that node re-fires; that needs explicit idempotency
  guarding we would otherwise not need. See ADR-002 for the Temporal boundary.
- **Burr** — the closest philosophical match (explicit state machine plus
  persistence and a trace UI). Declined only because the trace UI is the main
  draw and OpenTelemetry export gives us that without a new dependency.

## Consequences

**Positive.** The escalation policy stays ~180 lines of dependency-free,
directly unit-testable Python. The "escalation terminates" property is provable
by inspection of `ESCALATION_LADDER` plus the `attempted_modes` guard, rather
than resting on a framework's execution semantics. Removing the placeholder also
removes a documented capability that did not exist.

**Negative.** We own the control loop, including any durability or
human-in-the-loop primitives we later need. If Roubaix grows an approval gate or
a multi-day workflow, this decision should be revisited — that is the point at
which a durable-execution runtime starts paying for itself (see ADR-002).

**Follow-up.** `docs/architecture.md`, `README.md`, `AGENTS.md`, and
`pyproject.toml` were updated to remove AdalFlow. The runtime-control layer is
now described as what it is: a deterministic controller, with DSPy/GEPA scoped
to offline routing optimization only.
