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
# Honest caveat: at ~120 tokens this prefix is under every provider's minimum
# cacheable prefix, so Roubaix gets no prefix-cache discount today. The minimum
# is model-dependent and NOT monotonic with model size — it ranges from 512
# tokens on some models to 4096 on others, and below the minimum caching fails
# silently (no error, just a zero cache-write count). Picking a cheaper model
# can therefore silently cost you all caching.
#
# The structure is here so the discount becomes a content change rather than a
# rewrite: once the ontology summary and few-shot examples move into the prefix
# it clears the lower thresholds comfortably. Until then, do not claim
# prefix-cache savings — verify with the provider's reported cache-read token
# count before putting a number in the README.
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
        self._http: httpx.AsyncClient | None = None

    async def synthesize(
        self,
        request: QueryRequest,
        route: RouteDecision,
        packed: PackedEvidence,
    ) -> SynthesisResult:
        if not self.api_key:
            # No provider configured. Expected in CI and local dev — labelled
            # so it can never be mistaken for a synthesized answer.
            return SynthesisResult(
                answer=self._fallback_answer(request, route, packed),
                input_tokens_estimate=self._estimate_tokens(request, route, packed),
                unsynthesized=True,
                failure_reason="no_api_key_configured",
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
            client = await self._client()
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
        # ValueError covers a malformed JSON body, which is not an httpx.HTTPError
        # and previously escaped as an unhandled 500.
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "synthesis_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            # A provider outage is not an answer. The caller gets a placeholder
            # that is explicitly marked failed, and the orchestrator refuses to
            # accept or cache it. Returning a fluent template as an accepted
            # answer is how a dead provider becomes invisible.
            return SynthesisResult(
                answer=self._fallback_answer(request, route, packed),
                input_tokens_estimate=self._estimate_tokens(request, route, packed),
                failed=True,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )

        answer = self._extract_answer(body)
        usage = body.get("usage") or {}
        reported = usage.get("prompt_tokens")
        measured = isinstance(reported, int) and reported > 0
        input_tokens = (
            int(reported)
            if isinstance(reported, int) and reported > 0
            else self._estimate_tokens(request, route, packed)
        )
        return SynthesisResult(
            answer=answer,
            input_tokens_estimate=input_tokens,
            usage_measured=measured,
        )

    async def _client(self) -> httpx.AsyncClient:
        """Lazily create a pooled client.

        A fresh AsyncClient per request means a full TLS handshake per
        synthesis, which is pure added latency on the hot path.
        """
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.timeout_s)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

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
