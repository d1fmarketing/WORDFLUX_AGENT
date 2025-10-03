#!/usr/bin/env python3
"""Change Requester Agent - Handles content rejection and change requests."""

import logging
import os
import json
from datetime import datetime, timezone
from typing import Dict, Any

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ChangeRequesterAgent(BaseAgent):
    """Agent that handles content rejection and requests changes."""

    def __init__(self):
        super().__init__("change_requester")

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Request changes to content and move it back to In Progress.

        Expected payload:
        {
            "action": "request_changes",
            "card": {
                "id": "c-12345",
                "title": "Blog post about Redis"
            },
            "from_column": "Waiting Approval",
            "change_notes": "Please fix the code examples in section 3",
            "cockpit": true
        }
        """
        card = payload.get("card", {})
        card_id = card.get("id", "unknown")
        title = card.get("title", "Untitled")
        change_notes = payload.get("change_notes", "Changes requested")

        logger.info(f"Requesting changes for: {title} (ID: {card_id})")

        try:
            timestamp = datetime.now(timezone.utc)
            change_request_id = f"chg-{card_id}-{int(timestamp.timestamp())}"

            # Update card metadata to include change request
            if "meta" not in card:
                card["meta"] = {}
            card["meta"]["last_change_request"] = change_request_id
            card["meta"]["change_notes"] = change_notes
            card["meta"]["iteration"] = card["meta"].get("iteration", 0) + 1
            card["meta"]["requested_at"] = timestamp.isoformat()

            # Send notifications if Slack configured
            if os.getenv("SLACK_WEBHOOK_URL"):
                self._send_notification(card, change_notes)

            return {
                "success": True,
                "card_id": card_id,
                "change_request_id": change_request_id,
                "notes": change_notes,
                "iteration": card["meta"]["iteration"],
                "message": f"Changes requested for: {title}"
            }

        except Exception as e:
            logger.error(f"Failed to request changes for {card_id}: {e}")
            return {
                "success": False,
                "card_id": card_id,
                "error": str(e),
                "message": f"Failed to request changes for: {title}"
            }

    def _send_notification(self, card: Dict[str, Any], change_notes: str) -> None:
        """Send Slack notification about change request."""
        try:
            from src.agents.slack_notifier import SlackNotifierAgent
            slack = SlackNotifierAgent()

            author = card.get("meta", {}).get("author", "unknown")
            iteration = card.get("meta", {}).get("iteration", 1)

            message = f"🔄 Changes Requested: {card.get('title', 'Untitled')}"
            if author and author != "unknown":
                message += f"\nAuthor: @{author}"
            message += f"\nIteration: #{iteration}"
            message += f"\n\nFeedback:\n{change_notes}"

            slack.run({
                "message": message,
                "channel": "#content-changes",
                "username": "Review Bot",
                "icon_emoji": ":repeat:"
            })

        except Exception as e:
            logger.warning(f"Could not send change request notification: {e}")


def build_agent() -> ChangeRequesterAgent:
    """Factory function to create ChangeRequesterAgent instance."""
    return ChangeRequesterAgent()


__all__ = ["ChangeRequesterAgent", "build_agent"]