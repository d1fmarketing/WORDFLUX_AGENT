"""Agent package initialisation and registrations."""
from __future__ import annotations

from src.core.registry import register_agent
from src.agents.echo_agent import build_agent

register_agent("echo", build_agent)

__all__ = ["build_agent"]
