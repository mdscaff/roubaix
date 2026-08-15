"""Optional Langfuse tracing for synthesis and eval debugging.

Install with the ``eval`` extra. Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY,
and optionally LANGFUSE_HOST. Tracing is disabled when Langfuse is not
configured or not installed, and a tracing failure must never fail a query —
this is an enhancement, not a validation (see docs/architecture.md).

Written against the Langfuse v4 API, which is built on OpenTelemetry:
``start_as_current_observation(as_type="generation")`` replaces the v2/v3
``Langfuse.start_span``, which no longer exists. The span is yielded so the
caller can attach measured token usage and cost once the provider responds.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_CLIENT: Any = None
_CLIENT_FAILED = False


def _client() -> Any:
    """Return a process-wide Langfuse client, or None when unavailable.

    Constructing a client per span, as this module previously did, meant a new
    client object on every synthesis call.
    """
    global _CLIENT, _CLIENT_FAILED  # noqa: PLW0603
    if _CLIENT_FAILED:
        return None
    if _CLIENT is None:
        try:
            from langfuse import get_client  # type: ignore[import-not-found]

            _CLIENT = get_client()
        except Exception as exc:  # noqa: BLE001 - optional dependency
            logger.debug("langfuse_unavailable", extra={"error": str(exc)})
            _CLIENT_FAILED = True
            return None
    return _CLIENT


def flush() -> None:
    """Flush buffered spans. Call at shutdown, never on the request path."""
    if _CLIENT is not None:
        try:
            _CLIENT.flush()
        except Exception as exc:  # noqa: BLE001 - shutdown is best-effort
            logger.debug("langfuse_flush_failed", extra={"error": str(exc)})


def langfuse_enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


@contextmanager
def trace_synthesis_span(
    *,
    name: str,
    run_id: str | None,
    baseline: str | None,
    query_id: str | None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
) -> Iterator[Any]:
    """Open a Langfuse generation span when configured; otherwise no-op.

    Yields the span (or ``None``) so the caller can attach measured usage and
    cost after the provider responds. No flush happens here: flush is a
    synchronous network call and this runs inside the async request path.
    """
    if not langfuse_enabled():
        yield None
        return

    client = _client()
    if client is None:
        yield None
        return

    span_metadata = {
        "run_id": run_id,
        "baseline": baseline,
        "query_id": query_id,
        **(metadata or {}),
    }
    try:
        with client.start_as_current_observation(
            name=name,
            as_type="generation",
            metadata=span_metadata,
            model=model,
        ) as span:
            yield span
    except Exception as exc:  # noqa: BLE001 - tracing must never fail a query
        logger.debug("langfuse_span_failed", extra={"error": str(exc)})
        yield None


def record_usage(span: Any, *, input_tokens: int, output_tokens: int, usd: float | None) -> None:
    """Attach measured usage and cost to *span*, if there is one.

    Best-effort by design: an observability write must never turn a successful
    answer into a failed request.
    """
    if span is None:
        return
    try:
        details: dict[str, Any] = {
            "usage_details": {"input": input_tokens, "output": output_tokens}
        }
        if usd is not None:
            details["cost_details"] = {"total": usd}
        span.update(**details)
    except Exception as exc:  # noqa: BLE001 - enhancement, not validation
        logger.debug("langfuse_usage_update_failed", extra={"error": str(exc)})


def record_attributes(span: Any, attributes: dict[str, Any]) -> None:
    """Attach OTel-named attributes to *span*, if there is one.

    Langfuse v4 wraps an OpenTelemetry span, so convention-named attributes
    land where an OTel-aware backend expects them. Best-effort, like all
    tracing here.
    """
    if span is None:
        return
    try:
        otel_span = getattr(span, "_otel_span", None)
        if otel_span is not None:
            otel_span.set_attributes(attributes)
        else:
            span.update(metadata=attributes)
    except Exception as exc:  # noqa: BLE001 - enhancement, not validation
        logger.debug("langfuse_attribute_update_failed", extra={"error": str(exc)})
