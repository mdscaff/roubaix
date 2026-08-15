"""Routing-only evaluation.

Routing is the one decision Roubaix can measure without a live retrieval
substrate or an LLM: it is a pure function of the query. That makes it the only
part of the pipeline that can be gated in CI honestly, and it is worth
separating from the full-pipeline eval, whose numbers are meaningless against
stub retrieval.

Two reference points are reported alongside accuracy, because accuracy on its
own is unreadable:

- **best fixed mode** — what a router that always answers the single commonest
  label would score. A router that does not beat this has earned nothing.
- **tuning vs held-out** — accuracy on the corpus the rules were written against
  is an upper bound, not a measurement. The held-out number is the real one, and
  the gap between them is the overfitting estimate.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.models import QueryRequest, SearchMode
from app.evals.models import EvalQuery
from app.services.router import QueryRouter


@dataclass
class RoutingMiss:
    query_id: str
    bucket: str
    query: str
    expected: str
    actual: str
    signals: list[str]
    confident: bool


@dataclass
class RoutingReport:
    corpus: str
    total: int
    correct: int
    best_fixed_mode_accuracy: float
    by_bucket: dict[str, float] = field(default_factory=dict)
    misses: list[RoutingMiss] = field(default_factory=list)
    confident_accuracy: float | None = None
    unconfident_share: float = 0.0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def lift_over_best_fixed(self) -> float:
        return self.accuracy - self.best_fixed_mode_accuracy


def load_corpus(path: Path) -> list[EvalQuery]:
    rows: list[EvalQuery] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(EvalQuery.model_validate_json(line))
    return rows


def evaluate_routing(corpus_path: Path, router: QueryRouter | None = None) -> RoutingReport:
    router = router or QueryRouter()
    rows = [row for row in load_corpus(corpus_path) if row.expected_mode is not None]

    correct = 0
    misses: list[RoutingMiss] = []
    per_bucket: dict[str, list[bool]] = defaultdict(list)
    confident_hits = 0
    confident_total = 0

    for row in rows:
        decision = router.route(
            QueryRequest(
                query=row.query,
                dataset=row.dataset,
                freshness_required=row.freshness_required,
            )
        )
        hit = decision.mode is row.expected_mode
        correct += hit
        per_bucket[row.bucket].append(hit)
        if decision.confident:
            confident_total += 1
            confident_hits += hit
        if not hit:
            misses.append(
                RoutingMiss(
                    query_id=row.query_id,
                    bucket=row.bucket,
                    query=row.query,
                    expected=(row.expected_mode or SearchMode.CHUNKS).value,
                    actual=decision.mode.value,
                    signals=decision.signals,
                    confident=decision.confident,
                )
            )

    label_counts = Counter(row.expected_mode for row in rows)
    best_fixed = max(label_counts.values()) / len(rows) if rows else 0.0

    return RoutingReport(
        corpus=str(corpus_path),
        total=len(rows),
        correct=correct,
        best_fixed_mode_accuracy=best_fixed,
        by_bucket={b: sum(v) / len(v) for b, v in sorted(per_bucket.items())},
        misses=misses,
        confident_accuracy=(confident_hits / confident_total) if confident_total else None,
        unconfident_share=1.0 - (confident_total / len(rows)) if rows else 0.0,
    )


def format_report(reports: list[RoutingReport]) -> str:
    lines = ["# Routing evaluation", ""]
    lines.append("| Corpus | n | Accuracy | Best fixed mode | Lift | Unconfident |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for r in reports:
        name = Path(r.corpus).stem
        lines.append(
            f"| {name} | {r.total} | {r.accuracy:.0%} | {r.best_fixed_mode_accuracy:.0%} | "
            f"{r.lift_over_best_fixed:+.0%} | {r.unconfident_share:.0%} |"
        )

    for r in reports:
        lines.extend(["", f"## {Path(r.corpus).stem}", ""])
        for bucket, acc in r.by_bucket.items():
            lines.append(f"- `{bucket}`: {acc:.0%}")
        if r.confident_accuracy is not None:
            lines.append(
                f"- accuracy when the router reported confidence: {r.confident_accuracy:.0%}"
            )
        if r.misses:
            lines.extend(["", "### Misses", ""])
            for m in r.misses:
                fired = ", ".join(m.signals) or "no signals fired"
                lines.append(
                    f"- `{m.query_id}` expected `{m.expected}`, routed `{m.actual}` "
                    f"({fired}) — {m.query!r}"
                )
    lines.extend(
        [
            "",
            "Accuracy on the corpus the rules were written against is an upper "
            "bound, not a measurement. Quote the held-out number.",
            "",
        ]
    )
    return "\n".join(lines)


def to_json(reports: list[RoutingReport]) -> str:
    return json.dumps(
        [
            {
                "corpus": r.corpus,
                "n": r.total,
                "accuracy": round(r.accuracy, 4),
                "best_fixed_mode_accuracy": round(r.best_fixed_mode_accuracy, 4),
                "lift_over_best_fixed": round(r.lift_over_best_fixed, 4),
                "unconfident_share": round(r.unconfident_share, 4),
                "by_bucket": {k: round(v, 4) for k, v in r.by_bucket.items()},
                "misses": [m.__dict__ for m in r.misses],
            }
            for r in reports
        ],
        indent=2,
    )
