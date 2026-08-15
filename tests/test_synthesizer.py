"""Tests for AnswerSynthesizer, including the failure paths.

A provider outage that returns a fluent placeholder marked `accepted` is the
same class of defect as fabricated retrieval evidence, so the failure paths
matter more here than the happy path.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.domain.models import PackedEvidence, QueryRequest, RouteDecision, SearchMode
from app.services.synthesizer import AnswerSynthesizer


def _args() -> tuple[QueryRequest, RouteDecision, PackedEvidence]:
    return (
        QueryRequest(query="what port does billing expose"),
        RouteDecision(mode=SearchMode.CHUNKS, rationale="test"),
        PackedEvidence(
            mode=SearchMode.CHUNKS,
            summary="billing listens on 8443",
            evidence_items=["billing listens on 8443"],
        ),
    )


def _synth_with(handler: Any) -> AnswerSynthesizer:
    """Build a synthesizer whose HTTP client is backed by *handler*."""
    synth = AnswerSynthesizer(api_key="test-key", model="openai/gpt-4o-mini")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _client() -> httpx.AsyncClient:
        return client

    synth._client = _client  # type: ignore[method-assign]
    return synth


@pytest.mark.asyncio
async def test_returns_provider_answer_and_measured_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Port 8443."}}],
                "usage": {"prompt_tokens": 123},
            },
        )

    result = await _synth_with(handler).synthesize(*_args())
    assert result.answer == "Port 8443."
    assert result.input_tokens_estimate == 123
    assert result.usage_measured is True
    assert result.failed is False


@pytest.mark.asyncio
async def test_missing_usage_falls_back_to_a_labelled_estimate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "Port 8443."}}]})

    result = await _synth_with(handler).synthesize(*_args())
    assert result.input_tokens_estimate > 0
    assert result.usage_measured is False  # must not be reported as measured


@pytest.mark.asyncio
async def test_http_error_is_marked_failed_not_answered() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    result = await _synth_with(handler).synthesize(*_args())
    assert result.failed is True
    assert result.failure_reason is not None


@pytest.mark.asyncio
async def test_malformed_json_body_is_handled_not_raised() -> None:
    """Regression: ValueError is not an httpx.HTTPError and escaped as a 500."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="this is not json")

    result = await _synth_with(handler).synthesize(*_args())
    assert result.failed is True


@pytest.mark.asyncio
async def test_transport_error_is_marked_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await _synth_with(handler).synthesize(*_args())
    assert result.failed is True
    assert "ConnectError" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_empty_choices_does_not_masquerade_as_an_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    result = await _synth_with(handler).synthesize(*_args())
    assert "No completion returned" in result.answer


@pytest.mark.asyncio
async def test_no_api_key_is_labelled_unsynthesized_not_failed() -> None:
    """CI and local dev have no provider; that is expected, not an outage."""
    synth = AnswerSynthesizer(api_key="", model="openai/gpt-4o-mini")
    result = await synth.synthesize(*_args())
    assert result.unsynthesized is True
    assert result.failed is False
    assert result.failure_reason == "no_api_key_configured"


@pytest.mark.asyncio
async def test_static_prefix_is_byte_identical_across_requests() -> None:
    """Prefix caching requires an exact match; drift silently disables it."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content)["messages"][0]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    synth = _synth_with(handler)
    await synth.synthesize(*_args())
    req, route, packed = _args()
    req.query = "a completely different question"
    await synth.synthesize(req, route, packed)
    assert len(seen) == 2
    assert seen[0] == seen[1]
