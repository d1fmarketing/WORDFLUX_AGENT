"""Agent package initialisation and registrations."""
from __future__ import annotations

from src.core.registry import register_agent
from src.agents.echo_agent import build_agent as build_echo_agent
from src.agents.stripe_disputes import build_agent as build_stripe_agent

register_agent("echo", build_echo_agent)
register_agent("stripe.export_disputes", build_stripe_agent)

__all__ = ["build_echo_agent", "build_stripe_agent"]
