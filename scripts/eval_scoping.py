#!/usr/bin/env python3
"""Measure NodeSet scoping precision against the hand-labelled corpus.

Like routing, scope derivation is a pure function of the query and a static
index — no Cognee, no LLM, no network — so this is the second pipeline metric
that can be gated in CI without lying about what was measured. The Phase C1
acceptance gate from docs/implementation-plan.md:

- precision of derived NodeSets ≥70% against hand labels, and
- a caller-supplied scope is never narrowed or replaced.

Examples:
  uv run python scripts/eval_scoping.py
  uv run python scripts/eval_scoping.py --json
  uv run python scripts/eval_scoping.py --min-precision 0.70
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.evals.scoping_eval import evaluate_scoping, format_scoping_report, scoping_report_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate NodeSet scoping precision.")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPO_ROOT / "evals" / "queries_scoping.jsonl",
        help="Scoping-labelled JSONL corpus.",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=REPO_ROOT / "evals" / "nodesets_eval.json",
        help="NodeSet alias index the labels were written against.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    parser.add_argument(
        "--min-precision",
        type=float,
        default=None,
        help="Exit non-zero if precision falls below this threshold.",
    )
    args = parser.parse_args()

    report = evaluate_scoping(args.corpus, args.index)
    print(scoping_report_json(report) if args.json else format_scoping_report(report))

    failures: list[str] = []
    if args.min_precision is not None and report.precision < args.min_precision:
        failures.append(f"precision {report.precision:.0%} < {args.min_precision:.0%} threshold")
    if not report.caller_scope_preserved:
        failures.append("caller-supplied scope was narrowed or replaced — invariant violated")

    if failures:
        print("\nFAILED: " + "; ".join(failures), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
