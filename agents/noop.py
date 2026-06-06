"""
No-op agent backend — fallback when no agent is configured.
"""

from .base import AgentBackend


class NoOpAgent(AgentBackend):
    """Returns an error message when no backend is configured."""

    def health_check(self) -> dict:
        return {"ok": False, "error": "No agent backend configured"}

    def get_models(self) -> list:
        return []

    def chat(
        self,
        call_sid: str,
        user_text: str,
        system_prompt: str,
        conversation_id: str | None = None,
        history: list | None = None,
    ) -> str:
        return "No agent backend configured. Please set AGENT_PROVIDER in your .env file."
