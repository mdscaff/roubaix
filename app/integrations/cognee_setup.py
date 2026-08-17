"""Bootstrap Cognee from Roubaix settings and optional pgGraph adapter.

Call ``configure_cognee()`` once at process startup, before the first Cognee I/O.
Cognee reads ``.env`` on import; this module bridges Roubaix-specific names
(``OPENROUTER_API_KEY``, ``DEFAULT_MODEL``) onto Cognee's ``LLM_*`` / ``EMBEDDING_*``
keys when those are not already set explicitly.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1"

_COGNEE_STATUS: dict[str, Any] = {"configured": False}


def _provider_model(model: str, default_provider: str) -> str:
    if "/" in model:
        return model
    return f"{default_provider}/{model}"


def resolve_llm_api_key() -> str | None:
    """Prefer explicit LLM_API_KEY, then OpenRouter, then OpenAI."""
    explicit = os.getenv("LLM_API_KEY")
    if explicit:
        return explicit
    openrouter = os.getenv("OPENROUTER_API_KEY") or settings.openrouter_api_key
    if openrouter:
        return openrouter
    return os.getenv("OPENAI_API_KEY") or settings.openai_api_key


def resolve_embedding_api_key() -> str | None:
    """Prefer explicit EMBEDDING_API_KEY, then OpenAI, then LLM key."""
    explicit = os.getenv("EMBEDDING_API_KEY")
    if explicit:
        return explicit
    openai = os.getenv("OPENAI_API_KEY") or settings.openai_api_key
    if openai:
        return openai
    return resolve_llm_api_key()


def build_cognee_env_overrides() -> dict[str, str]:
    """Map Roubaix settings onto Cognee environment variables."""
    overrides: dict[str, str] = {}

    llm_api_key = resolve_llm_api_key()
    if llm_api_key and not os.getenv("LLM_API_KEY"):
        overrides["LLM_API_KEY"] = llm_api_key

    if (os.getenv("OPENROUTER_API_KEY") or settings.openrouter_api_key) and not os.getenv(
        "LLM_ENDPOINT"
    ):
        endpoint = settings.default_llm_endpoint or OPENROUTER_ENDPOINT
        overrides["LLM_ENDPOINT"] = endpoint

    if not os.getenv("LLM_MODEL"):
        model = os.getenv("DEFAULT_MODEL") or settings.default_model
        overrides["LLM_MODEL"] = _provider_model(model, "openai")

    if not os.getenv("LLM_PROVIDER"):
        overrides["LLM_PROVIDER"] = "openai"

    embedding_api_key = resolve_embedding_api_key()
    if embedding_api_key and not os.getenv("EMBEDDING_API_KEY"):
        overrides["EMBEDDING_API_KEY"] = embedding_api_key

    # The embedding endpoint has to follow the embedding *key*. Bridging
    # LLM_ENDPOINT but not this one sent an OpenRouter key to api.openai.com on
    # any OpenRouter-only setup, so embeddings 401'd while chat worked — the
    # confusing half-failure, since cognify's first visible error is unrelated.
    # Only when no OpenAI key exists: with one present the key resolver prefers
    # it, and OpenAI's own endpoint is the right target. Verified 2026-08-17
    # that OpenRouter serves /v1/embeddings with real vectors.
    if (
        not os.getenv("EMBEDDING_ENDPOINT")
        and not (os.getenv("OPENAI_API_KEY") or settings.openai_api_key)
        and (os.getenv("OPENROUTER_API_KEY") or settings.openrouter_api_key)
    ):
        overrides["EMBEDDING_ENDPOINT"] = settings.default_llm_endpoint or OPENROUTER_ENDPOINT

    if not os.getenv("EMBEDDING_MODEL"):
        embedding_model = os.getenv("DEFAULT_EMBEDDING_MODEL") or settings.default_embedding_model
        overrides["EMBEDDING_MODEL"] = _provider_model(embedding_model, "openai")

    if not os.getenv("EMBEDDING_PROVIDER"):
        overrides["EMBEDDING_PROVIDER"] = "openai"

    if not os.getenv("EMBEDDING_DIMENSIONS"):
        dimensions = os.getenv("DEFAULT_EMBEDDING_DIMENSIONS")
        overrides["EMBEDDING_DIMENSIONS"] = dimensions or str(settings.default_embedding_dimensions)

    if settings.graph_database_provider and not os.getenv("GRAPH_DATABASE_PROVIDER"):
        overrides["GRAPH_DATABASE_PROVIDER"] = settings.graph_database_provider

    if settings.app_env == "development" and not os.getenv("ENABLE_BACKEND_ACCESS_CONTROL"):
        overrides["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"

    return overrides


def apply_cognee_env_overrides(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Write resolved overrides into ``os.environ`` before ``import cognee``."""
    resolved = overrides if overrides is not None else build_cognee_env_overrides()
    for key, value in resolved.items():
        os.environ.setdefault(key, value)
    return resolved


