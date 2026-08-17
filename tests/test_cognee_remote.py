"""Tests for remote-mode CogneeClient (a self-hosted sidecar or Cognee Cloud).

All fakes, no server: the contract under test is what Roubaix *sends* and how
it degrades, not whether cognee's HTTP layer works. The wiring was separately
verified end to end against a real `cognee.api.client` REST server on
2026-08-17 — a live server is not something CI can assume.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from app.domain.models import SearchMode
from app.integrations.cognee_client import CogneeClient


class FakeRemote:
    """Stands in for cognee's CloudClient."""

    def __init__(self, search_result: Any = None, fail: Exception | None = None) -> None:
        self.search_result = search_result if search_result is not None else ["a chunk"]
        self.fail = fail
        self.search_calls: list[tuple[str, dict]] = []
        self.add_calls: list[tuple[str, dict]] = []
        self.cognify_calls: list[dict] = []
        self.closed = False

    async def search(self, query: str, **kwargs: Any) -> Any:
        if self.fail is not None:
            raise self.fail
        self.search_calls.append((query, kwargs))
        return self.search_result

    async def add(self, data: Any, **kwargs: Any) -> dict:
        self.add_calls.append((data, kwargs))
        return {"ok": True}

    async def cognify(self, **kwargs: Any) -> dict:
        self.cognify_calls.append(kwargs)
        return {"ok": True}

    async def close(self) -> None:
        self.closed = True


def _client(remote: FakeRemote, url: str = "http://sidecar:8000") -> CogneeClient:
    client = CogneeClient(base_url=url, api_key="ck_test")

    async def _remote() -> FakeRemote:
        client._remote_client = remote
        return remote

    client._remote = _remote  # type: ignore[method-assign]
    return client


def test_a_service_url_selects_remote_mode() -> None:
    assert CogneeClient(base_url="http://sidecar:8000")._use_remote() is True
    assert CogneeClient(base_url=None)._use_remote() is False


def test_the_older_base_url_alias_still_selects_remote_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing .env files use COGNEE_BASE_URL; cognee itself reads
    COGNEE_SERVICE_URL. Both must reach the same place or an upgrade silently
    drops back to the embedded SDK against an empty store."""
    from app.core import config

    monkeypatch.setattr(config.settings, "cognee_service_url", None)
    monkeypatch.setattr(config.settings, "cognee_base_url", "http://legacy:8000")
    assert config.settings.cognee_remote_url == "http://legacy:8000"

    monkeypatch.setattr(config.settings, "cognee_service_url", "http://current:8000")
    assert config.settings.cognee_remote_url == "http://current:8000"


needs_cognee = pytest.mark.skipif(
    not importlib.util.find_spec("cognee"),
    reason="search_type mapping needs cognee's SearchType enum (uv sync --extra opt)",
)


@needs_cognee
@pytest.mark.asyncio
async def test_remote_search_sends_mode_dataset_scope_and_budget() -> None:
    remote = FakeRemote(search_result=["billing depends on warehouse"])
    client = _client(remote)

    result = await client.search(
        query="how is billing connected to warehouse",
        mode=SearchMode.TRIPLET_COMPLETION,
        dataset="prod",
        node_sets=["billing", "warehouse"],
        evidence_budget=5,
    )

    assert not result.degraded
    _query, kwargs = remote.search_calls[0]
    assert kwargs["datasets"] == ["prod"]
    assert kwargs["top_k"] == 5
    assert kwargs["node_name"] == ["billing", "warehouse"]
    assert kwargs["only_context"] is True
    assert kwargs["search_type"] is not None
    # The evidence has to arrive as the mode's shape, not raw strings.
    assert result.evidence.triplets


@needs_cognee
@pytest.mark.asyncio
async def test_remote_search_records_the_scope_operator_it_could_not_send() -> None:
    """The remote search endpoint has no node_name_filter_operator parameter,
    so the explicit OR used in embedded mode cannot cross the wire. Recorded
    rather than hidden: a silently different scope semantic is the kind of gap
    that gets mistaken for a retrieval bug."""
    remote = FakeRemote()
    client = _client(remote)

    result = await client.search(
        query="q", mode=SearchMode.CHUNKS, dataset="prod", node_sets=["a", "b"]
    )

    assert result.retrieval_stats["node_name_filter_operator_sent"] is False
    assert result.retrieval_stats["remote"] is True
    assert "node_name_filter_operator" not in remote.search_calls[0][1]


@pytest.mark.asyncio
async def test_an_unreachable_service_degrades_and_names_itself_remote() -> None:
    """Fail closed, and say which substrate failed: 'remote' vs 'live' is the
    difference between debugging a sidecar and debugging a local store."""
    client = _client(FakeRemote(fail=ConnectionError("no route to host")))

    result = await client.search(query="q", mode=SearchMode.CHUNKS, dataset="prod")

    assert result.degraded is True
    assert result.degraded_reason is not None
    assert result.degraded_reason.startswith("remote_search_failed")


@pytest.mark.asyncio
async def test_remote_ingest_leaves_triplet_embedding_to_the_service() -> None:
    """embed_triplets drives local memify pipelines. Running it against a
    remote store would build embeddings in an unrelated in-process database,
    so remote ingest must not call it."""
    remote = FakeRemote()
    client = _client(remote)
    called = False

    import app.integrations.cognee_client as module

    async def _tripwire(dataset: str) -> dict:
        nonlocal called
        called = True
        return {"ok": True}

    original = module.embed_triplets
    module.embed_triplets = _tripwire  # type: ignore[assignment]
    try:
        outcome = await client.ingest("some text", dataset="prod", node_sets=["billing"])
    finally:
        module.embed_triplets = original  # type: ignore[assignment]

    assert called is False
    assert outcome["remote"] is True
    assert outcome["triplet_embeddings"] == "owned_by_remote_service"
    assert remote.add_calls[0][1]["dataset_name"] == "prod"
    assert remote.cognify_calls[0]["datasets"] == ["prod"]


@pytest.mark.asyncio
async def test_aclose_releases_the_remote_session() -> None:
    remote = FakeRemote()
    client = _client(remote)
    await client.search(query="q", mode=SearchMode.CHUNKS, dataset="prod")

    await client.aclose()

    assert remote.closed is True
    assert client._remote_client is None
