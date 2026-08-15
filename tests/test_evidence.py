from app.domain.models import RetrievalEvidence, RetrievalResult, SearchMode
from app.services.evidence import EvidencePacker


def _result(mode: SearchMode, **evidence: object) -> RetrievalResult:
    return RetrievalResult(
        mode=mode,
        evidence=RetrievalEvidence(**evidence),  # type: ignore[arg-type]
        retrieval_stats={},
    )


def test_triplet_packing() -> None:
    packed = EvidencePacker().pack(
        _result(
            SearchMode.TRIPLET_COMPLETION,
            triplets=[{"subject": "A", "predicate": "depends_on", "object": "B"}],
        )
    )
    assert packed.evidence_items == ["A depends_on B"]


def test_pack_honours_the_route_evidence_budget() -> None:
    """The budget is the router's cost decision; ignoring it makes routing decorative."""
    packed = EvidencePacker().pack(
        _result(SearchMode.CHUNKS, chunks=[f"chunk {i}" for i in range(10)]),
        evidence_budget=3,
    )
    assert len(packed.evidence_items) == 3
    assert packed.dropped_over_budget == 7


def test_pack_deduplicates_ignoring_case_and_whitespace() -> None:
    packed = EvidencePacker().pack(
        _result(
            SearchMode.CHUNKS,
            chunks=["Billing calls Warehouse", "billing   calls warehouse", "other fact"],
        )
    )
    assert packed.evidence_items == ["Billing calls Warehouse", "other fact"]
    assert packed.dropped_duplicates == 1


def test_pack_enforces_a_token_budget_not_just_an_item_count() -> None:
    packed = EvidencePacker().pack(
        _result(SearchMode.CHUNKS, chunks=[f"item{i} " + "word " * 500 for i in range(5)]),
        evidence_budget=5,
        token_budget=200,
    )
    assert len(packed.evidence_items) == 1  # first item admitted, rest priced out
    assert packed.dropped_over_budget == 4
    assert "withheld" in packed.summary  # reduction is disclosed, not silent


def test_pack_always_keeps_at_least_one_item_even_if_oversized() -> None:
    """Returning nothing would trip fail-closed on an otherwise valid retrieval."""
    packed = EvidencePacker().pack(
        _result(SearchMode.CHUNKS, chunks=["word " * 1000]),
        token_budget=10,
    )
    assert len(packed.evidence_items) == 1


def test_pack_populates_evidence_hashes_for_provenance() -> None:
    packed = EvidencePacker().pack(_result(SearchMode.CHUNKS, chunks=["a", "b"]))
    assert len(packed.evidence_hashes) == len(packed.evidence_items) == 2
    assert all(len(h) == 16 for h in packed.evidence_hashes)


def test_pack_skips_blank_items() -> None:
    packed = EvidencePacker().pack(_result(SearchMode.CHUNKS, chunks=["  ", "", "real"]))
    assert packed.evidence_items == ["real"]


def test_temporal_packing_keeps_text_alongside_timestamps() -> None:
    """Timestamps alone are not an answer to a freshness question."""
    packed = EvidencePacker().pack(
        _result(SearchMode.TEMPORAL, timestamps=["2026-04-12"], chunks=["rollout reached stage 3"])
    )
    assert "2026-04-12" in packed.evidence_items
    assert "rollout reached stage 3" in packed.evidence_items


def test_degraded_flag_propagates_from_retrieval_to_packed_evidence() -> None:
    result = _result(SearchMode.CHUNKS, chunks=["stub"])
    result.degraded = True
    result.degraded_reason = "live_search_failed: RuntimeError"
    packed = EvidencePacker().pack(result)
    assert packed.degraded is True
    assert packed.degraded_reason == "live_search_failed: RuntimeError"
