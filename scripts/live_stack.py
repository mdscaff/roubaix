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
                (cognee's ingestion imports it even for local files), a graph
                adapter that needs no download (`turso`), and
                COGNEE_SKIP_CONNECTION_TEST=true.
    cognify  -> hard-requires a working LLM (entity extraction has no mock);
                without one it spins in litellm retries until timeout.
    search   -> requires cognified data, so it inherits the LLM requirement.

The docker-less embedded profile this script configures (SQLite relational +
LanceDB vector + kuzu graph) is for development and small corpora. kuzu is the
default because it is the only embedded backend with Cypher: turso and
cognee's Postgres adapter both set `supports_cypher_queries = False`, which
costs CYPHER *and* NATURAL_LANGUAGE. kuzu installs a JSON extension on first
use, so behind a restricted egress allowlist set GRAPH_DATABASE_PROVIDER=turso
and accept losing those two modes.

Cognee documents Postgres-based graph storage as a demo feature — numbers from
any of these profiles are not production-representative, and reports say which
profile produced them.
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
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings
from app.integrations.cognee_setup import (
    resolve_embedding_api_key,
    resolve_llm_api_key,
)

# Every mode the router can choose gets smoke-tested. CYPHER and
# NATURAL_LANGUAGE were absent, which is exactly why a permanently dead CYPHER
# mode passed a green standup for as long as it did.
SMOKE_MODES = (
    "CHUNKS",
    "TRIPLET_COMPLETION",
    "GRAPH_COMPLETION",
    "CYPHER",
    "NATURAL_LANGUAGE",
)

SMOKE_QUERY = "What does the billing service expose?"

# CYPHER takes a Cypher string, so probing it with the English question tests
# nothing except that English is not Cypher — it reported "unsupported by this
# backend" on a backend that supports Cypher perfectly well. Each mode gets
# input it can actually accept.
SMOKE_QUERIES = {"CYPHER": "MATCH (n) RETURN count(n) AS n_nodes"}
CORPUS = REPO_ROOT / "evals" / "corpus" / "roubaix_basics.md"
REPORT_DIR = REPO_ROOT / "evals" / "live"


def _check(name: str, ok: bool, detail: str, fix: str | None = None) -> dict:
    return {"check": name, "ok": ok, "detail": detail, **({"fix": fix} if fix else {})}


def _reachable(url: str, timeout: float = 8.0) -> bool:
    """True when *url* answers with any HTTP status at all.

    An HTTP error (401, 404) still means the network path is open — the
    failure mode being probed here is egress blocking, where the connection
    never completes. Empirically (2026-08-16): behind a restricted egress
    policy, api.openai.com, openrouter.ai, api.cognee.ai, huggingface.co,
    registry.ollama.ai, and every container-registry CDN were all blocked,
    while pypi.org and github.com release downloads were open.
    """
    try:
        req = urlrequest.Request(url, method="HEAD")
        urlrequest.urlopen(req, timeout=timeout)
        return True
    except Exception as exc:  # noqa: BLE001 - any HTTP status means reachable
        return hasattr(exc, "code")


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

    # Through the same resolvers the runtime uses, NOT raw os.getenv: keys set
    # in a gitignored .env (which is what this script's own fix text tells you
    # to do, and what the README documents) are read by pydantic settings and
    # never appear in the process environment. Checking os.getenv reported "no
    # LLM key in env" to correctly-configured setups and refused to start.
    llm_key = os.getenv("LLM_API_KEY") or resolve_llm_api_key()
    checks.append(
        _check(
            "llm_key",
            bool(llm_key),
            "LLM key present" if llm_key else "no LLM key in env",
            fix=(
                "set OPENROUTER_API_KEY or OPENAI_API_KEY (a gitignored .env is "
                "the right place). cognify's entity extraction has no mock: "
                "without a working LLM it spins in litellm retries until "
                "timeout — measured, not assumed."
            ),
        )
    )

    # A key is necessary but not sufficient: the provider must also be
    # reachable, and in sandboxed environments the egress allowlist — not the
    # key — is usually the binding constraint.
    endpoint = (
        "https://openrouter.ai"
        if (os.getenv("OPENROUTER_API_KEY") or settings.openrouter_api_key)
        else "https://api.openai.com"
    )
    llm_reachable = _reachable(endpoint)
    checks.append(
        _check(
            "llm_egress",
            llm_reachable,
            f"{endpoint} reachable" if llm_reachable else f"{endpoint} is egress-blocked",
            fix=(
                "this is a network-policy problem, not a key problem: add the "
                "provider's domain to the environment's egress allowlist "
                "(for Claude Code web environments, the owner configures this "
                "in the environment's network settings)"
            ),
        )
    )

    # Cognee Cloud: the settings exist (COGNEE_API_KEY / COGNEE_BASE_URL), but
    # the installed SDK ships NO cloud transport and CogneeClient implements
    # none — a cloud key alone cannot make this repository talk to cognee.ai.
    # Stated here so a set key is never mistaken for a working path.
    if os.getenv("COGNEE_API_KEY"):
        cloud_base = os.getenv("COGNEE_BASE_URL", "https://api.cognee.ai")
        cloud_reachable = _reachable(cloud_base)
        checks.append(
            _check(
                "cognee_cloud",
                False,
                (
                    f"COGNEE_API_KEY is set and {cloud_base} is "
                    f"{'reachable' if cloud_reachable else 'egress-blocked'}, but "
                    "no cloud transport exists in this repository or in the "
                    "installed cognee SDK — the key cannot be used yet"
                ),
                fix=(
                    "a Cognee Cloud REST client is unimplemented (see "
                    "docs/implementation-plan.md, Going live); the local SDK "
                    "path with an LLM key is the supported route today"
                ),
            )
        )

    # OpenRouter counts: it serves /v1/embeddings (verified 2026-08-17 with
    # real vectors), and cognee_setup points EMBEDDING_ENDPOINT at it when no
    # OpenAI key exists. Omitting it here reported "no embedding provider" to
    # environments that had a working one, whose documented fix — mock
    # vectors — would have thrown away real ranking for no reason.
    have_embed_key = bool(resolve_embedding_api_key())
    mock_on = os.getenv("MOCK_EMBEDDING", "").lower() in ("true", "1", "yes")
    if have_embed_key:
        explicit = (
            os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or settings.openai_api_key
        )
        provider = "openai" if explicit else "openrouter"
        checks.append(
            _check("embeddings", True, f"real embedding provider configured ({provider})")
        )
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
        # Not a failure any more: kuzu/ladybug is the intended default because
        # it is the only embedded backend with Cypher. The download is a real
        # constraint though, so it is named rather than discovered at runtime.
        checks.append(
            _check(
                "graph_store",
                True,
                "kuzu/ladybug (embedded, supports CYPHER and NATURAL_LANGUAGE). "
                "Installs its JSON extension from extension.ladybugdb.com on "
                "first use; set GRAPH_DATABASE_PROVIDER=turso for an "
                "offline-capable profile that has no Cypher",
            )
        )
    else:
        checks.append(_check("graph_store", True, f"provider={graph}"))

    return checks


def apply_embedded_profile(work_dir: Path, allow_mock_embeddings: bool) -> dict[str, str]:
    """Set the docker-less embedded profile. Returns what was set, for the report."""
    # kuzu (which cognee now serves through its renamed Ladybug adapter) is the
    # default because turso has no Cypher: `supports_cypher_queries = False`
    # costs CYPHER *and* NATURAL_LANGUAGE, so a turso dev profile cannot
    # exercise two modes the router can choose. The cost of kuzu is a one-time
    # `INSTALL JSON` extension download on first use, which fails behind a
    # restricted egress allowlist — set GRAPH_DATABASE_PROVIDER=turso to take
    # the offline-capable profile back, at the price of those two modes.
    applied = {
        "GRAPH_DATABASE_PROVIDER": "kuzu",
        "GRAPH_DATASET_DATABASE_HANDLER": "kuzu",
        "COGNEE_SKIP_CONNECTION_TEST": "true",
        "ENABLE_BACKEND_ACCESS_CONTROL": "false",
        "DATA_ROOT_DIRECTORY": str(work_dir / "data"),
        "SYSTEM_ROOT_DIRECTORY": str(work_dir / "system"),
    }
    if allow_mock_embeddings and not resolve_embedding_api_key():
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

    # cognify alone does NOT populate the triplet_text collection, so
    # TRIPLET_COMPLETION stays dead without this. Shared with every other
    # ingestion path — see cognee_client.embed_triplets.
    from app.integrations.cognee_client import embed_triplets

    report["triplet_embeddings"] = await embed_triplets(dataset)

    for mode_name in SMOKE_MODES:
        mode = SearchMode(mode_name)
        result = await client.search(
            query=SMOKE_QUERIES.get(mode_name, SMOKE_QUERY),
            mode=mode,
            dataset=dataset,
        )
        report["smoke"].append(
            {
                "mode": mode_name,
                "live": not result.degraded,
                # A mode the backend cannot serve is a different fact from a
                # mode that broke: turso and postgres have no Cypher at all,
                # and calling that "retrieval is degraded" would condemn a
                # perfectly good stack on a capability the profile never had.
                "unsupported_by_backend": result.degraded_kind == "capability",
                "degraded_reason": result.degraded_reason,
                "items": sum(
                    len(getattr(result.evidence, f))
                    for f in ("chunks", "triplets", "graph_paths", "rows", "timestamps")
                ),
            }
        )

    # Warm-load the resident graph from the freshly cognified store and
    # record how much of it became Tier-0-resident.
    from app.services.memgraph import InMemoryGraph, warm_load_from_cognee

    graph = InMemoryGraph()
    report["memgraph_warm_loaded_edges"] = await warm_load_from_cognee(graph)

    unsupported = [s["mode"] for s in report["smoke"] if s["unsupported_by_backend"]]
    broken = [
        s["mode"]
        for s in report["smoke"]
        if not s["live"] and not s["unsupported_by_backend"]
    ]
    report["all_modes_live"] = all(s["live"] for s in report["smoke"])
    report["unsupported_modes"] = unsupported
    report["broken_modes"] = broken
    # Quality is about whether the evidence and ranking can be trusted, so a
    # mode this backend never had must not condemn it — but a mode that broke
    # must. Reported separately from all_modes_live so neither fact hides the
    # other.
    report["quality_meaningful"] = not broken and not mock_embeddings
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
    # An unsupported mode is reported on BOTH paths. Printing it only on the
    # failure path is how a dead retrieval mode hides behind a green standup —
    # which is precisely what happened with CYPHER.
    unsupported = report.get("unsupported_modes") or []
    if unsupported:
        print(
            f"\nUnsupported by this graph backend: {', '.join(unsupported)}. "
            "The router can still choose these modes; the controller escalates "
            "past them. Use kuzu or neo4j if you need them."
        )

    if not report.get("quality_meaningful"):
        # Distinct causes reach here; naming the wrong one sends the reader
        # hunting for a key that is already set.
        if mock_embeddings:
            reason = "ranking is meaningless (mock embeddings)"
        else:
            broken = report.get("broken_modes") or []
            reason = f"embeddings are real, but these modes broke: {', '.join(broken)}"
        print(
            f"Stack is up in plumbing-only mode: retrieval executes, {reason}. "
            "Do not quote numbers from this stack.",
        )
    else:
        print("Live stack is up. evals/live reports from this stack are real retrieval.")


if __name__ == "__main__":
    main()
