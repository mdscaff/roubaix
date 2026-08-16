"""Deterministic answers from the resident graph (Tier 0's front door).

Turns a natural-language query into a graph operation — edge check, neighbor
listing, path search — and a templated answer, with the matched edges as the
evidence. No LLM anywhere: zero tokens, zero cost, microseconds.

The matcher is deliberately conservative. It answers only when:

1. the query matches one of a small set of structural patterns, AND
2. every entity phrase resolves to a resident node exactly (canonical match).

Everything else returns None and the normal pipeline runs. The fast path's
failure mode must be "fell through", never "guessed" — a Tier 0 that guesses
is a wrong-answer generator with excellent latency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.memgraph import Edge, InMemoryGraph

# Relation vocabulary shared by the patterns below. Built with an explicit
# join rather than one long literal: a regex too wide to read is a regex too
# wide to review.
_RELATIONS = "|".join(
    (
        r"depend(?:s|ent)? on",
        r"connected to",
        r"linked to",
        r"related to",
        r"call(?:s|ing)?",
        r"use[sd]?",
        r"write(?:s)? to",
        r"read(?:s)? from",
        r"own(?:s|ed by)?",
    )
)
_IN_RELATIONS = "|".join((r"call(?:s)?", r"use(?:s)?", r"write(?:s)? to", r"read(?:s)? from"))

# Two-entity structural questions. Group 1 = subject phrase, group 2 = object.
_EDGE_PATTERNS = (
    re.compile(rf"^(?:does|is|do)\s+(.+?)\s+(?:{_RELATIONS})\s+(.+?)$"),
    re.compile(r"^how (?:is|are)\s+(.+?)\s+(?:connected|related|linked)\s+to\s+(.+?)$"),
)

# One-entity neighborhood questions: (pattern, direction) where direction "in"
# asks who points AT the entity and "out" asks what the entity points at.
_NEIGHBOR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:what|which|who)(?:\s+\w+)?\s+depend(?:s)? on\s+(.+?)$"), "in"),
    (
        re.compile(rf"^(?:what|which|who)(?:\s+\w+)?\s+(?:{_IN_RELATIONS})\s+(.+?)$"),
        "in",
    ),
    (re.compile(r"^what does\s+(.+?)\s+depend on$"), "out"),
    (re.compile(r"^what does\s+(.+?)\s+(?:call|use|write to|read from)$"), "out"),
)


@dataclass(frozen=True)
class GraphAnswer:
    answer: str
    edges: list[Edge]
    pattern: str
    signals: list[str] = field(default_factory=list)


class GraphAnswerer:
    def __init__(self, graph: InMemoryGraph) -> None:
        self.graph = graph

    def try_answer(self, normalized_query: str) -> GraphAnswer | None:
        """Answer *normalized_query* from the resident graph, or fall through."""
        if self.graph.node_count == 0:
            return None
        q = normalized_query.strip()

        for pattern in _EDGE_PATTERNS:
            match = pattern.match(q)
            if match:
                return self._answer_two_entity(match.group(1), match.group(2))

        for pattern, direction in _NEIGHBOR_PATTERNS:
            match = pattern.match(q)
            if match:
                return self._answer_neighborhood(match.group(1), direction)

        return None

    # --- two entities: edge, then path ---------------------------------------

    def _answer_two_entity(self, a_phrase: str, b_phrase: str) -> GraphAnswer | None:
        a = self.graph.resolve(a_phrase)
        b = self.graph.resolve(b_phrase)
        if a is None or b is None:
            return None  # unresolved entity → the slow path knows more than we do

        direct = self.graph.edges_between(a, b)
        if direct:
            facts = "; ".join(e.as_text() for e in direct)
            return GraphAnswer(
                answer=f"Yes — {facts}.",
                edges=direct,
                pattern="edge",
                signals=[f"memgraph.edge.{len(direct)}"],
            )

        trail = self.graph.path(a, b)
        if trail:
            chain = " → ".join([trail[0].subject, *(e.object for e in trail)])
            facts = "; ".join(e.as_text() for e in trail)
            return GraphAnswer(
                answer=f"Connected via {len(trail)} hop(s): {chain}. ({facts}.)",
                edges=trail,
                pattern="path",
                signals=[f"memgraph.path.{len(trail)}"],
            )

        # Both entities are resident and no connection exists. That is a real
        # finding, not a miss: the graph can assert absence within itself.
        return GraphAnswer(
            answer=(
                f"No connection found between {a} and {b} in the resident graph "
                f"(both are known; no linking edges within 4 hops)."
            ),
            edges=[],
            pattern="no_path",
            signals=["memgraph.no_path"],
        )

    # --- one entity: neighborhood --------------------------------------------

    def _answer_neighborhood(self, phrase: str, direction: str) -> GraphAnswer | None:
        entity = self.graph.resolve(phrase)
        if entity is None:
            return None
        edges = (
            self.graph.neighbors_in(entity)
            if direction == "in"
            else self.graph.neighbors_out(entity)
        )
        if not edges:
            # Unlike no_path, an empty neighborhood is weak evidence: the graph
            # may simply not have learned this area yet. Fall through.
            return None
        if direction == "in":
            names = sorted({e.subject for e in edges})
            answer = f"{', '.join(names)} → {entity} ({len(edges)} edge(s))."
        else:
            names = sorted({e.object for e in edges})
            answer = f"{entity} → {', '.join(names)} ({len(edges)} edge(s))."
        return GraphAnswer(
            answer=answer,
            edges=list(edges),
            pattern=f"neighbors_{direction}",
            signals=[f"memgraph.neighbors_{direction}.{len(edges)}"],
        )
