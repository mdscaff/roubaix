"""Query normalization for cache keys and routing signals.

Three distinct forms, deliberately separated because they have different
correctness requirements:

``normalize``
    Order-preserving canonical text. Used for cache keys and for router phrase
    matching. Order **must** be preserved: "does A depend on B" and "does B
    depend on A" are different questions with different answers.

``keywords``
    Stop-word-stripped tokens, order preserved. Used for routing signals and
    lexical overlap scoring.

``fingerprint``
    Order-insensitive keyword bag. Useful for near-duplicate *detection* and
    telemetry clustering. Never use it as a cache key — it collides across
    inverted relationships by construction.
"""

from __future__ import annotations

import hashlib
import re

# Common English function words that carry no retrieval signal. Kept minimal to
# avoid stripping semantically meaningful words. Note that words which appear in
# router phrases (``how``, ``depends on``, ...) are matched against the
# order-preserving form, not against this stripped form.
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

# Bumped whenever routing rules, evidence packing, or the synthesis prompt
# change in a way that invalidates previously cached answers. Included in every
# cache key so a deploy cannot serve answers produced under an older policy.
POLICY_VERSION = "2"


class QueryNormalizer:
    """Produces canonical query forms for cache lookups and routing."""

    def normalize(self, query: str) -> str:
        """Return the order-preserving canonical form of *query*.

        Lowercases, replaces punctuation with whitespace, and collapses runs of
        whitespace. Token order and stop words are preserved so that phrase
        semantics survive — this string is safe to use as a cache key input.
        """
        q = _NON_ALPHA.sub(" ", query.lower().strip())
        return " ".join(q.split())

    def keywords(self, query: str) -> list[str]:
        """Return content tokens of *query* with stop words removed, in order."""
        return [t for t in self.normalize(query).split() if t not in _STOP_WORDS]

    def fingerprint(self, query: str) -> str:
        """Return an order-insensitive keyword bag for *query*.

        For near-duplicate detection and telemetry clustering only. This form
        deliberately discards word order, so it collides across inverted
        relationships ("A depends on B" / "B depends on A") and must never be
        used to key a cache.
        """
        return " ".join(sorted(self.keywords(query)))

    def content_key(
        self,
        normalized_query: str,
        dataset: str,
        *,
        freshness_required: bool = False,
        node_sets: list[str] | None = None,
        model: str | None = None,
        user_id: str | None = None,
        policy_version: str = POLICY_VERSION,
    ) -> str:
        """SHA-256 content address for cache lookups.

        Every input that can change the answer must be part of the key. Beyond
        the query text itself that means the dataset, the freshness contract,
        the NodeSet scope, the synthesis model, the caller identity, and the
        policy version — a cached answer produced under a different routing
        policy, a different model, or for a different caller is not a valid
        answer for this request.

        ``user_id`` is included even though datasets are currently shared: the
        cache is process-global, so the moment any per-caller scoping exists,
        omitting it becomes a cross-tenant disclosure rather than a cache miss.
        That is not a failure mode worth discovering later.
        """
        payload = "|".join(
            [
                f"v={policy_version}",
                f"q={normalized_query}",
                f"ds={dataset}",
                f"fresh={int(freshness_required)}",
                f"ns={','.join(sorted(node_sets or []))}",
                f"model={model or ''}",
                f"user={user_id or ''}",
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()
