"""
Hermes Gateway agent backend.

Wraps the Hermes Agent Gateway API (POST /v1/responses) which provides
stateful multi-turn conversations with tools and memory.
"""

import os
import requests

from .base import AgentBackend


class HermesGatewayAgent(AgentBackend):
    """Agent backend that talks to Hermes Gateway."""

    def __init__(self):
        self.base_url = os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642").rstrip("/")
        self.token = os.environ.get("HERMES_GATEWAY_TOKEN", "")
        self.model_override = os.environ.get("HERMES_MODEL_OVERRIDE", "")

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def health_check(self) -> dict:
        try:
            r = requests.get(f"{self.base_url}/v1/models", headers=self._headers(), timeout=3)
            if r.status_code == 200:
                models = r.json().get("data", [])
                return {"ok": True, "model": models[0].get("id", "unknown") if models else "unknown"}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_models(self) -> list:
        try:
            r = requests.get(f"{self.base_url}/v1/models", headers=self._headers(), timeout=5)
            if r.status_code == 200:
                return [m.get("id", "") for m in r.json().get("data", [])]
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
        resp = requests.post(
            f"{self.base_url}/v1/responses",
            headers=self._headers(),
            json={
                "model": self.model_override or "default",
                "input": user_text,
                "conversation": conversation_id or f"call-{call_sid}",
                "instructions": system_prompt,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract text from Responses API format
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        return content["text"].strip()

        # Fallback for chat completions format
        if "choices" in data:
            return data["choices"][0]["message"]["content"].strip()

        raise RuntimeError("Unexpected response format from Hermes Gateway")
