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

    if (os.getenv("OPENROUTER_API_KEY") or settings.openrouter_api_key) and not os.getenv("LLM_ENDPOINT"):
        endpoint = settings.default_llm_endpoint or "https://openrouter.ai/api/v1"
        overrides["LLM_ENDPOINT"] = endpoint

    if not os.getenv("LLM_MODEL"):
        model = os.getenv("DEFAULT_MODEL") or settings.default_model
        overrides["LLM_MODEL"] = _provider_model(model, "openai")

    if not os.getenv("LLM_PROVIDER"):
        overrides["LLM_PROVIDER"] = "openai"

    embedding_api_key = resolve_embedding_api_key()
    if embedding_api_key and not os.getenv("EMBEDDING_API_KEY"):
        overrides["EMBEDDING_API_KEY"] = embedding_api_key

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

    return overrides


def apply_cognee_env_overrides(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Write resolved overrides into ``os.environ`` before ``import cognee``."""
    resolved = overrides if overrides is not None else build_cognee_env_overrides()
    for key, value in resolved.items():
        os.environ.setdefault(key, value)
    return resolved


def register_pggraph_adapter() -> bool:
    """Register the community pgGraph adapter when configured."""
    provider = os.getenv("GRAPH_DATABASE_PROVIDER", settings.graph_database_provider or "")
    if provider != "pggraph":
        return False
    try:
        from cognee_community_graph_adapter_pggraph import register  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "GRAPH_DATABASE_PROVIDER=pggraph but cognee-community-graph-adapter-pggraph "
            "is not installed. Install from cognee-community/packages/graph/pggraph."
        )
        return False
    register()
    return True


def configure_cognee() -> dict[str, Any]:
    """Apply env bridges and runtime Cognee config. Idempotent per process."""
    global _COGNEE_STATUS

    if not settings.cognee_setup_enabled:
        _COGNEE_STATUS = {"configured": False, "reason": "disabled"}
        return _COGNEE_STATUS

    overrides = apply_cognee_env_overrides()

    try:
        import cognee  # type: ignore[import-not-found]
    except ImportError:
        _COGNEE_STATUS = {
            "configured": False,
            "reason": "cognee_not_installed",
            "env_overrides": sorted(overrides.keys()),
        }
        return _COGNEE_STATUS

    pggraph_registered = register_pggraph_adapter()

    runtime_sets: dict[str, str] = {}
    if settings.graph_database_provider:
        cognee.config.set("graph_database_provider", settings.graph_database_provider)
        runtime_sets["graph_database_provider"] = settings.graph_database_provider

    _COGNEE_STATUS = {
        "configured": True,
        "pggraph_registered": pggraph_registered,
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
