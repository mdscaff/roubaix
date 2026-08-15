"""Token estimation and cost accounting.

Cost is the metric Roubaix claims to improve, so it has to be measured rather
than asserted. Two rules apply here:

- Prefer the provider's reported ``usage`` over any local estimate. The
  estimate exists for the fallback path and for pre-flight budgeting.
- Never report an estimate as if it were measured. Callers get
  ``estimated=True/False`` and the eval harness records which one it used.
"""

from __future__ import annotations

from dataclasses import dataclass

# Chars per token for the heuristic estimator. Roughly right for English prose
# with GPT/Claude-family BPE tokenizers; wrong for code and non-Latin scripts,
# which is why measured usage always wins when available.
_CHARS_PER_TOKEN = 4

# USD per 1M tokens, (input, output). Deliberately a small explicit table:
# an unknown model yields a null cost rather than a confidently wrong number.
MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "anthropic/claude-haiku-4.5": (1.00, 5.00),
    "anthropic/claude-sonnet-4.5": (3.00, 15.00),
}


def estimate_tokens(text: str) -> int:
    """Return a heuristic token count for *text*.

    Uses ``tiktoken`` when it is installed, otherwise a chars-per-token
    approximation. Never returns 0 for non-empty input.
    """
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore[import-not-found]

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text) // _CHARS_PER_TOKEN)


@dataclass(frozen=True)
class CostEstimate:
    """Cost for one synthesis call."""

    input_tokens: int
    output_tokens: int
    usd: float | None
    estimated: bool
    model: str

    def as_telemetry(self) -> dict[str, object]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.usd,
            "cost_is_estimate": self.estimated,
            "cost_model": self.model,
        }


def price_for(model: str) -> tuple[float, float] | None:
    """Return (input, output) USD per 1M tokens for *model*, if known."""
    if model in MODEL_PRICES_USD_PER_MTOK:
        return MODEL_PRICES_USD_PER_MTOK[model]
    # Tolerate provider prefixes ("openrouter/openai/gpt-4o-mini").
    suffix_matches = [
        price for name, price in MODEL_PRICES_USD_PER_MTOK.items() if model.endswith(name)
    ]
    return suffix_matches[0] if len(suffix_matches) == 1 else None


def max_input_tokens_for_budget(model: str, usd_budget: float, *, reserved_output_tokens: int = 400) -> int | None:
    """Largest input token count that keeps one synthesis call under *usd_budget*.

    Returns ``None`` when the model is unpriced — an unknown price must not be
    silently treated as free. ``reserved_output_tokens`` holds back room for the
    completion, which is billed at the higher rate.
    """
    price = price_for(model)
    if price is None:
        return None
    input_rate, output_rate = price
    remaining = usd_budget - (reserved_output_tokens * output_rate) / 1_000_000
    if remaining <= 0 or input_rate <= 0:
        return 0
    return int((remaining * 1_000_000) / input_rate)


def cost_for(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated: bool,
) -> CostEstimate:
    """Return a :class:`CostEstimate`, with ``usd=None`` for unpriced models."""
    price = price_for(model)
    usd = None
    if price is not None:
        usd = (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
    return CostEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=usd,
        estimated=estimated,
        model=model,
    )
