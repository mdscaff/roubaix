"""Deterministic scored router.

The router maps a query to the cheapest retrieval mode that can plausibly
answer it. It is a scored rule engine rather than an if/elif ladder for three
reasons:

1. Competing signals are common ("match all services *connected to* the billing
   node" is both structural and relational). A ladder resolves those by author
   ordering; a score resolves them by evidence weight.
2. Every decision carries the named signals that produced it, so routing can be
   explained and replayed from telemetry instead of re-argued.
3. Scores and confidence are the handoff surface for a learned second stage:
   when nothing clears MIN_SCORE, or the win is not confident, an optimized
   module can take the decision without the cheap path ever paying for an LLM
   call. (GEPA reflects on instruction *text*, not on numeric weights — these
   weights are hand-tuned and measured, not optimizer output.)

Patterns are matched against the *order-preserving* normalized query. Matching
against the sorted keyword bag (the previous behaviour) makes every multi-word
phrase unmatchable and silently collapses inverted relationships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.models import QueryRequest, RouteDecision, SearchMode
from app.services.normalizer import QueryNormalizer

# Relative retrieval cost, cheapest first. Used to break score ties toward the
# cheaper mode — the project's core thesis is "cheapest valid mode wins".
COST_RANK: dict[SearchMode, int] = {
    SearchMode.CHUNKS: 0,
    SearchMode.RAG_COMPLETION: 1,
    SearchMode.TRIPLET_COMPLETION: 2,
    SearchMode.GRAPH_COMPLETION: 3,
    SearchMode.GRAPH_SUMMARY_COMPLETION: 4,
    SearchMode.CYPHER: 5,
    SearchMode.NATURAL_LANGUAGE: 5,
    SearchMode.TEMPORAL: 6,
}


# Negation flips the meaning of a signal without removing its keywords:
# "services NOT connected to billing" is not a relationship lookup, and
# "nothing changed today" is not a freshness question. A signal preceded by a
# negation inside this character window is discarded rather than scored.
_NEGATION = re.compile(r"\b(not|no|never|without|lack|lacks|lacking|except|excluding|besides)\b")
_NEGATION_WINDOW = 24


@dataclass(frozen=True)
class Signal:
    """A named regex whose match contributes *weight* to a mode's score."""

    name: str
    pattern: re.Pattern[str]
    weight: float

    @classmethod
    def of(cls, name: str, pattern: str, weight: float = 1.0) -> Signal:
        return cls(name=name, pattern=re.compile(pattern), weight=weight)

    def fires(self, text: str) -> bool:
        """True when this signal matches *text* and is not negated."""
        match = self.pattern.search(text)
        if match is None:
            return False
        window = text[max(0, match.start() - _NEGATION_WINDOW) : match.start()]
        return _NEGATION.search(window) is None


@dataclass(frozen=True)
class ModeRule:
    """Scoring rule for a single retrieval mode."""

    mode: SearchMode
    evidence_budget: int
    rationale: str
    signals: tuple[Signal, ...] = field(default_factory=tuple)
    requires_freshness_validation: bool = False


