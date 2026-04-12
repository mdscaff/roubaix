from app.domain.models import RetrievalEvidence, RetrievalResult, SearchMode
from app.services.evidence import EvidencePacker


def test_triplet_packing() -> None:
    packer = EvidencePacker()
    result = RetrievalResult(
        mode=SearchMode.TRIPLET_COMPLETION,
        evidence=RetrievalEvidence(triplets=[{"subject": "A", "predicate": "depends_on", "object": "B"}]),
        retrieval_stats={},
    )
    packed = packer.pack(result)
    assert packed.evidence_items == ["A depends_on B"]
