"""Tests for NodeSet scope derivation (Phase C1)."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import QueryRequest, SearchMode
from app.services.router import QueryRouter
from app.services.scoping import NodeSetIndex, load_index

INDEX = {
    "billing": ["billing service", "invoices"],
    "warehouse": ["data warehouse", "ingest"],
    "auth": ["auth gateway", "identity provider"],
}


def _router() -> QueryRouter:
    return QueryRouter(node_set_index=NodeSetIndex(INDEX))


# --- index matching ----------------------------------------------------------


def test_single_entity_derives_its_nodeset() -> None:
    matches = NodeSetIndex(INDEX).derive("What port does the billing service expose?")
    # The nodeset's own name is tried first, so it is the credited alias here.
    assert matches == [("billing", "billing")]


def test_matched_alias_is_deterministic() -> None:
    """The credited alias must not depend on set iteration order — telemetry
    that varies per process cannot be replayed."""
    results = {tuple(NodeSetIndex(INDEX).derive("billing service port")) for _ in range(20)}
    assert len(results) == 1


def test_multiple_entities_derive_multiple_nodesets() -> None:
    matched = dict(NodeSetIndex(INDEX).derive("How is billing connected to the data warehouse?"))
    assert set(matched) == {"billing", "warehouse"}


def test_multi_word_alias_requires_all_its_tokens() -> None:
    """"data warehouse" must not fire on a query that only says "data"."""
    assert NodeSetIndex(INDEX).derive("What's going on with data?") == []


def test_matching_is_stemmed_not_substring() -> None:
    """"invoices" matches "invoice"; "auth" must not fire inside "author"."""
    index = NodeSetIndex(INDEX)
    assert dict(index.derive("where is the invoice stored")).get("billing") == "invoices"
    assert index.derive("who is the author of this doc") == []


def test_nodeset_name_is_an_implicit_alias() -> None:
    assert dict(NodeSetIndex(INDEX).derive("is auth down")).get("auth") == "auth"


# --- router integration ------------------------------------------------------


def test_router_derives_scope_when_caller_sent_none() -> None:
    decision = _router().route(QueryRequest(query="What port does the billing service expose?"))
    assert decision.node_sets == ["billing"]
    assert "scope.entity_match:billing" in decision.signals


def test_caller_supplied_scope_always_wins() -> None:
    """The caller's contract is never widened or second-guessed."""
    decision = _router().route(
        QueryRequest(query="What port does the billing service expose?", node_sets=["auth"])
    )
    assert decision.node_sets == ["auth"]
    assert "scope.caller_supplied" in decision.signals
    assert not any(s.startswith("scope.entity_match") for s in decision.signals)


def test_no_index_means_no_derivation() -> None:
    decision = QueryRouter(node_set_index=None).route(
        QueryRequest(query="What port does the billing service expose?")
    )
    # load_index() with no configured path returns None, so scope stays empty.
    assert decision.node_sets == []


def test_derivation_is_deterministic_for_cache_correctness() -> None:
    """Derived scope is a pure function of the query — identical queries derive
    identical scope, which is what keeps the cache correct without putting the
    derived scope into the key."""
    router = _router()
    q = QueryRequest(query="How is billing connected to the data warehouse?")
    a = router.route(q).node_sets
    b = router.route(QueryRequest(query=q.query)).node_sets
    assert a == b


def test_scope_applies_on_the_freshness_override_path_too() -> None:
    decision = _router().route(
        QueryRequest(query="latest billing incident", freshness_required=True)
    )
    assert decision.mode is SearchMode.TEMPORAL
    assert decision.node_sets == ["billing"]


# --- index loading -----------------------------------------------------------


def test_load_index_from_file(tmp_path: Path) -> None:
    path = tmp_path / "nodesets.json"
    path.write_text(json.dumps(INDEX), encoding="utf-8")
    index = load_index(path)
    assert index is not None
    assert index.size == 3


def test_malformed_index_is_logged_not_raised(tmp_path: Path) -> None:
    """Scope derivation is an enhancement; a broken index file must not take
    down routing."""
    path = tmp_path / "nodesets.json"
    path.write_text("this is not json", encoding="utf-8")
    assert load_index(path) is None


def test_unconfigured_index_is_none() -> None:
    assert load_index(None) is None
