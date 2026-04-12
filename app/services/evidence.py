from app.core.config import settings
from app.domain.models import PackedEvidence, RetrievalResult, SearchMode


class EvidencePacker:
    def pack(self, result: RetrievalResult) -> PackedEvidence:
        items: list[str] = []
        if result.mode == SearchMode.TRIPLET_COMPLETION:
            items = [f"{t['subject']} {t['predicate']} {t['object']}" for t in result.evidence.triplets]
        elif result.mode in {SearchMode.CHUNKS, SearchMode.RAG_COMPLETION}:
            items = result.evidence.chunks
        elif result.mode in {SearchMode.GRAPH_COMPLETION, SearchMode.GRAPH_SUMMARY_COMPLETION}:
            items = [str(path) for path in result.evidence.graph_paths]
        elif result.mode in {SearchMode.CYPHER, SearchMode.NATURAL_LANGUAGE}:
            items = [str(row) for row in result.evidence.rows]
        elif result.mode == SearchMode.TEMPORAL:
            items = result.evidence.timestamps

        items = items[: settings.max_evidence_items]
        summary = "\n".join(items) if items else "No evidence items returned."
        return PackedEvidence(
            mode=result.mode,
            summary=summary,
            evidence_items=items,
            provenance=result.evidence.provenance,
        )
