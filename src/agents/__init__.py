"""Agent package initialisation and registrations."""
from __future__ import annotations

from src.core.registry import register_agent
from src.agents.echo_agent import build_agent as build_echo_agent
from src.agents.stripe_disputes import build_agent as build_stripe_agent
from src.agents.slack_notifier import build_agent as build_slack_agent

register_agent("echo", build_echo_agent)
register_agent("stripe.export_disputes", build_stripe_agent)
register_agent("stripe_disputes", build_stripe_agent)  # Alias for API compatibility
register_agent("slack_notifier", build_slack_agent)
register_agent("slack.notify", build_slack_agent)  # Alias for event mapping

__all__ = ["build_echo_agent", "build_stripe_agent", "build_slack_agent"]
