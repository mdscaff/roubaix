"""Query normalization for cache-friendly canonical forms.

Inspired by HyperSpace AGI architect-v1 four-layer resolution pipeline:
normalize input before cache/similarity lookups to maximize hit rates.
"""

import hashlib
import re

# Common English stop words that don't affect routing decisions.
# Kept minimal to avoid stripping semantically meaningful words.
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "having",
    "i", "me", "my", "we", "our", "you", "your",
    "it", "its", "this", "that", "these", "those",
    "of", "in", "to", "for", "with", "on", "at", "by", "from", "as",
    "and", "but", "or", "so", "if", "then", "than",
    "can", "will", "would", "should", "could", "may", "might",
    "what", "which", "who", "whom", "whose",
    "please", "just", "also", "very", "really",
})

_NON_ALPHA = re.compile(r"[^a-z0-9\s]")


class QueryNormalizer:
    """Produces a canonical query form for cache and similarity lookups.

    Pipeline: lowercase → strip punctuation → remove stop words → sort keywords.
    The sorted keyword string maximizes exact-match cache hits for semantically
    identical queries phrased differently.
    """

    def normalize(self, query: str) -> str:
        """Return canonical form of *query*."""
        q = query.lower().strip()
        q = _NON_ALPHA.sub(" ", q)
        tokens = q.split()
        tokens = [t for t in tokens if t not in _STOP_WORDS]
        tokens.sort()
        return " ".join(tokens)

    def content_key(self, normalized_query: str, dataset: str, mode: str | None = None) -> str:
        """SHA-256 content address for cache lookups.

        Incorporates dataset and optional mode so identical queries against
        different datasets or modes resolve independently.
        """
        payload = f"{normalized_query}|{dataset}"
        if mode:
            payload += f"|{mode}"
        return hashlib.sha256(payload.encode()).hexdigest()
