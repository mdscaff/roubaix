"""OpenTelemetry GenAI attribute naming for Roubaix telemetry.

Roubaix's answer telemetry already carries everything a GenAI trace needs; it
was just under Roubaix-private key names, which no observability backend knows
how to read. This module maps it onto the OpenTelemetry GenAI semantic
conventions so any OTel-aware backend can chart cost and token usage without a
custom integration. Langfuse v4 is itself OpenTelemetry-based, so these names
land in real spans today.

Two rules:

- **Never invent a ``gen_ai.*`` key.** That namespace belongs to the spec and is
  still moving. Roubaix-specific dimensions go under ``roubaix.*``, where they
  cannot collide with a future convention.
- **Do not claim compliance.** Nothing in the GenAI conventions is marked
  Stable. The accurate statement is "emits OpenTelemetry GenAI semantic
  convention attributes (Development stability)", and the attribute set is
  expected to churn.
"""

from __future__ import annotations

from typing import Any

# Pinned so a convention change is a deliberate edit rather than a silent drift
# in what downstream dashboards receive.
SEMCONV_VERSION = "1.36.0"

# Roubaix telemetry key -> OTel GenAI attribute name.
_GEN_AI_MAP: dict[str, str] = {
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
}

# Roubaix telemetry keys that have no convention equivalent. These are the
# retrieval-routing dimensions that make this system what it is, so they are
# namespaced rather than dropped.
_ROUBAIX_KEYS: tuple[str, ...] = (
    "evidence_items",
    "evidence_tokens",
    "evidence_dropped_duplicates",
    "evidence_dropped_over_budget",
    "retrieval_ms",
    "synthesis_ms",
    "total_ms",
    "cache_hit",
    "degraded",
    "degraded_reason",
    "stop_reason",
    "escalation_reason",
    "attempted_modes",
    "route_signals",
    "route_confident",
    "widened",
    "temporal_grounded",
    "budget_downgrade",
    "estimated_cost_usd",
    "cost_is_estimate",
)


def gen_ai_attributes(
    telemetry: dict[str, Any],
    *,
    model: str | None = None,
    provider: str | None = None,
    operation: str = "chat",
    route_mode: str | None = None,
) -> dict[str, Any]:
    """Return OTel-named attributes for one answer.

    Values that are ``None`` are omitted: an absent attribute is honest, an
    attribute set to null is noise in every backend that ingests it.
    """
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": operation,
        "gen_ai.semconv.version": SEMCONV_VERSION,
    }
    if model:
        attributes["gen_ai.request.model"] = model
    if provider:
        attributes["gen_ai.provider.name"] = provider

    for source, target in _GEN_AI_MAP.items():
        value = telemetry.get(source)
        if value is not None:
            attributes[target] = value

    if route_mode:
        attributes["roubaix.route.mode"] = route_mode
    for key in _ROUBAIX_KEYS:
        value = telemetry.get(key)
        if value is not None:
            attributes[f"roubaix.{key}"] = value

    return attributes
