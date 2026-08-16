"""Tier 0: the resident in-memory graph.

Roubaix's problem statement, restated (2026-08-16): **fast and cheapest is the
product.** The fastest possible answer is a traversal of a graph already in
process memory — no LLM, no network, no disk, zero tokens, microseconds. This
module is that tier.

Contract with the rest of the pipeline:

- **Fail-open, never fail-closed.** Tier 0 answers only when it is sure: the
  query matches a structural pattern AND its entities resolve unambiguously to
  resident nodes. Anything else falls through to the normal pipeline. A fast
  path that guesses is a wrong-answer generator with good latency.
- **Every answer cites its edges.** A Tier-0 answer is grounded *by
  construction* — the evidence is the edge list itself, and each edge carries
  the provenance it was promoted with.
- **The graph learns from the slow path.** `promote()` ingests triplets from
  accepted, non-degraded answers, so a query that had to pay for retrieval
  teaches the graph, and the next query in that neighborhood answers here.
  Degraded evidence is never promoted — fabricated edges would make the
  fastest tier the least trustworthy one.
- **Bounded.** Node count is capped; when full, promotion evicts the least
  recently *used* node (reads refresh recency). An unbounded cache with a
  graph API is still an unbounded cache.

Concurrency note: all state lives behind synchronous methods called from a
single asyncio event loop; there is no cross-thread access and therefore no
locking. If a thread pool ever touches this, add a lock first.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.services.normalizer import QueryNormalizer
from app.services.sufficiency import _stem


def _canon(name: str) -> str:
    """Canonical node key: normalized, stemmed tokens, order preserved."""
    normalizer = QueryNormalizer()
    return " ".join(_stem(t) for t in normalizer.normalize(name).split())


@dataclass(frozen=True)
class Edge:
    subject: str
    predicate: str
    object: str
    provenance: str = ""

    def as_text(self) -> str:
        return f"{self.subject} {self.predicate} {self.object}"


@dataclass
class _Node:
    label: str
    out_edges: list[Edge] = field(default_factory=list)
    in_edges: list[Edge] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)


class InMemoryGraph:
    """Adjacency-indexed triple store resident in process memory."""

    def __init__(self, max_nodes: int = 50_000) -> None:
        self._max_nodes = max_nodes
        self._nodes: OrderedDict[str, _Node] = OrderedDict()
        self.promoted_edges = 0

    # --- writes --------------------------------------------------------------

    def add_edge(self, subject: str, predicate: str, obj: str, provenance: str = "") -> bool:
        """Insert one edge. Returns False for junk input; dedups exact edges."""
        subject, predicate, obj = subject.strip(), predicate.strip(), obj.strip()
        if not subject or not predicate or not obj or subject == obj:
            return False
        edge = Edge(subject=subject, predicate=predicate, object=obj, provenance=provenance)
        s_node = self._node(subject)
        if any(
            e.predicate == edge.predicate and _canon(e.object) == _canon(obj)
            for e in s_node.out_edges
        ):
            return False  # already known
        o_node = self._node(obj)
        s_node.out_edges.append(edge)
        o_node.in_edges.append(edge)
        self.promoted_edges += 1
        self._evict()
        return True

    def promote(self, triplet_texts: list[str], provenance: str) -> int:
        """Ingest "subject predicate object" lines from packed evidence.

        Only 3+-token lines split cleanly; anything else is skipped — a
        promotion that guesses at structure would poison the fastest tier.
        Returns the number of edges actually added.
        """
        added = 0
        for text in triplet_texts:
            parts = text.split()
            if len(parts) < 3:
                continue
            # First token(s) = subject, last = object, middle = predicate is
            # ambiguous for multi-word entities; the packer emits triplets as
            # "subject predicate object" from structured dicts, so the common
            # case is exactly three fields. Longer lines take first/last word
            # groups only when the middle is a single token — else skip.
            if len(parts) == 3:
                added += self.add_edge(parts[0], parts[1], parts[2], provenance)
        return added

    # --- reads (each refreshes recency) --------------------------------------

    def resolve(self, phrase: str) -> str | None:
        """Resolve a phrase to a resident node label, or None.

        Exact canonical match only. Substring/fuzzy resolution is deliberately
        absent: an ambiguous resolution on the fast path is a wrong answer.
        """
        node = self._nodes.get(_canon(phrase))
        if node is None:
            return None
        self._touch(_canon(phrase))
        return node.label

    def edges_between(self, a: str, b: str) -> list[Edge]:
        """Direct edges a→b and b→a."""
        ka, kb = _canon(a), _canon(b)
        node = self._nodes.get(ka)
        if node is None or kb not in self._nodes:
            return []
        self._touch(ka)
        self._touch(kb)
        forward = [e for e in node.out_edges if _canon(e.object) == kb]
        backward = [e for e in self._nodes[kb].out_edges if _canon(e.object) == ka]
        return forward + backward

    def neighbors_out(self, a: str, predicate: str | None = None) -> list[Edge]:
        node = self._nodes.get(_canon(a))
        if node is None:
            return []
        self._touch(_canon(a))
        if predicate is None:
            return list(node.out_edges)
        p = _canon(predicate)
        return [e for e in node.out_edges if _canon(e.predicate) == p]

    def neighbors_in(self, a: str, predicate: str | None = None) -> list[Edge]:
        node = self._nodes.get(_canon(a))
        if node is None:
            return []
        self._touch(_canon(a))
        if predicate is None:
            return list(node.in_edges)
        p = _canon(predicate)
        return [e for e in node.in_edges if _canon(e.predicate) == p]

    def path(self, a: str, b: str, max_hops: int = 4) -> list[Edge] | None:
        """Shortest directed-or-reverse path a↝b by BFS, or None."""
        ka, kb = _canon(a), _canon(b)
        if ka not in self._nodes or kb not in self._nodes:
            return None
        self._touch(ka)
        self._touch(kb)
        frontier: list[tuple[str, list[Edge]]] = [(ka, [])]
        seen = {ka}
        for _ in range(max_hops):
            next_frontier: list[tuple[str, list[Edge]]] = []
            for key, trail in frontier:
                node = self._nodes[key]
                for edge in node.out_edges + node.in_edges:
                    other = (
                        _canon(edge.object) if _canon(edge.subject) == key else _canon(edge.subject)
                    )
                    if other in seen:
                        continue
                    new_trail = [*trail, edge]
                    if other == kb:
                        return new_trail
                    seen.add(other)
                    next_frontier.append((other, new_trail))
            frontier = next_frontier
            if not frontier:
                return None
        return None

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(n.out_edges) for n in self._nodes.values())

    # --- internals -----------------------------------------------------------

    def _node(self, label: str) -> _Node:
        key = _canon(label)
        node = self._nodes.get(key)
        if node is None:
            node = _Node(label=label)
            self._nodes[key] = node
        self._touch(key)
        return node

    def _touch(self, key: str) -> None:
        node = self._nodes.get(key)
        if node is not None:
            node.last_used = time.monotonic()
            self._nodes.move_to_end(key)

    def _evict(self) -> None:
        while len(self._nodes) > self._max_nodes:
            evicted_key, evicted = self._nodes.popitem(last=False)
            # Remove dangling references to the evicted node from neighbors.
            for edge in evicted.out_edges:
                target = self._nodes.get(_canon(edge.object))
                if target:
                    target.in_edges = [
                        e for e in target.in_edges if _canon(e.subject) != evicted_key
                    ]
            for edge in evicted.in_edges:
                source = self._nodes.get(_canon(edge.subject))
                if source:
                    source.out_edges = [
                        e for e in source.out_edges if _canon(e.object) != evicted_key
                    ]


def build_graph() -> InMemoryGraph | None:
    """Construct the configured resident graph, or None when disabled.

    Warm-loads from ``ROUBAIX_MEMGRAPH_SEED_PATH`` (JSON list of
    ``[subject, predicate, object]``) when set; a malformed seed file is
    logged and skipped — the graph still starts empty and learns from
    promotion. An enhancement, never a validation.
    """
    import json
    import logging
    from pathlib import Path

    from app.core.config import settings

    if not settings.memgraph_enabled:
        return None
    graph = InMemoryGraph(max_nodes=settings.memgraph_max_nodes)
    if settings.memgraph_seed_path:
        try:
            rows = json.loads(Path(settings.memgraph_seed_path).read_text(encoding="utf-8"))
            loaded = sum(
                graph.add_edge(str(r[0]), str(r[1]), str(r[2]), provenance="seed")
                for r in rows
                if isinstance(r, (list, tuple)) and len(r) >= 3
            )
            logging.getLogger(__name__).info(
                "memgraph_seeded", extra={"edges": loaded, "path": settings.memgraph_seed_path}
            )
        except Exception as exc:  # noqa: BLE001 - enhancement, never a validation
            logging.getLogger(__name__).warning(
                "memgraph_seed_unreadable",
                extra={"path": settings.memgraph_seed_path, "error": str(exc)},
            )
    return graph


class GraphDataEngine(Protocol):
    """The slice of Cognee's graph adapters the warm-load depends on."""

    async def get_graph_data(
        self,
    ) -> tuple[list[tuple[Any, dict[str, Any]]], list[tuple[Any, Any, Any, dict[str, Any]]]]: ...