# Signals use word boundaries throughout. Bare substring matching produces
# false positives that are expensive here: "concurrent" contains "current",
# which routed capacity questions to TEMPORAL and gave them a 120s cache TTL.
RULES: tuple[ModeRule, ...] = (
    ModeRule(
        mode=SearchMode.TEMPORAL,
        evidence_budget=6,
        rationale="freshness-sensitive query",
        requires_freshness_validation=True,
        signals=(
            Signal.of("temporal.deictic", r"\b(today|yesterday|now|currently|current)\b", 2.0),
            Signal.of(
                "temporal.recency", r"\b(latest|newest|recent|recently|up[- ]to[- ]date)\b", 2.0
            ),
            Signal.of(
                "temporal.window", r"\b(this|last|past)\s+(week|month|quarter|year|day)\b", 2.0
            ),
            Signal.of("temporal.change", r"\b(changed|change[ds]?|updated|as of|since)\b", 1.0),
            Signal.of("temporal.status", r"\b(status|incident|outage|rollout)\b", 0.5),
        ),
    ),
    ModeRule(
        mode=SearchMode.CYPHER,
        evidence_budget=5,
        rationale="structural graph query",
        signals=(
            Signal.of("structural.explicit", r"\b(cypher|graph query|query the graph)\b", 3.0),
            Signal.of("structural.match", r"\bmatch (all|every|any|nodes?|the)\b", 3.0),
            Signal.of(
                "structural.topology", r"\b(edges?|degree|subgraph|traversals?|adjacen\w+)\b", 2.0
            ),
            Signal.of(
                "structural.count",
                r"\b(how many|count|list all)\b.*\b(nodes?|edges?|services?)\b",
                1.5,
            ),
        ),
    ),
    ModeRule(
        mode=SearchMode.GRAPH_COMPLETION,
        evidence_budget=10,
        rationale="multi-hop / impact query",
        signals=(
            Signal.of(
                "multihop.direction",
                r"\b(downstream|upstream|transitive\w*|end[- ]to[- ]end)\b",
                3.0,
            ),
            Signal.of(
                "multihop.impact",
                r"\b(affect\w*|impact\w*|blast radius|what breaks|knock[- ]on)\b",
                2.0,
            ),
            Signal.of("multihop.chain", r"\b(chain|path|trace|propagat\w+)\b", 2.0),
            Signal.of(
                "multihop.conditional", r"\bif\b.*\b(fail\w*|down|breaks?|unavailable)\b", 2.0
            ),
        ),
    ),
    ModeRule(
        mode=SearchMode.TRIPLET_COMPLETION,
        evidence_budget=8,
        rationale="relationship-heavy query",
        signals=(
            Signal.of("relation.noun", r"\b(relationship|association|linkage)\b", 3.0),
            Signal.of(
                "relation.verb",
                r"\b(relate[sd]?|connect(s|ed|ion)?|link(s|ed)?|depend(s|ent)? on)\b",
                2.0,
            ),
            Signal.of("relation.between", r"\bbetween\b.*\band\b", 1.5),
            Signal.of("relation.owner", r"\b(owns?|owned by|belongs to|reports to)\b", 1.5),
        ),
    ),
    ModeRule(
        mode=SearchMode.GRAPH_SUMMARY_COMPLETION,
        evidence_budget=10,
        rationale="broad explanatory query",
        signals=(
            Signal.of("summary.explicit", r"\b(summar(y|ise|ize|izing)|overview|recap)\b", 3.0),
            Signal.of("summary.themes", r"\b(themes?|landscape|big picture|high[- ]level)\b", 2.5),
            Signal.of(
                "summary.organisation",
                # Kept on one line: splitting a regex across implicit string
                # concatenation is how stray whitespace gets into a pattern.
                r"\bhow (are|is|do|does)\b.*\b(organi[sz]ed|structured|arranged|grouped|laid out)\b",  # noqa: E501
                3.0,
            ),
            Signal.of("summary.explain", r"\bexplain how\b|\bwalk me through\b", 2.0),
        ),
    ),
)

# Fallback when nothing scores: the cheapest mode that can answer a local
# factual lookup.
DEFAULT_RULE = ModeRule(
    mode=SearchMode.CHUNKS,
    evidence_budget=8,
    rationale="default low-cost retrieval",
)

# A mode must clear this score to beat the cheap default. Single weak signals
# (a bare "status", say) should not buy graph depth.
MIN_SCORE = 2.0

# A win is confident when it beats the runner-up by this factor. A query that
# scores 3.0 TEMPORAL against 2.5 TRIPLET_COMPLETION was not really classified,
# it was broken by rounding — and that is worth recording rather than hiding.
# Downstream, low confidence is what an escalation policy (and later a learned
# router) should key on.
CONFIDENCE_MARGIN = 2.0


