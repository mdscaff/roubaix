"""NodeSet scope derivation by lexical entity anchoring.

`CLAUDE.md` calls NodeSet scoping a first-class cost lever, but until now the
scope was caller-supplied only: no caller scope, no scoping, and the cheapest
cost lever in the system sat inert on every organic query.

This is Phase C1 of docs/implementation-plan.md. The validated mechanism
(SubgraphRAG, ICLR 2025) is: anchor on entities linked from the query to prune
the graph space *before* retrieval, with no LLM in the retrieval loop. The
first stage is deliberately lexical — published routing work finds surface
keyword matching beats dense embeddings for query scoping, and a lexical index
is deterministic, explainable, and free. The learned triple scorer that
SubgraphRAG layers on top is Phase C2, deferred until live telemetry can mine
its weak-supervision labels (a condition recorded in the plan).

Ground rules:

- **A caller-supplied scope always wins.** Derivation runs only when the
  request carries no scope; the caller's contract is never widened or
  second-guessed.
- **Matching is stemmed and token-exact**, the same discipline as the router
  and the sufficiency gate — substring matching is the "port" ∈ "support" bug
  class, three times avoided now.
- **No index, no derivation.** The index loads from a JSON file
  (`ROUBAIX_NODESET_INDEX_PATH`, shape: {"nodeset": ["alias", ...]}). Absent
  or malformed, the service behaves exactly as before; a scoping index is an
  enhancement.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.config import settings
from app.services.normalizer import QueryNormalizer
from app.services.sufficiency import _stem

logger = logging.getLogger(__name__)


class NodeSetIndex:
    """Maps NodeSet names to query-matchable aliases.

    An alias may be multi-word ("data warehouse"); it matches when all of its
    stemmed tokens appear in the query's stemmed keyword set. The NodeSet's own
    name is always an implicit alias.
    """

    def __init__(
        self,
        index: dict[str, list[str]],
        normalizer: QueryNormalizer | None = None,
    ) -> None:
        self.normalizer = normalizer or QueryNormalizer()
        # Precompute stemmed token sets per (nodeset, alias).
        self._aliases: list[tuple[str, str, frozenset[str]]] = []
        for nodeset, aliases in index.items():
            # Deterministic order: the nodeset's own name first, then aliases
            # as declared. A set literal here made the matched-alias signal
            # vary per process (set iteration order), which a flaky test
            # caught — nondeterministic telemetry is unreplayable telemetry.
            ordered: list[str] = []
            for candidate in (nodeset, *aliases):
                if candidate not in ordered:
                    ordered.append(candidate)
            for alias in ordered:
                tokens = frozenset(_stem(t) for t in self.normalizer.normalize(alias).split() if t)
                if tokens:
                    self._aliases.append((nodeset, alias, tokens))

    def derive(self, query: str) -> list[tuple[str, str]]:
        """Return [(nodeset, matched_alias)] for *query*, deduplicated by
        nodeset, in first-match order."""
        query_tokens = {_stem(t) for t in self.normalizer.normalize(query).split()}
        matched: dict[str, str] = {}
        for nodeset, alias, alias_tokens in self._aliases:
            if nodeset in matched:
                continue
            if alias_tokens <= query_tokens:
                matched[nodeset] = alias
        return list(matched.items())

    @property
    def size(self) -> int:
        return len({nodeset for nodeset, _, _ in self._aliases})


def load_index(path: str | Path | None = None) -> NodeSetIndex | None:
    """Load the configured index, or None when unconfigured or unreadable.

    Unreadable is logged, not raised: scope derivation is an enhancement, and a
    malformed index file must not take down routing.
    """
    raw_path = path if path is not None else settings.nodeset_index_path
    if not raw_path:
        return None
    try:
        data = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("index root must be an object of {nodeset: [aliases]}")
        index = {
            str(name): [str(a) for a in aliases]
            for name, aliases in data.items()
            if isinstance(aliases, list)
        }
        return NodeSetIndex(index)
    except Exception as exc:  # noqa: BLE001 - enhancement, never a validation
        logger.warning(
            "nodeset_index_unreadable",
            extra={"path": str(raw_path), "error": str(exc)},
        )
        return None
