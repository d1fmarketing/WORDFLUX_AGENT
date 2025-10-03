"""Agent package initialisation and registrations."""
from __future__ import annotations

from src.core.registry import register_agent
from src.agents.echo_agent import build_agent as build_echo_agent
from src.agents.stripe_disputes import build_agent as build_stripe_agent
from src.agents.slack_notifier import build_agent as build_slack_agent
from src.agents.linear_connector import build_agent as build_linear_agent
from src.agents.playbook_runner import build_agent as build_playbook_agent
from src.agents.content_publisher import build_agent as build_content_publisher
from src.agents.task_starter import build_agent as build_task_starter
from src.agents.review_requester import build_agent as build_review_requester
from src.agents.content_approver import build_agent as build_content_approver
from src.agents.metrics_reporter import build_agent as build_metrics_reporter
from src.agents.planner_agent import build_agent as build_planner_agent
from src.agents.change_requester import build_agent as build_change_requester
from src.agents.scheduler import build_agent as build_scheduler
from src.agents.task_pauser import build_agent as build_task_pauser
from src.agents.board_operator import build_agent as build_board_operator
from src.agents.link_validator import build_agent as build_link_validator
from src.agents.seo_analyzer import build_agent as build_seo_analyzer

register_agent("echo", build_echo_agent)
register_agent("stripe.export_disputes", build_stripe_agent)
register_agent("stripe_disputes", build_stripe_agent)  # Alias for API compatibility
register_agent("slack_notifier", build_slack_agent)
register_agent("slack.notify", build_slack_agent)  # Alias for event mapping
register_agent("linear_connector", build_linear_agent)
register_agent("linear.update", build_linear_agent)  # Alias for event mapping
register_agent("playbook_runner", build_playbook_agent)
register_agent("playbook", build_playbook_agent)  # Alias

# Cockpit action agents
register_agent("content_publisher", build_content_publisher)
register_agent("task_starter", build_task_starter)
register_agent("review_requester", build_review_requester)
register_agent("content_approver", build_content_approver)
register_agent("metrics_reporter", build_metrics_reporter)

# Autonomous agents
register_agent("planner_agent", build_planner_agent)
register_agent("planner", build_planner_agent)  # Alias
register_agent("change_requester", build_change_requester)
register_agent("scheduler", build_scheduler)
register_agent("task_pauser", build_task_pauser)

# Board operator (chat integration)
register_agent("board_operator", build_board_operator)
register_agent("card_mover", build_board_operator)  # Alias for move operations
register_agent("card_creator", build_board_operator)  # Alias for create operations
register_agent("card_updater", build_board_operator)  # Alias for update operations
register_agent("card_commenter", build_board_operator)  # Alias for comment operations

# Content quality agents
register_agent("link_validator", build_link_validator)
register_agent("validate_links", build_link_validator)  # Alias for action mapping
register_agent("seo_analyzer", build_seo_analyzer)
register_agent("analyze_seo", build_seo_analyzer)  # Alias for action mapping

__all__ = ["build_echo_agent", "build_stripe_agent", "build_slack_agent",
           "build_linear_agent", "build_playbook_agent", "build_content_publisher",
           "build_task_starter", "build_review_requester", "build_content_approver",
           "build_metrics_reporter", "build_planner_agent", "build_change_requester",
           "build_scheduler", "build_task_pauser", "build_board_operator", "build_link_validator",
           "build_seo_analyzer"]
