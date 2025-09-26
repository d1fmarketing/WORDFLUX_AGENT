"""Core abstractions for building WordFlux agents."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

Payload = Dict[str, Any]
Result = Dict[str, Any]


class BaseAgent(ABC):
    """Base class that all agents should inherit from."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def run(self, payload: Payload) -> Result:
        """Execute the agent with the provided payload."""


__all__ = ["BaseAgent", "Payload", "Result"]
