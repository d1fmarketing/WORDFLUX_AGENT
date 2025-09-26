"""Agent registry for lookup and instantiation."""
from __future__ import annotations

from typing import Callable, Dict, List

from src.core.base_agent import BaseAgent

AgentFactory = Callable[[], BaseAgent]

_registry: Dict[str, AgentFactory] = {}


def register_agent(name: str, factory: AgentFactory) -> None:
    """Register an agent factory under a normalized name."""
    key = name.strip().lower()
    if not key:
        raise ValueError("Agent name cannot be empty.")
    if key in _registry and _registry[key] is not factory:
        raise ValueError(f"Agent '{name}' is already registered.")
    _registry[key] = factory


def create_agent(name: str) -> BaseAgent:
    """Instantiate an agent by name."""
    key = name.strip().lower()
    try:
        factory = _registry[key]
    except KeyError as exc:
        available = ", ".join(sorted(_registry.keys())) or "<none>"
        raise KeyError(f"Agent '{name}' is not registered. Available: {available}.") from exc
    return factory()


def available_agents() -> List[str]:
    """Return the list of registered agent identifiers."""
    return sorted(_registry.keys())


def unregister_agent(name: str) -> None:
    """Remove an agent registration, primarily for testing."""
    key = name.strip().lower()
    _registry.pop(key, None)


__all__ = ["register_agent", "create_agent", "available_agents", "unregister_agent"]
