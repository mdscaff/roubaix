#!/usr/bin/env python3
"""Ingest the Roubaix eval corpus into Cognee and run cognify."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from app.core.config import settings
from app.integrations.cognee_client import CogneeClient
from app.integrations.cognee_setup import configure_cognee

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "evals" / "corpus" / "roubaix_basics.md"


def _apply_local_dev_defaults() -> None:
    os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
    os.environ.setdefault("DB_PROVIDER", "postgres")
    os.environ.setdefault("DB_HOST", "localhost")
    os.environ.setdefault("DB_PORT", "5433")
    os.environ.setdefault("DB_NAME", "cognee")
    os.environ.setdefault("DB_USERNAME", "cognee")
    os.environ.setdefault("DB_PASSWORD", "cognee")


async def seed(dataset: str, corpus_path: Path) -> dict[str, object]:
    _apply_local_dev_defaults()
    status = configure_cognee()
    if not status.get("configured"):
        raise RuntimeError(f"Cognee is not configured: {status}")

    content = corpus_path.read_text(encoding="utf-8")
    client = CogneeClient()
    result = await client.ingest(content, dataset=dataset)
    return {"dataset": dataset, "corpus": str(corpus_path), **result}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Cognee with the Roubaix eval corpus.")
    parser.add_argument(
        "--dataset",
        default=settings.default_dataset,
        help="Cognee dataset name (default: ROUBAIX_DEFAULT_DATASET)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="Path to markdown/text corpus",
    )
    args = parser.parse_args()
    outcome = asyncio.run(seed(args.dataset, args.corpus))
    print(outcome)


if __name__ == "__main__":
    main()
