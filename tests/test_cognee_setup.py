"""Tests for Cognee startup configuration bridging."""

from __future__ import annotations

import pytest

from app.integrations import cognee_setup


@pytest.fixture(autouse=True)
def clear_cognee_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "LLM_API_KEY",
        "LLM_ENDPOINT",
        "LLM_MODEL",
        "LLM_PROVIDER",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_DIMENSIONS",
        "GRAPH_DATABASE_PROVIDER",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_llm_api_key_prefers_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    assert cognee_setup.resolve_llm_api_key() == "or-key"


def test_resolve_llm_api_key_honors_explicit_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "explicit")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert cognee_setup.resolve_llm_api_key() == "explicit"


def test_build_cognee_env_overrides_from_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    monkeypatch.setenv("DEFAULT_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("DEFAULT_EMBEDDING_DIMENSIONS", "1536")
    overrides = cognee_setup.build_cognee_env_overrides()
    assert overrides["LLM_API_KEY"] == "or-key"
    assert overrides["LLM_ENDPOINT"] == "https://openrouter.ai/api/v1"
    assert overrides["EMBEDDING_API_KEY"] == "oa-key"
    assert overrides["EMBEDDING_MODEL"] == "openai/text-embedding-3-small"
    assert overrides["EMBEDDING_DIMENSIONS"] == "1536"


def test_configure_cognee_without_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cognee_setup, "_try_import_cognee", lambda: None)
    status = cognee_setup.configure_cognee()
    assert status["configured"] is False
    assert status["reason"] == "cognee_not_installed"


def test_provider_model_adds_prefix() -> None:
    assert cognee_setup._provider_model("gpt-4o-mini", "openai") == "openai/gpt-4o-mini"
    assert cognee_setup._provider_model("openai/gpt-4o-mini", "openai") == "openai/gpt-4o-mini"
