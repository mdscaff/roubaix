#!/usr/bin/env python3
"""Stand up (or preflight) a live Cognee stack for Roubaix.

One command, three outcomes, each explicit:

- **live-ready** (exit 0): every prerequisite present; the corpus is seeded and
  a smoke search per retrieval mode ran against real retrieval. A stamped JSON
  report lands in `evals/live/`.
- **prerequisites missing** (exit 2): each missing prerequisite is named with
  its exact failure mode and fix. Nothing half-starts.
- **plumbing-only** (exit 0 with --allow-mock-embeddings): the stack runs with
  Cognee's MOCK_EMBEDDING vectors. Retrieval executes end-to-end but ranking is
  meaningless, and every report this mode produces is stamped
  `"quality_meaningful": false`. This mode exists to validate wiring, not to
  produce numbers.

The prerequisite boundary below was measured empirically in an offline
environment (2026-08-16), not assumed:

    add      -> works with no LLM and no network, GIVEN: the `s3fs` module
                (cognee's ingestion imports it even for local files), the
                embedded `turso` graph adapter (the default `ladybug` adapter
                downloads a JSON extension from extension.ladybugdb.com at
                first use, which fails behind restricted egress), and
                COGNEE_SKIP_CONNECTION_TEST=true.
    cognify  -> hard-requires a working LLM (entity extraction has no mock);
                without one it spins in litellm retries until timeout.
    search   -> requires cognified data, so it inherits the LLM requirement.

The docker-less embedded profile this script configures (SQLite relational +
LanceDB vector + turso/SQLite graph) is for development and small corpora.
Cognee documents Postgres-based graph storage as a demo feature too — numbers
from either are not production-representative, and reports say which profile
produced them.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SMOKE_MODES = ("CHUNKS", "TRIPLET_COMPLETION", "GRAPH_COMPLETION")
CORPUS = REPO_ROOT / "evals" / "corpus" / "roubaix_basics.md"
REPORT_DIR = REPO_ROOT / "evals" / "live"


def _check(name: str, ok: bool, detail: str, fix: str | None = None) -> dict:
    return {"check": name, "ok": ok, "detail": detail, **({"fix": fix} if fix else {})}


def preflight(allow_mock_embeddings: bool) -> list[dict]:
    """Each check names its precise failure and the fix. No half-starts."""
    checks: list[dict] = []

    checks.append(
        _check(
            "cognee_sdk",
            importlib.util.find_spec("cognee") is not None,
            "cognee importable" if importlib.util.find_spec("cognee") else "cognee not installed",
            fix="uv sync --extra opt",
        )
    )
    checks.append(
        _check(
            "s3fs",
            importlib.util.find_spec("s3fs") is not None,
            "s3fs importable (cognee.add imports it even for local files)"
            if importlib.util.find_spec("s3fs")
            else "s3fs missing — cognee.add raises ModuleNotFoundError without it",
            fix="uv sync --extra opt  (s3fs is declared there)",
        )
    )

    llm_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv(
        "OPENROUTER_API_KEY"
    )
    checks.append(
        _check(
            "llm_key",
            bool(llm_key),
            "LLM key present" if llm_key else "no LLM key in env",
            fix=(
                "set OPENROUTER_API_KEY or OPENAI_API_KEY. cognify's entity "
                "extraction has no mock: without a working LLM it spins in "
                "litellm retries until timeout — measured, not assumed."
            ),
        )
    )

    have_embed_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("EMBEDDING_API_KEY"))
    mock_on = os.getenv("MOCK_EMBEDDING", "").lower() in ("true", "1", "yes")
    if have_embed_key:
        checks.append(_check("embeddings", True, "real embedding provider configured"))
    elif allow_mock_embeddings or mock_on:
        checks.append(
            _check(
                "embeddings",
                True,
                "MOCK_EMBEDDING — vectors are fake, ranking is meaningless; "
                "plumbing-only mode, stamped on every report",
            )
        )
    else:
        checks.append(
            _check(
                "embeddings",
                False,
                "no embedding provider",
                fix=(
                    "set OPENAI_API_KEY (embeddings), or pass "
                    "--allow-mock-embeddings for plumbing-only validation"
                ),
            )
        )

    graph = os.getenv("GRAPH_DATABASE_PROVIDER", "")
    if graph in ("", "ladybug", "kuzu"):
        checks.append(
            _check(
                "graph_store",
                False,
                "default ladybug/kuzu adapter downloads its JSON extension from "
                "extension.ladybugdb.com at first use — fails behind restricted egress",
                fix=(
                    "this script sets the embedded turso profile "
                    "(GRAPH_DATABASE_PROVIDER=turso, SQLite-backed, no downloads); "
                    "or run docker compose -f docker/docker-compose.yml up for pgGraph"
                ),
            )
        )
    else:
        checks.append(_check("graph_store", True, f"provider={graph}"))

    return checks


def apply_embedded_profile(work_dir: Path, allow_mock_embeddings: bool) -> dict[str, str]:
    """Set the docker-less embedded profile. Returns what was set, for the report."""
    applied = {
        "GRAPH_DATABASE_PROVIDER": "turso",
        "GRAPH_DATASET_DATABASE_HANDLER": "turso",
        "COGNEE_SKIP_CONNECTION_TEST": "true",
        "ENABLE_BACKEND_ACCESS_CONTROL": "false",
        "DATA_ROOT_DIRECTORY": str(work_dir / "data"),
        "SYSTEM_ROOT_DIRECTORY": str(work_dir / "system"),
    }
    if allow_mock_embeddings and not os.getenv("OPENAI_API_KEY"):
        applied["MOCK_EMBEDDING"] = "true"
    for key, value in applied.items():
        os.environ.setdefault(key, value)
    return {k: os.environ[k] for k in applied}


async def seed_and_smoke(dataset: str, mock_embeddings: bool) -> dict:
    """Seed the corpus and run one search per smoke mode. Returns the report body."""
    # Late imports: configure_cognee must run before cognee initialises.
    from app.integrations.cognee_setup import configure_cognee

    configure_cognee()
    import cognee

    from app.domain.models import SearchMode
    from app.integrations.cognee_client import CogneeClient

    client = CogneeClient()
    report: dict = {"dataset": dataset, "seed": {}, "smoke": []}

    content = CORPUS.read_text(encoding="utf-8")
    await cognee.add(content, dataset_name=dataset)
    await cognee.cognify(datasets=[dataset])
    report["seed"] = {"corpus": str(CORPUS), "bytes": len(content)}

    for mode_name in SMOKE_MODES:
        mode = SearchMode(mode_name)
        result = await client.search(
            query="What does the billing service expose?",
            mode=mode,
            dataset=dataset,
        )
        report["smoke"].append(
            {
                "mode": mode_name,
                "live": not result.degraded,
                "degraded_reason": result.degraded_reason,
                "items": sum(
                    len(getattr(result.evidence, f))
                    for f in ("chunks", "triplets", "graph_paths", "rows", "timestamps")
                ),
            }
        )

    report["all_modes_live"] = all(s["live"] for s in report["smoke"])
    report["quality_meaningful"] = report["all_modes_live"] and not mock_embeddings
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Stand up or preflight live Cognee.")
    parser.add_argument("--dataset", default="roubaix_live")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=REPO_ROOT / ".cognee_live",
        help="Where the embedded profile keeps its databases (gitignored).",
    )
    parser.add_argument(
        "--allow-mock-embeddings",
        action="store_true",
        help="Run with fake vectors to validate plumbing. Ranking is meaningless "
        "and the report is stamped quality_meaningful=false.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Report prerequisites and exit without touching anything.",
    )
    args = parser.parse_args()

    checks = preflight(args.allow_mock_embeddings)
    for check in checks:
        marker = "ok " if check["ok"] else "MISSING"
        print(f"[{marker}] {check['check']}: {check['detail']}")
        if not check["ok"] and check.get("fix"):
            print(f"          fix: {check['fix']}")

    # graph_store "missing" is fixable by this script itself; everything else is not.
    blocking = [c for c in checks if not c["ok"] and c["check"] != "graph_store"]
    if args.preflight_only:
        raise SystemExit(2 if blocking else 0)
    if blocking:
        print(
            f"\n{len(blocking)} prerequisite(s) missing — refusing to half-start. "
            f"Fix the items above and re-run.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    profile = apply_embedded_profile(args.work_dir, args.allow_mock_embeddings)
    mock_embeddings = os.environ.get("MOCK_EMBEDDING", "").lower() in ("true", "1", "yes")

    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "mock_embeddings": mock_embeddings,
        "checks": checks,
    }
    try:
        report.update(asyncio.run(seed_and_smoke(args.dataset, mock_embeddings)))
    except Exception as exc:  # noqa: BLE001 - report the boundary, don't stack-trace it
        report["failed"] = f"{type(exc).__name__}: {exc}"
        report["quality_meaningful"] = False

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPORT_DIR / f"live-{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")

    if report.get("failed"):
        print(f"FAILED at: {report['failed']}", file=sys.stderr)
        raise SystemExit(1)
    if not report.get("quality_meaningful"):
        print(
            "Stack is up in plumbing-only mode: retrieval executes, ranking is "
            "meaningless (mock embeddings). Do not quote numbers from this stack.",
        )
    else:
        print("Live stack is up. evals/live reports from this stack are real retrieval.")


if __name__ == "__main__":
    main()
