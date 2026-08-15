"""LLM synthesis with cache-friendly static prefix and dynamic evidence suffix."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.tokens import estimate_tokens
from app.domain.models import PackedEvidence, QueryRequest, RouteDecision, SynthesisResult
from app.integrations.cognee_setup import resolve_llm_api_key

logger = logging.getLogger(__name__)

# Everything stable lives in the prefix so it stays byte-identical across
# requests; only the query and evidence vary. Provider prompt caches key on an
# exact prefix match, so any per-request text here (a timestamp, the mode, the
# query) would invalidate the cache on every call.
#
# Honest caveat: at this length the prefix is well under the minimum cacheable
# prefix of the major providers (Anthropic bills a cache write only at 1024+
# tokens; OpenAI auto-caches from 1024). Roubaix therefore gets no prefix-cache
# discount today. The structure is here so that when the ontology summary and
# few-shot examples land in the prefix, the discount is a config change rather
# than a rewrite. Do not claim prefix-cache savings until measured.
STATIC_SYSTEM_PROMPT = (
    "You are Roubaix, a retrieval-grounded assistant.\n"
    "Rules:\n"
    "1. Answer only from the supplied evidence.\n"
    "2. If the evidence is insufficient, say so briefly and do not invent facts.\n"
    "3. Do not use prior knowledge to fill gaps in the evidence.\n"
    "4. Prefer the most specific evidence item over the most general one.\n"
    "5. Keep answers short; cite the evidence lines you used."
)


class AnswerSynthesizer:
    """OpenAI-compatible chat completion for answer synthesis."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        endpoint: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.api_key = api_key or resolve_llm_api_key()
        self.model = model or settings.default_model
        self.endpoint = (endpoint or settings.default_llm_endpoint or "https://openrouter.ai/api/v1").rstrip("/")
        self.timeout_s = timeout_s if timeout_s is not None else settings.synthesis_timeout_s

    async def synthesize(
        self,
        request: QueryRequest,
        route: RouteDecision,
        packed: PackedEvidence,
    ) -> SynthesisResult:
        if not self.api_key:
            return SynthesisResult(
                answer=self._fallback_answer(request, route, packed),
                input_tokens_estimate=self._estimate_tokens(request, route, packed),
            )

        user_content = (
            f"Query: {request.query}\n"
            f"Route: {route.mode.value}\n"
            f"Rationale: {route.rationale}\n\n"
            f"Evidence:\n{packed.summary}"
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": STATIC_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(
                    f"{self.endpoint}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            logger.warning("synthesis_http_error", extra={"error": str(exc)})
            return SynthesisResult(
                answer=self._fallback_answer(request, route, packed),
                input_tokens_estimate=self._estimate_tokens(request, route, packed),
            )

        answer = self._extract_answer(body)
        usage = body.get("usage") or {}
        reported = usage.get("prompt_tokens")
        measured = isinstance(reported, int) and reported > 0
        input_tokens = int(reported) if measured else self._estimate_tokens(request, route, packed)
        return SynthesisResult(
            answer=answer,
            input_tokens_estimate=input_tokens,
            usage_measured=measured,
        )

    @staticmethod
    def _extract_answer(body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            return "No completion returned by the LLM provider."
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        return "No completion returned by the LLM provider."

    @staticmethod
    def _fallback_answer(request: QueryRequest, route: RouteDecision, packed: PackedEvidence) -> str:
        return (
            f"Roubaix response using mode={route.mode.value}. "
            f"Query: {request.query}. "
            f"Evidence items: {len(packed.evidence_items)}."
        )

    @staticmethod
    def _estimate_tokens(request: QueryRequest, route: RouteDecision, packed: PackedEvidence) -> int:
        material = " ".join([STATIC_SYSTEM_PROMPT, request.query, route.rationale, packed.summary])
        return max(1, estimate_tokens(material))
