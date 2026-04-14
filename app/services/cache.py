"""Content-addressed LRU cache with TTL.

Inspired by HyperSpace AGI architect-v1 Content Store layer:
SHA-256 keyed, bounded size, time-to-live expiry.
Freshness-sensitive entries use a shorter TTL.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class _CacheEntry:
    value: Any
    expires_at: float


class ContentAddressedCache:
    """LRU + TTL cache keyed by SHA-256 content hashes.

    Parameters
    ----------
    max_size:
        Maximum number of entries before LRU eviction.
    default_ttl_s:
        Default time-to-live in seconds for cache entries.
    freshness_ttl_s:
        Shorter TTL used for entries flagged as freshness-sensitive.
    """

    def __init__(
        self,
        max_size: int = 4096,
        default_ttl_s: float = 3600.0,
        freshness_ttl_s: float = 120.0,
    ) -> None:
        self._max_size = max_size
        self._default_ttl_s = default_ttl_s
        self._freshness_ttl_s = freshness_ttl_s
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()

    def get(self, key: str) -> Any | None:
        """Return cached value for *key*, or ``None`` on miss / expiry."""
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        # Move to end (most-recently used).
        self._store.move_to_end(key)  # type: ignore[attr-defined]
        return entry.value

    def put(self, key: str, value: Any, *, freshness_sensitive: bool = False) -> None:
        """Store *value* under *key* with appropriate TTL."""
        ttl = self._freshness_ttl_s if freshness_sensitive else self._default_ttl_s
        now = time.monotonic()
        if key in self._store:
            self._store.move_to_end(key)  # type: ignore[attr-defined]
        self._store[key] = _CacheEntry(value=value, expires_at=now + ttl)
        self._evict()

    @property
    def size(self) -> int:
        return len(self._store)

    def _evict(self) -> None:
        """Remove oldest entries if over capacity."""
        while len(self._store) > self._max_size:
            self._store.pop(next(iter(self._store)))
