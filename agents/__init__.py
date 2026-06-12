"""
Agent backends — pluggable AI provider system for dialtone.

Usage:
    from agents import get_agent_backend
    backend = get_agent_backend()
    reply = backend.chat(call_sid, user_text, system_prompt)

Supported AGENT_PROVIDER values:
    hermes-gateway  — Hermes Agent Gateway (recommended)
    openai          — OpenAI API
    openrouter      — OpenRouter
    xiaomi          — Xiaomi MiMo
    ollama          — Ollama (local)
    lmstudio        — LM Studio (local)
    openai-compat   — Custom OpenAI-compatible endpoint (uses LLM_BASE_URL_OVERRIDE etc.)
    (empty)         — Auto-detect (try Hermes Gateway, then legacy LLM)
"""

import os

from .base import AgentBackend
from .noop import NoOpAgent

# Out-of-the-box default: local Ollama (offline, keyless). server.py imports
# this so the displayed provider and the actually-selected backend can't drift.
DEFAULT_AGENT_PROVIDER = "ollama"

# Singleton instance — lazy-initialized
_backend: AgentBackend | None = None


def reset_agent_backend() -> None:
    """Drop the cached backend so the next call re-reads configuration."""
    global _backend
    _backend = None


def get_agent_backend() -> AgentBackend:
    """Return the configured agent backend (singleton)."""
    global _backend
    if _backend is not None:
        return _backend

    provider = os.environ.get("AGENT_PROVIDER", DEFAULT_AGENT_PROVIDER).strip().lower()

    # ── Explicit provider ──────────────────────────────────────────
    if provider == "hermes-gateway":
        from .hermes_gateway import HermesGatewayAgent
        _backend = HermesGatewayAgent()
        return _backend

    if provider == "openai-compat":
        from .openai_compat import OpenAICompatAgent
        base_url = os.environ.get("LLM_BASE_URL_OVERRIDE", "")
        api_key = os.environ.get("LLM_API_KEY_OVERRIDE", "")
        model = os.environ.get("LLM_MODEL_OVERRIDE", "")
        if not base_url:
            print("⚠️  openai-compat requires LLM_BASE_URL_OVERRIDE in .env")
            _backend = NoOpAgent()
            return _backend
        _backend = OpenAICompatAgent(base_url=base_url, api_key=api_key, model=model)
        return _backend

    if provider in ("openai", "openrouter", "xiaomi", "ollama", "lmstudio"):
        from .openai_compat import OpenAICompatAgent
        # Derive params from provider name
        base_url, api_key, model = _resolve_openai_compat(provider)
        _backend = OpenAICompatAgent(base_url=base_url, api_key=api_key, model=model)
        return _backend

    # ── Auto-detect ("auto", or empty string written by the settings UI) ──
    if provider in ("", "auto"):
        _backend = _auto_detect()
        return _backend

    # Unknown provider → NoOp
    print(f"⚠️  Unknown AGENT_PROVIDER={provider!r}, falling back to no-op")
    _backend = NoOpAgent()
    return _backend


def _auto_detect() -> AgentBackend:
    """Try Hermes Gateway first, then legacy LLM, then no-op."""
    # 1. Try Hermes Gateway
    hermes_url = os.environ.get("HERMES_GATEWAY_URL", "")
    if hermes_url:
        from .hermes_gateway import HermesGatewayAgent
        backend = HermesGatewayAgent()
        health = backend.health_check()
        if health.get("ok"):
            print(f"  ✅ Agent: Hermes Gateway at {hermes_url}")
            return backend
        print(f"  ⚠️  Hermes Gateway at {hermes_url} unreachable ({health.get('error', '?')}), trying fallback...")

    # 2. Try legacy OpenAI-compatible LLM
    from .openai_compat import OpenAICompatAgent
    backend = OpenAICompatAgent()
    if backend.api_key:
        health = backend.health_check()
        if health.get("ok"):
            print(f"  ✅ Agent: {backend.base_url} ({backend.model})")
            return backend
        # Key present but endpoint unreachable — still use it, calls may work later
        print(f"  ⚠️  LLM at {backend.base_url} unreachable ({health.get('error', '?')}), will retry on calls")
        return backend

    # 3. Nothing configured
    print("  ⚠️  No agent backend configured — set AGENT_PROVIDER in .env")
    return NoOpAgent()


def _resolve_openai_compat(provider: str) -> tuple[str, str, str]:
    """Return (base_url, api_key, model) for a named OpenAI-compat provider."""
    if provider == "xiaomi":
        return (
            os.environ.get("XIAOMI_BASE_URL", "https://token-plan-ams.xiaomimimo.com/v1"),
            os.environ.get("XIAOMI_API_KEY", ""),
            os.environ.get("LLM_MODEL", "mimo-v2.5"),
        )
    if provider == "openrouter":
        return (
            os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            os.environ.get("OPENROUTER_API_KEY", ""),
            os.environ.get("LLM_MODEL", "openrouter/auto"),
        )
    if provider == "ollama":
        return (
            os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "ollama",  # Ollama doesn't need a real key
            os.environ.get("LLM_MODEL", "qwen3:8b"),  # matches server.py default
        )
    if provider == "lmstudio":
        return (
            os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            "lm-studio",
            os.environ.get("LLM_MODEL", "default"),
        )
    # Default: openai
    return (
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        os.environ.get("OPENAI_API_KEY", ""),
        os.environ.get("LLM_MODEL", "gpt-4o-mini"),
    )
