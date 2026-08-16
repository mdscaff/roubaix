"""Scoping-precision evaluation (Phase C1 acceptance gate).

NodeSet derivation, like routing, is a pure function of the query and a static
index — so its acceptance gate can run in CI with no Cognee instance, no LLM,
and no network. The plan's gate: **≥70% precision against hand labels on a
scoping-labelled extension of the held-out corpus, and derivation must never
narrow a caller-supplied scope.**

Precision is the gated number because a false scope actively *hides* evidence:
retrieval filtered to the wrong NodeSet returns confidently wrong context.
Recall is reported but not gated — a missed scope merely forgoes the cost
saving, and C1 is explicitly the lexical stage with known gaps (recorded as
``sc-recall-*`` rows) that Phase C2's learned scorer exists to close.

Honesty caveat, stated rather than implied: the index and the labels are
authored by the same hand, so this measures the lexical matcher against human
judgment on this corpus — it is not a blind benchmark. The corpus mitigates
where it can: labels record judgment even where the matcher is known to miss
(``sc-recall-*``) or misfire (``sc-trap-003/004``), so neither metric can
reach 100% by construction.

Measurement runs through ``QueryRouter.route`` with the index injected — the
real wiring, not the index in isolation — so a regression in the router's
scope plumbing fails this gate too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from app.domain.models import QueryRequest
from app.services.router import QueryRouter
from app.services.scoping import NodeSetIndex


class ScopingQuery(BaseModel):
    query_id: str
    query: str
    expected_node_sets: list[str]
    notes: str | None = None


@dataclass
class ScopingMisfire:
    query_id: str
    query: str
    derived: list[str]
    expected: list[str]


@dataclass
class ScopingReport:
    corpus: str
    index_path: str
    total_rows: int
    true_positives: int
    false_positives: int
    false_negatives: int
    misfires: list[ScopingMisfire] = field(default_factory=list)
    misses: list[ScopingMisfire] = field(default_factory=list)
    caller_scope_preserved: bool = True

    @property
    def precision(self) -> float:
        derived = self.true_positives + self.false_positives
        return self.true_positives / derived if derived else 1.0

    @property
    def recall(self) -> float:
        expected = self.true_positives + self.false_negatives
        return self.true_positives / expected if expected else 1.0


def load_scoping_corpus(path: Path) -> list[ScopingQuery]:
    rows: list[ScopingQuery] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(ScopingQuery.model_validate_json(line))
    return rows


def evaluate_scoping(corpus_path: Path, index_path: Path) -> ScopingReport:
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    router = QueryRouter(node_set_index=NodeSetIndex(index_data))
    rows = load_scoping_corpus(corpus_path)

    tp = fp = fn = 0
    misfires: list[ScopingMisfire] = []
    misses: list[ScopingMisfire] = []
    caller_scope_preserved = True

    for row in rows:
        derived = list(router.route(QueryRequest(query=row.query)).node_sets)
        expected = set(row.expected_node_sets)
        row_fp = [n for n in derived if n not in expected]
        row_fn = [n for n in expected if n not in derived]
        tp += len([n for n in derived if n in expected])
        fp += len(row_fp)
        fn += len(row_fn)
        if row_fp:
            misfires.append(
                ScopingMisfire(
                    query_id=row.query_id,
                    query=row.query,
                    derived=derived,
                    expected=sorted(expected),
                )
            )
        if row_fn:
            misses.append(
                ScopingMisfire(
                    query_id=row.query_id,
                    query=row.query,
                    derived=derived,
                    expected=sorted(expected),
                )
            )

        # The invariant half of the gate: with a caller-supplied scope, the
        # decision must carry exactly that scope — never the derived one.
        caller = router.route(QueryRequest(query=row.query, node_sets=["caller-scope"]))
        if list(caller.node_sets) != ["caller-scope"]:
            caller_scope_preserved = False

    return ScopingReport(
        corpus=str(corpus_path),
        index_path=str(index_path),
        total_rows=len(rows),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        misfires=misfires,
        misses=misses,
        caller_scope_preserved=caller_scope_preserved,
    )


def format_scoping_report(report: ScopingReport) -> str:
    lines = [
        "# NodeSet scoping evaluation (Phase C1 acceptance gate)",
        "",
        (
            f"Corpus: `{Path(report.corpus).name}` ({report.total_rows} rows) · "
            f"index: `{Path(report.index_path).name}`"
        ),
        "",
        "| Metric | Value | Gated? |",
        "| --- | ---: | --- |",
        f"| Precision (derived NodeSets correct) | {report.precision:.0%} | yes, ≥70% |",
        f"| Recall (expected NodeSets found) | {report.recall:.0%} | no — reported only |",
        f"| Caller scope preserved | {report.caller_scope_preserved} | yes, must hold |",
        "",
        (
            f"Pairs: {report.true_positives} correct, {report.false_positives} misfires, "
            f"{report.false_negatives} misses."
        ),
    ]
    if report.misfires:
        lines.extend(["", "## Misfires (count against precision)", ""])
        for m in report.misfires:
            lines.append(
                f"- `{m.query_id}` derived {m.derived}, expected {m.expected} — {m.query!r}"
            )
    if report.misses:
        lines.extend(["", "## Misses (count against recall; Phase C2's territory)", ""])
        for m in report.misses:
            lines.append(
                f"- `{m.query_id}` derived {m.derived}, expected {m.expected} — {m.query!r}"
            )
    lines.extend(
        [
            "",
            (
                "Labels and index share an author; this measures the matcher "
                "against human judgment, not a blind benchmark. Precision is "
                "gated because a false scope hides evidence; recall is C2's "
                "problem and is reported, not gated."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def scoping_report_json(report: ScopingReport) -> str:
    return json.dumps(
        {
            "corpus": report.corpus,
            "index": report.index_path,
            "rows": report.total_rows,
            "precision": round(report.precision, 4),
            "recall": round(report.recall, 4),
            "true_positives": report.true_positives,
            "false_positives": report.false_positives,
            "false_negatives": report.false_negatives,
            "caller_scope_preserved": report.caller_scope_preserved,
            "misfires": [m.__dict__ for m in report.misfires],
            "misses": [m.__dict__ for m in report.misses],
        },
        indent=2,
    )
