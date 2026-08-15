"""Optional Langfuse tracing for synthesis and eval debugging.

Install with: pip install langfuse
Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and optionally LANGFUSE_HOST.
Tracing is disabled when Langfuse is not configured or not installed.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


_CLIENT: Any = None


def _client() -> Any:
    """Return a process-wide Langfuse client, or None when unavailable.

    Constructing a client per span, as this module previously did, meant a new
    client object on every synthesis call.
    """
    global _CLIENT
    if _CLIENT is None:
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]
        except ImportError:
            return None
        _CLIENT = Langfuse()
    return _CLIENT


def flush() -> None:
    """Flush buffered spans. Call at shutdown, never on the request path."""
    if _CLIENT is not None:
        _CLIENT.flush()


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
) -> Iterator[None]:
    """Open a Langfuse span when configured; otherwise no-op."""
    if not langfuse_enabled():
        yield
        return

    client = _client()
    if client is None:
        yield
        return

    span_metadata = {
        "run_id": run_id,
        "baseline": baseline,
        "query_id": query_id,
        **(metadata or {}),
    }
    span = client.start_span(name=name, metadata=span_metadata)
    try:
        yield
    finally:
        span.end()
        # No flush here. flush() is a synchronous network call, and this
        # context manager runs inside the async request path — flushing per
        # span blocked the event loop on every query. Langfuse buffers and
        # flushes in the background; call flush() at shutdown instead.
