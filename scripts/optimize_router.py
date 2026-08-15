#!/usr/bin/env python3
"""Compile the Roubaix router with GEPA.

Requires the ``opt`` extra and a configured LM. This costs real money — see the
budget note in app/integrations/gepa_optimizer.optimize.

Examples:
  uv run --extra opt python scripts/optimize_router.py --dry-run
  uv run --extra opt python scripts/optimize_router.py \
      --task-model openai/gpt-4.1-mini --reflection-model openai/gpt-5 --auto light

Compile on the tuning corpus only. Compiling on `queries_heldout.jsonl` would
destroy the one unbiased measurement in this repository — the held-out corpus is
how the compiled program gets *judged*, via scripts/eval_routing.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_ARTIFACT = REPO_ROOT / "artifacts" / "router_gepa.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="GEPA-compile the Roubaix router.")
    parser.add_argument("--corpus", type=Path, default=REPO_ROOT / "evals" / "queries.jsonl")
    parser.add_argument("--out", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--task-model", type=str, default="openai/gpt-4.1-mini")
    parser.add_argument("--reflection-model", type=str, default="openai/gpt-5")
    parser.add_argument("--auto", choices=["light", "medium", "heavy"], default="light")
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the corpus split and configuration without calling an LM.",
    )
    args = parser.parse_args()

    if "heldout" in args.corpus.stem:
        parser.error(
            "Refusing to compile on the held-out corpus. It is the evaluation set; "
            "training on it would make its accuracy meaningless."
        )

    try:
        from app.integrations.gepa_optimizer import load_examples, optimize
    except ImportError as exc:
        print(f"DSPy is not installed: {exc}\nInstall with: uv sync --extra opt", file=sys.stderr)
        raise SystemExit(2) from exc

    if args.dry_run:
        examples = load_examples(args.corpus)
        split = max(2, int(len(examples) * 0.6))
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "corpus": str(args.corpus),
                    "labelled_examples": len(examples),
                    "train_examples": split,
                    "val_examples": len(examples) - split,
                    "task_model": args.task_model,
                    "reflection_model": args.reflection_model,
                    "auto": args.auto,
                    "note": (
                        "auto=light is roughly 1300 metric calls. No LM was called "
                        "for this dry run."
                    ),
                },
                indent=2,
            )
        )
        return

    result = optimize(
        corpus_path=args.corpus,
        out_path=args.out,
        task_model=args.task_model,
        reflection_model=args.reflection_model,
        auto=args.auto,
        num_threads=args.num_threads,
        seed=args.seed,
    )
    print(json.dumps(result, indent=2))
    print(
        "\nNow judge it on the held-out corpus:\n"
        f"  uv run --extra opt python scripts/eval_routing.py --dspy-artifact {args.out}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
