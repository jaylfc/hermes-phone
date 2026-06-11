"""Offline-by-default: a fresh install (no .env keys) should select the local
Ollama backend — asserting the backend the factory actually builds, not just
the display constant (regression: #67, where the factory default diverged
from server.py and a fresh install silently got NoOpAgent)."""

import pytest

import agents
import server
from agents.openai_compat import OpenAICompatAgent

# Every env var that can influence backend selection in agents/__init__.py
_SELECTION_VARS = (
    "AGENT_PROVIDER", "HERMES_GATEWAY_URL", "HERMES_GATEWAY_TOKEN",
    "LLM_BASE_URL_OVERRIDE", "LLM_API_KEY_OVERRIDE", "LLM_MODEL_OVERRIDE",
    "LLM_PROVIDER", "LLM_MODEL", "OLLAMA_BASE_URL", "LMSTUDIO_BASE_URL",
    "XIAOMI_API_KEY", "XIAOMI_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
)


@pytest.fixture()
def scrubbed_env(monkeypatch):
    """Simulate a fresh install: no selection-relevant env vars, no cached backend."""
    for var in _SELECTION_VARS:
        monkeypatch.delenv(var, raising=False)
    agents.reset_agent_backend()
    yield
    agents.reset_agent_backend()


def test_display_constant_matches_factory_default():
    assert server.AGENT_PROVIDER == agents.DEFAULT_AGENT_PROVIDER == "ollama"


def test_fresh_install_selects_local_ollama(scrubbed_env):
    backend = agents.get_agent_backend()
    assert isinstance(backend, OpenAICompatAgent)
    assert backend.base_url == "http://localhost:11434/v1"
    assert backend.model.startswith("qwen3")  # installer tiers the exact tag by RAM


def test_explicit_auto_still_auto_detects(scrubbed_env, monkeypatch):
    # "auto" (or the empty string the settings UI writes) must keep auto-detect
    # behaviour; with nothing configured that lands on NoOpAgent.
    from agents.noop import NoOpAgent
    monkeypatch.setenv("AGENT_PROVIDER", "auto")
    agents.reset_agent_backend()
    assert isinstance(agents.get_agent_backend(), NoOpAgent)


def test_voice_defaults_to_local():
    assert server.STT_PROVIDER == "whisper"
    assert server.TTS_PROVIDER == "kokoro"
    # USE_LOCAL_VOICE default is "auto" (local-first); conftest forces it off for
    # test isolation, so we don't assert on it here.
