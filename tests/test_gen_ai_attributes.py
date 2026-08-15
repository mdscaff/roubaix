"""Tests for OpenTelemetry GenAI attribute mapping."""

from __future__ import annotations

from app.observability.gen_ai import SEMCONV_VERSION, gen_ai_attributes


def _telemetry() -> dict[str, object]:
    return {
        "input_tokens": 512,
        "output_tokens": 64,
        "estimated_cost_usd": 0.00012,
        "cost_is_estimate": True,
        "evidence_items": 6,
        "stop_reason": "sufficient_evidence",
        "route_signals": ["multihop.impact"],
        "degraded": False,
        "degraded_reason": None,
    }


def test_usage_maps_onto_convention_names() -> None:
    attrs = gen_ai_attributes(_telemetry(), model="openai/gpt-4o-mini", provider="openrouter")
    assert attrs["gen_ai.usage.input_tokens"] == 512
    assert attrs["gen_ai.usage.output_tokens"] == 64
    assert attrs["gen_ai.request.model"] == "openai/gpt-4o-mini"
    assert attrs["gen_ai.provider.name"] == "openrouter"
    assert attrs["gen_ai.operation.name"] == "chat"


def test_roubaix_dimensions_are_namespaced_not_smuggled_into_gen_ai() -> None:
    """The gen_ai.* namespace belongs to the spec; inventing keys there collides."""
    attrs = gen_ai_attributes(_telemetry(), route_mode="GRAPH_COMPLETION")
    assert attrs["roubaix.route.mode"] == "GRAPH_COMPLETION"
    assert attrs["roubaix.stop_reason"] == "sufficient_evidence"
    assert attrs["roubaix.evidence_items"] == 6
    invented = [k for k in attrs if k.startswith("gen_ai.") and "usage" not in k]
    assert set(invented) <= {
        "gen_ai.operation.name",
        "gen_ai.request.model",
        "gen_ai.provider.name",
        "gen_ai.semconv.version",
    }


def test_none_values_are_omitted_rather_than_emitted_as_null() -> None:
    attrs = gen_ai_attributes(_telemetry())
    assert "roubaix.degraded_reason" not in attrs
    assert attrs["roubaix.degraded"] is False  # False is a value, not an absence


def test_semconv_version_is_pinned_and_reported() -> None:
    """Nothing in the GenAI conventions is Stable, so the version travels with the data."""
    assert gen_ai_attributes({})["gen_ai.semconv.version"] == SEMCONV_VERSION


def test_empty_telemetry_still_produces_a_valid_attribute_set() -> None:
    attrs = gen_ai_attributes({})
    assert attrs["gen_ai.operation.name"] == "chat"
    assert not any(k.startswith("roubaix.") for k in attrs)
