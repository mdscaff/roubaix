#!/usr/bin/env python3
"""Run offline eval baselines against a JSONL query corpus.

Examples:
  uv run python scripts/run_eval.py
  uv run python scripts/run_eval.py --baselines roubaix_rules,chunks_only
  uv run python scripts/report_eval.py evals/runs/run-20260526T120000Z
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.evals.baselines import Baseline  # noqa: E402
from app.evals.report import generate_report  # noqa: E402
from app.evals.runner import run_eval  # noqa: E402


def _parse_baselines(raw: str | None) -> list[Baseline] | None:
    if not raw:
        return None
    return [Baseline(item.strip()) for item in raw.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Roubaix offline eval baselines.")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPO_ROOT / "evals" / "queries.jsonl",
        help="Path to JSONL eval corpus.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "evals" / "runs",
        help="Directory for run artifacts.",
    )
    parser.add_argument(
        "--baselines",
        type=str,
        default=None,
        help="Comma-separated baselines: chunks_only,graph_only,roubaix_rules",
    )
    parser.add_argument("--run-id", type=str, default=None, help="Optional run identifier.")
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate report.md immediately after the run.",
    )
    args = parser.parse_args()

    summary = asyncio.run(
        run_eval(
            corpus_path=args.corpus,
            output_dir=args.output_dir,
            baselines=_parse_baselines(args.baselines),
            run_id=args.run_id,
        )
    )
    run_dir = args.output_dir / summary.run_id
    print(f"Wrote {run_dir / 'results.jsonl'}")
    print(f"Wrote {run_dir / 'summary.json'}")

    if args.report:
        report = generate_report(run_dir)
        print(f"Wrote {run_dir / 'report.md'}")
        print()
        print(report)


if __name__ == "__main__":
    main()
