#!/usr/bin/env python3
"""Scheduler Agent - Handles content scheduling and rescheduling."""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class SchedulerAgent(BaseAgent):
    """Agent that handles content scheduling and rescheduling."""

    def __init__(self):
        super().__init__("scheduler")

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Schedule or reschedule content publication.

        Expected payload:
        {
            "action": "reschedule",
            "card": {
                "id": "c-12345",
                "title": "Blog post about Redis",
                "meta": {"scheduled_time": "2025-09-30T10:00:00Z"}
            },
            "from_column": "Scheduled",
            "new_time": "2025-10-01T14:00:00Z",
            "reason": "Conflict with other content"
        }
        """
        card = payload.get("card", {})
        card_id = card.get("id", "unknown")
        title = card.get("title", "Untitled")
        action = payload.get("action", "reschedule")

        logger.info(f"Scheduling action '{action}' for: {title} (ID: {card_id})")

        try:
            meta = card.get("meta", {})
            old_time = meta.get("scheduled_time")
            new_time = payload.get("new_time")
            reason = payload.get("reason", "Rescheduled")

            # If no new time provided, suggest next day
            if not new_time:
                if old_time:
                    base = datetime.fromisoformat(old_time.replace("Z", "+00:00"))
                    new_dt = base + timedelta(days=1)
                else:
                    new_dt = datetime.now(timezone.utc) + timedelta(days=1)
                    new_dt = new_dt.replace(hour=10, minute=0, second=0, microsecond=0)
                new_time = new_dt.isoformat()

            # Validate new time is in the future
            new_dt = datetime.fromisoformat(new_time.replace("Z", "+00:00"))
            if new_dt < datetime.now(timezone.utc):
                raise ValueError("Cannot schedule in the past")

            # Update card metadata
            meta["scheduled_time"] = new_time
            meta["previous_scheduled_time"] = old_time
            meta["reschedule_reason"] = reason
            meta["rescheduled_at"] = datetime.now(timezone.utc).isoformat()

            # Send notification if Slack configured
            if os.getenv("SLACK_WEBHOOK_URL"):
                self._send_notification(card, old_time, new_time, reason)

            return {
                "success": True,
                "card_id": card_id,
                "title": title,
                "old_time": old_time,
                "new_time": new_time,
                "reason": reason,
                "message": f"Content rescheduled from {old_time[:16] if old_time else 'N/A'} to {new_time[:16]}"
            }

        except Exception as e:
            logger.error(f"Failed to schedule {card_id}: {e}")
            return {
                "success": False,
                "card_id": card_id,
                "error": str(e),
                "message": f"Failed to schedule: {title}"
            }

    def _send_notification(self, card: Dict[str, Any], old_time: str,
                          new_time: str, reason: str) -> None:
        """Send Slack notification about scheduling change."""
        try:
            from src.agents.slack_notifier import SlackNotifierAgent
            slack = SlackNotifierAgent()

            message = f"📅 Content Rescheduled: {card.get('title', 'Untitled')}"
            message += f"\nOld time: {old_time[:16] if old_time else 'N/A'}"
            message += f"\nNew time: {new_time[:16]}"
            if reason:
                message += f"\nReason: {reason}"

            slack.run({
                "message": message,
                "channel": "#content-schedule",
                "username": "Scheduler Bot",
                "icon_emoji": ":calendar:"
            })

        except Exception as e:
            logger.warning(f"Could not send scheduling notification: {e}")


def build_agent() -> SchedulerAgent:
    """Factory function to create SchedulerAgent instance."""
    return SchedulerAgent()


__all__ = ["SchedulerAgent", "build_agent"]