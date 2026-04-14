"""Tests for the tiered resolution pipeline in QueryOrchestrator."""

import pytest

from app.domain.models import QueryRequest, SearchMode
from app.integrations.cognee_client import CogneeClient
from app.services.cache import ContentAddressedCache
from app.services.evidence import EvidencePacker
from app.services.normalizer import QueryNormalizer
from app.services.orchestrator import QueryOrchestrator
from app.services.router import QueryRouter
from app.services.runtime_controller import RuntimeController


def _make_orchestrator(cache: ContentAddressedCache | None = None) -> QueryOrchestrator:
    normalizer = QueryNormalizer()
    return QueryOrchestrator(
        router=QueryRouter(normalizer=normalizer),
        cognee_client=CogneeClient(),
        evidence_packer=EvidencePacker(),
        runtime_controller=RuntimeController(),
        normalizer=normalizer,
        cache=cache or ContentAddressedCache(),
    )


@pytest.mark.asyncio
async def test_first_call_is_cache_miss() -> None:
    orch = _make_orchestrator()
    result = await orch.answer(QueryRequest(query="What is the latest status?"))
    assert result.cache_hit is False
    assert result.accepted is True


@pytest.mark.asyncio
async def test_second_identical_call_is_cache_hit() -> None:
    orch = _make_orchestrator()
    r1 = await orch.answer(QueryRequest(query="What is the latest status?"))
    r2 = await orch.answer(QueryRequest(query="What is the latest status?"))
    assert r1.cache_hit is False
    assert r2.cache_hit is True


@pytest.mark.asyncio
async def test_normalized_variants_hit_same_cache() -> None:
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="What is the latest status?"))
    # Same query with different casing and extra spaces
    r2 = await orch.answer(QueryRequest(query="  what IS THE latest status  "))
    assert r2.cache_hit is True


@pytest.mark.asyncio
async def test_different_datasets_are_separate_cache_entries() -> None:
    orch = _make_orchestrator()
    await orch.answer(QueryRequest(query="test query", dataset="ds_a"))
    r2 = await orch.answer(QueryRequest(query="test query", dataset="ds_b"))
    assert r2.cache_hit is False