async def warm_load_from_cognee(graph: InMemoryGraph, engine: GraphDataEngine | None = None) -> int:
    """Load the Cognee graph store into the resident graph at startup.

    Uses the adapter-agnostic ``get_graph_data()`` interface, so it works over
    turso/pgGraph/neo4j alike. Node labels come from each node's ``name``
    property (falling back to ``id``); edges map (source, relationship,
    target). Everything flows through ``add_edge``, so canonicalization,
    dedup, junk rejection, and the LRU bound apply to warm-loaded edges
    exactly as to promoted ones.

    An enhancement, never a validation: any failure — cognee not installed,
    not configured, an empty store — logs and returns 0, and the graph still
    learns from promotion at runtime. *engine* is injectable for tests.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        if engine is None:
            from cognee.infrastructure.databases.graph.get_graph_engine import (  # type: ignore[import-not-found]
                get_graph_engine,
            )

            engine = await get_graph_engine()
        nodes, edges = await engine.get_graph_data()
    except Exception as exc:  # noqa: BLE001 - enhancement, never a validation
        logger.info(
            "memgraph_warm_load_skipped",
            extra={"reason": f"{type(exc).__name__}: {exc}"},
        )
        return 0

    names: dict[str, str] = {}
    for node_id, props in nodes:
        node_props = props or {}
        names[str(node_id)] = str(node_props.get("name") or node_id)

    loaded = 0
    for source_id, target_id, relationship, _props in edges:
        loaded += graph.add_edge(
            names.get(str(source_id), str(source_id)),
            str(relationship),
            names.get(str(target_id), str(target_id)),
            provenance="cognee:warm_load",
        )
    logger.info(
        "memgraph_warm_loaded",
        extra={"edges": loaded, "store_nodes": len(nodes), "store_edges": len(edges)},
    )
    return loaded
