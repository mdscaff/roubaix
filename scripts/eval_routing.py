#!/usr/bin/env python3
"""Measure router accuracy against the tuning and held-out corpora.

Routing is a pure function of the query, so this needs no Cognee instance, no
LLM, and no network. That makes it the one pipeline metric that can be gated in
CI without lying about what was measured.

Examples:
  uv run python scripts/eval_routing.py
  uv run python scripts/eval_routing.py --json
  uv run python scripts/eval_routing.py --min-heldout-accuracy 0.75
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.evals.routing_eval import evaluate_routing, format_report, to_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Roubaix routing accuracy.")
    parser.add_argument(
        "--corpus",
        type=Path,
        action="append",
        help="JSONL corpus (repeatable). Defaults to the tuning and held-out sets.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    parser.add_argument(
        "--min-heldout-accuracy",
        type=float,
        default=None,
        help="Exit non-zero if held-out accuracy falls below this threshold.",
    )
    parser.add_argument(
        "--require-lift",
        action="store_true",
        help="Exit non-zero if the router does not beat the best single fixed mode.",
    )
    parser.add_argument(
        "--dspy-artifact",
        type=Path,
        default=None,
        help=(
            "Evaluate a GEPA-compiled router instead of the deterministic one. "
            "Requires the `opt` extra and calls an LM for unconfident queries."
        ),
    )
    args = parser.parse_args()

    router = None
    if args.dspy_artifact is not None:
        from app.integrations.dspy_program import DspyRouter, load_compiled

        router = DspyRouter(program=load_compiled(args.dspy_artifact))

    corpora = args.corpus or [
        REPO_ROOT / "evals" / "queries.jsonl",
        REPO_ROOT / "evals" / "queries_heldout.jsonl",
    ]
    reports = [evaluate_routing(path, router=router) for path in corpora]
    if router is not None:
        print(
            f"\nLearned stage consulted on {router.llm_calls} queries "
            f"({router.fallbacks} fell back to the deterministic decision).\n",
            file=sys.stderr,
        )
    print(to_json(reports) if args.json else format_report(reports))

    failures: list[str] = []
    heldout = next((r for r in reports if "heldout" in Path(r.corpus).stem), None)
    if args.min_heldout_accuracy is not None:
        if heldout is None:
            failures.append("no held-out corpus was evaluated")
        elif heldout.accuracy < args.min_heldout_accuracy:
            failures.append(
                f"held-out accuracy {heldout.accuracy:.0%} < "
                f"{args.min_heldout_accuracy:.0%} threshold"
            )
    if args.require_lift and heldout is not None and heldout.lift_over_best_fixed <= 0:
        failures.append(
            f"held-out accuracy {heldout.accuracy:.0%} does not beat the best "
            f"single fixed mode ({heldout.best_fixed_mode_accuracy:.0%})"
        )

    if failures:
        print("\nFAILED: " + "; ".join(failures), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
