# ADR-004: Evaluate Strands Agents SDK — adopt the patterns, refuse the dependency

- **Status:** Accepted
- **Date:** 2026-08-15
- **Related:** [ADR-003](ADR-003-reject-adalflow-keep-explicit-controller.md) (AdalFlow rejection)

## Context

The Strands Agents SDK (AWS, Apache 2.0, `strands-agents` 1.52.0) is marketed as
"build an agent harness and control it end-to-end", and maps onto a three-layer
model of agent engineering — **harness** (the environment around the model),
**loop** (work → evidence → feedback → stop conditions), and **graph** (explicit
workflow topology). Roubaix has hand-built pieces of all three, so the question
is whether to adopt the SDK.

The three-layer framing is genuinely useful and worth adopting as vocabulary.
Its own advice is the relevant part: **choose a layer by diagnosing the failure
mode, not by defaulting to the most complex one.**

## Decision

**Do not take the dependency. Reimplement four specific patterns.**

Strands is a more seductive mistake than AdalFlow. AdalFlow was wrong because it
was an offline optimization library posing as runtime control. Strands genuinely
*is* runtime control — which is why it feels like a match. It is still the wrong
fit, for a precise reason:

> Every control primitive Strands has is denominated in units Roubaix does not
> have.

Walking its taxonomy against this pipeline:

| Strands primitive | Roubaix reality |
|---|---|
| `limits={"turns": N}` | Roubaix's turn count is always 1. The cap is unreachable by construction. |
| `BeforeToolCallEvent`, `AfterToolCallEvent`, `BeforeToolsEvent`, `AfterToolsEvent` | Four of ten hook events, carrying the richest mutation power. Roubaix has no tools. Dead surface. |
| `MessageAddedEvent`, `SessionManager`, `conversation_manager` | No multi-turn conversation. |
| `AfterInvocationEvent.resume` | The autonomous-looping escape hatch. Roubaix runs one bounded pass. |
| `Confirm` / interrupts | No human in the request path. |

That leaves roughly four usable events, and none of them fit the stages that
matter. **There is no extension point for a stage that is neither a model call
nor a tool call** — which is route, retrieve, pack, and control: precisely the
four stages that need instrumentation and veto. Adopting it would mean smuggling
them through an untyped `invocation_state` bag or emitting counterfeit tool
events. That is ADR-003's failure mode in a new costume: bending the
architecture to fit a framework's ontology.

The dependency arithmetic reinforces it. `boto3`, `botocore`, `mcp`, and
`watchdog` are mandatory core dependencies, not extras — taken into a FastAPI
service to obtain, in effect, an event bus. Release cadence is roughly weekly
minors on a 1.x, and the surface is visibly still moving (the steering subtree
ships in two locations in a single release). And Strands has **no cost budget at
all** — token counts only, no price table. Roubaix is already ahead of it on the
project's stated first priority.

## What was adopted

Four patterns, implemented directly, no dependency:

1. **Stop reasons as a closed vocabulary.** Strands puts `"limit_turns"` in the
   same `StopReason` union as `"end_turn"`: a budget trip is an *outcome*, not
   an error. Roubaix's `StopReason` enum does the same, which turns "how often
   did we stop on the latency ceiling" into a query over telemetry instead of a
   grep over log strings. This also closes an earlier audit finding that
   escalation reasons were free text and could not be aggregated.

2. **`max_latency_ms` made real**, as a stop reason rather than an exception.
   It had been declared on the request model and read nowhere.

3. **Per-check error policy defaulting to fail-closed.** Strands'
   `on_error: "throw" | "proceed" | "deny"` documents `'proceed'` as *"fail-open:
   a broken handler silently stops enforcing its policy."* For a system whose
   thesis is fail-closed, naming the failure mode of each check *itself* is the
   right generalization. `CheckErrorPolicy` defaults to DENY.

4. **OTel GenAI attribute names.** Strands' tracer is the most credible part of
   its codebase and follows the conventions properly. Roubaix adopts the
   *names* (`gen_ai.usage.input_tokens`, `gen_ai.request.model`) without the
   tracer, keeping Roubaix dimensions under `roubaix.*` so nothing collides with
   a namespace the spec owns.

Two further ideas are recorded but **not** built, because nothing would consume
them yet: write-locked event objects with a per-field mutation allowlist, and a
priority-ordered hook registry with reverse teardown. A bus with one subscriber
is ceremony. The forcing function, if it is ever built, is to reimplement the
widen/escalate ladder as its first consumer — if the abstraction cannot express
the ladder cleanly, that is cheap to learn.

## What was explicitly not adopted

- **The `Graph` / `Swarm` multi-agent engine.** Roubaix is a fixed pipeline, not
  a topology. Worth noting their own cycle safety is weaker than the docs imply:
  `max_node_executions` defaults to `None` (unbounded), and `_validate_graph()`
  does not actually detect cycles despite a comment saying it does. Roubaix's
  escalation ladder terminates by construction.
- **Steering handlers** (an LLM judge returning proceed/guide/interrupt). It
  costs an extra LLM call per decision and is the "untestable abstraction"
  `CLAUDE.md` warns against. The deterministic router scores 85% held-out
  against that entire category.
- **Session/conversation persistence.** No multi-turn state to persist.

## Consequences

**Positive.** Roubaix keeps a ~200-line dependency-free controller whose
termination is provable by inspection, and gains a closed stop-reason
vocabulary, a real latency ceiling, an explicit policy for its own check
failures, and vendor-portable telemetry names. Total cost was well under the
~150 lines a full hook system would have taken.

**Negative.** If Roubaix ever grows tool calling, multi-turn conversation, or a
human approval gate, this decision should be revisited — those are exactly the
units Strands is built in, and at that point the fit inverts.

**Note on evidence.** This ADR is based on reading the published 1.52.0 wheel
and repository source. The vendor's documentation site was unreachable from the
evaluation environment, and download/adoption figures quoted in secondary
coverage could not be verified; none of them affected the decision.
