"""Set-level evidence sufficiency gate.

The runtime controller previously gated synthesis on evidence *count* and token
volume, so one irrelevant chunk passed. The 2024-2026 literature converged on
two constraints this module implements (see docs/implementation-plan.md,
Phase A, for the sources and measured results):

1. **Sufficiency is a property of the evidence set, not of items.** A
   per-chunk relevance scorer structurally cannot detect a missing multi-hop
   link — it cannot observe an absent bridge passage — and mean-pooling
   per-item scores dilutes a decisive refutation. Every signal here is
   computed over the packed set as a whole.
2. **The gate must be external to the synthesis model.** Frontier models
   answer instead of abstaining on insufficient context, so "the model will
   say it doesn't know" is not a gate.

Two tiers, cheapest first:

- **Tier 0** (always on, pure Python): set-level lexical signals — query-term
  coverage across the whole set, the fraction of items that support at least
  one query term, and provenance diversity. Free, deterministic, explainable.
- **Tier 1** (optional): a pluggable per-item support scorer aggregated into
  the set verdict, run only when Tier 0 returns UNCERTAIN. The intended scorer
  is MiniCheck (770M encoder, GPT-4-level fact-checking at ~400x lower cost);
  its ``[0,1]`` output is *not* demonstrated to be calibrated, so the
  threshold is config, tuned empirically, and documented as such. Loading is
  lazy and any failure leaves the verdict UNCERTAIN — the gate is an
  enhancement, never a validation.

A Tier 2 LLM autorater (Sufficient Context style) is described in the plan and
deliberately **not built**: this repository does not ship named-but-fake
features, and the autorater needs an async call path the sync controller does
not have today.

``REFUTED`` exists in the verdict vocabulary because the controller's contract
handles it (fail closed with ``EVIDENCE_CONFLICT``), but **no current tier
emits it**: lexical overlap cannot detect contradiction, and MiniCheck scores
support, not refutation. It is reserved for a future entailment tier, and
saying so here beats a verdict that silently can never fire.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.core.config import settings
from app.domain.models import PackedEvidence, QueryRequest
from app.services.normalizer import QueryNormalizer

logger = logging.getLogger(__name__)

# Crude suffix stripper so "expose" matches "exposes" and "depend" matches
# "depends"/"depended". Deliberately not a real stemmer: three suffixes and a
# minimum stem length, because a heavier stemmer buys little at this
# granularity and introduces its own false merges. "es" is deliberately NOT in
# the list — stripping it maps "exposes"→"expos" while "expose" stays intact,
# so the variants stop colliding, which defeats the purpose; stripping bare
# "s" maps both to "expose".
_SUFFIXES = ("ing", "ed", "s")
_MIN_STEM = 3


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM:
            return token[: -len(suffix)]
    return token


class SufficiencyVerdict(StrEnum):
    SUFFICIENT = "sufficient"
    UNCERTAIN = "uncertain"
    INSUFFICIENT = "insufficient"
    # Reserved: producible only by a future entailment tier. See module docstring.
    REFUTED = "refuted"


@dataclass(frozen=True)
class SufficiencyResult:
    """Verdict plus the signals that produced it, for telemetry and replay."""

    verdict: SufficiencyVerdict
    coverage: float
    supporting_ratio: float
    provenance_diversity: int
    tier: int
    signals: list[str] = field(default_factory=list)

    def as_signals(self) -> list[str]:
        return [
            f"sufficiency.{self.verdict.value}",
            f"sufficiency.tier{self.tier}",
            f"sufficiency.coverage_{self.coverage:.2f}",
            *self.signals,
        ]


class SupportScorer(Protocol):
    """Tier-1 per-item support scorer.

    Returns one probability in [0, 1] per evidence item that the item supports
    the query's information need. Implementations must be safe to call from a
    request path (bounded latency) — the gate handles exceptions, but not
    hangs.
    """

    def score(self, query: str, items: list[str]) -> list[float]: ...


class MiniCheckScorer:
    """MiniCheck-FT5 adapter (``verify`` extra). Lazily loaded.

    MiniCheck grounds a *claim* in a *document*; the query is used as the claim
    proxy, which is a weaker signal than checking a drafted answer and is why
    Tier 1 only refines UNCERTAIN rather than overriding Tier 0.
    """

    def __init__(self, model_name: str = "ft5") -> None:
        self._model_name = model_name
        self._scorer: Any = None

    def score(self, query: str, items: list[str]) -> list[float]:
        if self._scorer is None:
            from minicheck.minicheck import MiniCheck  # type: ignore[import-not-found]

            self._scorer = MiniCheck(model_name=self._model_name)
        _, probs, _, _ = self._scorer.score(docs=items, claims=[query] * len(items))
        return [float(p) for p in probs]


class SufficiencyGate:
    """Tiered, set-level sufficiency check. Pure function of (request, packed)."""

    def __init__(
        self,
        normalizer: QueryNormalizer | None = None,
        support_scorer: SupportScorer | None = None,
        *,
        min_coverage: float | None = None,
        min_supporting_ratio: float | None = None,
        tier1_support_threshold: float | None = None,
    ) -> None:
        self.normalizer = normalizer or QueryNormalizer()
        self._scorer = support_scorer
        self._scorer_failed = False
        self.min_coverage = (
            settings.sufficiency_min_coverage if min_coverage is None else min_coverage
        )
        self.min_supporting_ratio = (
            settings.sufficiency_min_supporting_ratio
            if min_supporting_ratio is None
            else min_supporting_ratio
        )
        self.tier1_support_threshold = (
            settings.sufficiency_tier1_threshold
            if tier1_support_threshold is None
            else tier1_support_threshold
        )

    def check(self, request: QueryRequest, packed: PackedEvidence) -> SufficiencyResult:
        keywords = self.normalizer.keywords(request.query)
        items = packed.evidence_items

        if not items:
            return SufficiencyResult(
                verdict=SufficiencyVerdict.INSUFFICIENT,
                coverage=0.0,
                supporting_ratio=0.0,
                provenance_diversity=0,
                tier=0,
                signals=["sufficiency.empty_set"],
            )
        if not keywords:
            # A query with no content terms ("tell me more") cannot be checked
            # lexically; that is uncertainty, not a verdict either way.
            return SufficiencyResult(
                verdict=SufficiencyVerdict.UNCERTAIN,
                coverage=0.0,
                supporting_ratio=0.0,
                provenance_diversity=self._provenance_diversity(packed),
                tier=0,
                signals=["sufficiency.no_query_terms"],
            )

        # --- Tier 0: set-level lexical signals ---
        # Coverage is computed over the UNION of the set's tokens, which is
        # what makes it set-level: a term satisfied by any item counts, so
        # multi-item answers are not penalized for splitting facts across
        # items. Matching is token-exact, not substring — substring matching is
        # the bug class where "port" matches "support" and "current" matches
        # "concurrent", already fixed once in the router.
        item_tokens = [
            {_stem(t) for t in self.normalizer.normalize(item).split()} for item in items
        ]
        set_tokens: set[str] = set().union(*item_tokens)
        stemmed_keywords = [_stem(kw) for kw in keywords]
        covered = [kw for kw in stemmed_keywords if kw in set_tokens]
        coverage = len(covered) / len(stemmed_keywords)
        supporting = sum(
            1 for tokens in item_tokens if any(kw in tokens for kw in stemmed_keywords)
        )
        supporting_ratio = supporting / len(items)
        diversity = self._provenance_diversity(packed)

        tier0_signals: list[str] = []
        missing = [kw for kw in stemmed_keywords if kw not in covered]
        if missing:
            tier0_signals.append(f"sufficiency.uncovered_terms_{len(missing)}")

        if coverage >= self.min_coverage and supporting_ratio >= self.min_supporting_ratio:
            return SufficiencyResult(
                verdict=SufficiencyVerdict.SUFFICIENT,
                coverage=coverage,
                supporting_ratio=supporting_ratio,
                provenance_diversity=diversity,
                tier=0,
                signals=tier0_signals,
            )
        # Coverage near zero means the set is about something else entirely —
        # the case the count-based heuristic could never see.
        if coverage < self.min_coverage / 2:
            return SufficiencyResult(
                verdict=SufficiencyVerdict.INSUFFICIENT,
                coverage=coverage,
                supporting_ratio=supporting_ratio,
                provenance_diversity=diversity,
                tier=0,
                signals=[*tier0_signals, "sufficiency.off_topic_set"],
            )

        # --- Tier 1: pluggable support scorer, only on the uncertain band ---
        tier1 = self._tier1(request.query, items)
        if tier1 is not None:
            mean_support, min_support = tier1
            tier1_signals = [
                *tier0_signals,
                f"sufficiency.tier1_mean_{mean_support:.2f}",
                f"sufficiency.tier1_min_{min_support:.2f}",
            ]
            verdict = (
                SufficiencyVerdict.SUFFICIENT
                if mean_support >= self.tier1_support_threshold
                else SufficiencyVerdict.INSUFFICIENT
            )
            return SufficiencyResult(
                verdict=verdict,
                coverage=coverage,
                supporting_ratio=supporting_ratio,
                provenance_diversity=diversity,
                tier=1,
                signals=tier1_signals,
            )

        return SufficiencyResult(
            verdict=SufficiencyVerdict.UNCERTAIN,
            coverage=coverage,
            supporting_ratio=supporting_ratio,
            provenance_diversity=diversity,
            tier=0,
            signals=tier0_signals,
        )

    def _tier1(self, query: str, items: list[str]) -> tuple[float, float] | None:
        """Return (mean, min) support, or None when no scorer is available.

        Failures latch: a scorer that raised once (missing extra, model load
        failure) is not retried per query. The verdict stays UNCERTAIN — this
        tier is an enhancement, and the existing count/token floor still runs.
        """
        if self._scorer is None or self._scorer_failed:
            return None
        try:
            probs = self._scorer.score(query, items)
            if not probs:
                return None
            return sum(probs) / len(probs), min(probs)
        except Exception as exc:  # noqa: BLE001 - enhancement, never a validation
            self._scorer_failed = True
            logger.warning(
                "sufficiency_tier1_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None

    @staticmethod
    def _provenance_diversity(packed: PackedEvidence) -> int:
        seen = set()
        for entry in packed.provenance:
            seen.add(str(sorted(entry.items())))
        return len(seen) if seen else (1 if packed.evidence_items else 0)


def build_gate(normalizer: QueryNormalizer | None = None) -> SufficiencyGate:
    """Construct the configured gate. Tier 1 attaches only when enabled AND
    importable; a missing ``verify`` extra silently yields a Tier-0-only gate."""
    scorer: SupportScorer | None = None
    if settings.sufficiency_tier1_enabled:
        try:
            import minicheck  # type: ignore[import-not-found]  # noqa: F401

            scorer = MiniCheckScorer()
        except ImportError:
            logger.warning(
                "sufficiency_tier1_unavailable",
                extra={"hint": "install the `verify` extra: uv sync --extra verify"},
            )
    return SufficiencyGate(normalizer=normalizer, support_scorer=scorer)
