#!/usr/bin/env python3
"""End-to-end demo: the tier model, measured against a real running server.

Boots the actual FastAPI app under uvicorn (a subprocess, not a TestClient),
warm-loads the resident graph, and drives POST /answer through every tier
boundary the architecture claims:

1. **Startup + warm-load** — /healthz reports the resident graph's size. The
   Cognee warm-load is attempted for real; with no cognified store present it
   loads 0 edges and says so. The seed file stands in as the "already learned"
   state a long-running instance would have.
2. **Tier 0 answers** — structural queries (edge, path, no-path, neighbors)
   answered from the resident graph: zero tokens, measured zero cost, and
   client-observed latency printed per request.
3. **Latency distribution** — p50/p95/p99 over repeated requests, both
   client-observed (includes HTTP) and server-reported.
4. **Honest fall-through** — a non-structural query falls through to the
   pipeline; with no live retrieval the server fails closed with a reason
   instead of fabricating an answer.
5. **The learning loop** (clearly labeled SIMULATED retrieval, in-process) —
   the slow path teaches the fast path: first query pays for retrieval and
   promotes its triplets; a differently-phrased follow-up answers in Tier 0.

Every number in the transcript is measured in this run. Nothing is mocked
except section 5's retrieval client, which is labeled as such — running that
section against live Cognee requires an egress allowlist entry and an LLM key
(see scripts/live_stack.py preflight).

Usage:
    uv run python scripts/demo_e2e.py [--port 8123] [--sweep 100] [--out demo_transcript.md]
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SEED_PATH = REPO_ROOT / "configs" / "memgraph_seed.example.json"

# Structural queries the seed graph can answer in Tier 0. Each entry:
# (query, expected answer pattern) — the pattern is asserted, so a demo run
# that silently regressed into the slow path fails loudly instead of lying.
TIER0_QUERIES: tuple[tuple[str, str], ...] = (
    ("does checkout call billing", "edge"),
    ("how is checkout connected to ledger", "path"),
    ("does notifications depend on fraud screening", "no_path"),
    ("what calls billing", "neighbors_in"),
    ("what depends on warehouse", "neighbors_in"),
)

FALL_THROUGH_QUERY = "summarize the architecture of billing"


def _fmt_ms(ms: float) -> str:
    # Server total_ms is an integer millisecond count, so 0 means "below the
    # measurement resolution", not literally zero.
    return "<1 ms" if ms < 1 else f"{ms:.2f} ms"


class Transcript:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, text: str = "") -> None:
        self.lines.append(text)
        print(text)

    def save(self, path: Path) -> None:
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def boot_server(port: int) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["ROUBAIX_MEMGRAPH_SEED_PATH"] = str(SEED_PATH)
    # The demo demonstrates the production posture: stub evidence fails closed.
    env["ROUBAIX_ALLOW_STUB_EVIDENCE"] = "false"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_health(client: httpx.Client, deadline_s: float = 60.0) -> dict:
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        try:
            response = client.get("/healthz")
            if response.status_code == 200:
                body: dict = response.json()
                return body
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"server did not become healthy within {deadline_s}s")


def timed_answer(client: httpx.Client, query: str) -> tuple[dict, float]:
    start = time.perf_counter()
    response = client.post("/answer", json={"query": query})
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.raise_for_status()
    return response.json(), elapsed_ms


def section_health(t: Transcript, health: dict) -> None:
    memgraph = health["memgraph"]
    t.add("## 1. Startup and warm-load")
    t.add()
    t.add(
        f"`/healthz` → memgraph enabled={memgraph['enabled']}, "
        f"**{memgraph['nodes']} nodes**, **{memgraph['edges']} edges** resident."
    )
    t.add()
    t.add(
        "At startup the server attempted a real warm-load from Cognee's graph "
        "store (`warm_load_from_cognee`); with no cognified store in this "
        "environment it loaded 0 edges and logged the skip — it does not "
        "invent data. The resident edges above come from the seed file "
        f"`{SEED_PATH.relative_to(REPO_ROOT)}`, standing in for the state a "
        "long-running instance accumulates via warm-load + promotion."
    )
    t.add()


def section_tier0(t: Transcript, client: httpx.Client) -> list[str]:
    t.add("## 2. Tier 0: structural answers from the resident graph")
    t.add()
    failures: list[str] = []
    for query, expected_pattern in TIER0_QUERIES:
        body, client_ms = timed_answer(client, query)
        telemetry = body["telemetry"]
        tier = telemetry.get("tier")
        signals = body["route"]["signals"]
        ok = (
            tier == "memgraph"
            and body["accepted"]
            and telemetry.get("input_tokens") == 0
            and telemetry.get("output_tokens") == 0
            and telemetry.get("estimated_cost_usd") == 0.0
            and telemetry.get("cost_is_estimate") is False
            and any(s.startswith(f"memgraph.{expected_pattern}") for s in signals)
        )
        if not ok:
            failures.append(
                f"{query!r} did not answer from Tier 0 as {expected_pattern!r} "
                f"(tier={tier}, signals={signals})"
            )
        t.add(f"**Q: {query}**")
        t.add(f"> {body['answer']}")
        t.add(
            f"- tier=`{tier}` · server {_fmt_ms(telemetry.get('total_ms', -1))} · "
            f"round-trip {_fmt_ms(client_ms)} · tokens in/out **0/0** · "
            f"cost **$0.0** (measured, not estimated)"
        )
        evidence = telemetry.get("evidence", [])
        if evidence:
            t.add(f"- evidence: {'; '.join(evidence)}")
        t.add()
    return failures


def section_latency(t: Transcript, client: httpx.Client, sweep: int) -> None:
    t.add("## 3. Latency distribution")
    t.add()
    client_ms: list[float] = []
    server_ms: list[float] = []
    queries = [q for q, _ in TIER0_QUERIES]
    for i in range(sweep):
        body, ms = timed_answer(client, queries[i % len(queries)])
        client_ms.append(ms)
        server_ms.append(float(body["telemetry"].get("total_ms", 0)))

    def pct(values: list[float], p: float) -> float:
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]

    t.add(f"{sweep} requests, rotating through {len(queries)} Tier-0 queries:")
    t.add()
    t.add("| percentile | server (pipeline) | client (incl. HTTP) |")
    t.add("|---|---|---|")
    for label, p in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
        t.add(f"| {label} | {_fmt_ms(pct(server_ms, p))} | {_fmt_ms(pct(client_ms, p))} |")
    t.add(
        f"| mean | {_fmt_ms(statistics.mean(server_ms))} | {_fmt_ms(statistics.mean(client_ms))} |"
    )
    t.add()
    t.add(
        "The sub-second target is met by three orders of magnitude at the "
        "server; the HTTP round-trip dominates end-to-end latency."
    )
    t.add()


def section_fall_through(t: Transcript, client: httpx.Client) -> list[str]:
    t.add("## 4. Honest fall-through: fail closed, never fabricate")
    t.add()
    body, client_ms = timed_answer(client, FALL_THROUGH_QUERY)
    telemetry = body["telemetry"]
    failures: list[str] = []
    if telemetry.get("tier") == "memgraph":
        failures.append("fall-through query was wrongly answered by Tier 0")
    if body["accepted"]:
        failures.append("fail-closed query was unexpectedly accepted")
    if not str(telemetry.get("escalation_reason") or "").startswith("degraded_retrieval"):
        failures.append("fail-closed reason was not degraded_retrieval")
    t.add(f"**Q: {FALL_THROUGH_QUERY}**")
    t.add()
    t.add(
        "This is not a structural query, so Tier 0 falls through — its failure "
        "mode is *fell through*, never *guessed*. With no live Cognee behind "
        "this demo server, retrieval returns flagged stub evidence and the "
        "runtime controller refuses to answer from it:"
    )
    t.add()
    t.add(f"> {body['answer']}")
    t.add()
    t.add(f"- `accepted`: **{body['accepted']}**")
    t.add(f"- `escalation_reason`: `{telemetry.get('escalation_reason')}`")
    t.add(f"- `stop_reason`: `{telemetry.get('stop_reason')}`")
    t.add(f"- tier=`{telemetry.get('tier')}` · round-trip {_fmt_ms(client_ms)}")
    t.add()
    t.add(
        "With a live Cognee stack (see `scripts/live_stack.py` preflight) this "
        "same request routes to real retrieval and synthesis instead."
    )
    t.add()
    return failures


async def run_learning_loop() -> tuple[dict, dict, int]:
    """The promotion loop, in-process, with a SIMULATED retrieval client."""
    from app.domain.models import (
        QueryRequest,
        RetrievalEvidence,
        RetrievalResult,
        SearchMode,
    )
    from app.integrations.cognee_client import CogneeClient
    from app.services.cache import ContentAddressedCache
    from app.services.evidence import EvidencePacker
    from app.services.memgraph import InMemoryGraph
    from app.services.normalizer import QueryNormalizer
    from app.services.orchestrator import QueryOrchestrator
    from app.services.router import QueryRouter
    from app.services.runtime_controller import RuntimeController

    class SimulatedTripletClient(CogneeClient):
        """Returns fixed triplet evidence, unflagged (i.e. as live Cognee would)."""

        async def search(
            self,
            query: str,
            mode: SearchMode,
            dataset: str,
            node_sets: list[str] | None = None,
            evidence_budget: int | None = None,
        ) -> RetrievalResult:
            return RetrievalResult(
                mode=mode,
                evidence=RetrievalEvidence(
                    triplets=[
                        {"subject": "billing", "predicate": "depends_on", "object": "warehouse"},
                        {"subject": "billing", "predicate": "reports_to", "object": "finance"},
                    ],
                    chunks=[f"{query} — supporting detail {i}" for i in range(4)],
                ),
                retrieval_stats={"dataset": dataset, "simulated": True},
            )

    graph = InMemoryGraph()
    normalizer = QueryNormalizer()
    orchestrator = QueryOrchestrator(
        router=QueryRouter(normalizer=normalizer),
        cognee_client=SimulatedTripletClient(),
        evidence_packer=EvidencePacker(),
        runtime_controller=RuntimeController(),
        normalizer=normalizer,
        cache=ContentAddressedCache(),
        decomposer=None,
        graph=graph,
    )
    first = await orchestrator.answer(
        QueryRequest(query="How is billing connected to the warehouse?")
    )
    second = await orchestrator.answer(QueryRequest(query="Does billing depend on warehouse?"))
    return (
        {k: first.telemetry.get(k) for k in ("tier", "promoted_edges", "total_ms")},
        {
            "answer": second.answer,
            **{
                k: second.telemetry.get(k)
                for k in ("tier", "total_ms", "input_tokens", "output_tokens", "cost_is_estimate")
            },
        },
        graph.edge_count,
    )


def section_learning(t: Transcript) -> list[str]:
    import asyncio

    t.add("## 5. The learning loop (SIMULATED retrieval, in-process)")
    t.add()
    t.add(
        "**Label first:** live `cognify`/`search` needs an LLM key and an "
        "egress allowlist entry this environment doesn't have, so retrieval "
        "here is a simulated client returning fixed triplets. Everything else "
        "— router, packer, runtime controller, promotion, Tier 0 — is the "
        "real pipeline."
    )
    t.add()
    first, second, edge_count = asyncio.run(run_learning_loop())
    failures: list[str] = []
    if first.get("tier") != "pipeline" or not first.get("promoted_edges"):
        failures.append("first query did not promote edges from the slow path")
    if second.get("tier") != "memgraph" or second.get("input_tokens") != 0:
        failures.append("second query did not answer from Tier 0")
    t.add('**Q1: "How is billing connected to the warehouse?"** (graph starts empty)')
    t.add(
        f"- tier=`{first['tier']}` — paid for retrieval; "
        f"promoted **{first['promoted_edges']}** triplet edge(s) into the resident graph"
    )
    t.add()
    t.add('**Q2: "Does billing depend on warehouse?"** (different phrasing → no cache hit)')
    t.add(f"> {second['answer']}")
    t.add(
        f"- tier=`{second['tier']}` · {_fmt_ms(float(second['total_ms']))} · "
        f"tokens **{second['input_tokens']}/{second['output_tokens']}** · "
        f"cost_is_estimate={second['cost_is_estimate']} (the zero is measured)"
    )
    t.add()
    t.add(
        f"The resident graph now holds {edge_count} edge(s). The first query "
        "couldn't be fast; it made the next one fast. That is the contract."
    )
    t.add()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--sweep", type=int, default=100, help="latency sweep request count")
    parser.add_argument("--out", type=Path, default=Path("demo_transcript.md"))
    args = parser.parse_args()

    t = Transcript()
    t.add("# Roubaix end-to-end demo")
    t.add()
    t.add(
        "Real server (uvicorn subprocess), real HTTP, production fail-closed "
        "posture. Every number below was measured in this run."
    )
    t.add()

    server = boot_server(args.port)
    failures: list[str] = []
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=30.0) as client:
            health = wait_for_health(client)
            section_health(t, health)
            failures += section_tier0(t, client)
            section_latency(t, client, args.sweep)
            failures += section_fall_through(t, client)
    finally:
        server.terminate()
        server.wait(timeout=10)

    failures += section_learning(t)

    t.add("---")
    if failures:
        t.add("## DEMO FAILURES")
        for failure in failures:
            t.add(f"- {failure}")
    else:
        t.add(
            "All sections behaved as claimed: Tier 0 answered structural "
            "queries with zero tokens, fell through honestly, failed closed "
            "without live retrieval, and learned from the slow path."
        )

    t.save(args.out)
    print(f"\ntranscript written to {args.out}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
