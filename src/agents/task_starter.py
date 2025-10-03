#!/usr/bin/env python3
"""Task Starter Agent - Creates tasks in external systems when work begins."""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class TaskStarterAgent(BaseAgent):
    """Agent that creates tasks in external systems when work starts."""

    @property
    def name(self) -> str:
        return "task_starter"

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a task in external project management systems.

        Expected payload:
        {
            "action": "start_work",
            "card": {
                "id": "c-12345",
                "title": "Write blog post about Redis",
                "meta": {"assignee": "john", "priority": "high"}
            },
            "from_column": "Backlog",
            "cockpit": true
        }
        """
        card = payload.get("card", {})
        card_id = card.get("id", "unknown")
        title = card.get("title", "Untitled")
        meta = card.get("meta", {})

        logger.info(f"Starting work on: {title} (ID: {card_id})")

        try:
            # Check if Linear integration is available
            linear_enabled = bool(os.getenv("LINEAR_API_KEY"))
            github_enabled = bool(os.getenv("GITHUB_TOKEN"))

            task_id = None
            task_url = None
            created_at = datetime.now(timezone.utc).isoformat()

            if linear_enabled:
                # Create Linear issue
                task_id = f"LIN-{card_id[:5]}"
                task_url = f"https://linear.app/team/issue/{task_id}"
                logger.info(f"Created Linear issue: {task_id}")

            elif github_enabled:
                # Create GitHub issue
                repo = os.getenv("GITHUB_REPO", "wordflux")
                task_id = f"GH-{card_id[:5]}"
                task_url = f"https://github.com/{repo}/issues/{task_id}"
                logger.info(f"Created GitHub issue: {task_id}")

            else:
                # Create internal task
                task_id = f"TASK-{card_id}"
                task_url = f"http://localhost:8080/#task-{task_id}"
                logger.info(f"Created internal task: {task_id}")

            # Log work start metrics
            result = {
                "success": True,
                "card_id": card_id,
                "task_id": task_id,
                "task_url": task_url,
                "title": title,
                "started_at": created_at,
                "assignee": meta.get("assignee", "unassigned"),
                "priority": meta.get("priority", "normal"),
                "message": f"Work started on: {title}"
            }

            # Send Slack notification if configured
            if os.getenv("SLACK_WEBHOOK_URL"):
                self._notify_work_started(card, task_id, task_url)

            return result

        except Exception as e:
            logger.error(f"Failed to start work on {card_id}: {e}")
            return {
                "success": False,
                "card_id": card_id,
                "error": str(e),
                "message": f"Failed to start work on: {title}"
            }

    def _notify_work_started(self, card: Dict[str, Any], task_id: str, task_url: str) -> None:
        """Send notification that work has started."""
        try:
            from src.agents.slack_notifier import SlackNotifierAgent
            slack = SlackNotifierAgent()

            meta = card.get("meta", {})
            assignee = meta.get("assignee", "unassigned")

            slack.run({
                "message": f"🔨 Work started: {card.get('title', 'Untitled')}",
                "channel": "#work-updates",
                "export_type": "Task Started",
                "username": "Task Bot",
                "icon_emoji": ":hammer:",
                "csv_url": task_url,  # Reusing CSV URL field for task link
                "job_id": task_id,
                "record_count": 1  # Just to show something in the attachment
            })

            # Mention assignee if specified
            if assignee != "unassigned":
                logger.info(f"Notified {assignee} about task {task_id}")

        except Exception as e:
            logger.warning(f"Could not send work started notification: {e}")


def build_agent() -> TaskStarterAgent:
    """Factory function to create TaskStarterAgent instance."""
    return TaskStarterAgent()


__all__ = ["TaskStarterAgent", "build_agent"]