#!/usr/bin/env python3
"""Generate markdown report for an eval run directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.evals.report import generate_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Roubaix eval report.md")
    parser.add_argument("run_dir", type=Path, help="Path to evals/runs/<run-id>/")
    args = parser.parse_args()
    report = generate_report(args.run_dir)
    print(report)


if __name__ == "__main__":
    main()
