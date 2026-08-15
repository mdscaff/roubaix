"""GEPA optimization for the Roubaix router.

Requires the ``opt`` extra. Run via ``scripts/optimize_router.py``.

## The metric is the whole design

Two properties matter more than the optimizer configuration:

**It is cost-aware.** A metric that rewards only correctness converges on
GRAPH_SUMMARY_COMPLETION for everything — the most expensive mode is never
*wrong*, it is just wasteful, and a correctness-only score cannot see waste.
The score is therefore ``0.7 * correct + 0.3 * cost_efficiency``, and the
feedback distinguishes over-escalation (expensive, invisible to the user) from
under-escalation (cheap, visible as a retry).

**It returns text, not a float.** GEPA's reflection prompt receives the
``feedback`` string verbatim; a bare score gives the proposer nothing to reason
about beyond "this was bad". The feedback here names the failure in Roubaix's
own vocabulary — which mode was chosen, its cost rank, which mode was
sufficient, and what the rationale claimed — so a proposed instruction change
can actually address the observed error.

## Status

No compile run has been recorded in this repository. The program, the metric,
and the artifact loading path are implemented and tested; the numbers a compile
would produce are not claimed anywhere. Compiling needs an LM and a budget —
see the cost note on :func:`optimize`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import dspy

from app.domain.models import SearchMode
from app.integrations.dspy_program import RouterProgram
from app.services.router import COST_RANK

logger = logging.getLogger(__name__)

MAX_COST_RANK = max(COST_RANK.values())
CORRECTNESS_WEIGHT = 0.7
COST_WEIGHT = 0.3


def score_and_feedback(
    expected: SearchMode,
    chosen: SearchMode,
    *,
    query: str,
    rationale: str,
) -> tuple[float, str]:
    """Return a cost-aware score and natural-language feedback.

    Split out from the DSPy metric signature so it can be tested directly,
    without an LM or a compiled program.
    """
    expected_rank = COST_RANK[expected]
    chosen_rank = COST_RANK[chosen]
    correct = chosen is expected
    cost_efficiency = 1.0 - (chosen_rank / MAX_COST_RANK)
    score = CORRECTNESS_WEIGHT * float(correct) + COST_WEIGHT * cost_efficiency

    if correct:
        return score, (
            f"Correct: {chosen.value} (cost rank {chosen_rank}). Rationale given: {rationale!r}"
        )

    if chosen_rank > expected_rank:
        feedback = (
            f"OVER-ESCALATED. Chose {chosen.value} (cost rank {chosen_rank}) when "
            f"{expected.value} (cost rank {expected_rank}) was sufficient. "
            f"Query: {query!r}. Rationale given: {rationale!r}. "
            f"This is the expensive failure mode: the answer may well be correct, "
            f"so nothing downstream reports a problem, but graph depth was bought "
            f"and not needed."
        )
    else:
        feedback = (
            f"UNDER-ESCALATED. Chose {chosen.value} (cost rank {chosen_rank}) when "
            f"{expected.value} (cost rank {expected_rank}) was needed. "
            f"Query: {query!r}. Rationale given: {rationale!r}. "
            f"This returns insufficient evidence and forces the runtime controller "
            f"to widen or escalate, so it costs a retrieval as well as latency."
        )
    return score, feedback


def router_metric(
    gold: Any,
    pred: Any,
    trace: Any = None,
    pred_name: str | None = None,
    pred_trace: Any = None,
    program_trace: Any = None,
) -> dspy.Prediction:
    """GEPA feedback metric. Signature matches ``dspy.GEPAFeedbackMetric``."""
    expected = SearchMode(gold.expected_mode)
    rationale = str(getattr(pred, "rationale", ""))
    try:
        chosen = SearchMode(pred.mode)
    except (ValueError, AttributeError):
        return dspy.Prediction(
            score=0.0,
            feedback=(
                f"Emitted {getattr(pred, 'mode', None)!r}, which is not a valid mode. "
                f"Valid values: {[m.value for m in SearchMode]}."
            ),
        )

    score, feedback = score_and_feedback(
        expected, chosen, query=str(gold.query), rationale=rationale
    )
    return dspy.Prediction(score=score, feedback=feedback)


def load_examples(corpus_path: Path) -> list[dspy.Example]:
    """Load labelled routing examples from a JSONL eval corpus."""
    examples: list[dspy.Example] = []
    for raw in corpus_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        if not row.get("expected_mode"):
            continue
        examples.append(
            dspy.Example(
                query=row["query"],
                freshness_required=row.get("freshness_required", False),
                expected_mode=row["expected_mode"],
            ).with_inputs("query", "freshness_required")
        )
    return examples


def optimize(
    *,
    corpus_path: Path,
    out_path: Path,
    task_model: str,
    reflection_model: str,
    auto: str = "light",
    num_threads: int = 4,
    seed: int = 0,
) -> dict[str, Any]:
    """Compile :class:`RouterProgram` with GEPA and save the artifact.

    Cost note, because this is not free: ``auto="light"`` is roughly 1300 metric
    calls for a small program, ``"medium"`` ~1700, ``"heavy"`` ~2000. Budget
    against that before the first run.

    The models are deliberately separate. Reflection happens rarely and needs a
    strong model; evaluation happens constantly and should use a cheap one.
    Reversing that is the easiest way to waste the whole budget.
    """
    dspy.configure(lm=dspy.LM(task_model, temperature=0.0))

    examples = load_examples(corpus_path)
    if len(examples) < 4:
        raise ValueError(f"{corpus_path} has {len(examples)} labelled examples; too few to split.")
    split = max(2, int(len(examples) * 0.6))
    trainset, valset = examples[:split], examples[split:]

    optimizer = dspy.GEPA(
        metric=router_metric,
        auto=auto,
        reflection_lm=dspy.LM(reflection_model, temperature=1.0, max_tokens=32000),
        reflection_minibatch_size=3,
        candidate_selection_strategy="pareto",
        use_merge=True,
        max_merge_invocations=5,
        skip_perfect_score=True,
        track_stats=True,
        failure_score=0.0,
        perfect_score=1.0,
        num_threads=num_threads,
        seed=seed,
        log_dir=str(out_path.parent / "gepa_log"),
    )

    compiled = optimizer.compile(RouterProgram(), trainset=trainset, valset=valset)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Plain-text JSON: check the artifact into git. It is the reproducibility
    # story for a learned component, and it diffs.
    compiled.save(str(out_path))

    return {
        "status": "ok",
        "artifact": str(out_path),
        "train_examples": len(trainset),
        "val_examples": len(valset),
        "task_model": task_model,
        "reflection_model": reflection_model,
        "auto": auto,
    }
