#!/usr/bin/env python3
"""Task Pauser Agent - Handles pausing and resuming tasks."""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class TaskPauserAgent(BaseAgent):
    """Agent that pauses and resumes tasks in the workflow."""

    def __init__(self):
        super().__init__("task_pauser")

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Pause or resume a task.

        Expected payload:
        {
            "action": "pause",
            "card": {
                "id": "c-12345",
                "title": "Blog post about Redis"
            },
            "from_column": "In Progress",
            "pause_reason": "Waiting for design assets",
            "pause_duration": "2h"
        }
        """
        card = payload.get("card", {})
        card_id = card.get("id", "unknown")
        title = card.get("title", "Untitled")
        action = payload.get("action", "pause")
        from_column = payload.get("from_column", "Unknown")

        logger.info(f"Task {action} requested for: {title} (ID: {card_id})")

        try:
            if action == "pause":
                return self._pause_task(card, payload, from_column)
            elif action in ["resume", "unpause"]:
                return self._resume_task(card, payload, from_column)
            else:
                raise ValueError(f"Unknown pause action: {action}")

        except Exception as e:
            logger.error(f"Failed to {action} task {card_id}: {e}")
            return {
                "success": False,
                "card_id": card_id,
                "error": str(e),
                "message": f"Failed to {action}: {title}"
            }

    def _pause_task(self, card: Dict[str, Any], payload: Dict[str, Any],
                   from_column: str) -> Dict[str, Any]:
        """Pause a task."""
        card_id = card.get("id")
        title = card.get("title")
        pause_reason = payload.get("pause_reason", "Paused")
        pause_duration = payload.get("pause_duration")

        timestamp = datetime.now(timezone.utc)
        pause_id = f"pause-{card_id}-{int(timestamp.timestamp())}"

        # Update card metadata
        meta = card.get("meta", {})
        meta["paused"] = True
        meta["pause_id"] = pause_id
        meta["pause_reason"] = pause_reason
        meta["paused_at"] = timestamp.isoformat()
        meta["paused_from"] = from_column

        if pause_duration:
            # Calculate auto-resume time
            resume_time = self._parse_duration(pause_duration, timestamp)
            meta["auto_resume_at"] = resume_time.isoformat()

        # Send notification if Slack configured
        if os.getenv("SLACK_WEBHOOK_URL"):
            self._send_notification(card, "paused", pause_reason)

        return {
            "success": True,
            "card_id": card_id,
            "title": title,
            "pause_id": pause_id,
            "paused": True,
            "reason": pause_reason,
            "paused_from": from_column,
            "auto_resume_at": meta.get("auto_resume_at"),
            "message": f"Task paused: {title}"
        }

    def _resume_task(self, card: Dict[str, Any], payload: Dict[str, Any],
                    from_column: str) -> Dict[str, Any]:
        """Resume a paused task."""
        card_id = card.get("id")
        title = card.get("title")
        meta = card.get("meta", {})

        pause_id = meta.get("pause_id")
        paused_from = meta.get("paused_from", "In Progress")

        timestamp = datetime.now(timezone.utc)

        # Calculate pause duration
        paused_at_str = meta.get("paused_at")
        if paused_at_str:
            paused_at = datetime.fromisoformat(paused_at_str.replace("Z", "+00:00"))
            pause_duration = (timestamp - paused_at).total_seconds()
        else:
            pause_duration = 0

        # Update card metadata
        meta["paused"] = False
        meta["resumed_at"] = timestamp.isoformat()
        meta["pause_duration_seconds"] = pause_duration

        # Keep pause history
        if "pause_history" not in meta:
            meta["pause_history"] = []
        meta["pause_history"].append({
            "pause_id": pause_id,
            "paused_at": meta.get("paused_at"),
            "resumed_at": timestamp.isoformat(),
            "duration_seconds": pause_duration,
            "reason": meta.get("pause_reason")
        })

        # Clean up pause metadata
        meta.pop("pause_id", None)
        meta.pop("pause_reason", None)
        meta.pop("paused_at", None)
        meta.pop("auto_resume_at", None)

        # Send notification if Slack configured
        if os.getenv("SLACK_WEBHOOK_URL"):
            duration_str = self._format_duration(pause_duration)
            self._send_notification(card, "resumed", f"Paused for {duration_str}")

        return {
            "success": True,
            "card_id": card_id,
            "title": title,
            "paused": False,
            "resume_to": paused_from,
            "pause_duration_seconds": pause_duration,
            "message": f"Task resumed: {title}"
        }

    def _parse_duration(self, duration_str: str, from_time: datetime) -> datetime:
        """Parse duration string like '2h', '1d', '30m' into future datetime."""
        duration_str = duration_str.strip().lower()

        if duration_str.endswith('m'):
            minutes = int(duration_str[:-1])
            return from_time + timedelta(minutes=minutes)
        elif duration_str.endswith('h'):
            hours = int(duration_str[:-1])
            return from_time + timedelta(hours=hours)
        elif duration_str.endswith('d'):
            days = int(duration_str[:-1])
            return from_time + timedelta(days=days)
        else:
            # Default: treat as hours
            hours = int(duration_str)
            return from_time + timedelta(hours=hours)

    def _format_duration(self, seconds: float) -> str:
        """Format seconds into human-readable duration."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m"
        elif seconds < 86400:
            return f"{int(seconds / 3600)}h"
        else:
            return f"{int(seconds / 86400)}d"

    def _send_notification(self, card: Dict[str, Any], action: str, details: str = "") -> None:
        """Send Slack notification about pause/resume."""
        try:
            from src.agents.slack_notifier import SlackNotifierAgent
            slack = SlackNotifierAgent()

            if action == "paused":
                emoji = "⏸️"
                verb = "Paused"
            else:
                emoji = "▶️"
                verb = "Resumed"

            message = f"{emoji} Task {verb}: {card.get('title', 'Untitled')}"
            if details:
                message += f"\n{details}"

            slack.run({
                "message": message,
                "channel": "#task-updates",
                "username": "Pause Bot",
                "icon_emoji": ":pause_button:" if action == "paused" else ":arrow_forward:"
            })

        except Exception as e:
            logger.warning(f"Could not send pause notification: {e}")


def build_agent() -> TaskPauserAgent:
    """Factory function to create TaskPauserAgent instance."""
    return TaskPauserAgent()


__all__ = ["TaskPauserAgent", "build_agent"]