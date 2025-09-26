"""Simple agent that echoes an input message."""
from __future__ import annotations

from typing import Any, Dict

from src.core.base_agent import BaseAgent, Payload, Result


class EchoAgent(BaseAgent):
    """Returns the provided message along with basic metadata."""

    def __init__(self) -> None:
        super().__init__(name="echo")

    def run(self, payload: Payload) -> Result:
        message = str(payload.get("message", ""))
        response: Dict[str, Any] = {
            "agent": self.name,
            "message": message,
            "characters": len(message),
            "words": len(message.split()) if message else 0,
        }
        return response


def build_agent() -> EchoAgent:
    return EchoAgent()


__all__ = ["EchoAgent", "build_agent"]
