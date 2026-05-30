"""Optional Langfuse tracing for synthesis and eval debugging.

Install with: pip install langfuse
Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and optionally LANGFUSE_HOST.
Tracing is disabled when Langfuse is not configured or not installed.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


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

    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except ImportError:
        yield
        return

    client = Langfuse()
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
        client.flush()