class QueryRouter:
    """Deterministic baseline router before DSPy optimization."""

    def __init__(
        self,
        normalizer: QueryNormalizer | None = None,
        rules: tuple[ModeRule, ...] = RULES,
    ) -> None:
        self.normalizer = normalizer or QueryNormalizer()
        self.rules = rules

    def route(self, request: QueryRequest) -> RouteDecision:
        # Use the pre-normalized form if the orchestrator already computed it.
        # Either way this is the order-preserving form, which is what the
        # phrase patterns above are written against.
        q = request.normalized_query or self.normalizer.normalize(request.query)

        scores: dict[str, float] = {}
        fired: dict[SearchMode, list[str]] = {}
        for rule in self.rules:
            matched = [s for s in rule.signals if s.fires(q)]
            if not matched:
                continue
            scores[rule.mode.value] = sum(s.weight for s in matched)
            fired[rule.mode] = [s.name for s in matched]

        # An explicit freshness contract from the caller overrides scoring:
        # the caller is asserting a requirement, not offering a hint.
        if request.freshness_required:
            return self._decision(
                self._rule_for(SearchMode.TEMPORAL),
                request,
                signals=["caller.freshness_required", *fired.get(SearchMode.TEMPORAL, [])],
                scores=scores,
                confident=True,  # an explicit caller contract, not a guess
            )

        winner = self._select(scores)
        if winner is None:
            # Nothing cleared the bar. The cheap default is the right answer,
            # but it is a fallback rather than a classification, so it is not
            # reported as confident.
            return self._decision(DEFAULT_RULE, request, signals=[], scores=scores, confident=False)
        return self._decision(
            self._rule_for(winner),
            request,
            signals=fired.get(winner, []),
            scores=scores,
            confident=self._is_confident(scores, winner),
        )

    def _select(self, scores: dict[str, float]) -> SearchMode | None:
        """Highest score wins; ties break toward the cheaper mode."""
        eligible = [(SearchMode(m), s) for m, s in scores.items() if s >= MIN_SCORE]
        if not eligible:
            return None
        return max(eligible, key=lambda pair: (pair[1], -COST_RANK[pair[0]]))[0]

    @staticmethod
    def _is_confident(scores: dict[str, float], winner: SearchMode) -> bool:
        """True when the winner beat every other mode by CONFIDENCE_MARGIN."""
        others = [s for mode, s in scores.items() if mode != winner.value]
        if not others:
            return True
        return scores[winner.value] >= CONFIDENCE_MARGIN * max(*others, 1.0)

    def _rule_for(self, mode: SearchMode) -> ModeRule:
        for rule in self.rules:
            if rule.mode == mode:
                return rule
        return DEFAULT_RULE

    @staticmethod
    def _decision(
        rule: ModeRule,
        request: QueryRequest,
        *,
        signals: list[str],
        scores: dict[str, float],
        confident: bool = True,
    ) -> RouteDecision:
        return RouteDecision(
            mode=rule.mode,
            # NodeSet scope is caller-supplied for now. Learned scoping is the
            # first DSPy target — see docs/implementation-plan.md.
            node_sets=list(request.node_sets),
            evidence_budget=rule.evidence_budget,
            requires_freshness_validation=(
                rule.requires_freshness_validation or request.freshness_required
            ),
            rationale=rule.rationale,
            signals=signals,
            scores=scores,
            confident=confident,
        )


class ForcedModeRouter(QueryRouter):
    """Fixed SearchMode router for monolithic eval baselines."""

    def __init__(self, mode: SearchMode, evidence_budget: int = 8) -> None:
        super().__init__()
        self._forced_mode = mode
        self._evidence_budget = evidence_budget

    def route(self, request: QueryRequest) -> RouteDecision:
        return RouteDecision(
            mode=self._forced_mode,
            node_sets=list(request.node_sets),
            evidence_budget=self._evidence_budget,
            requires_freshness_validation=request.freshness_required,
            rationale=f"forced baseline mode: {self._forced_mode.value}",
            signals=["baseline.forced"],
        )
