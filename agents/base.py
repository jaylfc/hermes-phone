"""
Agent backend abstract interface.

All agent backends (Hermes Gateway, OpenAI-compatible, local, etc.)
implement this ABC so server.py can swap them via AGENT_PROVIDER.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentResponse:
    text: str


class AgentBackend(ABC):
    """Base class every agent backend must implement."""

    @abstractmethod
    def health_check(self) -> dict:
        """Return health status dict. At minimum: {"ok": bool}."""
        ...

    @abstractmethod
    def get_models(self) -> list:
        """Return list of available model IDs (strings)."""
        ...

    @abstractmethod
    def chat(
        self,
        call_sid: str,
        user_text: str,
        system_prompt: str,
        conversation_id: Optional[str] = None,
        history: Optional[list] = None,
    ) -> str:
        """Send a user message and return the assistant's reply text."""
        ...

    def on_call_start(self, call_sid: str, caller: str) -> None:
        """Called when a new call begins. Override for side-effects."""
        pass

    def on_call_end(self, call_sid: str, transcript: list) -> None:
        """Called when a call ends. Override for logging/analytics."""
        pass
