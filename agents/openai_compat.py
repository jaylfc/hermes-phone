"""
OpenAI-compatible agent backend.

Works with any provider that exposes /v1/chat/completions:
OpenAI, Xiaomi (MiMo), OpenRouter, Ollama, LM Studio, etc.
"""

import os

from .base import AgentBackend


class OpenAICompatAgent(AgentBackend):
    """Agent backend for any OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        # Auto-detect provider from env if not explicitly passed
        self.base_url = base_url or self._detect_base_url()
        self.api_key = api_key or self._detect_api_key()
        self.model = model or os.environ.get("LLM_MODEL", "mimo-v2.5")

    @staticmethod
    def _detect_base_url() -> str:
        provider = os.environ.get("LLM_PROVIDER", "xiaomi")
        if provider == "xiaomi":
            return os.environ.get("XIAOMI_BASE_URL", "https://token-plan-ams.xiaomimimo.com/v1")
        if os.environ.get("OPENROUTER_API_KEY"):
            return os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    @staticmethod
    def _detect_api_key() -> str:
        provider = os.environ.get("LLM_PROVIDER", "xiaomi")
        if provider == "xiaomi" and os.environ.get("XIAOMI_API_KEY"):
            return os.environ["XIAOMI_API_KEY"]
        if os.environ.get("OPENROUTER_API_KEY"):
            return os.environ["OPENROUTER_API_KEY"]
        return os.environ.get("OPENAI_API_KEY", "")

    def _client(self):
        """Lazy-init OpenAI client."""
        from openai import OpenAI
        return OpenAI(base_url=self.base_url, api_key=self.api_key)

    def health_check(self) -> dict:
        try:
            import requests
            r = requests.get(f"{self.base_url}/models", timeout=3)
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_models(self) -> list:
        try:
            import requests
            r = requests.get(f"{self.base_url}/models", timeout=5)
            if r.status_code == 200:
                data = r.json()
                # OpenAI format: {"data": [{"id": "model-name"}, ...]}
                if "data" in data:
                    return [m.get("id", "") for m in data["data"]]
                # Ollama format: {"models": [{"name": "model-name"}, ...]}
                if "models" in data:
                    return [m.get("name", "") for m in data["models"]]
        except Exception:
            pass
        return []

    def chat(
        self,
        call_sid: str,
        user_text: str,
        system_prompt: str,
        conversation_id: str | None = None,
        history: list | None = None,
    ) -> str:
        client = self._client()

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-40:])
        messages.append({"role": "user", "content": user_text})

        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=500,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
