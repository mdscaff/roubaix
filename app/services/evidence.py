"""Evidence packing.

The packer's job is to produce the *smallest* payload that still supports an
answer. Three things it must do that truncation alone does not:

1. Honour the route's evidence budget. The budget is the router's cost
   decision; ignoring it in favour of a global cap makes routing decorative.
2. Deduplicate. Graph and chunk retrieval routinely return the same fact
   through several paths, and duplicates are paid for twice.
3. Bound *tokens*, not item count. Twelve items is not a budget when items
   range from 20 to 2000 characters.
"""

from __future__ import annotations

import hashlib

from app.core.config import settings
from app.core.tokens import estimate_tokens
from app.domain.models import PackedEvidence, RetrievalResult, SearchMode


def _fingerprint(item: str) -> str:
    """Stable hash of an evidence item, insensitive to whitespace and case."""
    return hashlib.sha256(" ".join(item.lower().split()).encode()).hexdigest()[:16]


class EvidencePacker:
    def pack(
        self,
        result: RetrievalResult,
        *,
        evidence_budget: int | None = None,
        token_budget: int | None = None,
    ) -> PackedEvidence:
        raw = self._extract(result)

        max_items = min(
            evidence_budget or settings.max_evidence_items,
            settings.max_evidence_items,
        )
        max_tokens = token_budget or settings.evidence_token_budget

        # Deduplicate, preserving retrieval order (rank order carries signal).
        seen: set[str] = set()
        deduped: list[tuple[str, str]] = []
        duplicates = 0
        for item in raw:
            text = item.strip()
            if not text:
                continue
            digest = _fingerprint(text)
            if digest in seen:
                duplicates += 1
                continue
            seen.add(digest)
            deduped.append((text, digest))

        # Fill against both budgets, whichever binds first.
        items: list[str] = []
        hashes: list[str] = []
        tokens = 0
        over_budget = 0
        for text, digest in deduped:
            if len(items) >= max_items:
                over_budget += 1
                continue
            cost = estimate_tokens(text)
            if items and tokens + cost > max_tokens:
                over_budget += 1
                continue
            items.append(text)
            hashes.append(digest)
            tokens += cost

        # Reduction must be recoverable, not silently destructive: the summary
        # states what was withheld and how to get it back. A synthesizer that
        # cannot tell "there was no more evidence" from "there was more evidence
        # and it was dropped" will answer confidently in both cases.
        summary = "\n".join(items) if items else "No evidence items returned."
        if over_budget:
            summary += (
                f"\n\n[{over_budget} further evidence item(s) withheld to stay within "
                f"the {max_items}-item / {max_tokens}-token budget for this route. "
                f"They are not absent from the graph — re-query with a larger "
                f"evidence_budget or a narrower node_sets scope to retrieve them.]"
            )
        return PackedEvidence(
            mode=result.mode,
            summary=summary,
            evidence_items=items,
            evidence_hashes=hashes,
            provenance=result.evidence.provenance,
            degraded=result.degraded,
            degraded_reason=result.degraded_reason,
            token_estimate=tokens,
            dropped_duplicates=duplicates,
            dropped_over_budget=over_budget,
        )

    @staticmethod
    def _extract(result: RetrievalResult) -> list[str]:
        """Flatten mode-specific retrieval payloads into text items."""
        evidence = result.evidence
        if result.mode == SearchMode.TRIPLET_COMPLETION:
            return [
                f"{t.get('subject')} {t.get('predicate')} {t.get('object')}"
                for t in evidence.triplets
            ]
        if result.mode in {SearchMode.CHUNKS, SearchMode.RAG_COMPLETION}:
            return list(evidence.chunks)
        if result.mode in {SearchMode.GRAPH_COMPLETION, SearchMode.GRAPH_SUMMARY_COMPLETION}:
            return [str(path) for path in evidence.graph_paths]
        if result.mode in {SearchMode.CYPHER, SearchMode.NATURAL_LANGUAGE}:
            return [str(row) for row in evidence.rows]
        if result.mode == SearchMode.TEMPORAL:
            # Temporal retrieval returns timestamps plus whatever textual
            # evidence the substrate attached; both matter for a freshness
            # answer, and dropping the text was losing the actual content.
            return [*evidence.timestamps, *evidence.chunks]
        return []