def migrate_pggraph_provider() -> bool:
    """Redirect the retired ``pggraph`` provider onto cognee's native Postgres.

    The community adapter (``cognee-community-graph-adapter-pggraph``) is
    retired here, for a reason stronger than tidiness: it pins ``cognee==1.4.2``
    exactly, so keeping it made every future cognee upgrade unresolvable. Cognee
    ships a native ``postgres`` graph adapter — in 1.4.2 already — which is the
    same Postgres-backed graph the community package existed to provide.

    Existing ``GRAPH_DATABASE_PROVIDER=pggraph`` configs are rewritten to
    ``postgres`` rather than failed, since that is what they meant, but the
    rewrite is logged at WARNING: a storage backend must never change silently.

    Returns True when a rewrite happened.

    Note (upstream, checked 2026-08-17): cognee documents its Postgres graph
    store as a *demo* feature, recommends Kuzu or Neo4j for production, and
    sells a production-ready Postgres graph adapter as a licensed product.
    """
    provider = os.getenv("GRAPH_DATABASE_PROVIDER", settings.graph_database_provider or "")
    if provider != "pggraph":
        return False
    logger.warning(
        "graph_provider_migrated",
        extra={
            "from": "pggraph",
            "to": "postgres",
            "reason": (
                "the community pgGraph adapter is retired (it pinned cognee==1.4.2); "
                "cognee's native postgres graph adapter replaces it. Update "
                "GRAPH_DATABASE_PROVIDER=postgres in your .env to silence this."
            ),
        },
    )
    os.environ["GRAPH_DATABASE_PROVIDER"] = "postgres"
    return True


def _try_import_cognee() -> Any | None:
    try:
        import cognee  # type: ignore[import-not-found]

        return cognee
    except ImportError:
        return None


def configure_cognee() -> dict[str, Any]:
    """Apply env bridges and runtime Cognee config. Idempotent per process."""
    global _COGNEE_STATUS  # noqa: PLW0603

    if not settings.cognee_setup_enabled:
        _COGNEE_STATUS = {"configured": False, "reason": "disabled"}
        return _COGNEE_STATUS

    overrides = apply_cognee_env_overrides()

    cognee = _try_import_cognee()
    if cognee is None:
        _COGNEE_STATUS = {
            "configured": False,
            "reason": "cognee_not_installed",
            "env_overrides": sorted(overrides.keys()),
        }
        return _COGNEE_STATUS

    pggraph_migrated = migrate_pggraph_provider()

    runtime_sets: dict[str, str] = {}
    # Read the provider back from the environment, not from settings: the
    # pggraph migration rewrites it there, and pushing the stale settings value
    # into cognee would hand it a provider name that no longer exists.
    effective_provider = os.getenv("GRAPH_DATABASE_PROVIDER") or settings.graph_database_provider
    if effective_provider:
        cognee.config.set("graph_database_provider", effective_provider)
        runtime_sets["graph_database_provider"] = effective_provider

    _COGNEE_STATUS = {
        "configured": True,
        "pggraph_migrated": pggraph_migrated,
        "llm_model": os.getenv("LLM_MODEL"),
        "embedding_model": os.getenv("EMBEDDING_MODEL"),
        "embedding_dimensions": os.getenv("EMBEDDING_DIMENSIONS"),
        "graph_database_provider": os.getenv("GRAPH_DATABASE_PROVIDER"),
        "env_overrides": sorted(overrides.keys()),
        "runtime_sets": runtime_sets,
    }
    return _COGNEE_STATUS


def get_cognee_status() -> dict[str, Any]:
    return dict(_COGNEE_STATUS)
